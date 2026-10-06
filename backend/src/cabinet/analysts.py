"""AI employees (ROADMAP §3 layer 4): the Enrollment Analyst, the
Student Success Analyst, and the Chief of Staff.

Each analyst receives only the findings its role is permitted (the gate in
``permissions.py`` runs before any model call and logs ``data.granted`` with
exactly the fields received), builds the prompt, calls the provider, and
validates the output before anything is shown:

- every claim must carry the ID of a finding the analyst received, in brackets;
- every numeral and date in a claim must come from the findings *that claim
  cites* (per-claim attribution): ISO dates are whole tokens, signs must match
  exactly, an unsigned decline must say so with a decline word, and direction
  is checked per number (the direction word nearest each numeral), not per
  claim. See ``check_numerals`` for the full rule set;
- students are never described with "risk"/"at-risk"/"risk score"/"score"
  language, and a negated direction ("not down", "isn't up", "no decline")
  next to a change number is rejected.

What the validator covers and what it cannot: it guarantees that every number,
date, and direction word in the output traces to the cited findings — the
model cannot invent, misattribute, or flip a figure. It cannot judge
qualitative wording: "sharply", "worrying", or "strong" carry no number, so
they pass unchecked (the system prompt discourages them, and the human
reviewer is the last line). It also cannot judge whether a true number is
*relevant* — only that it is real.

A failed validation gets one corrective try by default
(``CABINET_VALIDATION_RETRIES``, 0 turns it off): the same inputs plus one
short message that states the validator's reason in plain words and adds no
data. The new answer is validated again. A replayed answer never takes this
path. A validation failure that remains is logged and returned as
unavailable — never shown. A successful run logs ``finding.produced``, with
the number of corrective tries in its ``validation_retries`` field. Only
validated output is recorded for replay: the runner calls the recorder's
``save`` after validation, so a bad live answer can never overwrite a good
recording.

The Chief of Staff (``run_chief_of_staff``) follows the same runner pattern
with two differences. It receives the aggregate findings for the asked
question's chief dispatch (all of M1–M8 for the default question, plus the
M9 count or its suppressed marker when the institution has authorized it;
row IDs stripped, like every role) plus the two analysts' *validated*
explanations — never student rows, never the counseling group — and its gate
is the aggregate grant (``data.granted`` at level ``aggregate``). It writes
exactly two briefing sections and answers with a JSON object only,
``{"executive_summary": ..., "limitations": ...}``: section 1 (five sentences
or fewer, every claim tagged with finding IDs) and section 7 (known
limitations, missing data, or conflicting definitions). Both texts go through
the same validation as the analysts — claim IDs, the numeral check per claim,
direction, dates, risk wording, student IDs — and the summary's sentence cap
is enforced structurally. Prose, markdown fences, or extra keys are rejected
and never shown. The Chief of Staff logs no ``finding.produced``; the API
logs ``briefing.produced`` once per run.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from weakref import WeakKeyDictionary

from cabinet.audit import AuditSink
from cabinet.counseling import M9_ID
from cabinet.permissions import (
    ROLE_TASK_FIELDS,
    findings_for_role,
    grant_aggregates,
    grant_authorized_aggregate,
    normalize_field,
    request_fields,
)
from cabinet.provider import (
    Explanation,
    Provider,
    ProviderUnavailable,
    RecordingProvider,
    canonical_findings_json,
)
from cabinet.questions import (
    DEFAULT_QUESTION,
    QUESTION_KEY,
    Question,
    question_payload,
    received_for,
)

logger = logging.getLogger(__name__)

ENROLLMENT_ANALYST = "enrollment_analyst"
STUDENT_SUCCESS_ANALYST = "student_success_analyst"
CHIEF_OF_STAFF = "chief_of_staff"

# The roles record_golden runs and records (both briefing analysts, then the
# Chief of Staff on their validated output — see record_golden.py).
ANALYST_RECORD_ROLES = (ENROLLMENT_ANALYST, STUDENT_SUCCESS_ANALYST)

# System prompts per role (ROADMAP §3 layer 4: each role has a task template
# and a system prompt). The four rules below are the cabinet's contract:
# explain, never create numbers; bracketed finding IDs on every claim; students
# are people who may need support, never risk scores; no speculation about
# causes not in the findings.
SYSTEM_PROMPTS: dict[str, str] = {
    ENROLLMENT_ANALYST: (
        "You are the Enrollment Analyst for a university president's weekly "
        "student-success briefing. You explain the findings you are given; you "
        "never create numbers. Every number you write must come from the "
        "findings object in the user message. Every claim you make ends with "
        "the ID of the finding it explains, in brackets, like [M2]. Students "
        "are people who may need support, never risk scores. Do not speculate "
        "about causes that are not in the findings. Write every number as "
        "digits. Write two to four short "
        "sentences for a busy executive."
    ),
    STUDENT_SUCCESS_ANALYST: (
        "You are the Student Success Analyst for a university president's "
        "weekly student-success briefing. You explain the common "
        "administrative and advising barriers among continuing students who "
        "have not registered, using only the findings you are given; you "
        "never create numbers. Every number you write must come from the "
        "findings object in the user message. Every claim you make ends with "
        "the ID of the finding it explains, in brackets, like [M3]. Students "
        "are people who may need support, never risk scores or financial "
        "units. Do not speculate about causes that are not in the findings. "
        "Never mention counseling, faith, or spiritual care. Write every "
        "number as digits. Write two to four short sentences for a busy "
        "executive."
    ),
    CHIEF_OF_STAFF: (
        "You are the Chief of Staff for a university president's weekly "
        "student-success briefing. You receive aggregate findings and the "
        "validated explanations of two analysts, and you write exactly two "
        "sections of the briefing: the executive summary (section 1) and the "
        "known limitations (section 7). Answer with a JSON object only, of "
        'the form {"executive_summary": "...", "limitations": "..."} — no '
        "markdown, no code fences, no other keys, no other text. The "
        "executive summary is five sentences or fewer for a busy executive. "
        "Every sentence ends with the IDs of every finding whose numbers it "
        "uses, in one bracket group like [M3, M4]; prefer one finding per "
        "sentence, and you do not have to cite every finding. The "
        "limitations section states known "
        "limitations, missing data, or conflicting definitions; you may note "
        "that registered credit hours versus the prior year (M7) is an "
        "optional measure, that all records are fictional demonstration "
        "data, and that the as-of date comes from the data rather than "
        "today's date; every sentence there ends with the IDs of the findings "
        "it uses, in brackets, too. Every number you write must come from the findings "
        "object; you never create numbers. Students are people who may need "
        "support, never risk scores. Do not speculate about causes that are "
        "not in the findings. Never mention counseling, faith, or spiritual "
        "care. Write every number as digits."
    ),
}


class OutputRejected(Exception):
    """The provider's output failed validation and must never be shown.

    ``unknown_finding_ids`` is set when the output cited finding IDs the role
    did not receive, so the corrective retry can name them in plain words.
    """

    def __init__(
        self, message: str, *, unknown_finding_ids: tuple[str, ...] = ()
    ) -> None:
        super().__init__(message)
        self.unknown_finding_ids = unknown_finding_ids


@dataclass(frozen=True)
class Claim:
    """One validated claim: its rendered text and the finding IDs it cites.

    ``text`` is what the UI renders: the claim body with any leading
    punctuation and conjunction ("and"/"but"/"while") stripped and the first
    letter capitalised, so a model's "…[M3], and 12 have had …" renders as
    "12 have had …". ``source_text`` is the body exactly as the model wrote
    it; validation always runs on ``source_text``, never on the trimmed
    rendering. ``source_text`` defaults to ``text`` for claims constructed
    outside ``parse_claims``.
    """

    text: str
    finding_ids: list[str]
    source_text: str = ""


@dataclass(frozen=True)
class AnalystResult:
    """What an analyst run yields. ``available=False`` means the UI renders
    its "model unavailable" state; nothing unvalidated is ever shown."""

    available: bool
    text: str | None
    claims: list[Claim]
    provider: str | None
    model_label: str | None
    recorded: bool
    reason: str | None
    # From a re-keyed golden recording: the key the text was first validated
    # under (None for a live answer or an original recording).
    rekeyed_from: str | None = None
    # Corrective tries made after a validation failure (0 when the first
    # answer passed or the retry is off).
    validation_retries: int = 0


def build_prompt(findings: dict[str, Any], role: str) -> tuple[str, str]:
    """(system, user) for one explain call. The findings JSON is the only
    data the model ever sees (ROADMAP §3: the model never touches a number it
    did not receive). For the Chief of Staff the payload is the
    ``chief_received`` object: the aggregate findings plus the analysts'
    validated explanations. A question other than the default adds one line —
    "The president asked: …" — carried under ``QUESTION_KEY``; the default
    question's prompt bytes are exactly the pre-registry ones, so its golden
    recordings still match."""
    if role.startswith("explore_"):
        # Explore's planner and writer (cabinet.explore.prompts): imported
        # here, lazily, because explore imports this module's validator.
        from cabinet.explore.prompts import build_explore_prompt

        return build_explore_prompt(findings, role)
    system = SYSTEM_PROMPTS.get(role)
    if system is None:
        raise ValueError(f"no system prompt for role {role!r}")
    brief = _question_brief_line(findings)
    if role == CHIEF_OF_STAFF:
        aggregate = findings["findings"]
        user = (
            brief + f"Role: {role}\n"
            f"Aggregate findings you may cite (IDs: {', '.join(sorted(aggregate))}). "
            "Every sentence must end with the IDs of every finding it uses, in one "
            "bracket group like [M3, M4], and every number must come from these "
            "findings:\n"
            + json.dumps(aggregate, indent=2, ensure_ascii=False)
            + "\nThe analysts' validated explanations (already checked against "
            "the findings; write your own sentences, do not copy their "
            "brackets):\n"
            + json.dumps(findings["analyst_explanations"], indent=2, ensure_ascii=False)
            + '\nAnswer with a JSON object only: {"executive_summary": "...", '
            '"limitations": "..."}.'
        )
        return system, user
    citable = {
        key: value for key, value in findings.items() if _FINDING_KEY_RE.fullmatch(key)
    }
    user = (
        brief + f"Role: {role}\n"
        f"Findings you may cite (IDs: {', '.join(sorted(citable))}). "
        "Every claim must end with its finding ID in brackets, and every "
        "number must come from these findings:\n"
        + json.dumps(citable, indent=2, ensure_ascii=False)
    )
    return system, user


# A finding key (M1 … M7), as opposed to the QUESTION_KEY a non-default
# question adds to the received payload.
_FINDING_KEY_RE = re.compile(r"M\d+")


def _question_brief_line(payload: dict[str, Any]) -> str:
    """The one line a non-default question adds to the user prompt ("The
    president asked: …"). The default question's payload carries no
    ``QUESTION_KEY``, so its prompt bytes are exactly the pre-registry ones."""
    question = payload.get(QUESTION_KEY)
    if not isinstance(question, dict):
        return ""
    brief = question.get("brief") or f"The president asked: {question.get('text')}"
    return str(brief) + "\n"


# --- output validation --------------------------------------------------------

# A claim terminator: one or more adjacent bracket groups like [M2] or
# [M1, M2], plus the sentence-ending period that may follow them. The claim
# body is everything since the previous terminator.
_CLAIM_END_RE = re.compile(r"((?:\[(?:M\d(?:\s*,\s*M\d)*)\](?:\s*\.)?\s*)+)")
_FINDING_ID_RE = re.compile(r"M\d")

# A numeral token: optional explicit sign (ASCII or U+2212 minus), digits with
# optional thousands separators (only in groups of three, so the comma in
# "119, versus 125" ends the numeral), optional decimal part, optional percent form
# ("%", "percent", "per cent"). Every digit run is examined, including digits
# glued to letters or hyphens ("FY2031", "STU-0999") — a model may not smuggle
# a number past the scan inside a word; student-ID-shaped tokens are rejected
# outright before this scan, and bracketed/prose finding IDs (M1-M7) are
# exempt. ISO and written dates are handled separately and stripped before
# this regex runs.
_NUMERAL_RE = re.compile(
    r"([−+\-]?)((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
    r"(\s*(?:%|percent\b|per\s+cent\b))?",
    re.IGNORECASE,
)

# A student-ID-shaped token (the fixture's STU-/PRI- row IDs). The model never
# receives row IDs, so one in the output means fabrication or leakage — reject
# outright, before the numeral scan would read its digits as a plain number.
_STUDENT_ID_RE = re.compile(r"\b(?:STU|PRI)-\d+\b", re.IGNORECASE)

# Cardinal number words ("forty-two", "Forty two", "eighteen", "one hundred
# twenty-five") and "point" decimals ("four point eight percent"). A live
# model run wrote "Forty-two continuing students…" and the digit-only scan
# passed it unchecked — so word forms are converted to digits and validated
# exactly like numeral tokens, percent forms included. Ordinals ("first") and
# the article "a" are not in the vocabulary and are never treated as counts; a
# lone "one" ("one of the students") is ignored by the parser below.
_UNIT_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
_TENS_WORDS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
_SCALE_WORDS = ("hundred", "hundreds", "thousand", "thousands")
# Single-digit words: the only words allowed after "point".
_DIGIT_WORDS = {word: value for word, value in _UNIT_WORDS.items() if value < 10}
_WORDS_ALT = "|".join(
    sorted(
        [*_UNIT_WORDS, *_TENS_WORDS, *_SCALE_WORDS],
        key=len,
        reverse=True,
    )
)
_DIGITS_ALT = "|".join(sorted(_DIGIT_WORDS, key=len, reverse=True))
_PERCENT_SUFFIX = r"(?:\s*(?:%|percent\b|per\s+cent\b))?"
_NUMBER_WORD_RE = re.compile(
    r"\b(?:"
    + _WORDS_ALT
    + r")(?:[- ](?:"
    + _WORDS_ALT
    + r"))*"
    + r"(?:\s+point(?:[- ](?:"
    + _DIGITS_ALT
    + r"))+)?"
    + _PERCENT_SUFFIX,
    re.IGNORECASE,
)
_PERCENT_SUFFIX_RE = re.compile(r"\s*(?:%|percent\b|per\s+cent\b)\s*$", re.IGNORECASE)


def _parse_number_words(phrase: str, *, allow_lone_one: bool = False) -> int | None:
    """A matched number-word phrase -> its value, or None when the phrase is
    not a standalone cardinal: a lone "one" (unless ``allow_lone_one``, used
    for the integer part of a "point" decimal), or scale words with no unit or
    tens word to scale ("hundreds of students" says nothing checkable)."""
    words = [w for w in re.split(r"[-\s]+", phrase.lower()) if w]
    total = 0
    group = 0
    has_unit_or_tens = False
    for word in words:
        if word in _UNIT_WORDS:
            group += _UNIT_WORDS[word]
            has_unit_or_tens = True
        elif word in _TENS_WORDS:
            group += _TENS_WORDS[word]
            has_unit_or_tens = True
        elif word in ("hundred", "hundreds"):
            group = (group if group else 1) * 100
        else:  # thousand(s)
            total += (group if group else 1) * 1000
            group = 0
    if not has_unit_or_tens:
        return None
    if not allow_lone_one and len(words) == 1 and words[0] == "one":
        return None
    return total + group


def _parse_number_word_token(phrase: str) -> tuple[Decimal, bool] | None:
    """One number-word match -> (value, is_percent), or None to skip it.

    Handles plain cardinals ("forty-two") and "point" decimals ("four point
    eight percent" -> 4.8, percent). The optional percent suffix makes the
    token percent-form, so it must match a percent finding exactly like a
    digit token would.
    """
    is_percent = False
    percent_match = _PERCENT_SUFFIX_RE.search(phrase)
    if percent_match:
        is_percent = True
        phrase = phrase[: percent_match.start()]
    parts = re.split(r"\bpoint\b", phrase, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) == 2:
        integer = _parse_number_words(parts[0], allow_lone_one=True)
        if integer is None:
            return None
        digits = "".join(
            str(_DIGIT_WORDS[w]) for w in re.split(r"[-\s]+", parts[1].lower()) if w
        )
        if not digits:
            return None
        return Decimal(f"{integer}.{digits}"), is_percent
    value = _parse_number_words(phrase)
    if value is None:
        return None
    return Decimal(value), is_percent


# Finding IDs mentioned in prose (e.g. "finding M2") are not numerals.
_PROSE_ID_RE = re.compile(r"\bM\d+\b")

_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")

_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
# Unambiguous written dates: "November 20, 2025", "Nov 20, 2025".
_WRITTEN_DATE_RE = re.compile(
    r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")"
    r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?,\s+(\d{4})\b",
    re.IGNORECASE,
)

# Month and day without a year: "as of November 20", "Nov 3rd". Accepted when
# the month and day match an allowed date (the year is the findings' business,
# not the prose's); anything else date-shaped is rejected. Runs after the
# with-year form, and the lookahead keeps it off "November 20, 2025".
_WRITTEN_DATE_NO_YEAR_RE = re.compile(
    r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")"
    r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b(?!\s*,\s*\d{4})",
    re.IGNORECASE,
)

# Direction words (rule (c)): an unsigned numeral matching a
# negative finding is allowed only with a decline word and never with an
# increase word; mirrored for positive findings. Direction is per NUMBER:
# each direction word attaches to the nearest numeral, so "down 4.8 % and
# hours are up 2.7 %" fails on the 2.7 only.
_DECLINE_RE = re.compile(
    r"\b(?:down|lower|below|fewer|decline|declined|decrease|decreased"
    r"|dropped|fell|behind)\b",
    re.IGNORECASE,
)
_INCREASE_RE = re.compile(
    r"\b(?:up|higher|above|more|increase|increased|rose|grew|ahead)\b",
    re.IGNORECASE,
)

# A negated direction ("not down", "isn't up", "no decline", "never fell")
# next to a change number inverts the sign check instead of honoring it —
# or worse, the direction word inside it would satisfy the check. Rejected
# outright for percent numerals. Direction words inside a negation are
# excluded from the ordinary direction assignment.
_NEGATED_DIRECTION_RE = re.compile(
    r"\b(?:[a-z]+n't|not|never|no)\s+"
    r"(?:down|up|lower|higher|decline|declined|decrease|decreased"
    r"|increase|increased|fell|rose|grew|dropped)\b",
    re.IGNORECASE,
)

# Students are people who may need support, never risk scores (system prompt
# rule 3). "risk", "at-risk", "at high risk", "risk score", or "score" in a
# claim that mentions students is rejected.
_RISK_SCORE_RE = re.compile(
    r"\b(?:at[-\s]high[-\s]risk|at[-\s]risk|risk(?:\s+scores?)?|scores?)\b",
    re.IGNORECASE,
)
_STUDENT_WORD_RE = re.compile(r"\bstudents?\b", re.IGNORECASE)


def _render_claim_text(body: str) -> str:
    """The claim body as the UI renders it.

    Strips leading punctuation (commas, semicolons) and one leading
    conjunction ("and"/"but"/"while", with its trailing space), then
    capitalises the first letter — a continuation like ", and 12 have had …"
    renders as "12 have had …". Falls back to the original body when nothing
    would remain.
    """
    text = body.lstrip(" \t,;")
    lowered = text.lower()
    for conjunction in ("and", "but", "while"):
        if lowered.startswith(conjunction) and (
            len(text) == len(conjunction) or text[len(conjunction)] in " \t"
        ):
            text = text[len(conjunction) :].lstrip(" \t")
            break
    if not text:
        return body
    if text[0].isalpha():
        text = text[0].upper() + text[1:]
    return text


def parse_claims(text: str) -> list[Claim]:
    """Split an explanation into claims. Every claim must carry at least one
    bracketed finding ID; any text without one rejects the output. Each
    claim's ``text`` is trimmed for rendering (see ``_render_claim_text``);
    its ``source_text`` keeps the original body for validation."""
    claims: list[Claim] = []
    pos = 0
    for match in _CLAIM_END_RE.finditer(text):
        body = " ".join(text[pos : match.start()].split())
        if not body:
            raise OutputRejected("a finding-ID bracket is not attached to a claim")
        claims.append(
            Claim(
                text=_render_claim_text(body),
                finding_ids=_FINDING_ID_RE.findall(match.group(1)),
                source_text=body,
            )
        )
        pos = match.end()
    trailing = text[pos:].strip()
    if trailing:
        raise OutputRejected(f"claim without a finding ID: {trailing[:60]!r}")
    if not claims:
        raise OutputRejected("no claims with finding IDs in the output")
    return claims


@dataclass(frozen=True)
class AllowedNumber:
    """A number a claim may use: its signed value and percent form.

    Percent-form numbers (from displays like ``−4.8 %``) only match
    percent-form tokens in the text; plain numbers only match plain tokens.
    """

    value: Decimal
    is_percent: bool


def _parse_numeral_token(
    sign: str, digits: str, percent: str | None
) -> tuple[Decimal, bool, bool]:
    """One regex match -> (signed value, is_percent, has_explicit_sign)."""
    magnitude = Decimal(digits.rstrip(",").replace(",", ""))
    is_percent = percent is not None
    if sign in ("-", "−"):
        return -magnitude, is_percent, True
    if sign == "+":
        return magnitude, is_percent, True
    return magnitude, is_percent, False


def _collect_allowed(node: Any, numbers: set[AllowedNumber], dates: set[date]) -> None:
    """Walk a findings value, collecting what prose may use from it.

    Numbers keep their sign; numerals inside strings (e.g. the display
    ``−4.8 %``) keep their percent flag; an ISO date string is collected as
    one whole date and never split into year/month/day numbers.
    """
    if isinstance(node, bool):
        return
    if isinstance(node, (int, float)):
        numbers.add(AllowedNumber(Decimal(str(node)), False))
    elif isinstance(node, str):
        iso = _ISO_DATE_RE.fullmatch(node.strip())
        if iso:
            dates.add(date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3))))
            return
        for match in _NUMERAL_RE.finditer(node):
            value, is_percent, _ = _parse_numeral_token(
                match.group(1), match.group(2), match.group(3)
            )
            numbers.add(AllowedNumber(value, is_percent))
    elif isinstance(node, dict):
        for item in node.values():
            _collect_allowed(item, numbers, dates)
    elif isinstance(node, list):
        for item in node:
            _collect_allowed(item, numbers, dates)


def _allowed_for_finding(
    finding: dict[str, Any],
) -> tuple[set[AllowedNumber], set[date]]:
    """The numbers and dates prose may use from one finding.

    Sources: ``display``, ``comparison``, and ``value``. A ratio finding's
    allowed percent comes from the ``display`` string only (the single source
    of truth — metrics renders it with ``Decimal`` ROUND_HALF_UP from the
    exact value); its raw ``value`` (``-0.048``) is never allowed, so percent
    prose must match the display form exactly.
    """
    numbers: set[AllowedNumber] = set()
    dates: set[date] = set()
    _collect_allowed(finding.get("display"), numbers, dates)
    _collect_allowed(finding.get("comparison"), numbers, dates)
    value = finding.get("value")
    if isinstance(value, bool):
        pass
    elif isinstance(value, (int, float)):
        display = finding.get("display")
        if not (isinstance(display, str) and display.rstrip().endswith("%")):
            numbers.add(AllowedNumber(Decimal(str(value)), False))
    elif isinstance(value, list):
        # Table findings (M5): the per-row office counts.
        _collect_allowed(value, numbers, dates)
    return numbers, dates


def allowed_numerals(findings: dict[str, Any]) -> tuple[set[AllowedNumber], set[date]]:
    """The numbers and dates usable from the given findings.

    Per-claim attribution lives in ``validate_explanation``, which calls this
    with only the findings the claim cites — never the union.
    """
    numbers: set[AllowedNumber] = set()
    dates: set[date] = set()
    for finding_id, finding in findings.items():
        if finding_id == "meta" or not isinstance(finding, dict):
            continue
        finding_numbers, finding_dates = _allowed_for_finding(finding)
        numbers |= finding_numbers
        dates |= finding_dates
    return numbers, dates


def _strip_dates(text: str, allowed_dates: set[date]) -> str:
    """Remove accepted date mentions; reject any other date-shaped token.

    A date is accepted only as an allowed ISO date (``2025-11-20``), an
    unambiguous written form of it (``November 20, 2025``, ``Nov 20, 2025``),
    or a month-and-day mention (``November 20``) whose month and day match an
    allowed date — then stripped before the numeral scan. A bare ``20`` or
    ``2025`` that is not part of such a date is left behind and must match a
    real number.
    """

    def check_iso(match: re.Match[str]) -> str:
        try:
            mentioned = date(
                int(match.group(1)), int(match.group(2)), int(match.group(3))
            )
        except ValueError:
            raise OutputRejected(
                f"invalid date {match.group(0)!r} in the output"
            ) from None
        if mentioned not in allowed_dates:
            raise OutputRejected(
                f"date {match.group(0)!r} is not in the cited findings"
            )
        return " "

    def check_written(match: re.Match[str]) -> str:
        month = _MONTHS[match.group(1).lower().rstrip(".")]
        try:
            mentioned = date(int(match.group(3)), month, int(match.group(2)))
        except ValueError:
            raise OutputRejected(
                f"invalid date {match.group(0)!r} in the output"
            ) from None
        if mentioned not in allowed_dates:
            raise OutputRejected(
                f"date {match.group(0)!r} is not in the cited findings"
            )
        return " "

    def check_month_day(match: re.Match[str]) -> str:
        month = _MONTHS[match.group(1).lower().rstrip(".")]
        day = int(match.group(2))
        if not 1 <= day <= 31:
            raise OutputRejected(f"invalid date {match.group(0)!r} in the output")
        if not any(d.month == month and d.day == day for d in allowed_dates):
            raise OutputRejected(
                f"date {match.group(0)!r} is not in the cited findings"
            )
        return " "

    return _WRITTEN_DATE_NO_YEAR_RE.sub(
        check_month_day,
        _WRITTEN_DATE_RE.sub(check_written, _ISO_DATE_RE.sub(check_iso, text)),
    )


def _direction_ok(
    token_value: Decimal,
    explicit_sign: bool,
    finding_value: Decimal,
    *,
    decline: bool,
    increase: bool,
) -> bool:
    """The direction rule (rule (c)) for one token/candidate pair."""
    if explicit_sign:
        # An explicitly signed numeral must match the sign exactly.
        return (token_value < 0) == (finding_value < 0) or (
            token_value == 0 and finding_value == 0
        )
    if finding_value < 0:
        # An unsigned magnitude of a negative finding is allowed only with a
        # decline word in the same claim, and never with an increase word.
        return decline and not increase
    # Mirror: an unsigned positive number must not be said as a decline.
    return not (finding_value > 0 and decline)


def _check_one_number(
    raw: str,
    value: Decimal,
    is_percent: bool,
    explicit_sign: bool,
    numbers: set[AllowedNumber],
    *,
    decline: bool,
    increase: bool,
    negated: bool,
    cited: str,
) -> None:
    """One numeral or number-word against the allowed set (shared rules)."""
    candidates = [
        allowed
        for allowed in numbers
        if allowed.is_percent == is_percent and abs(allowed.value) == abs(value)
    ]
    if not candidates:
        raise OutputRejected(
            f"numeral {raw!r} does not exist in the cited findings ({cited})"
        )
    # A negated direction ("not down", "not up") next to a change number
    # inverts the sign instead of honoring it; reject rather than guess.
    if negated and is_percent:
        raise OutputRejected(
            f"numeral {raw!r} sits next to a negated direction "
            "('not down'/'not up'), which cannot be checked against the "
            f"cited findings ({cited})"
        )
    # Direction applies to percent changes only; counts and baselines
    # ("1,872 hours last year") carry no direction of their own.
    if not any(
        not allowed.is_percent
        or _direction_ok(
            value,
            explicit_sign,
            allowed.value,
            decline=decline,
            increase=increase,
        )
        for allowed in candidates
    ):
        raise OutputRejected(
            f"numeral {raw!r} misstates the direction of the cited findings ({cited})"
        )


@dataclass(frozen=True)
class _NumberAnchor:
    """One number-like token in the prose: its span and parsed value."""

    start: int
    end: int
    raw: str
    value: Decimal
    is_percent: bool
    explicit_sign: bool


def _span_distance(start: int, end: int, anchor: _NumberAnchor) -> int:
    """Gap between a [start, end) span and an anchor; 0 when overlapping."""
    if end <= anchor.start:
        return anchor.start - end
    if start >= anchor.end:
        return start - anchor.end
    return 0


def _nearest_anchor(start: int, end: int, anchors: list[_NumberAnchor]) -> int | None:
    """The index of the anchor nearest to a span (leftmost wins ties)."""
    best: int | None = None
    best_distance: int | None = None
    for index, anchor in enumerate(anchors):
        distance = _span_distance(start, end, anchor)
        if best_distance is None or distance < best_distance:
            best = index
            best_distance = distance
    return best


def check_numerals(text: str, findings: dict[str, Any]) -> None:
    """The numeral test (ROADMAP §3 layer 5), structural version.

    Every numeral in ``text`` must come from ``findings`` (the caller passes
    only the findings the claim cites):

    - ISO dates are whole tokens: accepted only as the same ISO date, an
      unambiguous written form of it, or a month-and-day mention matching an
      allowed date's month and day, then stripped before the numeral scan.
    - An explicitly signed numeral must match the finding's sign exactly.
    - Direction is checked per number: each direction word attaches to its
      nearest numeral. An unsigned magnitude of a negative finding requires a
      decline word attached to that numeral and is rejected if an increase
      word attaches to it; mirrored for positive findings. "down 4.8 % and
      hours are up 2.7 %" therefore fails on the 2.7 only.
    - A negated direction ("not down", "isn't up", "no decline", "never
      fell") attached to a percent numeral is rejected outright.
    - Percent numbers match only percent-form tokens (``4.8 %``, ``4.8
      percent``, ``four point eight percent``) at the display's precision;
      raw fractions like ``0.048`` are never allowed.
    - Standalone cardinal number WORDS ("forty-two", "eighteen", "one hundred
      twenty-five") and "point" decimals ("four point eight") are converted
      to digits and validated by the same rules.
    - "Risk"/"at-risk"/"at high risk"/"risk score"/"score" language in a
      claim that mentions students is rejected: students are people who may
      need support, never risk scores.
    """
    numbers, dates = allowed_numerals(findings)
    cited = ", ".join(sorted(findings)) or "none"
    check_numbers_against(_PROSE_ID_RE.sub("", text), numbers, dates, cited)


def check_numbers_against(
    text: str,
    numbers: set[AllowedNumber],
    dates: set[date],
    cited: str,
    *,
    student_id_re: re.Pattern[str] = _STUDENT_ID_RE,
) -> None:
    """The rules of ``check_numerals`` against an explicit allowed set.

    Explore uses it with the numbers of its computed tables (and its own
    student-id pattern); ``check_numerals`` with the cited findings'.
    """
    prose = _strip_dates(text, dates)
    student_id = student_id_re.search(prose)
    if student_id:
        raise OutputRejected(
            f"student ID {student_id.group(0)!r} in the output; the analyst "
            "never receives row IDs, so this cannot come from the findings"
        )
    risk = _RISK_SCORE_RE.search(prose)
    if risk and _STUDENT_WORD_RE.search(prose):
        raise OutputRejected(
            f"{risk.group(0)!r} language about students is not allowed; "
            "students are people who may need support, never risk scores"
        )

    anchors: list[_NumberAnchor] = []
    for match in _NUMERAL_RE.finditer(prose):
        value, is_percent, explicit = _parse_numeral_token(
            match.group(1), match.group(2), match.group(3)
        )
        anchors.append(
            _NumberAnchor(
                match.start(),
                match.end(),
                match.group(0).strip(),
                value,
                is_percent,
                explicit,
            )
        )
    for match in _NUMBER_WORD_RE.finditer(prose):
        parsed = _parse_number_word_token(match.group(0))
        if parsed is None:
            continue
        anchors.append(
            _NumberAnchor(
                match.start(), match.end(), match.group(0), parsed[0], parsed[1], False
            )
        )
    anchors.sort(key=lambda anchor: anchor.start)

    # Attach every direction word to its nearest numeral (per-number, not
    # per-claim). Words inside a negation ("not down", "isn't up") do not
    # count as ordinary direction; the negation itself attaches to its
    # nearest numeral and rejects it if it is a percent change.
    negation_spans = [m.span() for m in _NEGATED_DIRECTION_RE.finditer(prose)]

    def in_negation(start: int) -> bool:
        return any(
            span_start <= start < span_end for span_start, span_end in negation_spans
        )

    decline = [False] * len(anchors)
    increase = [False] * len(anchors)
    negated = [False] * len(anchors)
    for match in _DECLINE_RE.finditer(prose):
        if in_negation(match.start()):
            continue
        index = _nearest_anchor(match.start(), match.end(), anchors)
        if index is not None:
            decline[index] = True
    for match in _INCREASE_RE.finditer(prose):
        if in_negation(match.start()):
            continue
        index = _nearest_anchor(match.start(), match.end(), anchors)
        if index is not None:
            increase[index] = True
    for span_start, span_end in negation_spans:
        index = _nearest_anchor(span_start, span_end, anchors)
        if index is not None:
            negated[index] = True

    for index, anchor in enumerate(anchors):
        _check_one_number(
            anchor.raw,
            anchor.value,
            anchor.is_percent,
            anchor.explicit_sign,
            numbers,
            decline=decline[index],
            increase=increase[index],
            negated=negated[index],
            cited=cited,
        )


def validate_explanation(text: str, findings: dict[str, Any]) -> list[Claim]:
    """Full output validation. Returns the claims, or raises OutputRejected.

    Per-claim attribution (rule (a)): each claim's numerals and
    dates are checked against only the findings that claim cites.
    """
    claims = parse_claims(text)
    received = set(findings)
    for claim in claims:
        unknown = [fid for fid in claim.finding_ids if fid not in received]
        if unknown:
            raise OutputRejected(
                f"claim cites finding(s) {', '.join(unknown)} the analyst did "
                "not receive",
                unknown_finding_ids=tuple(unknown),
            )
        cited = {fid: findings[fid] for fid in claim.finding_ids}
        check_numerals(claim.source_text or claim.text, cited)
    return claims


# --- the corrective retry -------------------------------------------------------

# How many corrective tries a runner makes after the provider's output fails
# validation. Each one is the same call plus one short message that states the
# validator's reason in plain words, never any data. 0 turns the retry off.
ENV_VALIDATION_RETRIES = "CABINET_VALIDATION_RETRIES"
DEFAULT_VALIDATION_RETRIES = 1
# Each try can take up to the provider's own time budget, so the setting is
# capped to keep one briefing within a few minutes of wall time.
MAX_VALIDATION_RETRIES = 3

_ANALYST_RULE = (
    "Every claim must end with the ID of each finding it uses, in brackets, "
    "and every number and date must come from the findings that claim cites."
)


def _chief_rule() -> str:
    """The Chief of Staff's output rule, restated in a correction."""
    return (
        'Answer with a JSON object only: {"executive_summary": "...", '
        '"limitations": "..."}. The executive summary has at most '
        f"{MAX_SUMMARY_SENTENCES} sentences. Every sentence must end with the "
        "IDs of the findings it uses, in one bracket group like [M3, M4], and "
        "every number and date must come from those findings."
    )


def validation_retries_from_env() -> int:
    """``CABINET_VALIDATION_RETRIES`` read at run time: default 1, 0 turns the
    corrective retry off, capped at ``MAX_VALIDATION_RETRIES``. A value that
    is not a whole number of zero or more falls back to the default."""
    raw = os.environ.get(ENV_VALIDATION_RETRIES, "").strip()
    if not raw:
        return DEFAULT_VALIDATION_RETRIES
    try:
        value = int(raw)
    except ValueError:
        value = -1
    if value < 0:
        logger.warning(
            "%s=%r is not a whole number of zero or more; using %d",
            ENV_VALIDATION_RETRIES,
            raw,
            DEFAULT_VALIDATION_RETRIES,
        )
        return DEFAULT_VALIDATION_RETRIES
    return min(value, MAX_VALIDATION_RETRIES)


def _join_ids(ids: tuple[str, ...]) -> str:
    if len(ids) == 1:
        return ids[0]
    return ", ".join(ids[:-1]) + " and " + ids[-1]


def correction_message(exc: OutputRejected, role: str) -> str:
    """The plain-words correction sent with a corrective try.

    It states why the previous answer failed and restates the role's output
    rule. It is built only from the validator's reason, which names finding
    IDs and tokens the model itself wrote, never from the findings, so it
    cannot carry a value the role did not receive.
    """
    if exc.unknown_finding_ids:
        ids = _join_ids(exc.unknown_finding_ids)
        opening = (
            f"Your previous answer cited {ids}, which you did not receive. "
            "Use only the findings you were given."
        )
    else:
        opening = f"Your previous answer was rejected because {exc}."
    rule = _chief_rule() if role == CHIEF_OF_STAFF else _ANALYST_RULE
    return f"{opening} {rule} Write your answer again."


def _provider_corrector(
    provider: Provider,
) -> Callable[[dict[str, Any], str, str], Explanation] | None:
    """The provider's ``correct`` method, or None when it offers none (the
    replay provider, and any provider that only implements ``explain``)."""
    if isinstance(provider, RecordingProvider) and not provider.can_correct:
        return None
    corrector = getattr(provider, "correct", None)
    return corrector if callable(corrector) else None


@dataclass(frozen=True)
class _Attempts:
    """What the explain-and-validate loop produced. ``explanation`` and
    ``validated`` are set only for output that passed validation;
    ``retries`` counts the corrective tries made; ``rejected`` is the last
    answer that failed validation (for provenance), never shown."""

    explanation: Explanation | None
    validated: Any
    retries: int
    reason: str | None
    rejected: Explanation | None = None


def _explain_with_retry(
    provider: Provider,
    received: dict[str, Any],
    role: str,
    validate: Callable[[str], Any],
    *,
    task_id: str,
) -> _Attempts:
    """Call the provider and validate. On a validation failure, make up to
    ``CABINET_VALIDATION_RETRIES`` corrective tries with the same inputs plus
    the plain-words reason, validating each. Only output that passed
    validation comes back as ``explanation``. A recorded (replayed) answer
    never takes the retry path, and neither does a provider without
    ``correct``."""
    role_name = role.replace("_", " ")
    allowed = validation_retries_from_env()
    corrector = _provider_corrector(provider)
    retries = 0
    try:
        explanation = provider.explain(received, role)
    except ProviderUnavailable as exc:
        logger.warning("%s unavailable (task %s): %s", role_name, task_id, exc.reason)
        return _Attempts(None, None, 0, exc.reason)
    while True:
        try:
            validated = validate(explanation.text)
        except OutputRejected as exc:
            logger.warning(
                "%s output rejected (task %s, provider %s, label %s, "
                "corrective tries so far %d): %s",
                role_name,
                task_id,
                explanation.provider,
                explanation.model_label,
                retries,
                exc,
            )
            reason = f"the model's output failed validation: {exc}"
            if explanation.recorded or corrector is None or retries >= allowed:
                return _Attempts(None, None, retries, reason, rejected=explanation)
            retries += 1
            try:
                explanation = corrector(received, role, correction_message(exc, role))
            except ProviderUnavailable as unavailable:
                logger.warning(
                    "%s corrective try unavailable (task %s): %s",
                    role_name,
                    task_id,
                    unavailable.reason,
                )
                return _Attempts(
                    None,
                    None,
                    retries,
                    f"{reason}; the corrective try was unavailable: "
                    f"{unavailable.reason}",
                    rejected=explanation,
                )
            continue
        return _Attempts(explanation, validated, retries, None)


# --- the analyst runner ---------------------------------------------------------

# ``data.granted`` dedupe within a process run, keyed per audit log (a process
# has exactly one): the grant precedes the provider call by design, but a
# page-load loop against a down model must not fill the log with grants that
# led nowhere. Key: (role, task_id, hash of the received findings).
_GRANTS_THIS_RUN: WeakKeyDictionary[AuditSink, set[tuple[str, str, str]]] = (
    WeakKeyDictionary()
)


def _grant_fields_once(
    role: str,
    fields: tuple[str, ...],
    task_id: str,
    findings: dict[str, Any],
    log: AuditSink,
) -> list[str]:
    """The field-request gate, with ``data.granted`` written at most once per
    (role, task_id, findings hash) for this audit log in this process."""
    findings_hash = hashlib.sha256(
        canonical_findings_json(findings).encode("utf-8")
    ).hexdigest()
    key = (role, task_id, findings_hash)
    seen = _GRANTS_THIS_RUN.setdefault(log, set())
    if key in seen:
        return [normalize_field(f) for f in fields]
    granted = request_fields(role, fields, task_id, log)
    seen.add(key)
    return granted


def run_analyst(
    role: str,
    findings_obj: dict[str, Any],
    provider: Provider,
    log: AuditSink,
    *,
    task_id: str | None = None,
    question: Question | None = None,
) -> AnalystResult:
    """One analyst run for any role: gate → scoped findings → provider → validate.

    The gate requests the role's task fields (``ROLE_TASK_FIELDS[role]``) and
    logs ``data.granted`` listing exactly the fields received, before any model
    call (at most once per (role, task_id, findings hash) in this process).
    ``task_id`` defaults to ``briefing-<role>``; the API passes the most recent
    question's task for that role when there is one. ``question`` defaults to
    the registry's default question, whose dispatch is the standing
    ``ROLE_FINDINGS`` split — passing nothing is exactly the pre-registry
    behavior. Output that fails validation gets up to
    ``CABINET_VALIDATION_RETRIES`` corrective tries (``_explain_with_retry``).
    On success logs ``finding.produced`` (with ``validation_retries``) and,
    when the provider is recording, saves the validated response to the
    replay cache under the original inputs — an answer that failed
    validation is never recorded. Provider or validation failure returns
    ``available=False`` with a reason and is logged with the task id;
    unvalidated text is never returned.
    """
    question = question or DEFAULT_QUESTION
    if task_id is None:
        task_id = f"briefing-{role}"
    received = received_for(question, role, findings_obj)
    granted = _grant_fields_once(role, ROLE_TASK_FIELDS[role], task_id, received, log)

    attempts = _explain_with_retry(
        provider,
        received,
        role,
        lambda text: validate_explanation(text, received),
        task_id=task_id,
    )
    explanation = attempts.explanation
    if explanation is None:
        rejected = attempts.rejected
        return AnalystResult(
            available=False,
            text=None,
            claims=[],
            provider=rejected.provider if rejected else None,
            model_label=rejected.model_label if rejected else None,
            recorded=rejected.recorded if rejected else False,
            rekeyed_from=rejected.rekeyed_from if rejected else None,
            reason=attempts.reason,
            validation_retries=attempts.retries,
        )
    claims: list[Claim] = attempts.validated

    # Recorded under the original inputs (never the correction), and only
    # after validation.
    if isinstance(provider, RecordingProvider):
        provider.save(received, role, explanation)

    log.append(
        "finding.produced",
        actor=role,
        payload={
            "task_id": task_id,
            "findings": sorted(
                key for key in received if _FINDING_KEY_RE.fullmatch(key)
            ),
            "granted_fields": granted,
            "provider": explanation.provider,
            "model_label": explanation.model_label,
            "validation_retries": attempts.retries,
        },
    )
    return AnalystResult(
        available=True,
        text=explanation.text,
        claims=claims,
        provider=explanation.provider,
        model_label=explanation.model_label,
        recorded=explanation.recorded,
        rekeyed_from=explanation.rekeyed_from,
        reason=None,
        validation_retries=attempts.retries,
    )


def run_enrollment_analyst(
    findings_obj: dict[str, Any],
    provider: Provider,
    log: AuditSink,
    *,
    task_id: str = "briefing-enrollment",
) -> AnalystResult:
    """The Enrollment Analyst, kept as a thin wrapper over ``run_analyst``."""
    return run_analyst(ENROLLMENT_ANALYST, findings_obj, provider, log, task_id=task_id)


# --- the Chief of Staff --------------------------------------------------------


@dataclass(frozen=True)
class ChiefResult:
    """What a Chief of Staff run yields: the two sections it writes, each
    validated. ``available=False`` means the UI falls back to the computed
    headline (section 1) and the static limitations list (section 7).
    ``text`` is the raw validated answer (the two-section JSON exactly as the
    provider wrote it); record_golden writes the golden file from it, never
    from the replay cache."""

    available: bool
    executive_summary: str | None
    summary_claims: list[Claim]
    limitations: str | None
    limitation_claims: list[Claim]
    provider: str | None
    model_label: str | None
    recorded: bool
    reason: str | None
    text: str | None = None
    # From a re-keyed golden recording: the key the text was first validated
    # under (None for a live answer or an original recording).
    rekeyed_from: str | None = None
    # Corrective tries made after a validation failure.
    validation_retries: int = 0


# A sentence ends at terminal punctuation followed by whitespace and the start
# of a new sentence (an uppercase letter or a bracket), or by the end of the
# text. A dot before a lowercase continuation ("... hours vs. prior year ...")
# or inside a decimal ("4.8") never ends a sentence. The five-sentence cap is
# structural: six or more sentence terminators in the executive summary rejects
# the output.
_SENTENCE_END_RE = re.compile(r"[.!?]+(?=\s+[A-Z\[]|\s*$)")

# Abbreviations whose dots never end a sentence. They are protected before
# counting so "Registered credit hours vs. prior year" or "Dr. Smith" do not
# read as sentence boundaries (the following word is often capitalized).
_SENTENCE_ABBREVIATIONS = ("vs.", "e.g.", "i.e.", "U.S.", "Dr.", "Mr.", "Ms.", "No.")
_ABBREVIATION_DOT = "\x00"

MAX_SUMMARY_SENTENCES = 5

CHIEF_OUTPUT_KEYS = ("executive_summary", "limitations")


def count_sentences(text: str) -> int:
    """The number of sentence terminators in ``text`` (the structural
    sentence count used for the summary cap)."""
    protected = text
    for abbreviation in _SENTENCE_ABBREVIATIONS:
        protected = protected.replace(
            abbreviation, abbreviation.replace(".", _ABBREVIATION_DOT)
        )
    return len(_SENTENCE_END_RE.findall(protected))


def parse_chief_output(text: str) -> tuple[str, str]:
    """The Chief of Staff's answer as (executive_summary, limitations).

    JSON only: anything that is not a bare JSON object with exactly the keys
    ``executive_summary`` and ``limitations``, both strings, is rejected —
    prose, markdown fences, extra keys, or missing keys are never shown.
    """
    candidate = text.strip()
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        raise OutputRejected(
            "the Chief of Staff's answer is not a JSON object only; "
            "it must answer with "
            '{"executive_summary": "...", "limitations": "..."} and nothing else'
        ) from None
    if not isinstance(data, dict):
        raise OutputRejected(
            "the Chief of Staff's answer is a JSON "
            f"{type(data).__name__}, not a JSON object"
        )
    keys = set(data)
    expected = set(CHIEF_OUTPUT_KEYS)
    if keys != expected:
        raise OutputRejected(
            "the Chief of Staff's answer must have exactly the keys "
            f"{sorted(expected)}; got {sorted(keys)}"
        )
    summary = data["executive_summary"]
    limitations = data["limitations"]
    if not isinstance(summary, str) or not isinstance(limitations, str):
        raise OutputRejected(
            "the Chief of Staff's answer must map both "
            '"executive_summary" and "limitations" to strings'
        )
    return summary, limitations


def validate_chief_output(
    text: str, findings: dict[str, Any]
) -> tuple[list[Claim], list[Claim]]:
    """Full validation of the Chief of Staff's two sections.

    Returns (summary claims, limitation claims) or raises OutputRejected.
    Both texts go through the same validation as the analysts — claim IDs,
    the numeral check per claim, direction, dates, risk wording, student IDs
    — against the aggregate findings the Chief of Staff received; the
    executive summary is additionally capped at five sentences, counted
    structurally.
    """
    summary, limitations = parse_chief_output(text)
    sentences = count_sentences(summary)
    if sentences > MAX_SUMMARY_SENTENCES:
        raise OutputRejected(
            f"the executive summary is {sentences} sentences; the cap is "
            f"{MAX_SUMMARY_SENTENCES}"
        )
    summary_claims = validate_explanation(summary, findings)
    limitation_claims = validate_explanation(limitations, findings)
    return summary_claims, limitation_claims


def chief_received(
    findings_obj: dict[str, Any],
    analyst_texts: dict[str, str],
    question: Question | None = None,
) -> dict[str, Any]:
    """Exactly what the Chief of Staff receives: the aggregate findings for
    the question's dispatch to it (row IDs stripped, like every role) and
    the two analysts'
    *validated* explanation texts. Never student rows, never the counseling
    group. The aggregate findings are the question's chief dispatch (all of
    M1–M8 for the default question); a question other than the default also
    carries itself under ``QUESTION_KEY``, so its id is part of the
    replay/recording hash. This whole object is the replay/recording key, so
    it stays stable when the analysts' texts are replayed from the golden
    recordings."""
    question = question or DEFAULT_QUESTION
    finding_ids = list(question.dispatch[CHIEF_OF_STAFF])
    # M9 exists only while the institution's counseling aggregate
    # authorization is recorded, and it answers the spring registration
    # question (it is a count within M2), so only Q1 carries it. When it is
    # absent the payload, and so Q1's replay hash, is exactly what it was.
    if question.id == DEFAULT_QUESTION.id and M9_ID in findings_obj:
        finding_ids.append(M9_ID)
    received: dict[str, Any] = {
        "findings": findings_for_role(CHIEF_OF_STAFF, findings_obj, finding_ids),
        "analyst_explanations": {
            role: analyst_texts[role] for role in sorted(analyst_texts)
        },
    }
    if question.id != DEFAULT_QUESTION.id:
        received[QUESTION_KEY] = question_payload(question)
    return received


def chief_aggregate_fields(received: dict[str, Any]) -> list[str]:
    """The source fields behind the aggregates the Chief of Staff received,
    normalized (``hold.x`` → ``holds.x``, list markers ``[]`` dropped) and
    sorted — the one derivation of the chief's field set, used for its
    ``task.assigned`` payload, its ``data.granted`` event (level
    ``aggregate``), and the ``/ask`` response's ``granted_fields``."""
    fields: set[str] = set()
    for finding in received["findings"].values():
        if not isinstance(finding, dict):
            continue
        source_fields = finding.get("source_fields")
        if isinstance(source_fields, list):
            fields.update(
                normalize_field(str(field).replace("[]", "")) for field in source_fields
            )
    return sorted(fields)


def _grant_chief_once(
    task_id: str,
    received: dict[str, Any],
    log: AuditSink,
    findings_obj: dict[str, Any] | None = None,
) -> list[str]:
    """The aggregate gate, with ``data.granted`` written at most once per
    (chief, task_id, received hash) for this audit log in this process."""
    received_hash = hashlib.sha256(
        canonical_findings_json(received).encode("utf-8")
    ).hexdigest()
    key = (CHIEF_OF_STAFF, task_id, received_hash)
    fields = chief_aggregate_fields(received)
    seen = _GRANTS_THIS_RUN.setdefault(log, set())
    if key in seen:
        return fields
    granted = grant_aggregates(
        CHIEF_OF_STAFF,
        fields,
        task_id,
        log,
        analyst_explanations=sorted(received["analyst_explanations"]),
    )
    if M9_ID in received["findings"] and findings_obj is not None:
        # The counseling aggregate gets its own event, after the aggregate
        # grant, so the chain shows it apart: aggregate only, the fields
        # code read, and the recorded authorization it rests on.
        m9 = findings_obj[M9_ID]
        grant_authorized_aggregate(
            CHIEF_OF_STAFF,
            M9_ID,
            list(m9.get("source_fields") or []),
            dict(m9.get("authorization") or {}),
            task_id,
            log,
        )
    seen.add(key)
    return granted


def run_chief_of_staff(
    findings_obj: dict[str, Any],
    analyst_texts: dict[str, str],
    provider: Provider,
    log: AuditSink,
    task_id: str,
    question: Question | None = None,
) -> ChiefResult:
    """One Chief of Staff run: aggregate gate → prompt → provider → validate.

    Mirrors ``run_analyst``: the gate logs ``data.granted`` (level
    ``aggregate``) listing the aggregate fields received, before any model
    call; the provider answers with the two-section JSON object; the output
    is parsed strictly and both texts validated against the aggregate
    findings, with the same corrective retry as the analysts; only validated
    output is recorded for replay. Provider or
    validation failure returns ``available=False`` with a reason and is
    logged with the task id; unvalidated text is never returned. Logs no
    ``finding.produced`` — the API logs ``briefing.produced`` once per run.
    ``question`` defaults to the registry's default question, exactly the
    pre-registry behavior.
    """
    received = chief_received(findings_obj, analyst_texts, question)
    _grant_chief_once(task_id, received, log, findings_obj)

    attempts = _explain_with_retry(
        provider,
        received,
        CHIEF_OF_STAFF,
        lambda text: validate_chief_output(text, received["findings"]),
        task_id=task_id,
    )
    explanation = attempts.explanation
    if explanation is None:
        rejected = attempts.rejected
        return ChiefResult(
            available=False,
            executive_summary=None,
            summary_claims=[],
            limitations=None,
            limitation_claims=[],
            provider=rejected.provider if rejected else None,
            model_label=rejected.model_label if rejected else None,
            recorded=rejected.recorded if rejected else False,
            rekeyed_from=rejected.rekeyed_from if rejected else None,
            reason=attempts.reason,
            validation_retries=attempts.retries,
        )
    summary_claims, limitation_claims = attempts.validated

    if isinstance(provider, RecordingProvider):
        provider.save(received, CHIEF_OF_STAFF, explanation)

    summary, limitations = parse_chief_output(explanation.text)
    return ChiefResult(
        available=True,
        executive_summary=summary,
        summary_claims=summary_claims,
        limitations=limitations,
        limitation_claims=limitation_claims,
        provider=explanation.provider,
        model_label=explanation.model_label,
        recorded=explanation.recorded,
        rekeyed_from=explanation.rekeyed_from,
        reason=None,
        text=explanation.text,
        validation_retries=attempts.retries,
    )
