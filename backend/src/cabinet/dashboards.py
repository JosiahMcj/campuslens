"""The Data page: time-series dashboards over the school records.

``GET /data/dashboards`` lists the dashboards the caller's role may open
(``cabinet.data_roles``), the filters and "compare by" choices, and the
academic years. ``GET /data/series?chart=<id>`` returns one chart: a value
per term (or per entering class), optionally narrowed by filters (one value
per student attribute, as query parameters) and split into series by one
attribute (``compare=``).

Every figure goes through the governed general analysis
(``cabinet.explore.general``): the same reviewed row sets, the same
aggregates, and the same small-cell rules, so a point over fewer than 10
students, or one that could be worked out from published totals for a
group that small, is withheld. A withheld point comes back as
``{"value": null, "status": "withheld"}``, never as a zero. Nothing here
returns a student row or an id.

Each chart is always computed over the whole record (every term, every
entering class) and the page slices the years it shows, so choosing a year
range never publishes a new window that could be subtracted from another.

Every successful series request writes one ``data.granted`` audit event
naming the person, the chart and the fields it read (``aggregate_only``).
"""

from __future__ import annotations

import json
import statistics
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from cabinet.counseling import MINIMUM_CELL_SIZE
from cabinet.data_roles import (
    CAMPUS,
    FINANCES,
    STUDENTS,
    attribute_allowed,
    dashboards_for,
)
from cabinet.explore import general
from cabinet.explore.catalog import (
    SchoolDataMissing,
    Vocab,
    catalog_for,
    connect_readonly,
    school_db_path,
)
from cabinet.explore.general import GROUPINGS, MEASURES, Measure

router = APIRouter()

REGULAR = general.REGULAR

# --- measures the Data page adds ------------------------------------------------
#
# The Explore measures are reused as they are; these few add a per-term
# reading the questions do not need. Each is a SUM or COUNT over one of the
# general analysis's reviewed row sets, so the same suppression applies.

AVG_GPA_TERM = Measure(
    "avg_gpa_term",
    "average cumulative GPA",
    "student",
    "gpa",
    "SUM(b.gpa)",
    "COUNT(*)",
    "b.gpa IS NOT NULL",
    "Average cumulative GPA of the students enrolled in each fall and spring term.",
    "Average cumulative GPA",
    "Sum of GPAs",
    "Students with a GPA",
    "term",
)
ATHLETE_SHARE = Measure(
    "athlete_share",
    "athlete share",
    "student",
    "pct",
    "SUM(b.athlete = 'athlete')",
    "COUNT(*)",
    "1 = 1",
    "Share of enrolled students who are intercollegiate athletes, each fall and "
    "spring term.",
    "Athletes (%)",
    "Athletes",
    "Students",
    "term",
)
NEW_STUDENTS = Measure(
    "new_students",
    "new students",
    "cohort",
    "count",
    "COUNT(*)",
    "COUNT(*)",
    "1 = 1",
    "Students who started at the university in the fall term, first-time and "
    "transfer, counted in the term they entered.",
    "New students",
    "New students",
    "New students",
    "cohort",
)
FIN_HOLD_RATE = Measure(
    "financial_hold_rate",
    "financial hold rate",
    "student_term",
    "pct",
    "SUM(b.fin_held)",
    "COUNT(*)",
    REGULAR,
    "Share of students enrolled in the fall or spring term who had a financial "
    "hold (an account balance) placed in that term.",
    "With a financial hold (%)",
    "With a financial hold",
    "Students",
    "window",
)
_FIN_ONLY = f"b.fin_held = 1 AND {REGULAR}"
FIN_HOLD_STUDENTS = Measure(
    "financial_hold_students",
    "students with a financial hold",
    "student_term",
    "count",
    "COUNT(*)",
    "COUNT(*)",
    _FIN_ONLY,
    "Enrolled students who had a financial hold placed in the fall or spring term.",
    "Students with a financial hold",
    "Students",
    "Students",
    "window",
)
FIN_BALANCE = Measure(
    "financial_hold_balance",
    "balance on financial holds",
    "student_term",
    "dollars",
    "SUM(b.fin_amount)",
    "COUNT(*)",
    _FIN_ONLY,
    "The balance owed on the financial holds placed on enrolled students in the "
    "term, in dollars, at the time each hold was placed.",
    "Balance on financial holds ($)",
    "Balance",
    "Students with a financial hold",
    "window",
)

_EXTRA_FIELDS: dict[str, tuple[str, ...]] = {
    "avg_gpa_term": ("student_term_records.cumulative_gpa",),
    "athlete_share": ("student_profiles.athlete",),
    "new_students": ("students.entry_term", "students.entry_type"),
    "financial_hold_rate": ("person_holds.category", "person_holds.term_code"),
    "financial_hold_students": ("person_holds.category", "person_holds.term_code"),
    "financial_hold_balance": (
        "person_holds.category",
        "person_holds.term_code",
        "person_holds.amount",
    ),
}


# --- charts -----------------------------------------------------------------------


@dataclass(frozen=True)
class Chart:
    id: str
    dashboard: str
    title: str
    measure: Measure
    x: str  # "term" (fall and spring terms) or "entry_cohort" (entering class)
    form: str  # "line" or "bar"
    # A fixed split ("by college"): the person's "compare by" does not apply.
    split: str | None = None
    stat: str = "value"  # "value", or "median" of the per-student amounts
    # Which entering classes the chart shows: "all" fall classes, or the
    # classes the measure follows ("ret", "g4", "g6"), or "settled" (all but
    # the last two, which have had too little time to tell).
    cohorts: str = "all"
    all_entrants: bool = False  # first-time AND transfer students
    # The attribute a share is a share of (the Pell share and Pell status):
    # narrowing to or splitting by it would only show 100% and 0%.
    about: str | None = None
    # Holds are placed through a term (many when it ends), so the term the
    # records end in is not complete yet: say so.
    partial_last: bool = False
    note: str = ""


def _c(*args: Any, **kwargs: Any) -> Chart:
    return Chart(*args, **kwargs)


M = MEASURES
CHARTS: tuple[Chart, ...] = (
    # Students
    _c("headcount", STUDENTS, "Students enrolled", M["headcount"], "term", "line"),
    _c(
        "headcount_by_college",
        STUDENTS,
        "Students enrolled by college",
        M["headcount"],
        "term",
        "line",
        split="college",
        note="Each student is counted in the college of their major that term.",
    ),
    _c(
        "new_students",
        STUDENTS,
        "New students each fall",
        NEW_STUDENTS,
        "entry_cohort",
        "bar",
        split="admit_type",
        all_entrants=True,
    ),
    _c(
        "retention",
        STUDENTS,
        "First-year retention by entering class",
        M["retention_rate"],
        "entry_cohort",
        "bar",
        cohorts="ret",
    ),
    _c(
        "grad4",
        STUDENTS,
        "Graduated within 4 years",
        M["grad_rate_4yr"],
        "entry_cohort",
        "bar",
        cohorts="g4",
        note="Only the classes that entered in {span} have had four years in these "
        "records.",
    ),
    _c(
        "grad6",
        STUDENTS,
        "Graduated within 6 years",
        M["grad_rate_6yr"],
        "entry_cohort",
        "bar",
        cohorts="g6",
        note="Only the classes that entered in {span} have had six years in these "
        "records.",
    ),
    _c(
        "stop_out",
        STUDENTS,
        "Not enrolled the next term (stop-out)",
        M["stop_out_rate"],
        "term",
        "line",
        note="{last} has no next term in the records yet.",
    ),
    _c(
        "dropout",
        STUDENTS,
        "Dropped out, by entering class",
        M["dropout_rate"],
        "entry_cohort",
        "bar",
        cohorts="settled",
        note="The two most recent entering classes are left out: they have had too "
        "little time to tell. Earlier classes have had longer to leave, so their "
        "rates are naturally higher.",
    ),
    _c("avg_gpa", STUDENTS, "Average GPA", AVG_GPA_TERM, "term", "line"),
    # Student finances
    _c(
        "financial_hold_students",
        FINANCES,
        "Students with a financial hold",
        FIN_HOLD_STUDENTS,
        "term",
        "line",
        partial_last=True,
    ),
    _c(
        "financial_hold_rate",
        FINANCES,
        "Share of students with a financial hold",
        FIN_HOLD_RATE,
        "term",
        "line",
        partial_last=True,
    ),
    _c(
        "financial_balance_total",
        FINANCES,
        "Total balance on financial holds",
        FIN_BALANCE,
        "term",
        "line",
        partial_last=True,
    ),
    _c(
        "financial_balance_median",
        FINANCES,
        "Typical (median) balance per student with a hold",
        FIN_BALANCE,
        "term",
        "line",
        stat="median",
        partial_last=True,
        note="The median: half of the students with a financial hold owed less, "
        "half owed more. Rounded to the nearest $10.",
    ),
    _c(
        "pell_share",
        FINANCES,
        "Pell grant recipients",
        M["pell_share"],
        "term",
        "line",
        about="pell",
    ),
    _c(
        "retention_by_pell",
        FINANCES,
        "First-year retention, Pell and not",
        M["retention_rate"],
        "entry_cohort",
        "bar",
        split="pell",
        cohorts="ret",
    ),
    _c(
        "grad4_by_pell",
        FINANCES,
        "Graduated within 4 years, Pell and not",
        M["grad_rate_4yr"],
        "entry_cohort",
        "bar",
        split="pell",
        cohorts="g4",
    ),
    # Campus life and academics
    _c(
        "on_campus",
        CAMPUS,
        "Living on campus",
        M["on_campus_share"],
        "term",
        "line",
        about="housing",
    ),
    _c("athletes", CAMPUS, "Athletes", ATHLETE_SHARE, "term", "line", about="athlete"),
    _c(
        "part_time",
        CAMPUS,
        "Studying part-time",
        M["part_time_share"],
        "term",
        "line",
        about="load",
    ),
    _c(
        "dfw",
        CAMPUS,
        "D, F or withdrawal rate",
        M["dfw_rate"],
        "term",
        "line",
        note="Fall and spring terms; summer sessions are left out.",
    ),
    _c(
        "probation",
        CAMPUS,
        "On academic probation",
        M["probation_rate"],
        "term",
        "line",
    ),
    _c(
        "advising",
        CAMPUS,
        "Met an advisor",
        M["advising_rate"],
        "term",
        "line",
    ),
)
CHARTS_BY_ID = {c.id: c for c in CHARTS}

DASHBOARDS: dict[str, dict[str, str]] = {
    STUDENTS: {
        "title": "Students",
        "intro": "Enrollment, new students, retention, graduation and GPA over time.",
    },
    FINANCES: {
        "title": "Student finances",
        "intro": "Financial holds, balances owed, and how Pell recipients fare.",
    },
    CAMPUS: {
        "title": "Campus life and academics",
        "intro": "Housing, athletics, course loads, grades and advising.",
    },
}

# What a chart may be narrowed by (one value each), and what it may be split
# by: every grouping with at most eight groups (the chart palette's size).
FILTER_KEYS: tuple[str, ...] = (
    "college",
    "major",
    "class_level",
    "admit_type",
    "residency",
    "first_generation",
    "pell",
    "gender",
    "race_ethnicity",
    "age_band",
    "load",
    "housing",
    "athlete",
    "honors",
)
COMPARE_KEYS: tuple[str, ...] = (
    "college",
    "class_level",
    "admit_type",
    "residency",
    "first_generation",
    "pell",
    "gender",
    "age_band",
    "load",
    "housing",
    "athlete",
    "honors",
)
SHORT_LABELS: dict[str, str] = {
    "first_generation": "First generation",
    "pell": "Pell grant",
    "race_ethnicity": "Race and ethnicity",
    "admit_type": "Entry type",
    "load": "Full or part time",
    "athlete": "Athlete",
    "honors": "Honors",
}


def key_label(key: str) -> str:
    return SHORT_LABELS.get(key, GROUPINGS[key].label)


def domain(key: str, v: Vocab) -> list[str]:
    """Every value of a grouping, in its natural order (series colours follow
    this order, so a group keeps its colour whatever else is shown)."""
    if key == "college":
        return list(v.colleges)
    if key == "major":
        return list(v.majors)
    if key == "class_level":
        return list(general.CLASS_ORDER)
    return [k for k in GROUPINGS[key].values if k != general.NOT_RECORDED]


def value_label(key: str, value: str, v: Vocab) -> str:
    return general._labels(key, value, v)


class NotApplicable(Exception):
    """A filter or split the chart's records do not carry."""


def applies(chart: Chart, key: str) -> bool:
    return (
        chart.measure.unit in GROUPINGS[key].units
        and key != chart.x
        and key != chart.about
    )


# --- computing one chart --------------------------------------------------------


class _SeriesRunner(general._Runner):
    """The general analysis's runner, reading each partial sum per group of
    attribute values instead of per student.

    Every cell a chart reads includes its x attribute (the term, or the
    entering class), and within one x value a student has exactly one value
    of every attribute (their major, load and housing that term; their
    profile; the term itself), so each student falls in exactly one group.
    Distinct students per group therefore add up across groups exactly as
    ``_Runner.cells`` counts them per student, and the same cells (and the
    same withholding) come out from far fewer rows."""

    def __init__(self, con: Any, req: general.Request, v: Vocab, x: str) -> None:
        super().__init__(con, req, v)
        self.x = x
        self._groups: dict[tuple[str, str], tuple[tuple[str, ...], list[Any]]] = {}

    def _grouped(self, window: tuple[str, str], attrs: tuple[str, ...]) -> Any:
        cached = self._groups.get(window)
        if cached is not None and set(attrs) <= set(cached[0]):
            return cached
        if cached is not None:
            attrs = tuple(sorted(set(attrs) | set(cached[0])))
        m = self.req.measure
        params = dict(self.params)
        params["term_from"], params["term_to"] = window
        cols = ", ".join(f"b.{k}" for k in attrs)
        sql = (
            general.UNIT_SQL[m.unit]
            + f"\nSELECT {cols}, COUNT(DISTINCT b.sid), {m.num}, {m.den} FROM b "
            f"WHERE {m.where} GROUP BY {cols}"
        )
        rows = [
            (tuple(str(x) for x in row[:-3]), row[-3], row[-2], row[-1])
            for row in self.con.execute(sql, params)
        ]
        self._groups[window] = (attrs, rows)
        return self._groups[window]

    def cells(
        self,
        keys: list[str],
        filters: dict[str, str],
        *,
        term_from: str | None = None,
        term_to: str | None = None,
    ) -> dict[tuple[str, ...], general.Cell]:
        if self.x not in keys:  # pragma: no cover - every chart cell has x
            return super().cells(keys, filters, term_from=term_from, term_to=term_to)
        for key in [*keys, *filters]:
            if key not in GROUPINGS:
                raise general.GeneralError(f"unknown grouping {key!r}")
        window = (
            term_from if term_from is not None else self.params["term_from"],
            term_to if term_to is not None else self.params["term_to"],
        )
        wanted = set(keys) | set(filters) | self.base_attrs
        attrs, rows = self._grouped(window, tuple(sorted(wanted)))
        pos = {a: i for i, a in enumerate(attrs)}
        key_pos = [pos[k] for k in keys]
        filter_pos = [(pos[k], val) for k, val in filters.items()]
        acc: dict[tuple[str, ...], list[float]] = {}
        for values, students, num, den in rows:
            if any(values[i] != val for i, val in filter_pos):
                continue
            cell_key = tuple(values[i] for i in key_pos)
            a = acc.get(cell_key)
            if a is None:
                acc[cell_key] = [students, float(num or 0), float(den or 0)]
            else:
                a[0] += students
                a[1] += float(num or 0)
                a[2] += float(den or 0)
        return {
            k: general.Cell(k, int(a[0]), a[1], a[2]) for k, a in sorted(acc.items())
        }


def _x_domain(chart: Chart, r: general._Runner, v: Vocab) -> list[dict[str, str]]:
    if chart.x == "term":
        return [
            {"key": t, "label": name, "year": v.term_year[t]}
            for t, name in v.terms.items()
            if v.term_season[t] != "Summer"
        ]
    falls = [t for t in v.terms if v.term_season[t] == "Fall"]
    if chart.cohorts in ("ret", "g4", "g6"):
        allowed = set(json.loads(r.params[f"{chart.cohorts}_cohorts"]))
        falls = [t for t in falls if t in allowed]
    elif chart.cohorts == "settled":
        falls = falls[:-2]
    return [
        {"key": v.term_year[t], "label": v.terms[t], "year": v.term_year[t]}
        for t in falls
    ]


def _medians(
    r: general._Runner, keys: list[str], filters: dict[str, str]
) -> dict[tuple[str, ...], float]:
    """The median per-student amount in each cell (the same rows as
    ``r.cells``; each row of the base is one student in one term)."""
    window = (r.params["term_from"], r.params["term_to"])
    wanted = tuple(sorted(set(keys) | set(filters) | r.base_attrs))
    rows = r._base(window, wanted)  # one row per student (and term)
    attrs = r._bases[window][0]
    pos = {a: i + 1 for i, a in enumerate(attrs)}
    per: dict[tuple[str, ...], dict[Any, float]] = {}
    for row in rows:
        if any(row[pos[k]] != val for k, val in filters.items()):
            continue
        cell_key = tuple(row[pos[k]] for k in keys)
        amounts = per.setdefault(cell_key, {})
        amounts[row[0]] = amounts.get(row[0], 0.0) + float(row[-2] or 0)
    return {k: statistics.median(a.values()) for k, a in per.items()}


MEDIAN_MINIMUM = 20  # students behind a published median
MEDIAN_ROUNDING = 100  # dollars


class TooManyAttributes(Exception):
    """A request that both narrows and splits, or narrows by two attributes."""


def _point(
    chart: Chart,
    cell: general.Cell | None,
    hidden: bool,
    median: float | None,
) -> dict[str, Any]:
    if cell is None:
        return {"value": None, "status": "none"}
    if hidden:
        return {"value": None, "status": "withheld"}
    m = chart.measure
    out: dict[str, Any] = {"status": "ok", "students": cell.students}
    if chart.stat == "median":
        # Rounded to the nearest $100: a median of an odd number of students
        # is one student's own balance, which is never published exactly.
        step = MEDIAN_ROUNDING
        out["value"] = int(round((median or 0.0) / step) * step)
    elif m.kind == "count":
        out["value"] = int(cell.num)
    elif m.kind == "dollars":
        out["value"] = round(cell.num, 2)
    elif not cell.den:
        return {"value": None, "status": "none"}
    elif m.kind == "pct":
        out["value"] = round(100.0 * cell.num / cell.den, 1)
        out["numerator"] = int(round(cell.num))
        out["denominator"] = int(round(cell.den))
    elif m.kind == "gpa":
        out["value"] = round(cell.num / cell.den, 2)
    else:
        out["value"] = round(cell.num / cell.den, 1)
    return out


def _small_share(cell: general.Cell) -> bool:
    """A share whose count either way is under 10 (a rate times its base
    gives the count, so the count is as public as the rate)."""
    return cell.num < MINIMUM_CELL_SIZE or cell.den - cell.num < MINIMUM_CELL_SIZE


def _complete(sizes: dict[str, float], hidden: set[str]) -> set[str]:
    """Complementary withholding for one partition with a published total:
    while the withheld groups add up to fewer than 10, withhold the next
    smallest visible group too."""
    hidden = set(hidden)
    if not hidden:
        return hidden
    visible = sorted((n, k) for k, n in sizes.items() if k not in hidden)
    while sum(sizes[k] for k in hidden) < MINIMUM_CELL_SIZE and visible:
        hidden.add(visible.pop(0)[1])
    return hidden


def _share_hidden(
    xs: list[str],
    main: dict[tuple[str, ...], general.Cell],
    hidden: set[tuple[str, ...]],
    total: dict[tuple[str, ...], general.Cell],
    total_hidden: set[tuple[str, ...]],
) -> tuple[set[tuple[str, ...]], set[tuple[str, ...]]]:
    """Withhold share points whose count either way is under 10, and then,
    within each term (or class), enough other groups that the published total
    minus the shown groups never gives a count under 10 either way."""
    hidden = set(hidden)
    total_hidden = set(total_hidden) | {k for k, c in total.items() if _small_share(c)}
    for x in xs:
        groups = {k[1]: c for k, c in main.items() if k[0] == x}
        pre = {g for g, c in groups.items() if (x, g) in hidden or _small_share(c)}
        if (x,) not in total_hidden:
            nums = {g: c.num for g, c in groups.items()}
            rest = {g: c.den - c.num for g, c in groups.items()}
            while True:
                grown = _complete(rest, _complete(nums, pre))
                if grown == pre:
                    break
                pre = grown
        hidden |= {(x, g) for g in pre}
    return hidden, total_hidden


def compute(
    con: Any, chart: Chart, filters: dict[str, str], compare: str | None, v: Vocab
) -> dict[str, Any]:
    """One chart as series of points.

    A request reads ONE attribute besides the term: it narrows to one group
    (a filter) or splits into groups (a comparison, or the chart's own
    split), never both, so every published figure is a cell of a one-way
    table (term by one attribute) whose only totals are the term totals,
    where complementary withholding is complete. A narrowed chart is the
    same table's one group, withheld exactly as in the split chart.

    Raises TooManyAttributes for more than one, and NotApplicable when the
    attribute cannot apply to the chart's records."""
    if len(filters) + (1 if compare else 0) > 1:
        raise TooManyAttributes()
    for key in filters:
        if not applies(chart, key):
            raise NotApplicable(key)
    narrowed = next(iter(filters.items()), None)
    dropped_split = (
        chart.split if narrowed is not None and chart.split != narrowed[0] else None
    )
    attr = narrowed[0] if narrowed is not None else (chart.split or compare)
    if attr is not None and not applies(chart, attr):
        raise NotApplicable(attr)
    m = chart.measure
    keys = [chart.x] + ([attr] if attr else [])
    r = _SeriesRunner(con, general.Request(m, tuple(keys), {}, None, None), v, chart.x)
    if chart.all_entrants:
        r.params["admit_default"] = 0
    main = r.cells(keys, {})
    hidden = general._hidden(r, keys, {}, main, v)
    total = r.cells([chart.x], {}) if attr else main
    total_hidden = general._hidden(r, [chart.x], {}, total, v) if attr else hidden
    xs = _x_domain(chart, r, v)
    x_keys = [x["key"] for x in xs]
    if m.kind == "pct":
        if attr:
            hidden, total_hidden = _share_hidden(
                x_keys, main, hidden, total, total_hidden
            )
        else:
            hidden = hidden | {k for k, c in main.items() if _small_share(c)}
            total_hidden = hidden
    medians: dict[tuple[str, ...], float] = {}
    total_med: dict[tuple[str, ...], float] = {}
    if chart.stat == "median":
        medians = _medians(r, keys, {})
        total_med = _medians(r, [chart.x], {}) if attr else medians
        hidden = hidden | {k for k, c in main.items() if c.students < MEDIAN_MINIMUM}
        total_hidden = total_hidden | {
            k for k, c in total.items() if c.students < MEDIAN_MINIMUM
        }

    def points(
        cells: dict[tuple[str, ...], general.Cell],
        withheld: set[Any],
        med: dict[tuple[str, ...], float],
        suffix: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        out = []
        for x in xs:
            key = (x["key"], *suffix)
            point = _point(chart, cells.get(key), key in withheld, med.get(key))
            out.append({"x": x["key"], **point})
        return out

    series: list[dict[str, Any]] = []
    notes: list[str] = []
    split: str | None = None
    if attr is None:
        label = "New students" if chart.all_entrants else "All students"
        series.append(
            {
                "key": "all",
                "label": label,
                "slot": 0,
                "points": points(main, hidden, medians, ()),
            }
        )
    elif narrowed is not None:
        key, value = narrowed
        series.append(
            {
                "key": value,
                "label": value_label(key, value, v),
                "slot": 0,
                "points": points(main, hidden, medians, (value,)),
            }
        )
        if dropped_split is not None:
            notes.append(
                f"Narrowed to one group, this chart is not split by "
                f"{key_label(dropped_split).lower()}: a chart narrows or splits, "
                "never both, so no group under 10 students can be worked out."
            )
    else:
        split = attr
        empty: list[str] = []
        withheld_all: list[str] = []
        for slot, value in enumerate(domain(attr, v)):
            pts = points(main, hidden, medians, (value,))
            name = value_label(attr, value, v)
            if not any(p["status"] == "ok" for p in pts):
                if any(p["status"] == "withheld" for p in pts):
                    withheld_all.append(name)
                else:
                    empty.append(name)
                continue
            series.append({"key": value, "label": name, "slot": slot, "points": pts})
        if withheld_all:
            notes.append(
                "Withheld in every year shown (fewer than 10 students, or it could "
                "reveal a group that small): " + ", ".join(withheld_all) + "."
            )
        if empty:
            notes.append("No students: " + ", ".join(empty) + ".")
        # Rates and averages also show everyone, for reference; counts do not
        # (the parts add up to it).
        if m.kind not in ("count", "dollars"):
            series.append(
                {
                    "key": "all",
                    "label": "All students",
                    "slot": None,
                    "points": points(total, total_hidden, total_med, ()),
                }
            )
    if m.unit == "cohort" and not chart.all_entrants and attr != "admit_type":
        notes.append("Entering students are first-time students.")
    if m.kind == "pct":
        notes.append(
            "A rate is also withheld when fewer than 10 are counted either way "
            "(for example fewer than 10 who returned, or fewer than 10 who did "
            "not)."
        )
    if chart.stat == "median":
        notes.append(
            f"A median needs at least {MEDIAN_MINIMUM} students with a hold, and "
            f"is rounded to the nearest ${MEDIAN_ROUNDING}."
        )
    if chart.partial_last and xs:
        notes.append(
            f"{xs[-1]['label']} is the latest term in the records and is not "
            "complete: holds placed later in that term are not in them yet."
        )
    if chart.note and xs:
        first, last = xs[0]["label"], xs[-1]["label"]
        span = first if first == last else f"{first} to {last}"
        notes.append(chart.note.format(first=first, last=last, span=span))
    return {
        "chart": chart.id,
        "title": chart.title,
        "form": chart.form,
        "kind": m.kind,
        "value_label": (
            "Median balance ($)" if chart.stat == "median" else m.value_label
        ),
        "definition": m.definition,
        "x_label": "Term" if chart.x == "term" else "Entering class",
        "x": xs,
        "split": (
            {"key": split, "label": key_label(split)} if split is not None else None
        ),
        "series": series,
        "notes": notes,
        "minimum_cell_size": MINIMUM_CELL_SIZE,
    }


def fields_read(chart: Chart, filters: dict[str, str], split: str | None) -> list[str]:
    m = chart.measure
    out = list(general._MEASURE_FIELDS.get(m.id, _EXTRA_FIELDS.get(m.id, ())))
    for key in [chart.x, *([split] if split else []), *filters]:
        out.extend(general._GROUPING_FIELDS.get(key, ()))
    out.extend(general._UNIT_FIELDS.get(m.unit, ()))
    return list(dict.fromkeys(out))


# --- cache ---------------------------------------------------------------------------

_CACHE: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()
_CACHE_SIZE = 512
_CACHE_LOCK = threading.Lock()
_INFLIGHT: dict[tuple[Any, ...], threading.Event] = {}


def _db_key() -> tuple[str, int, int]:
    db = school_db_path().expanduser()
    try:
        stat = db.stat()
    except OSError:
        raise SchoolDataMissing() from None
    return (str(db.resolve()), stat.st_size, stat.st_mtime_ns)


def cached_series(
    chart: Chart, filters: dict[str, str], compare: str | None
) -> dict[str, Any]:
    """``compute`` once per database, chart, filters and split; identical
    requests in flight wait for the first instead of computing again."""
    split = chart.split if chart.split is not None else compare
    key = (_db_key(), chart.id, split, tuple(sorted(filters.items())))
    while True:
        with _CACHE_LOCK:
            hit = _CACHE.get(key)
            if hit is not None:
                _CACHE.move_to_end(key)
                return hit
            waiting = _INFLIGHT.get(key)
            if waiting is None:
                done = threading.Event()
                _INFLIGHT[key] = done
                break
        waiting.wait(timeout=30)
    try:
        con = connect_readonly()
        try:
            v = catalog_for(con).vocab
            result = compute(con, chart, filters, compare, v)
        finally:
            con.close()
        with _CACHE_LOCK:
            _CACHE[key] = result
            while len(_CACHE) > _CACHE_SIZE:
                _CACHE.popitem(last=False)
        return result
    finally:
        with _CACHE_LOCK:
            _INFLIGHT.pop(key, None)
        done.set()


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
        _WARMED.clear()


_WARMED: set[tuple[Any, ...]] = set()


def warm(boards: tuple[str, ...]) -> None:
    """Compute the unfiltered charts of these dashboards in the background
    (once per database), so the first view of the page is quick. A chart a
    person asks for meanwhile waits for this computation, never repeats it."""
    try:
        key = (_db_key(), boards)
    except SchoolDataMissing:
        return
    with _CACHE_LOCK:
        if key in _WARMED:
            return
        _WARMED.add(key)
    charts = [c for c in CHARTS if c.dashboard in boards]

    def run() -> None:
        for chart in charts:
            try:
                cached_series(chart, {}, None)
            except Exception:  # noqa: BLE001 - warming is best effort
                return

    threading.Thread(target=run, name="data-page-warm", daemon=True).start()


# --- routes -------------------------------------------------------------------------


def _missing() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"available": False, "message": SchoolDataMissing().args[0]},
    )


def _filter_options(key: str, v: Vocab) -> list[dict[str, str]]:
    values = domain(key, v)
    options = [{"value": x, "label": value_label(key, x, v)} for x in values]
    if key in ("major", "college"):
        options.sort(key=lambda o: o["label"])
    return options


@router.get("/data/dashboards")
def get_dashboards(request: Request) -> JSONResponse:
    role = str(request.scope["cabinet_user"]["role"])
    try:
        con = connect_readonly()
    except SchoolDataMissing:
        return _missing()
    try:
        v = catalog_for(con).vocab
    finally:
        con.close()
    warm(dashboards_for(role))
    boards = [
        {
            "id": board,
            **DASHBOARDS[board],
            "charts": [
                {"id": c.id, "title": c.title, "form": c.form}
                for c in CHARTS
                if c.dashboard == board
            ],
        }
        for board in dashboards_for(role)
    ]
    return JSONResponse(
        content={
            "institution": v.institution,
            "fictional": True,
            "dashboards": boards,
            "filters": [
                {"key": k, "label": key_label(k), "options": _filter_options(k, v)}
                for k in FILTER_KEYS
                if attribute_allowed(role, k)
            ],
            "compare": [
                {"key": k, "label": key_label(k)}
                for k in COMPARE_KEYS
                if attribute_allowed(role, k)
            ],
            "years": list(v.academic_years),
            "minimum_cell_size": MINIMUM_CELL_SIZE,
        }
    )


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": message})


@router.get("/data/series")
async def get_series(request: Request) -> JSONResponse:
    return await run_in_threadpool(_series, request)


def _series(request: Request) -> JSONResponse:
    user = request.scope["cabinet_user"]
    role = str(user["role"])
    params = request.query_params
    chart_id = params.get("chart", "")
    store = request.app.state.auth

    def refuse(status: int, message: str, category: str) -> JSONResponse:
        """A refused chart request: logged (never a value), then answered."""
        store.audit_for(int(user["institution_id"])).append(
            "data.refused",
            actor=str(user["email"]),
            payload={
                "route": "/data/series",
                "chart": chart_id[:64],
                "category": category,
                "reason": message,
                "role": role,
            },
        )
        return _error(status, message)

    chart = CHARTS_BY_ID.get(chart_id)
    if chart is None:
        return refuse(404, "There is no such chart.", "unknown_chart")
    if chart.dashboard not in dashboards_for(role):
        return refuse(
            403, "This chart is not on a dashboard for your role.", "role_dashboard"
        )
    keys = [k for k, _ in params.multi_items()]
    if len(keys) != len(set(keys)):
        return refuse(422, "Each choice may be given once.", "invalid_request")
    allowed = set(FILTER_KEYS) | {"chart", "compare"}
    unknown = [k for k in keys if k not in allowed]
    if unknown:
        return refuse(
            422,
            "Unknown filter: " + ", ".join(sorted(unknown)) + ".",
            "invalid_request",
        )
    compare = params.get("compare") or None
    if compare is not None and compare not in COMPARE_KEYS:
        return refuse(422, "That comparison is not available.", "invalid_request")
    try:
        con = connect_readonly()
    except SchoolDataMissing:
        return _missing()
    try:
        v = catalog_for(con).vocab
    finally:
        con.close()
    filters: dict[str, str] = {}
    for key in FILTER_KEYS:
        value = params.get(key)
        if not value:
            continue
        if value not in domain(key, v):
            return refuse(
                422,
                f"That {key_label(key).lower()} is not available.",
                "invalid_request",
            )
        filters[key] = value
    for key in [*filters, *([compare] if compare else [])]:
        if not attribute_allowed(role, key):
            return refuse(
                403,
                f"Your role may not narrow or compare by {key_label(key).lower()}.",
                "role_attribute",
            )
    if len(filters) + (1 if compare else 0) > 1:
        return refuse(
            422,
            "A chart narrows to one group or compares groups, not both at once.",
            "one_attribute",
        )
    try:
        result = cached_series(chart, filters, compare)
    except SchoolDataMissing:
        return _missing()
    except NotApplicable as exc:
        key = str(exc.args[0])
        store.audit_for(int(user["institution_id"])).append(
            "data.refused",
            actor=str(user["email"]),
            payload={
                "route": "/data/series",
                "chart": chart.id,
                "category": "not_applicable",
                "reason": f"The chart cannot be narrowed or split by {key}.",
                "role": role,
            },
        )
        return JSONResponse(
            content={
                "chart": chart.id,
                "title": chart.title,
                "not_applicable": {"key": key, "label": key_label(key)},
            }
        )
    split = result["split"]["key"] if result["split"] else None
    store.audit_for(int(user["institution_id"])).append(
        "data.granted",
        actor=str(user["email"]),
        payload={
            "route": "/data/series",
            "analysis_id": "data_page",
            "dashboard": chart.dashboard,
            "chart": chart.id,
            "filters": filters,
            "compare": split,
            "fields_read": fields_read(chart, filters, split),
            "aggregate_only": True,
        },
    )
    return JSONResponse(content=result)
