"""The answer card: key points, a chart choice, the trend, and what to do next.

Every Explore answer gets a card built here, in code, from the tables the
answer was computed from. Nothing in it comes from a model:

- **Key points**: two to four short sentences: the highest and lowest
  group, the gap between the top group and the whole, the trend (the same
  figure against the same term a year earlier), and how many groups were
  withheld. Every number is a table cell with a claim ``{table, row,
  column}``, exactly like the answer's sentences. A figure worked out from
  two cells (a change, a gap) is a cell of the card's own small table, *Key
  figures*, whose rows name the two cells it was worked out from.
- **The trend read**: when the answer is a measure of the governed general
  analysis read in a term (or over a term window) and not already by term,
  the same measure, with the same filters, is read by term for the latest
  term and the same term a year earlier. It runs through ``execute`` like
  every other step, so the same suppression rules apply (a withheld term is
  a gap, never a zero) and it is audited as its own ``data.granted`` step.
  It is skipped when it would take longer than ``TREND_BUDGET_S``.
- **The chart**: chosen from the result's shape. A ranking by one grouping
  is a sorted horizontal bar chart with the whole as a reference line, a
  figure by term is a line, two groupings are grouped bars, and a single
  figure is a big number with its change since the year before.
- **Follow-up questions**: "Show trend" and "Break it down" offer questions
  that the rule planner answers with the same measure and filters. Each one
  is planned here before it is offered, so a button never leads to a
  question the system cannot answer.
- **A proposed plan**: a short list of staff actions drafted from the key
  points, labelled proposed. Nothing is stored or sent.
"""

from __future__ import annotations

import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from cabinet.counseling import MINIMUM_CELL_SIZE, SUPPRESSED_DISPLAY
from cabinet.explore import general
from cabinet.explore.answer import Sentence, _Builder
from cabinet.explore.catalog import ANALYSIS_BY_ID, Catalog, Column
from cabinet.explore.execute import StepHook, StepResult, execute
from cabinet.explore.planner import Step, rule_plan_detail

# The trend read is skipped when it would take longer than this.
TREND_BUDGET_S = 1.0
MAX_KEY_POINTS = 4
KEY_FIGURES_ID = "key_figures"
KEY_FIGURES_TITLE = "Key figures worked out from the tables above"
KEY_FIGURES_NOTE = (
    "Each figure here is worked out in code from two cells of the tables "
    "above (a difference, or a change as a share of the earlier figure). It "
    "reads no records of its own."
)
TREND_TITLE = "The same figure by term, for the trend"
REGISTRATION_PLAN_QUESTION = (
    "Create a seven-day action plan for Enrollment and Student Success."
)
# Rows that stand for the whole, not a group.
_TOTAL_NAMES = ("All students", "All terms")
# Label columns, in the order they name a row.
_LABEL_KEYS = (
    "major_name",
    "college_name",
    "term_name",
    "academic_year",
    "group",
    "group_2",
    "office",
    "course",
    "title",
    "name",
)
# The named analyses: the column that holds each one's figure, and whether
# its rows run through time (a line) or rank groups (bars).
_NAMED: dict[str, tuple[str, bool]] = {
    "gpa_by_major": ("avg_gpa", False),
    "gpa_by_college": ("avg_gpa", False),
    "dfw_by_course": ("dfw_rate", False),
    "course_dfw_trend": ("dfw_rate", True),
    "course_instructors": ("dfw_rate", False),
    "instructor_history": ("dfw_rate", False),
    "equity_gap": ("dfw_rate", False),
    "headcount_growth": ("growth", False),
    "enrollment_by_term": ("students", True),
    "continuing_registration_change": ("continuing", False),
    "withdrawal_by_modality": ("online_rate", True),
    "withdrawal_by_course_modality": ("online_rate", False),
    "standing_by_major": ("probation_rate", False),
    "graduations": ("graduates", False),
    "holds_by_office": ("holds", False),
    "advising_coverage": ("coverage", False),
    "credit_hours_by_term": ("attempted_hours", True),
}
# Groupings offered by "Break it down", in this order of preference.
_BREAKDOWN_ORDER = (
    "college",
    "major",
    "class_level",
    "first_generation",
    "pell",
    "residency",
    "admit_type",
    "load",
    "gender",
    "age_band",
    "housing",
    "athlete",
)
# Which office usually owns a figure, by the words in its label (a proposed
# plan names it; nothing is sent). Checked in order.
_OWNERS: tuple[tuple[str, str], ...] = (
    (r"\bhold", "Student Accounts (Bursar)"),
    (r"pell|aid\b", "Financial Aid"),
    (
        r"registr|headcount|enrolled|part-time|full-time|credits attempted",
        "the Registrar",
    ),
    (r"on campus|housing", "Student Life"),
    (
        r"retention|dropout|stop-out|stop out|probation|suspension|advis|"
        r"transfer|completion|major change",
        "Student Success",
    ),
    (r"gpa|d, f|withdrawal|graduat|time to degree", "Academic Affairs"),
)


@dataclass
class _Table:
    """The card's own small table (Key figures), built row by row."""

    index: int
    kind: str  # the kind of the difference column (points, count, gpa, ...)
    rows: list[dict[str, Any]] = field(default_factory=list)

    def add(self, what: str, compared: str, change: Any, pct: Any) -> int:
        self.rows.append(
            {"what": what, "compared": compared, "change": change, "change_pct": pct}
        )
        return len(self.rows) - 1

    def result(self) -> StepResult:
        columns = (
            Column("what", "Figure"),
            Column("compared", "Worked out from"),
            Column("change", "Difference", self.kind),
            Column("change_pct", "Change (%)", "pct"),
        )
        return StepResult(
            self.index,
            ANALYSIS_BY_ID[general.ANALYSIS_ID],
            {},
            [],
            (),
            columns,
            list(self.rows),
            [KEY_FIGURES_NOTE],
        )


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _diff_kind(kind: str) -> str:
    return {"pct": "points", "money": "money", "gpa": "gpa"}.get(kind, kind)


def _round(value: float, kind: str) -> float | int:
    if kind in ("count", "hours") or kind == "money" and value == int(value):
        return int(round(value))
    if kind == "gpa":
        return round(value, 2)
    return round(value, 1)


def _pct_change(now: float, then: float) -> float | None:
    if then == 0:
        return None
    return round(abs(100.0 * (now - then) / then), 1)


def _row_label(step: StepResult, row: int) -> str:
    cells = step.rows[row]
    parts = [
        str(cells[k])
        for k in _LABEL_KEYS
        if k in cells and cells[k] not in (None, "") and k != "course"
    ]
    if "course" in cells and cells["course"] not in (None, ""):
        parts.insert(0, str(cells["course"]))
    return ", ".join(parts)


def _is_total(step: StepResult, row: int) -> bool:
    cells = step.rows[row]
    return bool(cells.get("_total")) or any(
        cells.get(k) in _TOTAL_NAMES
        for k in ("major_name", "college_name", "term_name", "group")
    )


@dataclass
class _Shape:
    step: StepResult
    value: str
    kind: str
    label: str  # the value's column label ("Dropout rate (%)")
    form: str  # bar, line, grouped, number
    groups: list[str]  # general groupings, else []
    series: str | None = None  # the second label column (grouped / multi-line)


def _column(step: StepResult, key: str) -> Column | None:
    return next((c for c in step.columns if c.key == key), None)


def _shape(step: StepResult) -> _Shape | None:
    """What one step's table is: the column with its figure and the chart
    that fits it, or None when it has nothing to chart."""
    if step.error or not step.rows:
        return None
    a = step.analysis.id
    if a == general.ANALYSIS_ID:
        p = step.params
        groups = [g for g in (p.get("group_by"), p.get("then_by")) if g]
        col = _column(step, "value")
        if col is None:
            return None
        timed = [g for g in groups if g in ("term", "entry_cohort")]
        keys = _label_keys(groups)
        if not groups:
            form = "number"
        elif len(groups) == 2:
            form = "line" if timed else "grouped"
        else:
            form = "line" if timed else "bar"
        series = None
        if len(groups) == 2:
            x = groups.index(timed[0]) if timed else 0
            series = keys[1 - x]
        return _Shape(step, "value", col.kind, col.label, form, groups, series)
    named = _NAMED.get(a)
    if named is None:
        return None
    key, through_time = named
    col = _column(step, key)
    if col is None:
        # A role that may not see instructors gets the course as a whole.
        col = _column(step, "dfw_rate")
        if col is None:
            return None
        key = "dfw_rate"
    if a == "graduations" and step.params.get("group_by") == "year":
        through_time = True
    form = "number" if len(step.rows) == 1 else ("line" if through_time else "bar")
    return _Shape(step, key, col.kind, col.label, form, [])


def _label_keys(groups: list[str]) -> list[str]:
    """The label column of each general grouping, in order: major, college
    and term have their own; the others fill "group" then "group_2"."""
    out: list[str] = []
    slot = 0
    for g in groups:
        if g in ("major", "college", "term"):
            out.append(f"{g}_name")
        else:
            out.append("group" if slot == 0 else "group_2")
            slot += 1
    return out


def _ranked(shape: _Shape) -> list[int]:
    step = shape.step
    return [
        i
        for i in range(len(step.rows))
        if not _is_total(step, i) and _num(step.cell(i, shape.value)) is not None
    ]


def _total_row(step: StepResult) -> int | None:
    return next((i for i in range(len(step.rows)) if _is_total(step, i)), None)


def _withheld_groups(step: StepResult, shape: _Shape) -> int:
    """Groups withheld from the table: the general analysis's note (it leaves
    withheld groups out), else the rows whose figure is withheld."""
    for note in step.notes:
        match = re.match(r"(\d+) groups? (?:is|are) withheld", note)
        if match:
            return int(match.group(1))
    return sum(
        1
        for i in range(len(step.rows))
        if step.cell(i, shape.value) == SUPPRESSED_DISPLAY and not _is_total(step, i)
    )


# --- the trend read ----------------------------------------------------------------


def _prior_comparable(code: str, terms: dict[str, str]) -> str | None:
    """The same term a year earlier ("202620" -> "202520"), if it exists."""
    if len(code) != 6 or not code.isdigit():
        return None
    prior = f"{int(code[:4]) - 1}{code[4:]}"
    return prior if prior in terms else None


def _trend_plan(step: StepResult, catalog: Catalog) -> Step | None:
    """The trend read for a general-measure answer, or None when it does not
    apply: the measure must be read in a term or a term window, may be
    grouped by term, and the answer must not already be by term."""
    if step.analysis.id != general.ANALYSIS_ID or step.error:
        return None
    p = step.params
    measure = general.MEASURES.get(str(p.get("measure")))
    if measure is None or measure.scope not in ("term", "window"):
        return None
    if "term" not in general.allowed_groupings(measure):
        return None
    groups = [g for g in (p.get("group_by"), p.get("then_by")) if g]
    if "term" in groups:
        return None
    terms = catalog.vocab.terms
    regular = [t for t in terms if catalog.vocab.term_season[t] != "Summer"]
    if not regular:
        return None
    current = str(p.get("term_to") or p.get("term_from") or regular[-1])
    if current not in terms:
        return None
    prior = _prior_comparable(current, terms)
    if prior is None:
        return None
    params: dict[str, Any] = {
        "measure": measure.id,
        "group_by": "term",
        "order": "natural",
    }
    for key in general.GROUPING_KEYS:
        if key != "term" and p.get(key):
            params[key] = p[key]
    if measure.scope == "term":
        # A term measure reads one term; by term it reads a window. The
        # year between the two terms is enough.
        params["term_from"] = prior
        params["term_to"] = current
    return Step(general.ANALYSIS_ID, params)


def read_trend(
    step: StepResult,
    con: sqlite3.Connection,
    catalog: Catalog,
    role: str,
    index: int,
    on_step: StepHook | None = None,
    budget_s: float = TREND_BUDGET_S,
) -> StepResult | None:
    """The same measure by term (latest term and a year earlier), through
    ``execute`` so every suppression rule applies, or None when it does not
    apply or would take longer than ``budget_s``."""
    plan = _trend_plan(step, catalog)
    if plan is None:
        return None
    deadline = time.monotonic() + budget_s

    def stop() -> int:
        return 1 if time.monotonic() > deadline else 0

    def granted(
        _i: int, analysis_id: str, fields: tuple[str, ...], withheld: bool
    ) -> None:
        if on_step is not None:
            on_step(index, analysis_id, fields, withheld)

    con.set_progress_handler(stop, 20_000)
    try:
        results = execute([plan], con, catalog, role, on_step=granted)
    except sqlite3.OperationalError:
        return None  # over budget: interrupted
    finally:
        con.set_progress_handler(None, 0)
    if time.monotonic() > deadline or not results or results[0].error:
        return None
    result = results[0]
    result.index = index
    result.analysis = ANALYSIS_BY_ID[general.ANALYSIS_ID]
    return result


# --- key points --------------------------------------------------------------------


def _term_row(step: StepResult, code: str) -> int | None:
    return next((i for i, r in enumerate(step.rows) if r.get("term") == code), None)


@dataclass
class _Trend:
    """The latest term against the same term a year earlier, in one table."""

    step: StepResult
    now: int | None  # row of the latest term (None: withheld)
    then: int | None
    now_term: str
    then_term: str
    figure: int | None = None  # the Key figures row of the change


def _trend_rows(trend: StepResult, catalog: Catalog) -> _Trend | None:
    """Pick the latest term the trend table reads and a year before it."""
    terms = catalog.vocab.terms
    p = trend.params
    current = str(p.get("term_to") or "")
    if not current:
        shown = [r.get("term") for r in trend.rows if r.get("term") in terms]
        regular = [
            t for t in shown if catalog.vocab.term_season.get(str(t)) != "Summer"
        ]
        if not regular:
            return None
        current = str(regular[-1])
    prior = _prior_comparable(current, terms)
    if prior is None:
        return None
    now = _term_row(trend, current)
    then = _term_row(trend, prior)
    if now is not None and _num(trend.cell(now, "value")) is None:
        now = None
    if then is not None and _num(trend.cell(then, "value")) is None:
        then = None
    return _Trend(trend, now, then, terms[current], terms[prior])


def _change(
    table: _Table, what: str, step: StepResult, now: int, then: int, key: str, kind: str
) -> int:
    a = _num(step.cell(now, key)) or 0.0
    b = _num(step.cell(then, key)) or 0.0
    return table.add(
        what,
        f"{_row_label(step, now)} against {_row_label(step, then)}",
        _round(abs(a - b), kind),
        _pct_change(a, b),
    )


def _direction(step: StepResult, now: int, then: int, key: str) -> str:
    a = _num(step.cell(now, key)) or 0.0
    b = _num(step.cell(then, key)) or 0.0
    if a > b:
        return "up"
    if a < b:
        return "down"
    return "unchanged"


def _say_change(
    b: _Builder, table_step: StepResult, row: int, direction: str, kind: str
) -> _Builder:
    """ "up 1.2 points (4.1%)" from a Key figures row."""
    if direction == "unchanged":
        return b.t("unchanged")
    b.t(f"{direction} ").c(table_step, row, "change")
    if table_step.cell(row, "change_pct") is not None:
        b.t(" (").c(table_step, row, "change_pct").t(")")
    return b


@dataclass
class Card:
    key_points: list[Sentence]
    chart: dict[str, Any] | None
    extra_steps: list[StepResult]
    plan: list[Sentence]
    followups: dict[str, Any]

    def to_json(
        self, step_json: Callable[[StepResult], dict[str, Any]]
    ) -> dict[str, Any]:
        """The card as the API returns it. ``extra_steps`` continue the
        answer's step numbering: a claim's ``table`` counts the answer's
        steps first, then these."""
        extra = []
        for s in self.extra_steps:
            if s.analysis.id == general.ANALYSIS_ID and not s.params:
                extra.append(
                    {
                        "analysis_id": KEY_FIGURES_ID,
                        "title": KEY_FIGURES_TITLE,
                        "params_plain": [],
                        "fields_read": [],
                        "aggregate_only": True,
                        "table": {
                            "columns": [
                                {"key": c.key, "label": c.label, "kind": c.kind}
                                for c in s.columns
                            ],
                            "rows": [[r.get(c.key) for c in s.columns] for r in s.rows],
                        },
                        "notes": list(s.notes),
                    }
                )
                continue
            body = step_json(s)
            body["title"] = TREND_TITLE
            body["purpose"] = "trend"
            extra.append(body)
        return {
            "key_points": [k.to_json() for k in self.key_points],
            "chart": self.chart,
            "extra_steps": extra,
            "plan": [s.to_json() for s in self.plan],
            "followups": self.followups,
        }


def _main_shape(steps: list[StepResult]) -> _Shape | None:
    for step in steps:
        shape = _shape(step)
        if shape is not None:
            return shape
    return None


def build_card(
    steps: list[StepResult],
    question: str,
    con: sqlite3.Connection | None,
    catalog: Catalog,
    role: str,
    on_step: StepHook | None = None,
    trend_budget_s: float = TREND_BUDGET_S,
) -> Card:
    """The card for one answer. ``on_step`` is called before the trend read
    reads anything (the API writes ``data.granted`` from it)."""
    shape = _main_shape(steps)
    followups = _followups(steps, question, catalog)
    if shape is None:
        return Card([], None, [], [], followups)
    step = shape.step
    trend_step: StepResult | None = None
    if con is not None:
        trend_step = read_trend(
            step, con, catalog, role, len(steps), on_step, trend_budget_s
        )
    extra: list[StepResult] = [trend_step] if trend_step is not None else []
    table = _Table(len(steps) + len(extra), _diff_kind(shape.kind))
    points: list[tuple[str, Sentence]] = []
    trend: _Trend | None = None

    if shape.form == "number":
        points.extend(_number_points(shape, table, steps))
    elif shape.form == "line" and len(shape.groups) < 2:
        points.extend(_line_points(shape, table, steps, catalog))
    else:
        points.extend(_ranked_points(shape, table, steps))
    if trend_step is not None:
        trend = _trend_rows(trend_step, catalog)
        if trend is not None:
            sentence = _trend_point(shape, trend, table, steps, extra)
            if sentence is not None:
                points.append(("trend", sentence))
    withheld = _withheld_groups(step, shape)
    if withheld and shape.form != "number":
        rows_are = "term" if shape.form == "line" and not shape.groups else "group"
        noun = f"{rows_are} is" if withheld == 1 else f"{rows_are}s are"
        points.append(
            (
                "withheld",
                Sentence(
                    f"{withheld} {noun} withheld (fewer than {MINIMUM_CELL_SIZE} "
                    "students, or enough to protect one that small): they are "
                    "gaps, never zeros, and are not ranked."
                ),
            )
        )
    points = _cap(points)
    key_table = table.result() if table.rows else None
    if key_table is not None:
        extra.append(key_table)
    chart = _chart(shape, trend, table if key_table is not None else None)
    plan = _plan(shape, points, steps, extra)
    return Card([s for _, s in points], chart, extra, plan, followups)


def _cap(points: list[tuple[str, Sentence]]) -> list[tuple[str, Sentence]]:
    """At most four, dropping the gap first and then the lowest."""
    for drop in ("gap", "lowest", "peak"):
        if len(points) <= MAX_KEY_POINTS:
            break
        points = [p for p in points if p[0] != drop]
    return points[:MAX_KEY_POINTS]


def _with_steps(steps: list[StepResult], *extra: StepResult) -> list[StepResult]:
    return [*steps, *extra]


def _ranked_points(
    shape: _Shape, table: _Table, steps: list[StepResult]
) -> list[tuple[str, Sentence]]:
    step = shape.step
    ranked = _ranked(shape)
    if not ranked:
        return []
    values = {i: _num(step.cell(i, shape.value)) or 0.0 for i in ranked}
    hi = max(ranked, key=lambda i: (values[i], -i))
    lo = min(ranked, key=lambda i: (values[i], i))
    out: list[tuple[str, Sentence]] = []
    b = _Builder(steps)
    b.t("Highest: ").t(_row_label(step, hi)).t(", ").c(step, hi, shape.value).t(".")
    out.append(("highest", b.done()))
    if len(ranked) >= 2 and lo != hi:
        b = _Builder(steps)
        b.t("Lowest: ").t(_row_label(step, lo)).t(", ").c(step, lo, shape.value).t(".")
        out.append(("lowest", b.done()))
    total = _total_row(step)
    if (
        total is not None
        and shape.kind != "count"  # parts of a whole: a gap from it says nothing
        and _num(step.cell(total, shape.value)) is not None
    ):
        row = _change(
            table, "Gap from the whole", step, hi, total, shape.value, shape.kind
        )
        direction = _direction(step, hi, total, shape.value)
        if direction != "unchanged":
            key = table.result()
            verb = (
                " is "
                if "major_name" in step.rows[hi]
                or "college_name" in step.rows[hi]
                or shape.step.analysis.id != general.ANALYSIS_ID
                else " are "
            )
            b = _Builder(_with_steps(steps, key))
            b.t(_row_label(step, hi)).t(verb).c(key, row, "change")
            b.t(" above" if direction == "up" else " below")
            b.t(" the whole (").c(step, total, shape.value).t(" for all students).")
            out.append(("gap", b.done()))
        else:
            table.rows.pop()
    return out


def _line_points(
    shape: _Shape, table: _Table, steps: list[StepResult], catalog: Catalog
) -> list[tuple[str, Sentence]]:
    step = shape.step
    ranked = _ranked(shape)
    if not ranked:
        return []
    out: list[tuple[str, Sentence]] = []
    last = ranked[-1]
    b = _Builder(steps)
    b.t("Latest: ").t(_row_label(step, last)).t(", ").c(step, last, shape.value).t(".")
    out.append(("latest", b.done()))
    # The comparable earlier row: the same term a year earlier when the rows
    # are terms, else the row before.
    code = str(step.rows[last].get("term") or "")
    prior = _prior_comparable(code, catalog.vocab.terms) if code else None
    then = _term_row(step, prior) if prior else None
    if then is None and prior is not None:
        prior_name = catalog.vocab.terms[prior]
        out.append(
            (
                "trend",
                Sentence(
                    f"The {prior_name} figure is withheld or not recorded, so no "
                    "change is shown."
                ),
            )
        )
    else:
        if then is None and len(ranked) >= 2:
            then = ranked[-2]
        if then is not None and _num(step.cell(then, shape.value)) is not None:
            row = _change(table, "Change", step, last, then, shape.value, shape.kind)
            direction = _direction(step, last, then, shape.value)
            key = table.result()
            b = _Builder(_with_steps(steps, key))
            b.t("Since ").t(_row_label(step, then)).t(": ")
            _say_change(b, key, row, direction, shape.kind)
            b.t(", from ").c(step, then, shape.value).t(".")
            out.append(("trend", b.done()))
    if len(ranked) >= 4:
        values = {i: _num(step.cell(i, shape.value)) or 0.0 for i in ranked}
        peak = max(ranked, key=lambda i: (values[i], -i))
        if peak != last:
            b = _Builder(steps)
            b.t("Highest: ").t(_row_label(step, peak)).t(", ")
            b.c(step, peak, shape.value).t(".")
            out.append(("peak", b.done()))
    return out


def _number_points(
    shape: _Shape, table: _Table, steps: list[StepResult]
) -> list[tuple[str, Sentence]]:
    step = shape.step
    value = step.cell(0, shape.value)
    if value == SUPPRESSED_DISPLAY or _num(value) is None:
        return [
            (
                "withheld",
                Sentence(
                    "The figure is withheld: the group is too small to show "
                    "without risking identifying someone."
                ),
            )
        ]
    out: list[tuple[str, Sentence]] = []
    b = _Builder(steps)
    b.t(f"{shape.label.removesuffix(' (%)')}: ").c(step, 0, shape.value)
    if any(c.key == "scope" for c in step.columns):
        b.t(" in ").c(step, 0, "scope")
    if _column(step, "students") is not None and shape.value != "students":
        b.t(", across ").c(step, 0, "students").t(" students")
    out.append(("value", b.t(".").done()))
    # Registration change: its own row carries the change against a year
    # earlier.
    if (
        step.analysis.id == "continuing_registration_change"
        and _num(step.cell(0, "change_pct")) is not None
    ):
        b = _Builder(steps)
        b.t("Against ").c(step, 0, "prior_term_name").t(": ")
        b.c(step, 0, "prior_continuing").t(" then, a change of ")
        b.c(step, 0, "change_pct").t(".")
        out.append(("trend", b.done()))
    return out


def _trend_point(
    shape: _Shape,
    trend: _Trend,
    table: _Table,
    steps: list[StepResult],
    extra: list[StepResult],
) -> Sentence | None:
    t = trend.step
    if trend.now is None or trend.then is None:
        missing = trend.then_term if trend.then is None else trend.now_term
        return Sentence(
            f"The {missing} figure is withheld (a group too small to show), so "
            "no change is shown."
        )
    row = _change(
        table, "Change in a year", t, trend.now, trend.then, "value", shape.kind
    )
    trend.figure = row
    direction = _direction(t, trend.now, trend.then, "value")
    key = table.result()
    b = _Builder(_with_steps(steps, *extra, key))
    measure = general.MEASURES[str(t.params["measure"])]
    if shape.groups:
        b.t(f"All groups together, the {measure.label} was ")
    else:
        b.t("A year earlier: the figure was ")
        b.c(t, trend.then, "value").t(" in ").c(t, trend.then, "term_name")
        b.t(" and ").c(t, trend.now, "value").t(" in ").c(t, trend.now, "term_name")
        b.t(", ")
        _say_change(b, key, row, direction, shape.kind)
        return b.t(".").done()
    b.c(t, trend.now, "value").t(" in ")
    b.c(t, trend.now, "term_name").t(", ")
    _say_change(b, key, row, direction, shape.kind)
    b.t(" from ").c(t, trend.then, "value").t(" in ").c(t, trend.then, "term_name")
    return b.t(".").done()


# --- the chart templates ---------------------------------------------------------
#
# A fixed registry: every chart an answer can show is one of these. Code
# picks the template from the result's shape and plugs the computed cells in;
# a model may suggest a template id, but ``choose_template`` only accepts one
# whose shape matches, and no model ever draws or describes a chart. The UI
# holds the renderer for each id (ui/src/components/AnswerChart.tsx).


@dataclass(frozen=True)
class ChartTemplate:
    id: str
    title: str
    accepts: str  # the result shape it takes, in words


CHART_TEMPLATES: dict[str, ChartTemplate] = {
    t.id: t
    for t in (
        ChartTemplate(
            "ranking_bar",
            "Ranking bar",
            "one grouping: one figure per group, sorted, the whole as a reference line",
        ),
        ChartTemplate(
            "trend_line",
            "Trend line",
            "one figure by term (or entry cohort), with the change since a year "
            "earlier",
        ),
        ChartTemplate(
            "grouped_bars",
            "Comparison bars",
            "two groupings: one figure per pair of groups",
        ),
        ChartTemplate(
            "kpi_number",
            "Key figure",
            "a single figure, with its change since the same term a year earlier",
        ),
        ChartTemplate(
            "share_bar",
            "Share of the whole",
            "counts of people in each group of one grouping that add up to the whole "
            "(a stacked 100% bar)",
        ),
        ChartTemplate(
            "before_after",
            "Before and after",
            "one figure at two times (a year earlier and now, before and after a "
            "decision)",
        ),
        ChartTemplate(
            "funnel",
            "Funnel",
            "counts of people at stages, each stage part of the one before",
        ),
        ChartTemplate(
            "small_multiples",
            "Small multiples",
            "a figure by term for each of up to six groups (one small trend each)",
        ),
    )
}
_FORM_TEMPLATE = {
    "bar": "ranking_bar",
    "line": "trend_line",
    "grouped": "grouped_bars",
    "number": "kpi_number",
}
SMALL_MULTIPLES_MAX = 6


def _shape_templates(shape: _Shape) -> list[str]:
    """Every template this shape can fill, the default first."""
    step = shape.step
    out = [_FORM_TEMPLATE[shape.form]]
    if shape.form == "number" and step.analysis.id == "continuing_registration_change":
        out.insert(0, "before_after")
    if shape.form == "line" and shape.series is not None:
        groups = {str(r.get(shape.series)) for r in step.rows}
        if 2 <= len(groups) <= SMALL_MULTIPLES_MAX:
            out.insert(0, "small_multiples")
    if (
        shape.form == "bar"
        and shape.kind == "count"
        and _total_row(step) is not None
        and len(shape.groups) == 1
        and shape.groups[0] not in ("major", "college", "term", "entry_cohort")
        and not _withheld_groups(step, shape)
    ):
        # Counts of people that add up to the whole: a share of it.
        ranked = _ranked(shape)
        total = _num(step.cell(_total_row(step) or 0, shape.value)) or 0
        parts = sum(_num(step.cell(i, shape.value)) or 0 for i in ranked)
        if total and parts == total and len(ranked) <= SMALL_MULTIPLES_MAX:
            out.insert(0, "share_bar")
    return out


def choose_template(shape: _Shape, suggested: str | None = None) -> str:
    """The template for a result: a suggested id only when this shape can
    fill it, else the shape's default."""
    fits = _shape_templates(shape)
    if suggested in fits:
        return str(suggested)
    return fits[0]


# --- the chart -----------------------------------------------------------------------


def _chart(shape: _Shape, trend: _Trend | None, table: _Table | None) -> dict[str, Any]:
    step = shape.step
    labels = [k for k in _LABEL_KEYS if any(k in r for r in step.rows)]
    if shape.series is not None:
        labels = [k for k in labels if k != shape.series]
    chart: dict[str, Any] = {
        "template": choose_template(shape),
        "form": shape.form,
        "table": step.index,
        "value": shape.value,
        "value_label": shape.label,
        "kind": shape.kind,
        "label": labels,
        "series": shape.series,
        "reference_row": _total_row(step),
    }
    if shape.step.analysis.id == "continuing_registration_change":
        chart["before_after"] = {
            "before_label": "prior_term_name",
            "before": "prior_continuing",
            "after_label": "term_name",
            "after": "continuing",
            "change": "change_pct",
        }
    if trend is not None and trend.figure is not None and trend.now is not None:
        chart["trend"] = {
            "table": trend.step.index,
            "now_row": trend.now,
            "then_row": trend.then,
            "change_table": table.index if table is not None else None,
            "change_row": trend.figure,
            "direction": _direction(trend.step, trend.now, trend.then or 0, "value")
            if trend.then is not None
            else None,
        }
    return chart


# --- follow-up questions ----------------------------------------------------------


def _filters(p: dict[str, Any]) -> dict[str, str]:
    return {k: str(p[k]) for k in general.GROUPING_KEYS if k != "term" and p.get(k)}


def _subject_words(filters: dict[str, str], catalog: Catalog) -> tuple[str, str]:
    """(" for international students", " in Nursing") from the filters."""
    who: list[str] = []
    where = ""
    for key, value in filters.items():
        if key == "major":
            where = f" in {catalog.vocab.majors.get(value, value)}"
        elif key == "college":
            where = f" in the {catalog.vocab.colleges.get(value, value)}"
        elif key == "entry_cohort":
            who.append(f"students who entered in {value}")
        else:
            label = general.GROUPINGS[key].values.get(value, value)
            who.append(
                label[:1].lower() + label[1:]
                if not label.startswith(
                    (
                        "Pell",
                        "U.S.",
                        "Hispanic",
                        "Black",
                        "Asian",
                        "White",
                        "American",
                        "Native",
                    )
                )
                else label
            )
    return (f" for {' '.join(who)}" if who else ""), where


def _measure_question(
    measure: general.Measure,
    groups: list[str],
    filters: dict[str, str],
    catalog: Catalog,
) -> str | None:
    """A question the rule planner answers with exactly this measure,
    groupings and filters, or None."""
    nouns = " and ".join(general.GROUPINGS[g].noun for g in groups)
    who, where = _subject_words(filters, catalog)
    label = measure.label[:1].upper() + measure.label[1:]
    forms = [
        f"{label}{who}{where} by {nouns}",
        f"What is the {measure.label}{who}{where} by {nouns}?",
        f"{label} by {nouns}{who}{where}",
    ]
    for text in forms:
        try:
            planned, _ = rule_plan_detail(text, catalog)
        except Exception:  # a planner defect never breaks the answer
            continue
        if not planned or len(planned) != 1:
            continue
        s = planned[0]
        if s.analysis_id != general.ANALYSIS_ID:
            continue
        got = [g for g in (s.params.get("group_by"), s.params.get("then_by")) if g]
        if (
            s.params.get("measure") == measure.id
            and got == groups
            and _filters(s.params) == filters
        ):
            return text
    return None


def _norm(text: str) -> str:
    return " ".join(text.lower().replace("-", " ").split())


def _splits_itself(measure: general.Measure, grouping: str) -> bool:
    """A share split by the attribute it counts (the Pell share by Pell
    status) is always 100% and 0%: never offered."""
    if not measure.label.endswith(" share"):
        return False
    core = _norm(measure.label.removesuffix(" share"))
    return any(core in _norm(v) for v in general.GROUPINGS[grouping].values.values())


def _followups(
    steps: list[StepResult], question: str, catalog: Catalog
) -> dict[str, Any]:
    out: dict[str, Any] = {"trend": None, "breakdowns": [], "topic": None}
    ids = {s.analysis.id for s in steps}
    registration = bool(
        ids & {"enrollment_by_term", "continuing_registration_change"}
        or re.search(r"regist", question, re.IGNORECASE)
    )
    step = next((s for s in steps if not s.error and s.rows), None)
    if step is not None and step.analysis.id == general.ANALYSIS_ID:
        p = step.params
        measure = general.MEASURES.get(str(p.get("measure")))
        if measure is not None:
            if measure.id == "headcount":
                registration = True
            groups = [g for g in (p.get("group_by"), p.get("then_by")) if g]
            filters = _filters(p)
            allowed = general.allowed_groupings(measure)
            timed = (
                "term"
                if "term" in allowed
                else ("entry_cohort" if "entry_cohort" in allowed else None)
            )
            if timed is not None and timed not in groups and timed not in filters:
                out["trend"] = _measure_question(measure, [timed], filters, catalog)
            if len(groups) < 2:
                offered: list[dict[str, str]] = []
                for g in _BREAKDOWN_ORDER:
                    if len(offered) == 3:
                        break
                    if g in groups or g in filters or g not in allowed:
                        continue
                    if g == "major" and "college" in groups:
                        continue
                    if _splits_itself(measure, g):
                        continue
                    text = _measure_question(measure, [*groups, g], filters, catalog)
                    if text is not None:
                        offered.append(
                            {
                                "grouping": g,
                                "label": general.GROUPINGS[g].label,
                                "question": text,
                            }
                        )
                out["breakdowns"] = offered
    elif step is not None and step.analysis.id == "dfw_by_course" and step.rows:
        title = catalog.vocab.courses.get(str(step.rows[0].get("course")), "")
        if title:
            text = f"How has the DFW rate in {title} changed by term?"
            planned, _ = rule_plan_detail(text, catalog)
            if planned and planned[0].analysis_id == "course_dfw_trend":
                out["trend"] = text
    out["topic"] = "registration" if registration else None
    return out


# --- a proposed plan ------------------------------------------------------------------


def owner_office(label: str) -> str:
    for pattern, office in _OWNERS:
        if re.search(pattern, label, re.IGNORECASE):
            return office
    return "the responsible office"


def _plan(
    shape: _Shape,
    points: list[tuple[str, Sentence]],
    steps: list[StepResult],
    extra: list[StepResult],
) -> list[Sentence]:
    """Proposed staff actions from the key points: proposals only."""
    step = shape.step
    if step.analysis.id == general.ANALYSIS_ID:
        label = general.MEASURES[str(step.params["measure"])].label
    else:
        label = shape.label.removesuffix(" (%)")
        label = label[:1].lower() + label[1:]
    office = owner_office(label + " " + step.analysis.title)
    out: list[Sentence] = []
    kinds = {k: s for k, s in points}
    if "highest" in kinds and shape.form in ("bar", "grouped"):
        out.append(
            Sentence(
                f"Ask {office} to explain what is behind the highest {label} "
                f"and bring one change to try. {kinds['highest'].text}",
                list(kinds["highest"].claims),
            )
        )
    if "lowest" in kinds and shape.form in ("bar", "grouped"):
        out.append(
            Sentence(
                f"Ask {office} what the group with the lowest {label} does "
                f"differently. {kinds['lowest'].text}",
                list(kinds["lowest"].claims),
            )
        )
    if "trend" in kinds and kinds["trend"].claims:
        out.append(
            Sentence(
                f"Ask {office} whether the change in the {label} needs a "
                f"response this term. {kinds['trend'].text}",
                list(kinds["trend"].claims),
            )
        )
    if "withheld" in kinds and shape.form != "number":
        out.append(
            Sentence(
                "Keep the withheld groups out of any list of names: they are "
                "withheld to protect students, and the figures stay aggregate."
            )
        )
    out.append(
        Sentence(
            f"Look at the {label} again with {office} when the next term's "
            "records are in, and compare it with these figures."
        )
    )
    return out[:4]
