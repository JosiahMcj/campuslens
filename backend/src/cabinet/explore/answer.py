"""Answer writer: 1 to 4 plain sentences built from the computed tables.

The template answer is deterministic: every number in it is a table cell,
inserted as the cell's value, and each sentence carries claims
``{table, row, column}`` pointing at the cells it used. It is what replay and
fake modes show, and what a live run falls back to.

With a live provider the model may reword the answer. It receives only the
tables (titles, column labels, and rows; never the parameters, never a
student row) and must answer ``{"sentences": [...]}``. Every sentence then
goes through the cabinet's numeral validator (``analysts``), adapted to the
tables: each number must be a value in some cell (percent columns in percent
form), no student id, no risk-score language. Claims are rebuilt by finding
each number's cell. Anything that fails is discarded and the template answer
is used instead.

Numbers in a sentence are written for a reader (``reader_number``): a GPA to
2 decimals, a percentage of 100 or more as a whole number, "41.8%" with no
space. Each still links to its cell, and the validator accepts exactly that
rounded form of the cell's value.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from cabinet.analysts import (
    _NUMBER_WORD_RE,
    _NUMERAL_RE,
    AllowedNumber,
    OutputRejected,
    _parse_number_word_token,
    _parse_numeral_token,
    check_numbers_against,
)
from cabinet.counseling import SUPPRESSED_DISPLAY
from cabinet.explore.execute import STUDENT_ID_RE, StepResult
from cabinet.provider import Provider, ProviderUnavailable

logger = logging.getLogger(__name__)

WRITER_ROLE = "explore_writer"
MAX_SENTENCES = 4
SOURCE_TEMPLATE = "Calculated directly from the records"
SOURCE_MODEL = "Written by the Chief of Staff from the records"
# Sentences with no numbers, used when a step could not run or a template
# sentence unexpectedly fails its own check (the step's error stays in its
# JSON, never in a sentence, so no number can slip in through it).
STEP_ERROR_SENTENCE = (
    "One step could not run with these settings, so its table is empty."
)
UNCHECKED_SENTENCE = "The figures for this step are in its table."

_PERCENT_KINDS = ("pct",)
_EXPLORE_STUDENT_ID_RE = re.compile(r"\b(?:S|STU|PRI)-\d+\b", re.IGNORECASE)


@dataclass
class Sentence:
    text: str
    claims: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {"text": self.text, "claims": self.claims}


# --- formatting cells --------------------------------------------------------


def reader_number(value: float | int, kind: str) -> Decimal:
    """The number as a sentence shows it to a reader: a GPA to 2 decimals
    (2.623 -> 2.62) and a percentage of 100 or more as a whole number
    (110.8 -> 111); everything else exactly as the table holds it. Rounding
    is half up on the decimal value, so 2.615 -> 2.62 (never the binary
    float's 2.61). The table keeps full precision, and the validator accepts
    this form for the cell (``_cell_numbers``)."""
    number = Decimal(str(value))
    if kind == "gpa":
        return number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if kind == "pct" and abs(number) >= 100:
        return number.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return number


def _fmt(value: Any, kind: str) -> str:
    if isinstance(value, str) or value is None:
        return str(value)
    if isinstance(value, bool):
        return str(value)
    if kind == "pct":
        return f"{_signed(reader_number(value, kind))}%"
    if kind == "points":
        return f"{_signed(value)} points"
    if kind == "money":
        return f"${value:,.2f}"
    if kind == "gpa":
        return _signed(reader_number(value, kind))
    if kind in ("count", "hours") and isinstance(value, int):
        return _signed_int(value)
    return str(value)


def growth_words(value: Any) -> str:
    """" (more than doubled)" and the like for a growth percentage, else ""."""
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 100:
        return ""
    times = int(value // 100) + 1  # growth of 100 % to 199 % ends 2 to 3 times as big
    word = {2: "doubled", 3: "tripled", 4: "quadrupled"}.get(times)
    if word is None:
        return ""
    return f" ({word})" if value % 100 == 0 else f" (more than {word})"


def _signed(value: Any) -> str:
    return f"−{abs(value)}" if value < 0 else f"{value}"


def _signed_int(value: int) -> str:
    return f"−{abs(value):,}" if value < 0 else f"{value:,}"


class _Builder:
    """Builds one sentence and records a claim for every cell it inserts that
    carries a digit."""

    def __init__(self, steps: list[StepResult]) -> None:
        self.steps = steps
        self.parts: list[str] = []
        self.claims: list[dict[str, Any]] = []

    def t(self, text: str) -> _Builder:
        self.parts.append(text)
        return self

    def c(self, step: StepResult, row: int, key: str) -> _Builder:
        column = next(col for col in step.columns if col.key == key)
        value = step.cell(row, key)
        if value == SUPPRESSED_DISPLAY:
            text = f"withheld ({SUPPRESSED_DISPLAY} students)"
        else:
            text = _fmt(value, column.kind)
        self.parts.append(text)
        if re.search(r"\d", text):
            claim = {"table": step.index, "row": row, "column": key}
            if claim not in self.claims:
                self.claims.append(claim)
        return self

    def done(self) -> Sentence:
        return Sentence("".join(self.parts), self.claims)


# --- the template answer -----------------------------------------------------


def _primary(step: StepResult, steps: list[StepResult]) -> Sentence | None:
    """The main sentence for one step (None when the step has nothing to say)."""
    a = step.analysis.id
    b = _Builder(steps)
    p = step.params
    if step.error:
        return Sentence(STEP_ERROR_SENTENCE)
    if step.instructor_rows_withheld and a == "instructor_history":
        return Sentence("Instructor results are shown to the executive and admin only.")
    if not step.rows:
        return Sentence(
            f"No results matched {step.analysis.title.lower()} with these settings."
        )
    if step.instructor_rows_withheld and a == "course_instructors":
        b.t(
            "Instructor results are shown to the executive and admin only, so this "
            "shows "
        ).t(str(step.cell(0, "title"))).t(" as a whole: ")
        return (
            b.c(step, 0, "sections")
            .t(" sections in ")
            .c(step, 0, "terms")
            .t(" terms with a D, F or withdrawal rate of ")
            .c(step, 0, "dfw_rate")
            .t(".")
            .done()
        )
    order_low = p.get("order") in ("lowest_first", "slowest_first")
    if a == "gpa_by_major":
        if p.get("major"):
            b.t(f"{step.cell(0, 'major_name')} students average a cumulative GPA of ")
            return (
                b.c(step, 0, "avg_gpa")
                .t(" (")
                .c(step, 0, "students")
                .t(" students).")
                .done()
            )
        word = "lowest" if order_low else "highest"
        b.t(f"{step.cell(0, 'major_name')} has the {word} average cumulative GPA, ")
        return (
            b.c(step, 0, "avg_gpa")
            .t(" across ")
            .c(step, 0, "students")
            .t(" students.")
            .done()
        )
    if a == "gpa_by_college" and p.get("college"):
        college = step.cell(0, "college_name")
        b.t(f"Students in the {college} average a cumulative GPA of ")
        b.c(step, 0, "avg_gpa").t(" (").c(step, 0, "students")
        return b.t(" students).").done()
    if a == "gpa_by_college":
        word = "lowest" if order_low else "highest"
        b.t(
            f"The {step.cell(0, 'college_name')} has the {word} average cumulative "
            "GPA, "
        )
        return (
            b.c(step, 0, "avg_gpa")
            .t(" across ")
            .c(step, 0, "students")
            .t(" students.")
            .done()
        )
    if a == "dfw_by_course":
        major = p.get("major_required")
        if major:
            b.t(f"In {_major_name(steps, step)}, the historically ")
            b.t("hardest" if not order_low else "easiest").t(" required course is ")
        else:
            subject = _subject_name(step)
            b.t(f"Among {subject} courses, the one" if subject else "The course")
            b.t(" with the ").t("highest" if not order_low else "lowest").t(
                " D, F or withdrawal rate is "
            )
        b.c(step, 0, "course").t(
            f" {step.cell(0, 'title')}, with a D, F or withdrawal rate of "
        )
        b.c(step, 0, "dfw_rate").t(" (").c(step, 0, "dfw").t(" of ").c(
            step, 0, "graded"
        )
        return (
            b.t(" graded registrations over ")
            .c(step, 0, "sections")
            .t(" sections).")
            .done()
        )
    if a == "course_dfw_trend":
        total = len(step.rows) - 1
        b.t(f"{_course_title(steps, step)} has a D, F or withdrawal rate of ").c(
            step, total, "dfw_rate"
        )
        if step.cell(total, "dfw_rate") != SUPPRESSED_DISPLAY:
            b.t(" over all terms (").c(step, total, "dfw").t(" of ").c(
                step, total, "graded"
            )
            b.t(" graded registrations)")
        else:
            b.t(" over all terms")
        return b.t(".").done()
    if a == "course_instructors":
        chained = any(
            line.endswith(")") and "(from step" in line for line in step.params_plain
        )
        course = "it" if chained else _course_title(steps, step)
        b.c(step, 0, "instructor").t(
            f" {step.cell(0, 'name')} has taught {course} most: "
        )
        b.c(step, 0, "sections").t(" sections in ").c(step, 0, "terms").t(" terms")
        return (
            b.t(", with a D, F or withdrawal rate of ")
            .c(step, 0, "dfw_rate")
            .t(".")
            .done()
        )
    if a == "instructor_history":
        name = step.params_plain[0].split(": ", 1)[-1] if step.params_plain else ""
        name = re.sub(r"^I-\d+\s+", "", name)
        b.t(f"{name} has taught ").c(step, 0, "course").t(
            f" {step.cell(0, 'title')} most, "
        )
        b.c(step, 0, "sections").t(" sections with a D, F or withdrawal rate of ").c(
            step, 0, "dfw_rate"
        )
        return b.t(".").done()
    if a == "equity_gap":
        scope = (
            _course_title(steps, step) if p.get("course") else _major_name(steps, step)
        )
        reference = next(
            (
                n.removeprefix("Gaps are in percentage points against ").rstrip(".")
                for n in step.notes
                if n.startswith("Gaps are in percentage points against ")
            ),
            None,
        )
        ref = next(
            (i for i, r in enumerate(step.rows) if r.get("group") == reference), None
        )
        top = next((i for i in range(len(step.rows)) if i != ref), 0)
        b.t(f"In {scope}, ").c(step, top, "group").t(
            " have a D, F or withdrawal rate of "
        )
        b.c(step, top, "dfw_rate")
        if ref is not None and ref != top:
            b.t(" against ").c(step, ref, "dfw_rate").t(" for ").c(step, ref, "group")
            b.t(", a gap of ").c(step, top, "gap_points")
        return b.t(".").done()
    if a == "headcount_growth":
        b.t(f"{step.cell(0, 'major_name')} ")
        growth = step.cell(0, "growth")
        grew = isinstance(growth, (int, float)) and growth >= 0
        if grew and not order_low:
            # "Computer Science grew the fastest, 111% (more than doubled):"
            b.t("grew " if p.get("major") else "grew the fastest, ")
            shown = float(reader_number(growth, "pct"))
            b.c(step, 0, "growth").t(growth_words(shown)).t(": from ")
            b.c(step, 0, "start_headcount").t(" students in ").c(step, 0, "start_term")
            b.t(" to ").c(step, 0, "end_headcount").t(" in ").c(step, 0, "end_term")
            return b.t(".").done()
        if p.get("major"):
            b.t("went")
        elif order_low:
            b.t("grew slowest," if grew else "shrank the most,")
        else:
            b.t("grew fastest,")
        b.t(" from ").c(step, 0, "start_headcount").t(" students in ").c(
            step, 0, "start_term"
        )
        b.t(" to ").c(step, 0, "end_headcount").t(" in ").c(step, 0, "end_term")
        return b.t(", a change of ").c(step, 0, "growth").t(".").done()
    if a == "enrollment_by_term":
        last = len(step.rows) - 1
        b.t("In ").c(step, last, "term_name").t(", ").c(step, last, "students")
        b.t(" students were enrolled, against ").c(step, 0, "students").t(" in ")
        return b.c(step, 0, "term_name").t(".").done()
    if a == "continuing_registration_change":
        b.c(step, 0, "continuing").t(" continuing students registered for ")
        b.c(step, 0, "term_name").t(" against ").c(step, 0, "prior_continuing").t(
            " for "
        )
        b.c(step, 0, "prior_term_name")
        if step.cell(0, "change_pct") != SUPPRESSED_DISPLAY:
            b.t(", a change of ").c(step, 0, "change_pct")
        return b.t(".").done()
    if a == "withdrawal_by_modality":
        row = 0
        if p.get("order") != "largest_gap" and not p.get("term"):
            ranked = [
                i
                for i, r in enumerate(step.rows)
                if isinstance(r.get("gap_points"), float)
            ]
            row = max(ranked, key=lambda i: step.rows[i]["gap_points"]) if ranked else 0
        b.t("The largest online withdrawal gap was in " if not p.get("term") else "In ")
        b.c(step, row, "term_name").t(": ").c(step, row, "online_rate").t(
            " online against "
        )
        b.c(step, row, "in_person_rate").t(" in person")
        if isinstance(step.cell(row, "gap_points"), float):
            b.t(", a gap of ").c(step, row, "gap_points")
        return b.t(".").done()
    if a == "withdrawal_by_course_modality":
        subject = _subject_name(step)
        word = "lowest" if order_low else "highest"
        # The threshold is a parameter, not a table cell, so it is named in
        # "How this was answered" and the sentence says only that it applies.
        b.t(f"Among {subject + ' ' if subject else ''}courses with enough online ")
        b.t("students to rank, ")
        b.c(step, 0, "course").t(f" {step.cell(0, 'title')} has the {word} online ")
        b.t("withdrawal rate: ").c(step, 0, "online_rate").t(" (")
        b.c(step, 0, "online_w").t(" of ").c(step, 0, "online_graded")
        b.t(" online graded registrations)")
        if step.cell(0, "in_person_rate") is None:
            return b.t("; it was not taught in person.").done()
        if step.cell(0, "in_person_rate") == SUPPRESSED_DISPLAY:
            b.t("; its in-person rate is ").c(step, 0, "in_person_rate")
            return b.t(".").done()
        return b.t(", against ").c(step, 0, "in_person_rate").t(" in person.").done()
    if a == "standing_by_major":
        if p.get("major"):
            b.t(f"In {step.cell(0, 'major_name')}, ")
        else:
            word = "lowest" if order_low else "highest"
            b.t(f"{step.cell(0, 'major_name')} has the {word} probation rate: ")
        b.c(step, 0, "probation_rate").t(" of student terms were on probation and ")
        return b.c(step, 0, "suspension_rate").t(" were suspended.").done()
    if a == "graduations":
        if p.get("group_by") == "year":
            last = len(step.rows) - 1
            b.t("In ").c(step, last, "academic_year").t(", ").c(step, last, "graduates")
            scope = f" {_major_name(steps, step)}" if p.get("major") else ""
            return b.t(f"{scope} students graduated.").done()
        if p.get("academic_year"):
            b.t("In ").c(step, 0, "academic_year").t(", ")
        else:
            b.t("Over all years, ")
        b.t(f"{step.cell(0, 'major_name')} had the most graduates, ")
        return b.c(step, 0, "graduates").t(".").done()
    if a == "holds_by_office":
        b.t(f"{step.cell(0, 'office')} holds the most: ").c(step, 0, "holds")
        b.t(" holds on ").c(step, 0, "students").t(" students")
        if step.cell(0, "total_amount"):
            b.t(", totaling ").c(step, 0, "total_amount")
        return b.t(".").done()
    if a == "advising_coverage":
        word = "lowest" if order_low else "highest"
        b.t(f"{step.cell(0, 'major_name')} has the {word} advising coverage in ")
        b.c(step, 0, "term_name").t(": ").c(step, 0, "advised").t(" of ")
        b.c(step, 0, "students").t(" students, or ").c(step, 0, "coverage")
        return b.t(", had a completed appointment.").done()
    if a == "credit_hours_by_term":
        last = len(step.rows) - 1
        b.t("In ").c(step, last, "term_name").t(", ").c(step, last, "students")
        b.t(" students attempted ").c(step, last, "attempted_hours").t(
            " credit hours, "
        )
        return b.c(step, last, "average_attempted").t(" on average.").done()
    return None


def _secondary(step: StepResult, steps: list[StepResult]) -> Sentence | None:
    """An optional second sentence (the runner-up or a contrast)."""
    a = step.analysis.id
    if step.error or step.instructor_rows_withheld or len(step.rows) < 2:
        return None
    b = _Builder(steps)
    if a == "course_instructors":
        b.c(step, 1, "instructor").t(f" {step.cell(1, 'name')} taught ")
        b.c(step, 1, "sections").t(" sections, with a D, F or withdrawal rate of ")
        return b.c(step, 1, "dfw_rate").t(".").done()
    if a in (
        "gpa_by_major",
        "headcount_growth",
        "standing_by_major",
    ) and not step.params.get("major"):
        key = {
            "gpa_by_major": "avg_gpa",
            "headcount_growth": "growth",
            "standing_by_major": "probation_rate",
        }[a]
        b.t(f"Next is {step.cell(1, 'major_name')} at ").c(step, 1, key)
        return b.t(".").done()
    if a in ("dfw_by_course", "withdrawal_by_course_modality"):
        key = "dfw_rate" if a == "dfw_by_course" else "online_rate"
        b.t("Next is ").c(step, 1, "course").t(f" {step.cell(1, 'title')} at ")
        return b.c(step, 1, key).t(".").done()
    return None


def _major_name(steps: list[StepResult], step: StepResult) -> str:
    for line in step.params_plain:
        if line.startswith(("Required by major: ", "Major: ")):
            return re.sub(
                r"\s*\([A-Z]{2,4}\)(?: \(from step \d+\))?$", "", line.split(": ", 1)[1]
            )
    return "that major"


def _subject_name(step: StepResult) -> str | None:
    for line in step.params_plain:
        if line.startswith("Subject: "):
            return re.sub(r"\s*\([A-Z]{2,4}\)$", "", line.split(": ", 1)[1])
    return None


def _course_title(steps: list[StepResult], step: StepResult) -> str:
    for line in step.params_plain:
        if line.startswith("Course: "):
            value = re.sub(r" \(from step \d+\)$", "", line.split(": ", 1)[1])
            return re.sub(r"^[A-Z]{2,4} \d{4}\s+", "", value)
    return "This course"


def template_answer(steps: list[StepResult]) -> list[Sentence]:
    """1 to 4 sentences: one per step, then runner-up sentences from the last
    step backward while there is room."""
    primaries = [_primary(step, steps) for step in steps]
    room = MAX_SENTENCES - sum(1 for s in primaries if s is not None)
    extras: dict[int, Sentence] = {}
    for step in reversed(steps):
        if room <= 0:
            break
        extra = _secondary(step, steps)
        if extra is not None:
            extras[step.index] = extra
            room -= 1
    out: list[Sentence] = []
    for step, primary in zip(steps, primaries, strict=True):
        if primary is not None:
            out.append(primary)
        if step.index in extras:
            out.append(extras[step.index])
    return out[:MAX_SENTENCES] or [Sentence("No results matched this question.")]


# --- the numeral check against the tables ------------------------------------


def _cell_numbers(value: Any, kind: str) -> set[AllowedNumber]:
    numbers: set[AllowedNumber] = set()
    if isinstance(value, bool) or value is None:
        return numbers
    if isinstance(value, (int, float)):
        try:
            number = Decimal(str(value))
        except InvalidOperation:
            return numbers
        # The exact value and the one rounded form a sentence shows for it
        # (``reader_number``): a GPA of 2.623 is "2.623" or "2.62", a growth
        # of 110.8 % is "110.8%" or "111%", never any other rounding.
        for form in {number, reader_number(value, kind)}:
            numbers.add(AllowedNumber(form, False))
            if kind in _PERCENT_KINDS:
                numbers.add(AllowedNumber(form, True))
        return numbers
    for match in _NUMERAL_RE.finditer(str(value)):
        parsed, is_percent, _ = _parse_numeral_token(
            match.group(1), match.group(2), match.group(3)
        )
        numbers.add(AllowedNumber(parsed, is_percent))
    return numbers


def table_numbers(steps: list[StepResult]) -> set[AllowedNumber]:
    numbers: set[AllowedNumber] = set()
    for step in steps:
        for row in step.rows:
            for column in step.columns:
                numbers |= _cell_numbers(row.get(column.key), column.kind)
    return numbers


def _text_cells(steps: list[StepResult]) -> list[str]:
    """Text cells without digits (titles, names, labels), longest first."""
    cells = {
        str(value)
        for step in steps
        for row in step.rows
        for value in row.values()
        if isinstance(value, str) and len(value) >= 4 and not re.search(r"\d", value)
    }
    return sorted(cells, key=len, reverse=True)


def _mask_names(text: str, steps: list[StepResult]) -> str:
    """The text with digit-free table text (course titles such as "Risk
    Management and Insurance", names) blanked, so the risk-language rule
    reads only the words around the data. Numbers are untouched."""
    for cell in _text_cells(steps):
        text = re.sub(re.escape(cell), " ", text, flags=re.IGNORECASE)
    return text


def check_sentence(text: str, steps: list[StepResult]) -> None:
    """Every number in ``text`` must be a table cell, or OutputRejected."""
    if STUDENT_ID_RE.search(text):
        raise OutputRejected("a student id in the answer")
    no_dates: set[date] = set()
    check_numbers_against(
        _mask_names(text, steps),
        table_numbers(steps),
        no_dates,
        "the computed tables",
        student_id_re=_EXPLORE_STUDENT_ID_RE,
    )


def _tokens(text: str) -> list[tuple[int, int, Decimal, bool]]:
    """(start, end, value, is_percent) for every numeral and number word."""
    out: list[tuple[int, int, Decimal, bool]] = []
    for match in _NUMERAL_RE.finditer(text):
        value, is_percent, _ = _parse_numeral_token(
            match.group(1), match.group(2), match.group(3)
        )
        out.append((match.start(), match.end(), value, is_percent))
    for match in _NUMBER_WORD_RE.finditer(text):
        parsed = _parse_number_word_token(match.group(0))
        if parsed is not None:
            out.append((match.start(), match.end(), parsed[0], parsed[1]))
    return sorted(out, key=lambda t: t[0])


def _cells_holding(
    steps: list[StepResult], value: Decimal, is_percent: bool
) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for step in steps:
        for r, row in enumerate(step.rows):
            for column in step.columns:
                for allowed in _cell_numbers(row.get(column.key), column.kind):
                    if allowed.is_percent == is_percent and abs(allowed.value) == abs(
                        value
                    ):
                        found.append(
                            {"table": step.index, "row": r, "column": column.key}
                        )
                        break
    return found


def _row_labels(steps: list[StepResult]) -> list[tuple[str, int, int]]:
    """(label text, table, row) for every text cell that tells rows apart."""
    labels: list[tuple[str, int, int]] = []
    for step in steps:
        for column in step.columns:
            values = [row.get(column.key) for row in step.rows]
            texts = [v for v in values if isinstance(v, str)]
            if len(set(texts)) <= 1 and len(step.rows) > 1:
                continue  # the same in every row: it labels no row
            for r, value in enumerate(values):
                if (
                    isinstance(value, str)
                    and len(value) >= 3
                    and value != SUPPRESSED_DISPLAY
                ):
                    labels.append((value, step.index, r))
    return labels


def anchored_claims(text: str, steps: list[StepResult]) -> list[dict[str, Any]]:
    """Claims for a model sentence, with every number tied to its row.

    A number must sit in the row whose label (major, course, term, office,
    group, name) is mentioned nearest to it in the sentence, so a true number
    cannot be attached to the wrong row. A number inside a mentioned label
    ("MEEN 3310", "Spring 2021") is that cell, and a number from a one-row
    table needs no label. Raises OutputRejected otherwise."""
    spans: list[tuple[int, int, int, int]] = []  # start, end, table, row
    for label, table, row in _row_labels(steps):
        for match in re.finditer(re.escape(label), text, re.IGNORECASE):
            spans.append((match.start(), match.end(), table, row))
    by_index = {step.index: step for step in steps}
    claims: list[dict[str, Any]] = []
    for start, end, value, is_percent in _tokens(text):
        cells = _cells_holding(steps, value, is_percent)
        inside = [sp for sp in spans if sp[0] <= start and end <= sp[1]]
        if inside:
            chosen = [
                c
                for c in cells
                if (c["table"], c["row"]) in {(sp[2], sp[3]) for sp in inside}
            ]
        else:
            outside = [sp for sp in spans if sp[1] <= start or sp[0] >= end]
            nearest: set[tuple[int, int]] = set()
            if outside:
                gaps = [
                    (start - sp[1] if sp[1] <= start else sp[0] - end, sp)
                    for sp in outside
                ]
                best = min(g for g, _ in gaps)
                nearest = {(sp[2], sp[3]) for g, sp in gaps if g == best}
            chosen = [c for c in cells if (c["table"], c["row"]) in nearest]
            if not chosen:
                chosen = [c for c in cells if len(by_index[c["table"]].rows) == 1]
        if not chosen:
            raise OutputRejected(
                f"the number {text[start:end]!r} is not in the row the sentence "
                "names next to it"
            )
        if chosen[0] not in claims:
            claims.append(chosen[0])
    return claims


# --- the model rewrite -------------------------------------------------------


def writer_payload(steps: list[StepResult]) -> dict[str, Any]:
    """Everything the writer model receives: the computed tables only."""
    return {
        "tables": [
            {
                "table": step.index,
                "title": step.analysis.title,
                "columns": [c.label for c in step.columns],
                "rows": [[row.get(c.key) for c in step.columns] for row in step.rows],
            }
            for step in steps
            if step.rows
        ]
    }


def model_answer(steps: list[StepResult], provider: Provider) -> list[Sentence]:
    """The model's rewording, validated; raises OutputRejected or
    ProviderUnavailable (the caller then uses the template)."""
    payload = writer_payload(steps)
    if not payload["tables"]:
        raise OutputRejected("no tables to write from")
    text = provider.explain(payload, WRITER_ROLE).text.strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    try:
        data = json.loads(text)
    except ValueError:
        raise OutputRejected("the writer did not answer with JSON") from None
    if not isinstance(data, dict) or set(data) != {"sentences"}:
        raise OutputRejected('the writer must answer {"sentences": [...]}')
    sentences = data["sentences"]
    if (
        not isinstance(sentences, list)
        or not 1 <= len(sentences) <= MAX_SENTENCES
        or not all(isinstance(s, str) and s.strip() for s in sentences)
    ):
        raise OutputRejected(f"the writer must give 1 to {MAX_SENTENCES} sentences")
    out: list[Sentence] = []
    for sentence in sentences:
        cleaned = " ".join(sentence.split())
        if len(cleaned) > 600:
            raise OutputRejected("a sentence is too long")
        check_sentence(cleaned, steps)
        out.append(Sentence(cleaned, anchored_claims(cleaned, steps)))
    return out


def _claimed_cells(
    sentences: list[Sentence], steps: list[StepResult]
) -> set[tuple[int, int, str]]:
    """(table, row, value) for every NUMBER the sentences cite. The value,
    not the column, so "7 sections in 7 terms" counts both sevens of the row;
    text cells (an instructor id) are names, checked by label instead."""
    by_index = {step.index: step for step in steps}
    cells: set[tuple[int, int, str]] = set()
    for sentence in sentences:
        for claim in sentence.claims:
            step = by_index.get(claim["table"])
            if step is None or not 0 <= claim["row"] < len(step.rows):
                continue
            value = step.rows[claim["row"]].get(claim["column"])
            if isinstance(value, bool) or not isinstance(value, int | float):
                continue
            cells.add((claim["table"], claim["row"], str(value)))
    return cells


def require_complete(
    rewrite: list[Sentence], template: list[Sentence], steps: list[StepResult]
) -> None:
    """A rewording may change the words, never drop a fact: every figure the
    template answer states (by row and value) and every major, course, or
    name it mentions must also be in the rewording. Raises OutputRejected."""
    missing = _claimed_cells(template, steps) - _claimed_cells(rewrite, steps)
    if missing:
        raise OutputRejected(
            f"the rewording left out {len(missing)} figure(s) the computed "
            "answer states"
        )
    template_rows = _rows_named(" ".join(s.text for s in template), steps)
    rewrite_rows = _rows_named(" ".join(s.text for s in rewrite), steps)
    unnamed = template_rows - rewrite_rows
    if unnamed:
        raise OutputRejected(
            f"the rewording leaves out {len(unnamed)} major(s), course(s), or "
            "name(s) the computed answer names"
        )


# An instructor id ("I-0001") is not a name a reader sees; the screen shows
# the instructor's (fictional) name.
_ID_LABEL_RE = re.compile(r"^[A-Z]-\d{4}$")


def _rows_named(text: str, steps: list[StepResult]) -> set[tuple[int, int]]:
    """(table, row) for every row the text names by one of its labels (a
    major, course, title, term, office, group, or name), whole words only, so
    "male students" is not found inside "female students"."""
    named: set[tuple[int, int]] = set()
    for label, table, row in _row_labels(steps):
        if _ID_LABEL_RE.match(label):
            continue
        pattern = r"(?<!\w)" + re.escape(label) + r"(?!\w)"
        if re.search(pattern, text, re.IGNORECASE):
            named.add((table, row))
    return named


def write_answer(
    steps: list[StepResult], provider: Provider | None
) -> tuple[list[Sentence], str, str | None]:
    """(sentences, source, fallback reason). The template answer is always
    built and checked; a live provider may replace it with a validated
    rewording that keeps every figure and name the template states."""
    template = []
    for sentence in template_answer(steps):
        try:
            check_sentence(sentence.text, steps)
            template.append(sentence)
        except OutputRejected as exc:  # a defect: never shown, never a 500
            logger.error("template sentence failed its check: %s", exc)
            template.append(Sentence(UNCHECKED_SENTENCE))
    if provider is None:
        return template, SOURCE_TEMPLATE, None
    try:
        rewrite = model_answer(steps, provider)
        require_complete(rewrite, template, steps)
        return rewrite, SOURCE_MODEL, None
    except (OutputRejected, ProviderUnavailable) as exc:
        reason = exc.reason if isinstance(exc, ProviderUnavailable) else str(exc)
        logger.warning("explore writer fell back to the template: %s", reason)
        return template, SOURCE_TEMPLATE, reason
