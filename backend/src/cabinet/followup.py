# ruff: noqa: E501  (answer templates and intent patterns read best unwrapped)
"""Briefing follow-ups: the questions a president asks after the briefing.

"What is driving the gap?", "Create a seven-day action plan", "Show me the
calculation", "Prepare the follow-up for approval": each is answered here,
in code, from the same findings object the briefing was built from (M1-M8),
the briefing's staff actions and leadership decision, the dataset's metadata
and the audit log. No model is called and no student identifier or row ever
leaves this module: every count is computed here or read from a finding, and
groups under ``MINIMUM_CELL_SIZE`` students are withheld.

Every block of an answer carries a label:

- ``fact``: a figure computed in code from the records, with the findings it
  came from;
- ``interpretation``: a reading of those figures. Either a fixed sentence
  written for this answer (``author`` is CampusLens) or an AI employee's
  validated section from the stored briefing (``author`` names the employee);
- ``recommendation``: a proposal, never an approval;
- ``note``: what is missing, withheld or not done.

``classify`` decides what a question is: one of the approved briefing
questions in other words (``approved``), a counseling request to a named AI
employee (``denied``), a follow-up this module answers (``answer``), or
nothing it handles (None, and the question goes to Explore). Questions that
only make sense after a briefing ("the change", "this plan") are follow-ups
only when the conversation has one.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from cabinet.counseling import MINIMUM_CELL_SIZE
from cabinet.metrics import M3_AMOUNT_LIMIT
from cabinet.permissions import (
    REFUSED,
    ROLE_FINDINGS,
    ROLE_PERMISSIONS,
    ROLE_TASK_FIELDS,
)

CHIEF_OF_STAFF = "chief_of_staff"
ENROLLMENT_ANALYST = "enrollment_analyst"
STUDENT_SUCCESS_ANALYST = "student_success_analyst"

EMPLOYEE_TITLES = {
    CHIEF_OF_STAFF: "Chief of Staff",
    ENROLLMENT_ANALYST: "Enrollment Analyst",
    STUDENT_SUCCESS_ANALYST: "Student Success Analyst",
}

# The fields a counseling request asks for; the permission gate refuses them
# to every role (permissions.py), so asking for them is always a denial.
COUNSELING_FIELDS = ("counseling.counseling_notes", "counseling.chaplain_contact")

LABEL_FACT = "fact"
LABEL_INTERPRETATION = "interpretation"
LABEL_RECOMMENDATION = "recommendation"
LABEL_NOTE = "note"

AUTHOR_CAMPUSLENS = "CampusLens"


# --- classification --------------------------------------------------------------


def _fold(text: str) -> str:
    folded = text.lower().replace("’", "'").replace("‘", "'")
    folded = re.sub(r"[^a-z0-9'%$\-\s]", " ", folded)
    return " ".join(folded.split())


_EMPLOYEE_RE = re.compile(
    r"\b(enrollment analyst|student success analyst|chief of staff)\b"
)
_COUNSELING_RE = re.compile(
    r"\b(counsel\w*|chaplain\w*|spiritual\w*|pastoral|therap\w*|mental health)\b"
)

# Q1 in other words: "What should I know about spring registration today?",
# "Brief me on spring registration", "Give me the registration briefing".
_APPROVED_RES = (
    re.compile(
        r"^(?:so )?what (?:should|do|would) i (?:need to )?know about "
        r"(?:the |our |this )?(?:spring )?registration"
        r"(?: (?:today|right now|now|this week|so far|status))?$"
    ),
    re.compile(
        r"^(?:please )?(?:brief me on|give me (?:a|the|today's) (?:briefing on|update on)"
        r"|update me on) (?:the )?(?:spring )?registration(?: today| status)?$"
    ),
    re.compile(
        r"^(?:give me |show me )?(?:the |today's )?(?:spring )?registration briefing$"
    ),
    re.compile(r"^how is (?:spring )?registration (?:going|looking)(?: today)?$"),
)


def is_briefing_question(question: str) -> bool:
    """True when ``question`` is the spring registration briefing question
    in other words ("... spring registration today?")."""
    folded = _fold(question).rstrip("?. ").strip()
    return any(pattern.match(folded) for pattern in _APPROVED_RES)


@dataclass(frozen=True)
class Rule:
    intent: str
    pattern: re.Pattern[str]
    # True: matches only when the conversation already has a briefing (the
    # question leans on it: "the change", "this plan").
    needs_briefing: bool = False


def _r(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


# Order matters: the first rule that matches wins. The more specific asks
# (the calculation, the audit trail, the approval) come before the broad
# ones ("behind the registration gap" is the calculation, not the drivers).
_RULES: tuple[Rule, ...] = (
    Rule(
        "changes",
        _r(
            r"\b(?:what(?:'s| has| have)? changed|changes?) since (?:the |my |our )?(?:previous|last|prior|earlier) (?:executive )?briefing"
        ),
    ),
    Rule(
        "employees",
        _r(
            r"\b(?:which|what) ai employees? (?:produced|wrote|made|did|created|built|worked on)|\bwho (?:produced|wrote) (?:each|which) (?:part|section)"
        ),
    ),
    Rule(
        "permissions",
        _r(
            r"\b(?:data|fields?|records?|information)\b.*\b(?:each|every|the) (?:ai employee|analyst)s?\b.*\b(?:permitted|allowed|access|see)|\b(?:ai employees?|analysts?)\b.*\b(?:permitted|allowed) to (?:access|see|read)"
        ),
    ),
    Rule(
        "fact_vs_interpretation",
        _r(
            r"\b(?:verified )?facts?\b.*\b(?:ai )?interpretations?\b|\binterpretations?\b.*\bfacts?\b"
        ),
    ),
    Rule(
        "missing",
        _r(
            r"\b(?:information|data|evidence) (?:is |are )?(?:still )?missing\b|\bwhat(?:'s| is) missing\b|\bwhat (?:don't|do not) (?:we|you) know\b|\bbefore (?:leadership|i|we) (?:should |can )?act\b"
        ),
    ),
    Rule(
        "evidence_audit",
        _r(
            r"\baudit (?:trail|log)\b.*\b(?:recommendation|decision|briefing|this)\b|\bevidence and (?:the )?audit\b|\bevidence behind (?:this|the|your) recommendation"
        ),
    ),
    Rule(
        "calculation",
        _r(
            r"\b(?:source data|calculation|formula|calculated|computed|show (?:me )?the math|how (?:was|is|did you get) (?:the |this )?(?:gap|number|figure|decline|change))\b.*\b(?:gap|registration|decline|drop|change|number|figure|briefing|this|it)\b|\bhow (?:was|is) (?:the )?registration gap (?:calculated|computed)"
        ),
    ),
    Rule(
        "approval",
        _r(
            r"\b(?:prepare|draft|ready|get)\b.*\b(?:follow-?up|for (?:leadership )?approval|for my approval|for leadership)\b"
        ),
    ),
    Rule(
        "decision",
        _r(
            r"\b(?:executive|leadership) decision\b|\bdecisions? (?:from|by|for) me\b|\b(?:requires?|needs?) my (?:approval|decision|sign-?off)\b|\bwhat (?:do|should|must) i (?:need to )?(?:decide|approve)\b|\bwhat needs (?:my|leadership) approval\b"
        ),
    ),
    Rule(
        "plan",
        _r(
            r"\b(?:seven|7)[- ]day (?:action )?plan\b|\baction plan\b|\bwhat can (?:staff|the offices?|we) do (?:this week|now|today)\b|\bplan for (?:enrollment|student success)\b"
        ),
    ),
    Rule(
        "multi_barrier",
        _r(
            r"\bmore than one (?:registration )?(?:barrier|indicator|hold|problem)|\b(?:multiple|several|two or more) (?:registration )?(?:barriers|indicators)\b"
        ),
    ),
    Rule(
        "support",
        _r(
            r"\b(?:which|what) (?:student )?(?:groups?|students?|populations?)\b.*\b(?:human support|immediate|most affected|need (?:the )?most|need (?:help|support|outreach|attention))"
        ),
    ),
    Rule(
        "compare",
        _r(
            r"\b(?:compare|comparison|compared|versus|vs)\b.*\b(?:last year|prior year|same point|a year ago)\b|\bsame point last year\b"
        ),
    ),
    Rule(
        "programs",
        _r(
            r"\b(?:programs?|majors?|class levels?|departments?)\b.*\b(?:account for|drive|driving|behind|contribut\w*|explain)\b.*\b(?:change|decline|drop|gap)\b"
        ),
    ),
    Rule(
        "drivers",
        _r(
            r"\b(?:driv\w*|caus\w*|behind|explain\w*|reasons? for|why)\b.*\b(?:registration )?(?:gap|decline|drop|shortfall|fall|down)\b"
        ),
    ),
)

# Words that tie a question to the registration briefing on their own (no
# briefing in the conversation needed).
_BRIEFING_WORDS_RE = re.compile(
    r"\b(?:registration|registered|register|briefing|recommendation|"
    r"leadership|ai employees?|analysts?|chief of staff|action plan|"
    r"seven[- ]day|7[- ]day|holds?|advising)\b"
)

# Subjects that belong to Explore (the whole university's records): a
# question about them is not a briefing follow-up unless it also names the
# briefing's own topic.
_EXPLORE_SUBJECT_RE = re.compile(
    r"\b(?:gpa|grades?|dropout|drop out|retention|graduat\w*|courses?|"
    r"instructors?|professors?|faculty|teach\w*|probation|suspension|"
    r"withdraw\w*|enrollment trends?|ethnicity|race|gender|age|"
    r"(?:19|20)\d\d)\b"
)

# Rules that answer only about the briefing even without the topic words
# (their wording is specific enough on its own).
_SELF_ANCHORED = frozenset(
    {
        "changes",
        "employees",
        "permissions",
        "fact_vs_interpretation",
        "evidence_audit",
        "approval",
        "decision",
        "plan",
        "multi_barrier",
        "missing",
    }
)


@dataclass(frozen=True)
class Route:
    kind: str  # "approved" | "denied" | "answer"
    intent: str | None = None
    employee: str | None = None


def classify(question: str, has_briefing: bool) -> Route | None:
    """What ``question`` is, or None when this module does not handle it."""
    folded = _fold(question).rstrip("?. ").strip()
    if not folded:
        return None
    employee_match = _EMPLOYEE_RE.search(folded)
    if employee_match and _COUNSELING_RE.search(folded):
        employee = employee_match.group(1).replace(" ", "_")
        return Route("denied", employee=employee)
    if _COUNSELING_RE.search(folded):
        return None  # Explore's privacy guard answers (and records) it
    for approved in _APPROVED_RES:
        if approved.match(folded):
            return Route("approved")
    anchored = bool(_BRIEFING_WORDS_RE.search(folded))
    for rule in _RULES:
        if not rule.pattern.search(folded):
            continue
        if rule.intent in _SELF_ANCHORED or anchored:
            return Route("answer", intent=rule.intent)
        if has_briefing and not _EXPLORE_SUBJECT_RE.search(folded):
            return Route("answer", intent=rule.intent)
        return None
    return None


# --- the facts ------------------------------------------------------------------


@dataclass
class Context:
    """Everything an answer may read: aggregates and metadata, built from the
    institution's active dataset. ``document`` is read only to count students
    by program and class level, never returned."""

    findings: dict[str, Any]
    document: dict[str, Any]
    dataset: dict[str, Any]
    briefing: dict[str, Any] | None
    briefing_events: list[dict[str, Any]]
    approvals: dict[str, dict[str, Any]]
    decisions: list[dict[str, Any]]
    actions: list[dict[str, Any]]
    fictional: bool
    role: str
    extra: dict[str, Any] = field(default_factory=dict)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _value(findings: dict[str, Any], key: str) -> Any:
    finding = findings.get(key)
    return finding.get("value") if isinstance(finding, dict) else None


def _count(value: Any) -> str:
    return f"{value:,}" if isinstance(value, int) else "not available"


def _signed(value: int) -> str:
    return f"+{value}" if value > 0 else ("0" if value == 0 else f"−{abs(value)}")


def _pct(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "not available"
    text = f"{abs(value) * 100:.1f}%"
    return ("−" if value < 0 else "+" if value > 0 else "") + text


def _students(document: dict[str, Any], key: str) -> dict[str, dict[str, Any]]:
    rows = document.get(key) if isinstance(document, dict) else None
    out: dict[str, dict[str, Any]] = {}
    for row in rows if isinstance(rows, list) else []:
        profile = row.get("profile") if isinstance(row, dict) else None
        if isinstance(profile, dict) and isinstance(profile.get("student_id"), str):
            out[profile["student_id"]] = row
    return out


def _term_name(document: dict[str, Any], key: str) -> str | None:
    terms = document.get("terms") if isinstance(document, dict) else None
    term = terms.get(key) if isinstance(terms, dict) else None
    name = term.get("name") if isinstance(term, dict) else None
    return str(name) if name else None


def _long_date(iso: Any) -> str | None:
    try:
        day = date.fromisoformat(str(iso)[:10])
    except ValueError:
        return None
    return f"{day:%B} {day.day}, {day.year}"


def _short_date(day: date) -> str:
    return f"{day:%b} {day.day}"


@dataclass
class Derived:
    """Counts computed here from the findings' row lists; the lists stay
    inside this object."""

    current_registered: int | None
    prior_registered: int | None
    current_hours: int | None
    prior_hours: int | None
    multi_barrier: int | None
    no_indicator: int | None
    by_program: list[tuple[str, int, int]]
    by_class_now_vs_prior: list[tuple[str, int, int]]
    unregistered_by_class: list[tuple[str, int]]
    unregistered_programs: int | None


_CLASS_ORDER = ("freshman", "sophomore", "junior", "senior")


def derive(ctx: Context) -> Derived:
    findings = ctx.findings
    current = _students(ctx.document, "students")
    prior = _students(ctx.document, "prior_year_students")
    m1 = _dict(findings.get("M1"))
    m1_rows = _dict(m1.get("row_ids"))
    numerator = [i for i in m1_rows.get("numerator") or [] if i in current]
    denominator = [i for i in m1_rows.get("denominator") or [] if i in prior]
    comparison = _dict(m1.get("comparison"))
    prior_count = comparison.get("prior_year_registered_continuing")
    m7_comparison = _dict(_dict(findings.get("M7")).get("comparison"))

    def hours(rows: list[str], source: dict[str, dict[str, Any]]) -> int | None:
        total = 0
        for student_id in rows:
            value = (source[student_id].get("enrollment") or {}).get(
                "registered_credit_hours"
            )
            if not isinstance(value, int):
                return None
            total += value
        return total

    def program(row: dict[str, Any]) -> str:
        return str((row.get("profile") or {}).get("program") or "Unknown")

    def level(row: dict[str, Any]) -> str:
        return str((row.get("profile") or {}).get("class_level") or "unknown")

    now_programs = Counter(program(current[i]) for i in numerator)
    prior_programs = Counter(program(prior[i]) for i in denominator)
    by_program = sorted(
        (
            (name, prior_programs[name], now_programs[name])
            for name in set(now_programs) | set(prior_programs)
        ),
        key=lambda row: (row[2] - row[1], row[0]),
    )
    now_levels = Counter(level(current[i]) for i in numerator)
    prior_levels = Counter(level(prior[i]) for i in denominator)
    levels = [lv for lv in _CLASS_ORDER if lv in now_levels or lv in prior_levels]
    by_class = [(lv, prior_levels[lv], now_levels[lv]) for lv in levels]

    m2_rows = (findings.get("M2") or {}).get("row_ids")
    m2_ids = [i for i in m2_rows if i in current] if isinstance(m2_rows, list) else []
    unregistered_levels = Counter(level(current[i]) for i in m2_ids)
    unregistered_by_class = [
        (lv, unregistered_levels[lv]) for lv in _CLASS_ORDER if unregistered_levels[lv]
    ]
    row_rules = (findings.get("M8") or {}).get("row_rules")
    multi = (
        sum(1 for rules in row_rules.values() if len(rules) >= 2)
        if isinstance(row_rules, dict)
        else None
    )
    m2 = _value(findings, "M2")
    m8 = _value(findings, "M8")
    return Derived(
        current_registered=len(numerator) if numerator else None,
        prior_registered=prior_count if isinstance(prior_count, int) else None,
        current_hours=hours(numerator, current) if numerator else None,
        prior_hours=(
            m7_comparison.get("prior_year_registered_credit_hours")
            if isinstance(m7_comparison.get("prior_year_registered_credit_hours"), int)
            else None
        ),
        multi_barrier=multi,
        no_indicator=(m2 - m8 if isinstance(m2, int) and isinstance(m8, int) else None),
        by_program=by_program,
        by_class_now_vs_prior=by_class,
        unregistered_by_class=unregistered_by_class,
        unregistered_programs=(
            len({program(current[i]) for i in m2_ids}) if m2_ids else None
        ),
    )


def _cell(count: int) -> str:
    return (
        f"{count:,}"
        if count >= MINIMUM_CELL_SIZE
        else f"fewer than {MINIMUM_CELL_SIZE}"
    )


# --- blocks ---------------------------------------------------------------------


def text(
    label: str,
    body: str,
    finding_ids: list[str] | None = None,
    author: str | None = None,
) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "text", "label": label, "text": body}
    if finding_ids:
        block["finding_ids"] = finding_ids
    if author:
        block["author"] = author
    return block


def bullets(
    label: str, title: str, items: list[str], author: str | None = None
) -> dict[str, Any]:
    block: dict[str, Any] = {
        "type": "list",
        "label": label,
        "title": title,
        "items": items,
    }
    if author:
        block["author"] = author
    return block


def table(
    label: str,
    title: str,
    columns: list[str],
    rows: list[list[str]],
    finding_ids: list[str] | None = None,
    row_findings: list[list[str]] | None = None,
) -> dict[str, Any]:
    block: dict[str, Any] = {
        "type": "table",
        "label": label,
        "title": title,
        "columns": columns,
        "rows": rows,
    }
    if finding_ids:
        block["finding_ids"] = finding_ids
    if row_findings is not None:
        block["row_findings"] = row_findings
    return block


def _analyst_sections(ctx: Context, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    """The AI employees' own validated sections from the stored briefing,
    labelled as interpretation and named by author."""
    out: list[dict[str, Any]] = []
    sections = (ctx.briefing or {}).get("sections") or {}
    for key in keys:
        section = sections.get(key)
        if not isinstance(section, dict) or section.get("kind") != "available":
            continue
        provenance = section.get("provenance") or {}
        author = EMPLOYEE_TITLES.get(str(provenance.get("source")), "AI employee")
        body = re.sub(
            r"\s*\[M\d+(?:\s*,\s*M\d+)*\]", "", str(section.get("text") or "")
        ).strip()
        claims = section.get("claims") or []
        ids = sorted(
            {fid for claim in claims for fid in (claim.get("finding_ids") or [])}
        )
        if body:
            out.append(text(LABEL_INTERPRETATION, body, ids, author=f"{author} (AI)"))
    return out


def _source_line(ctx: Context) -> str:
    name = ctx.dataset.get("name") or "the active dataset"
    as_of = _long_date((ctx.findings.get("meta") or {}).get("as_of"))
    fictional = (
        " (fictional demonstration data)"
        if ctx.fictional and "fictional" not in str(name).lower()
        else ""
    )
    when = f", records as of {as_of}" if as_of else ""
    return f"Source: {name}{fictional}{when}."


def _due(ctx: Context, days: int) -> tuple[str, str]:
    """A proposed deadline ``days`` after the data date (never the wall
    clock), never later than the day registration closes."""
    meta = ctx.findings.get("meta") or {}
    try:
        start = date.fromisoformat(str(meta.get("as_of")))
    except ValueError:
        return (f"{days} days", f"{days} days")
    due = start + timedelta(days=days)
    close = (meta.get("terms") or {}).get("registration_close_date")
    try:
        if close is not None:
            due = min(due, date.fromisoformat(str(close)))
    except ValueError:
        pass
    return (f"{days} days", _short_date(due))


# --- the answers -----------------------------------------------------------------


def _drivers(ctx: Context, d: Derived) -> dict[str, Any]:
    f = ctx.findings
    m1 = f.get("M1") or {}
    m2, m3, m4, m8 = (_value(f, k) for k in ("M2", "M3", "M4", "M8"))
    limit = f"${M3_AMOUNT_LIMIT:,.0f}"
    rows = [
        [
            "Registration is behind last year",
            f"{_count(d.current_registered)} continuing students registered against {_count(d.prior_registered)} at the same point last year ({m1.get('display', 'not available')})"
        ],
        ["Continuing students not yet registered", _count(m2)],
        [
            f"Unresolved financial hold under {limit}",
            f"{_count(m3)} of {_count(m2)}"
        ],
        ["No advising appointment this term", f"{_count(m4)} of {_count(m2)}"],
        [
            "Both a small-balance hold and no advising",
            f"{_count(d.multi_barrier)} of {_count(m2)}"
        ],
        [
            "No recorded barrier at all",
            f"{_count(d.no_indicator)} of {_count(m2)}"
        ],
        [
            "Registered credit hours against last year",
            str((f.get("M7") or {}).get("display", "not available"))
        ],
    ]
    blocks: list[dict[str, Any]] = [
        table(
            LABEL_FACT,
            "Contributing factors, each with the number behind it",
            ["Factor", "Number"],
            rows,
            ["M1", "M2", "M3", "M4", "M7", "M8"],
            row_findings=[["M1"], ["M2"], ["M3"], ["M4"], ["M8"], ["M2", "M8"], ["M7"]],
        ),
        text(
            LABEL_INTERPRETATION,
            "The gap is concentrated among continuing students who have not registered "
            "yet, and the most common recorded barrier is a small financial hold, followed "
            "by no advising contact this term. These are associations in the records, not "
            "proven causes.",
            ["M2", "M3", "M4"],
            author=AUTHOR_CAMPUSLENS,
        ),
        *_analyst_sections(ctx, ("2", "3")),
        bullets(
            LABEL_FACT,
            "Data sources used",
            [
                _source_line(ctx),
                "Fields: registration status and date, continuing status, credit hours, holds (category, amount, office, resolved), last advising appointment.",
                "Counseling and chaplain records were not used. They are refused to every AI employee.",
            ],
        ),
        bullets(
            LABEL_NOTE,
            "Missing or uncertain",
            _missing_items(ctx, d)[:4],
        ),
    ]
    return {
        "title": "What is driving the registration gap",
        "blocks": blocks,
        "finding_ids": ["M1", "M2", "M3", "M4", "M7", "M8"],
    }


def _missing_items(ctx: Context, d: Derived) -> list[str]:
    m2 = _value(ctx.findings, "M2")
    items = [
        f"Why {_count(d.no_indicator)} of the {_count(m2)} unregistered continuing students have no recorded barrier. The records hold no reason, so staff have to ask them.",
        "Whether students know about their hold, or have already arranged a payment plan. The hold records do not say.",
        "Students' plans to transfer, stop out or graduate early. The records do not hold intent, and CampusLens does not predict it.",
        "The comparison is one point in time against the same date last year, not a trend across several years.",
        "What an emergency-aid review would cost. The records hold hold amounts, not aid budgets.",
    ]
    if ctx.fictional:
        items.append("All records are fictional demonstration data.")
    return items


def _support(ctx: Context, d: Derived) -> dict[str, Any]:
    f = ctx.findings
    m2, m3, m4 = (_value(f, k) for k in ("M2", "M3", "M4"))
    m5 = _value(f, "M5")
    bursar = None
    if isinstance(m5, list):
        top = max(m5, key=lambda row: row.get("count", 0), default=None)
        if top is not None:
            bursar = (str(top.get("office")), int(top.get("count", 0)))
    limit = f"${M3_AMOUNT_LIMIT:,.0f}"
    rows = [
        [
            "Small-balance hold and no advising contact",
            _count(d.multi_barrier),
            "Two barriers at once; first in line for a call",
            "Student Success with Financial Aid",
        ],
        [
            f"Unresolved financial hold under {limit}",
            _count(m3),
            "A small balance blocks registration",
            "Financial Aid, Bursar",
        ],
        [
            "No advising appointment this term",
            _count(m4),
            "Nobody has met with them this term",
            "Student Success",
        ],
        [
            "No recorded barrier",
            _count(d.no_indicator),
            "Not registered, and the records do not say why",
            "Enrollment",
        ],
    ]
    blocks: list[dict[str, Any]] = [
        text(
            LABEL_FACT,
            f"{_count(m2)} continuing students have not registered for {_term_name(ctx.document, 'current') or 'the coming term'}. These groups overlap, so they do not add up to that number.",
            ["M2"],
        ),
        table(
            LABEL_FACT,
            "Who needs a person to reach out first",
            ["Group", "Students", "Why", "Who acts"],
            rows,
            ["M2", "M3", "M4", "M8"],
            row_findings=[["M8"], ["M3"], ["M4"], ["M2", "M8"]],
        ),
    ]
    if d.unregistered_by_class:
        withheld = [lv for lv, n in d.unregistered_by_class if n < MINIMUM_CELL_SIZE]
        blocks.append(
            table(
                LABEL_FACT,
                "Unregistered continuing students by class level",
                ["Class level", "Students"],
                [[lv.capitalize(), _cell(n)] for lv, n in d.unregistered_by_class],
                ["M2"],
            )
        )
        if withheld:
            blocks.append(
                text(
                    LABEL_NOTE,
                    f"Groups under {MINIMUM_CELL_SIZE} students are withheld to protect privacy.",
                )
            )
    if d.unregistered_programs is not None:
        blocks.append(
            text(
                LABEL_FACT,
                f"By program, they are spread across {d.unregistered_programs} programs and no program has {MINIMUM_CELL_SIZE} or more of them, so program counts are withheld.",
                ["M2"],
            )
        )
    if bursar is not None:
        blocks.append(
            text(
                LABEL_FACT,
                f"Holds sit mostly with one office: {bursar[0]} holds {bursar[1]} of the unresolved holds.",
                ["M5"],
            )
        )
    blocks.append(
        text(
            LABEL_INTERPRETATION,
            "Start with the students who face two barriers, then the small-balance holds, "
            "because a hold is a barrier staff can remove this week. The students with no "
            "recorded barrier need a conversation to learn why.",
            author=AUTHOR_CAMPUSLENS,
        )
    )
    blocks.append(
        text(
            LABEL_NOTE,
            "No names, student ids, counseling or chaplain information are shown here. The figures open the evidence for authorized staff.",
        )
    )
    return {
        "title": "Students who need human support first",
        "blocks": blocks,
        "finding_ids": ["M2", "M3", "M4", "M5", "M8"],
    }


def _plan_rows(ctx: Context, d: Derived) -> tuple[list[list[str]], list[list[str]]]:
    f = ctx.findings
    m2, m3, m4 = (_value(f, k) for k in ("M2", "M3", "M4"))
    limit = f"${M3_AMOUNT_LIMIT:,.0f}"
    two = _due(ctx, 2)
    five = _due(ctx, 5)
    seven = _due(ctx, 7)
    rows = [
        [
            "Enrollment",
            f"Review the {_count(m2)} unregistered continuing students and assign each case to an advisor or office",
            f"{two[1]} ({two[0]})",
            f"All {_count(m2)} cases assigned",
        ],
        [
            "Student Success",
            f"Contact the {_count(m4)} students with no advising appointment this term",
            f"{five[1]} ({five[0]})",
            f"Students reached, of {_count(m4)}",
        ],
        [
            "Financial Aid",
            f"Review the {_count(m3)} small-balance cases (holds under {limit}) for eligibility",
            f"{five[1]} ({five[0]})",
            f"Holds reviewed, of {_count(m3)}",
        ],
    ]
    ids = [["M2"], ["M4"], ["M3"]]
    m5 = _value(f, "M5")
    if isinstance(m5, list):
        for row in sorted(m5, key=lambda r: -int(r.get("count", 0))):
            count = int(row.get("count", 0))
            rows.append(
                [
                    str(row.get("office")),
                    f"Resolve the {count} unresolved hold{'s' if count != 1 else ''} recorded for this office",
                    f"{seven[1]} ({seven[0]})",
                    f"Holds resolved, of {count}",
                ]
            )
            ids.append(["M5"])
    rows.append(
        [
            "Enrollment with Student Success",
            f"Reach the {_count(d.no_indicator)} students with no recorded barrier and learn why they have not registered",
            f"{seven[1]} ({seven[0]})",
            f"Students reached, of {_count(d.no_indicator)}",
        ]
    )
    ids.append(["M2", "M8"])
    return rows, ids


def _plan(ctx: Context, d: Derived) -> dict[str, Any]:
    rows, ids = _plan_rows(ctx, d)
    as_of = _long_date((ctx.findings.get("meta") or {}).get("as_of"))
    days = _value(ctx.findings, "M6")
    blocks = [
        text(
            LABEL_RECOMMENDATION,
            "PROPOSED, not executed. Nothing below has been assigned, sent or changed. "
            "Staff begin an action only when they choose to, and the leadership decision "
            "waits for your approval.",
        ),
        table(
            LABEL_RECOMMENDATION,
            "Seven-day action plan for Enrollment and Student Success (proposed)",
            ["Office", "Proposed action", "Deadline", "Success measure"],
            rows,
            ["M2", "M3", "M4", "M5", "M8"],
            row_findings=ids,
        ),
        text(
            LABEL_FACT,
            f"Deadlines count from the data date{f' ({as_of})' if as_of else ''}, and registration closes in {_count(days)} days.",
            ["M6"],
        ),
        text(
            LABEL_NOTE,
            'The emergency-aid eligibility review needs your decision before Financial Aid acts on it. Ask "Which part of this plan requires an executive decision from me?"',
        ),
    ]
    return {
        "title": "Seven-day action plan (proposed)",
        "blocks": blocks,
        "finding_ids": ["M2", "M3", "M4", "M5", "M6"],
    }


def _decision_block(ctx: Context) -> dict[str, Any] | None:
    if not ctx.decisions:
        return None
    decision = ctx.decisions[0]
    approval = ctx.approvals.get(decision["id"]) or {}
    return {
        "type": "approval",
        "label": LABEL_RECOMMENDATION,
        "decision_id": decision["id"],
        "title": decision["title"],
        "text": decision["text"],
        "office": decision["follow_up"]["office"],
        "follow_up": decision["follow_up"]["description"],
        "approved": bool(approval),
        "approved_by": approval.get("approved_by"),
        "approved_at": approval.get("approved_at"),
    }


def _decision(ctx: Context, d: Derived) -> dict[str, Any]:
    f = ctx.findings
    m3 = _value(f, "M3")
    m2 = _value(f, "M2")
    days = _value(f, "M6")
    limit = f"${M3_AMOUNT_LIMIT:,.0f}"
    rows, _ = _plan_rows(ctx, d)
    now = [f"{row[0]}: {row[1]}" for row in rows if row[0] != "Financial Aid"]
    blocks: list[dict[str, Any]] = [
        bullets(
            LABEL_FACT,
            "Staff can begin now (operational work within each office's authority)",
            now,
        ),
        bullets(
            LABEL_FACT,
            "Needs your decision (policy and funding)",
            [
                f"Whether to authorize a focused emergency-aid eligibility review for the {_count(m3)} continuing students whose barrier is an unresolved financial hold under {limit}. It commits Financial Aid staff time and may lead to aid spending, which is decided separately.",
            ],
        ),
        table(
            LABEL_INTERPRETATION,
            "Options and tradeoffs",
            ["Option", "What happens", "Tradeoff"],
            [
                [
                    "A. Approve the review",
                    f"Financial Aid reviews the {_count(m3)} cases this week",
                    "Staff time now; the fastest way to remove the most common barrier",
                ],
                [
                    "B. Wait",
                    "Holds go through the usual process",
                    f"No new commitment, but registration closes in {_count(days)} days",
                ],
                [
                    "C. Ask for more first",
                    "Staff gather what is missing (payment plans, aid budget)",
                    "Better information, at the cost of days",
                ],
            ],
            ["M3", "M6"],
            row_findings=[["M3"], ["M6"], []],
        ),
        text(
            LABEL_RECOMMENDATION,
            f"Recommendation (AI, not a decision): option A, because small-balance holds are the most common recorded barrier ({_count(m3)} of {_count(m2)}) and the review decides nothing about any student's aid. Nothing is approved until you click Approve.",
            ["M2", "M3"],
            author=AUTHOR_CAMPUSLENS,
        ),
    ]
    card = _decision_block(ctx)
    if card is not None:
        blocks.append(card)
    return {
        "title": "What needs your decision",
        "blocks": blocks,
        "finding_ids": ["M2", "M3", "M6"],
    }


def _calculation(ctx: Context, d: Derived) -> dict[str, Any]:
    f = ctx.findings
    m1 = f.get("M1") or {}
    meta = f.get("meta") or {}
    terms = meta.get("terms") or {}
    comparison = m1.get("comparison") or {}
    as_of = _long_date(meta.get("as_of")) or "not available"
    prior_date = (
        _long_date(comparison.get("prior_year_equivalent_date")) or "not available"
    )
    current_term = _term_name(ctx.document, "current") or str(terms.get("current", ""))
    prior_term = _term_name(ctx.document, "prior_year") or str(
        terms.get("prior_year", "")
    )
    uploaded = _long_date(ctx.dataset.get("uploaded_at"))
    produced = None
    if ctx.briefing_events:
        produced = (
            str(ctx.briefing_events[-1].get("ts", ""))[:16].replace("T", " ") + " UTC"
        )
    formula = "not available"
    if d.current_registered is not None and d.prior_registered:
        formula = f"{d.current_registered} ÷ {d.prior_registered} − 1 = {m1.get('display', '')}"
    rows = [
        [
            "Registered continuing students now",
            f"{_count(d.current_registered)} ({current_term}, as of {as_of})",
        ],
        [
            "Same point last year",
            f"{_count(d.prior_registered)} ({prior_term}, as of {prior_date})",
        ],
        ["Comparison date", f"{as_of} against {prior_date}"],
        ["Calculation", formula],
        ["Definition", str(m1.get("definition", "not available"))],
        [
            "Data source",
            f"{ctx.dataset.get('name') or 'active dataset'}: {', '.join(field_label(p).lower() for p in (m1.get('source_fields') or []))}",
        ],
        ["Records as of", as_of],
        ["Dataset loaded", uploaded or "not available"],
    ]
    if produced:
        rows.append(["Briefing produced", produced])
    return {
        "title": "How the registration gap is calculated",
        "blocks": [
            table(
                LABEL_FACT,
                "The registration gap, step by step",
                ["Item", "Value"],
                rows,
                ["M1"],
            ),
            text(
                LABEL_NOTE,
                "Computed in code from the records. The model never computes a number, and the student records behind each count open from the figure for authorized staff.",
                ["M1"],
            ),
        ],
        "finding_ids": ["M1"],
    }


def _approval(ctx: Context, d: Derived) -> dict[str, Any]:
    f = ctx.findings
    m2, m3 = _value(f, "M2"), _value(f, "M3")
    limit = f"${M3_AMOUNT_LIMIT:,.0f}"
    card = _decision_block(ctx)
    blocks: list[dict[str, Any]] = []
    if card is None:
        blocks.append(
            text(
                LABEL_NOTE,
                "There is no leadership decision in the current briefing to prepare.",
            )
        )
        return {
            "title": "Follow-up for leadership approval",
            "blocks": blocks,
            "finding_ids": [],
        }
    blocks.append(
        table(
            LABEL_RECOMMENDATION,
            "Recommended follow-up (prepared, awaiting your approval)",
            ["Item", "Detail"],
            [
                ["Proposed action", str(card["follow_up"])],
                ["Responsible department", str(card["office"])],
                [
                    "Reason",
                    f"An unresolved financial hold under {limit} is the most common recorded barrier among the {_count(m2)} unregistered continuing students",
                ],
                [
                    "Population affected",
                    f"{_count(m3)} continuing students (counts only, no names)",
                ],
                [
                    "What approval does",
                    "Authorizes the review only. No eligibility decision, no message sent, no record changed",
                ],
            ],
            ["M2", "M3"],
            row_findings=[[], [], ["M2", "M3"], ["M3"], []],
        )
    )
    blocks.append(card)
    blocks.append(
        text(
            LABEL_NOTE,
            "Preparing this changed nothing. Approving records your approval and a task for "
            "Financial Aid in the audit log. Sending anything to the office is a separate "
            "step a named staff member takes.",
        )
    )
    return {
        "title": "Follow-up for leadership approval",
        "blocks": blocks,
        "finding_ids": ["M2", "M3"],
    }


def _compare(ctx: Context, d: Derived) -> dict[str, Any]:
    f = ctx.findings
    m1 = f.get("M1") or {}
    m7 = f.get("M7") or {}
    comparison = m1.get("comparison") or {}
    prior_date = (
        _long_date(comparison.get("prior_year_equivalent_date"))
        or "the same date last year"
    )
    as_of = _long_date((f.get("meta") or {}).get("as_of")) or "the data date"
    rows = [
        [
            "Registered continuing students",
            _count(d.prior_registered),
            _count(d.current_registered),
            str(m1.get("display", "not available")),
        ],
        [
            "Registered credit hours",
            _count(d.prior_hours),
            _count(d.current_hours),
            str(m7.get("display", "not available")),
        ],
    ]
    return {
        "title": "Against the same point last year",
        "blocks": [
            table(
                LABEL_FACT,
                f"Same point last year ({prior_date}) against now ({as_of})",
                ["Measure", "Last year", "Now", "Change"],
                rows,
                ["M1", "M7"],
                row_findings=[["M1"], ["M7"]],
            ),
            text(
                LABEL_INTERPRETATION,
                "Both headcount and credit hours are behind, so the gap is fewer students registering, not the same students taking lighter loads.",
                ["M1", "M7"],
                author=AUTHOR_CAMPUSLENS,
            ),
        ],
        "finding_ids": ["M1", "M7"],
    }


def _programs(ctx: Context, d: Derived) -> dict[str, Any]:
    shown = [
        row
        for row in d.by_program
        if row[1] >= MINIMUM_CELL_SIZE and row[2] >= MINIMUM_CELL_SIZE
    ]
    withheld = len(d.by_program) - len(shown)
    blocks: list[dict[str, Any]] = []
    if d.by_class_now_vs_prior:
        level_rows = sorted(d.by_class_now_vs_prior, key=lambda r: r[2] - r[1])
        blocks.append(
            table(
                LABEL_FACT,
                "Registered continuing students by class level",
                ["Class level", "Last year", "Now", "Change"],
                [
                    [
                        lv.capitalize(),
                        _cell(p),
                        _cell(n),
                        _signed(n - p)
                        if min(p, n) >= MINIMUM_CELL_SIZE
                        else "withheld",
                    ]
                    for lv, p, n in level_rows
                ],
                ["M1"],
            )
        )
    if shown:
        blocks.append(
            table(
                LABEL_FACT,
                "Registered continuing students by program",
                ["Program", "Last year", "Now", "Change"],
                [[name, str(p), str(n), _signed(n - p)] for name, p, n in shown],
                ["M1"],
            )
        )
    if withheld:
        blocks.append(
            text(
                LABEL_NOTE,
                f"{withheld} program{'s are' if withheld != 1 else ' is'} withheld because a count is under {MINIMUM_CELL_SIZE} students.",
            )
        )
    if d.by_class_now_vs_prior:
        worst = min(d.by_class_now_vs_prior, key=lambda r: r[2] - r[1])
        if worst[2] - worst[1] < 0 and min(worst[1], worst[2]) >= MINIMUM_CELL_SIZE:
            blocks.append(
                text(
                    LABEL_INTERPRETATION,
                    f"Most of the decline sits with {worst[0]} students ({_signed(worst[2] - worst[1])}), so the earliest-year students are where outreach matters most.",
                    ["M1"],
                    author=AUTHOR_CAMPUSLENS,
                )
            )
    blocks.append(
        text(
            LABEL_NOTE,
            "These breakdowns are computed on request from the same records as the registration gap. They are not one of the briefing's contracted figures.",
        )
    )
    return {"title": "Where the change sits", "blocks": blocks, "finding_ids": ["M1"]}


def _multi_barrier(ctx: Context, d: Derived) -> dict[str, Any]:
    f = ctx.findings
    m2 = _value(f, "M2")
    m8 = f.get("M8") or {}
    rules = _list(m8.get("rules"))
    rows = [[str(rule.get("title")), _count(rule.get("count"))] for rule in rules]
    return {
        "title": "Students facing more than one barrier",
        "blocks": [
            text(
                LABEL_FACT,
                f"{_count(d.multi_barrier)} of the {_count(m2)} unregistered continuing students have more than one support indicator. {_count(_value(f, 'M8'))} have at least one.",
                ["M2", "M8"],
            ),
            table(
                LABEL_FACT,
                "Students per indicator rule",
                ["Indicator rule", "Students"],
                rows,
                ["M8"],
            ),
            text(
                LABEL_NOTE,
                "Each rule is a named check. The rules are never combined into a score or a ranking of students.",
            ),
        ],
        "finding_ids": ["M2", "M8"],
    }


def _fact_vs_interpretation(ctx: Context, d: Derived) -> dict[str, Any]:
    f = ctx.findings
    m2, m3, m4 = (_value(f, k) for k in ("M2", "M3", "M4"))
    rows = [
        [
            "Contact the students with no advising this term",
            f"FACT: {_count(m4)} students have no appointment this term",
            "INTERPRETATION: a call will help them register",
        ],
        [
            "Review the small-balance holds",
            f"FACT: {_count(m3)} students have a financial hold under ${M3_AMOUNT_LIMIT:,.0f}",
            "INTERPRETATION: the hold is what stops them",
        ],
        [
            "Reach the students with no recorded barrier",
            f"FACT: {_count(d.no_indicator)} of {_count(m2)} have no indicator",
            "INTERPRETATION: they need a conversation, not a fix",
        ],
        [
            "Approve the emergency-aid review",
            f"FACT: {_count(m3)} cases, registration closes in {_count(_value(f, 'M6'))} days",
            "INTERPRETATION: the review is worth the staff time",
        ],
    ]
    blocks = [
        table(
            LABEL_FACT,
            "Each recommended action: what is verified, and what is interpretation",
            ["Action", "Verified fact (computed)", "Interpretation (AI or CampusLens)"],
            rows,
            ["M2", "M3", "M4", "M6"],
            row_findings=[["M4"], ["M3"], ["M2", "M8"], ["M3", "M6"]],
        ),
        text(
            LABEL_NOTE,
            "Every number is computed in code, and a checker rejects any model sentence whose number does not match. The wording of the AI employees' sections is interpretation.",
        ),
    ]
    return {
        "title": "Verified facts and interpretations",
        "blocks": blocks,
        "finding_ids": ["M2", "M3", "M4", "M6"],
    }


def _missing(ctx: Context, d: Derived) -> dict[str, Any]:
    return {
        "title": "What is missing before leadership acts",
        "blocks": [
            bullets(
                LABEL_NOTE, "Not in the records, or uncertain", _missing_items(ctx, d)
            ),
            text(
                LABEL_INTERPRETATION,
                "None of these blocks the operational work. The emergency-aid review is the one step where the missing cost figure matters.",
                author=AUTHOR_CAMPUSLENS,
            ),
        ],
        "finding_ids": ["M2", "M8"],
    }


def _employees(ctx: Context, d: Derived) -> dict[str, Any]:
    sections = (ctx.briefing or {}).get("sections") or {}

    def source(key: str) -> str:
        section = sections.get(key)
        if not isinstance(section, dict):
            return "not produced yet"
        if section.get("kind") != "available":
            return "unavailable this run"
        provenance = section.get("provenance") or {}
        how = (
            "replayed from a recorded run"
            if provenance.get("recorded")
            else "written just now"
        )
        return f"{provenance.get('model_label') or 'model'}, {how}"

    rows = [
        [
            "Executive summary",
            "Chief of Staff (AI)",
            ", ".join(ROLE_FINDINGS[CHIEF_OF_STAFF]),
            source("1"),
        ],
        [
            "Enrollment section",
            "Enrollment Analyst (AI)",
            ", ".join(ROLE_FINDINGS[ENROLLMENT_ANALYST]),
            source("2"),
        ],
        [
            "Student success section",
            "Student Success Analyst (AI)",
            ", ".join(ROLE_FINDINGS[STUDENT_SUCCESS_ANALYST]),
            source("3"),
        ],
        ["Figures", "Code (no AI)", "M1 to M8", "computed from the records"],
        [
            "Staff actions and decision",
            "Code (no AI)",
            "M2 to M5",
            "templates filled from the figures",
        ],
        [
            "Limitations",
            "Chief of Staff (AI)",
            ", ".join(ROLE_FINDINGS[CHIEF_OF_STAFF]),
            source("7"),
        ],
    ]
    blocks: list[dict[str, Any]] = [
        table(
            LABEL_FACT,
            "Who produced each part of the briefing",
            ["Part", "Produced by", "Figures it received", "How"],
            rows,
        ),
    ]
    if ctx.briefing is None:
        blocks.insert(
            0,
            text(
                LABEL_NOTE,
                'No briefing has been produced for this dataset yet. Ask "What should I know about spring registration?" first.',
            ),
        )
    return {
        "title": "Which AI employee produced each part",
        "blocks": blocks,
        "finding_ids": [],
    }


# Plain names for the student-record fields (the same words as the UI's
# ui/src/fieldLabels.ts).
FIELD_LABELS = {
    "profile.student_id": "Student ID (pseudonymous)",
    "profile.program": "Program",
    "profile.class_level": "Class level",
    "profile.continuing": "Continuing student",
    "enrollment.term": "Term",
    "enrollment.registration_status": "Registration status",
    "enrollment.registered_credit_hours": "Registered credit hours",
    "enrollment.registration_date": "Registration date",
    "holds.category": "Hold type",
    "holds.amount": "Hold amount",
    "holds.responsible_office": "Office responsible for the hold",
    "holds.hold_date": "Date the hold was placed",
    "holds.resolved": "Hold resolved",
    "advising.advisor_id": "Advisor (pseudonymous)",
    "advising.last_appointment_date": "Last advising appointment",
    "advising.appointment_status": "Advising appointment status",
    "comparison.prior_year_equivalent_date": "Same date last year",
    "comparison.prior_term_status": "Status at the same point last year",
    "comparison.baseline": "Comparison term",
    "counseling.counseling_notes": "Counseling notes",
    "counseling.chaplain_contact": "Chaplain contact",
    "terms.current.registration_close_date": "Registration closing date",
    "terms.in_session.start_date": "Start of the current term",
    "terms.prior_year.prior_year_equivalent_date": "Same date last year",
}

_GROUP_WORDS = {
    "profile": "student profile",
    "enrollment": "enrollment",
    "holds": "holds",
    "advising": "advising",
    "comparison": "last year's comparison",
    "counseling": "counseling and chaplain records",
}


def field_label(path: str) -> str:
    key = path.replace("[]", "")
    return FIELD_LABELS.get(key, key)


def _refused_words(role: str) -> str:
    """The refused part of a role's permissions: whole groups by name, a
    partly refused group by its refused fields."""
    table_ = ROLE_PERMISSIONS[role]
    groups: dict[str, list[tuple[str, str]]] = {}
    for path, level in table_.items():
        groups.setdefault(path.split(".")[0], []).append((path, level))
    words: list[str] = []
    for group, fields in groups.items():
        refused = [path for path, level in fields if level == REFUSED]
        if not refused:
            continue
        if len(refused) == len(fields):
            words.append(_GROUP_WORDS.get(group, group))
        else:
            words.extend(field_label(path).lower() for path in refused)
    return ", ".join(words) if words else "none"


def _permissions(ctx: Context, d: Derived) -> dict[str, Any]:
    rows: list[list[str]] = []
    for role in (ENROLLMENT_ANALYST, STUDENT_SUCCESS_ANALYST, CHIEF_OF_STAFF):
        if role == CHIEF_OF_STAFF:
            allowed = "Totals for every figure, never a student record"
        else:
            allowed = ", ".join(field_label(path) for path in ROLE_TASK_FIELDS[role])
        rows.append(
            [
                EMPLOYEE_TITLES[role],
                allowed,
                ", ".join(ROLE_FINDINGS[role]),
                _refused_words(role),
            ]
        )
    return {
        "title": "What each AI employee may access",
        "blocks": [
            table(
                LABEL_FACT,
                "Data each AI employee was permitted to access",
                ["AI employee", "Fields it may read", "Figures it receives", "Refused"],
                rows,
            ),
            text(
                LABEL_NOTE,
                "Counseling and chaplain records are refused to every AI employee. Every grant and every refusal is recorded in the audit log, and no AI employee receives a student id.",
            ),
        ],
        "finding_ids": [],
    }


def _changes(ctx: Context, d: Derived) -> dict[str, Any]:
    events = ctx.briefing_events
    name = ctx.dataset.get("name") or "the active dataset"
    if len(events) < 2:
        body = (
            "This is the first executive briefing on this dataset, so there is no earlier briefing to compare with."
            if events
            else "No executive briefing has been produced on this dataset yet."
        )
        blocks = [text(LABEL_FACT, body)]
    else:
        previous = str(events[-2].get("ts", ""))[:16].replace("T", " ")
        latest = str(events[-1].get("ts", ""))[:16].replace("T", " ")
        changed_dataset = ctx.extra.get("dataset_changed_between", False)
        if changed_dataset:
            body = f"The dataset changed between the previous briefing ({previous} UTC) and the latest one ({latest} UTC), so the figures were recomputed."
        else:
            body = (
                f"Nothing in the figures changed. The previous briefing ({previous} UTC) and the latest one ({latest} UTC) "
                f"were both computed from {name}, so every figure is the same."
            )
        blocks = [text(LABEL_FACT, body)]
    decided = [did for did in ctx.approvals]
    blocks.append(
        text(
            LABEL_FACT,
            "Since then you approved the emergency-aid review."
            if decided
            else "No leadership decision has been approved yet.",
        )
    )
    return {
        "title": "What changed since the previous briefing",
        "blocks": blocks,
        "finding_ids": [],
    }


def _evidence_audit(ctx: Context, d: Derived) -> dict[str, Any]:
    f = ctx.findings
    m2, m3 = _value(f, "M2"), _value(f, "M3")
    evidence = table(
        LABEL_FACT,
        "Evidence behind the recommendation",
        ["Figure", "Value", "How it is computed"],
        [
            [
                str((f.get(k) or {}).get("title", k)),
                str((f.get(k) or {}).get("display", "not available")),
                str((f.get(k) or {}).get("definition", "")),
            ]
            for k in ("M1", "M2", "M3", "M6")
        ],
        ["M1", "M2", "M3", "M6"],
        row_findings=[["M1"], ["M2"], ["M3"], ["M6"]],
    )
    trail_rows = [
        [
            str(e.get("id")),
            str(e.get("ts", ""))[:19].replace("T", " "),
            _actor(str(e.get("actor"))),
            _event_words(e),
        ]
        for e in ctx.extra.get("trail", [])
    ]
    blocks: list[dict[str, Any]] = [
        text(
            LABEL_RECOMMENDATION,
            f"The recommendation is a focused emergency-aid eligibility review for the {_count(m3)} of {_count(m2)} unregistered continuing students with a small-balance hold.",
            ["M2", "M3"],
        ),
        evidence,
    ]
    if trail_rows:
        blocks.append(
            table(
                LABEL_FACT,
                "Audit trail of the latest briefing",
                ["Event", "Time (UTC)", "Who", "What"],
                trail_rows,
            )
        )
    else:
        blocks.append(text(LABEL_NOTE, "No briefing run is in the audit log yet."))
    blocks.append(
        text(
            LABEL_NOTE,
            "The audit log is append-only and hash-chained. The full log is under Audit log.",
        )
    )
    return {
        "title": "Evidence and audit trail",
        "blocks": blocks,
        "finding_ids": ["M1", "M2", "M3", "M6"],
    }


def _actor(actor: str) -> str:
    if actor in EMPLOYEE_TITLES:
        return EMPLOYEE_TITLES[actor]
    if actor == "executive":
        return "You"
    return actor


def _event_words(event: dict[str, Any]) -> str:
    kind = str(event.get("type"))
    payload = event.get("payload") or {}
    if kind == "question.asked":
        return "Question asked"
    if kind == "task.assigned":
        role = EMPLOYEE_TITLES.get(str(payload.get("role")), "an AI employee")
        return (
            f"Task assigned to the {role} ({', '.join(payload.get('findings') or [])})"
        )
    if kind == "data.granted":
        fields = (
            payload.get("fields")
            or payload.get("granted_fields")
            or payload.get("fields_read")
            or []
        )
        return f"Access granted to {len(fields)} fields"
    if kind == "data.refused":
        return "Access refused"
    if kind == "finding.produced":
        return "Section validated and produced"
    if kind == "briefing.produced":
        return "Briefing produced"
    if kind == "decision.approved":
        return "Decision approved"
    if kind == "task.created":
        return "Task created for the office"
    return kind


_ANSWERS = {
    "drivers": _drivers,
    "support": _support,
    "plan": _plan,
    "decision": _decision,
    "calculation": _calculation,
    "approval": _approval,
    "compare": _compare,
    "programs": _programs,
    "multi_barrier": _multi_barrier,
    "fact_vs_interpretation": _fact_vs_interpretation,
    "missing": _missing,
    "employees": _employees,
    "permissions": _permissions,
    "changes": _changes,
    "evidence_audit": _evidence_audit,
}

INTENTS: tuple[str, ...] = tuple(_ANSWERS)

# What to ask next after each answer: the demo's order, then the backups.
_NEXT: dict[str, tuple[str, ...]] = {
    "drivers": (
        "Which student groups need the most immediate human support, and why?",
        "Show me the source data and calculation behind the registration gap.",
    ),
    "support": (
        "Create a seven-day action plan for Enrollment and Student Success.",
        "How many students face more than one registration barrier?",
    ),
    "plan": (
        "Which part of this plan requires an executive decision from me?",
        "Which recommended actions are based on verified facts, and which are AI interpretations?",
    ),
    "decision": (
        "Prepare the recommended follow-up for leadership approval.",
        "What information is missing before leadership should act?",
    ),
    "calculation": (
        "Prepare the recommended follow-up for leadership approval.",
        "How does this compare with the same point last year?",
    ),
    "approval": (
        "Show me the evidence and audit trail behind this recommendation.",
        "What data was each AI employee permitted to access?",
    ),
    "compare": ("Which programs account for most of the change?",),
    "programs": (
        "Which student groups need the most immediate human support, and why?",
    ),
    "multi_barrier": (
        "Create a seven-day action plan for Enrollment and Student Success.",
    ),
    "fact_vs_interpretation": (
        "What information is missing before leadership should act?",
    ),
    "missing": ("Which part of this plan requires an executive decision from me?",),
    "employees": ("What data was each AI employee permitted to access?",),
    "permissions": ("Which AI employee produced each part of this briefing?",),
    "changes": (
        "What is driving the registration gap, and what evidence supports your conclusion?",
    ),
    "evidence_audit": ("What data was each AI employee permitted to access?",),
}


def answer(intent: str, ctx: Context) -> dict[str, Any]:
    """The answer for one follow-up intent: a title, labelled blocks, the
    findings it read, and what to ask next."""
    derived = derive(ctx)
    body = _ANSWERS[intent](ctx, derived)
    body["intent"] = intent
    body["suggestions"] = list(_NEXT.get(intent, ()))
    body["source"] = _source_line(ctx)
    return body
