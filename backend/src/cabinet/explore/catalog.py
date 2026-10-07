"""The analysis catalog: reviewed, parameterized analyses over the school database.

Every answer Explore gives comes from one of the analyses below. Each one has
an id, a plain title and description, typed parameters whose allowed values
are enumerated from the data (majors, colleges, course ids, subjects, terms,
instructors) or fixed lists, the fields it reads (named in the audit event),
its result columns with plain labels, and the code that computes it. The SQL
is parameterized and never built from user text: user text only ever chooses
an analysis and values from these lists.

Privacy rules applied here, in code:

- Results are aggregates only. No query selects a student id into a result,
  and ``execute`` scans every result for ``S-`` ids and refuses to return one.
- Any group statistic over fewer than ``MINIMUM_CELL_SIZE`` students is
  withheld (the cell reads "fewer than 10"), and such a group is left out of
  every ranking.
- Instructor-level analyses are marked ``instructor_level``; ``execute``
  gives their rows only to the executive and admin roles. Every instructor
  name in the school database is fictional and is labelled so.

The definitions match data/school/VERIFY.md (graded registration, D, F or
withdrawal rate, students in a major, continuing student, headcount,
withdrawal rate), and rates are rounded exactly as ``data/school/check.py``
rounds them, so the planted facts reproduce to the digit.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cabinet.counseling import MINIMUM_CELL_SIZE, SUPPRESSED_DISPLAY
from cabinet.explore import general as _general

REPO_ROOT = Path(__file__).resolve().parents[4]
ENV_SCHOOL_DB = "CABINET_SCHOOL_DB"
DEFAULT_SCHOOL_DB = REPO_ROOT / "var" / "school" / "school.db"

NOT_INSTALLED_MESSAGE = (
    "The demonstration university data is not installed. Run make school-data."
)

# The VERIFY.md definitions, as SQL fragments (constants, never user text).
DFW_SQL = "('D+','D','F','W')"
GRADED_SQL = "('A','A-','B+','B','B-','C+','C','C-','D+','D','F','W')"

# The latest student_term_records row per student (VERIFY.md "Students in a
# major"): the program and cumulative GPA a student is counted under.
LATEST_RECORD_CTE = """
WITH last AS (
    SELECT r.student_id, r.program_code, r.cumulative_gpa
    FROM student_term_records r
    WHERE r.term_code = (SELECT MAX(term_code) FROM student_term_records r2
                         WHERE r2.student_id = r.student_id)
      AND r.cumulative_gpa IS NOT NULL
)
"""

INSTRUCTOR_ROLES = ("executive", "admin")


class SchoolDataMissing(Exception):
    """The school database is not installed (or is not a school database)."""

    def __init__(self) -> None:
        super().__init__(NOT_INSTALLED_MESSAGE)


class AnalysisError(Exception):
    """A plan step cannot run (a missing or out-of-range parameter)."""


def pct(n: int, d: int) -> float:
    """A rate in percent, rounded to one decimal exactly as check.py does."""
    return round(100.0 * n / d, 1) if d else 0.0


def school_db_path() -> Path:
    """CABINET_SCHOOL_DB, else var/school/school.db."""
    override = os.environ.get(ENV_SCHOOL_DB, "").strip()
    return Path(override) if override else DEFAULT_SCHOOL_DB


def connect_readonly(path: Path | None = None) -> sqlite3.Connection:
    """A read-only connection to the school database, or SchoolDataMissing."""
    db = (path or school_db_path()).expanduser()
    if not db.is_file():
        raise SchoolDataMissing()
    try:
        con = sqlite3.connect(
            db.resolve().as_uri() + "?mode=ro", uri=True, check_same_thread=False
        )
        con.execute("PRAGMA query_only = ON")
        row = con.execute("SELECT value FROM meta WHERE key = 'institution'").fetchone()
    except sqlite3.Error:
        raise SchoolDataMissing() from None
    if row is None:
        con.close()
        raise SchoolDataMissing()
    return con


# --- vocabulary (allowed values, enumerated from the data) -------------------


@dataclass(frozen=True)
class Vocab:
    """Every value a parameter may take, read from the database once."""

    majors: dict[str, str]  # major code -> name
    major_program: dict[str, str]  # major code -> program code
    major_college: dict[str, str]  # major code -> college code
    colleges: dict[str, str]  # college code -> name
    subjects: dict[str, str]  # subject code -> name
    courses: dict[str, str]  # course id -> title
    terms: dict[str, str]  # term code -> name, in time order
    term_season: dict[str, str]  # term code -> Fall | Spring | Summer
    term_year: dict[str, str]  # term code -> academic year
    academic_years: tuple[str, ...]
    instructors: dict[str, str]  # instructor id -> "First Last" (fictional)
    hold_categories: tuple[str, ...]
    institution: str
    entry_cohorts: tuple[str, ...] = ()  # academic years of entry, oldest first

    def course_label(self, course: str) -> str:
        return f"{course} {self.courses.get(course, '')}".strip()

    def instructor_label(self, instructor: str) -> str:
        return f"{instructor} {self.instructors.get(instructor, '')} (fictional)"


def load_vocab(con: sqlite3.Connection) -> Vocab:
    def pairs(sql: str) -> dict[str, str]:
        return {str(a): str(b) for a, b in con.execute(sql)}

    terms_rows = con.execute(
        "SELECT term_code, name, season, academic_year FROM academic_periods ORDER "
        "BY sequence"
    ).fetchall()
    return Vocab(
        majors=pairs(
            "SELECT major_code, name FROM academic_programs ORDER BY major_code"
        ),
        major_program=pairs(
            "SELECT major_code, program_code FROM academic_programs ORDER BY major_code"
        ),
        major_college=pairs(
            "SELECT major_code, college_code FROM academic_programs ORDER BY major_code"
        ),
        colleges=pairs("SELECT college_code, name FROM colleges ORDER BY college_code"),
        subjects=pairs("SELECT subject_code, name FROM subjects ORDER BY subject_code"),
        courses=pairs("SELECT course_id, title FROM courses ORDER BY course_id"),
        terms={str(r[0]): str(r[1]) for r in terms_rows},
        term_season={str(r[0]): str(r[2]) for r in terms_rows},
        term_year={str(r[0]): str(r[3]) for r in terms_rows},
        academic_years=tuple(dict.fromkeys(str(r[3]) for r in terms_rows)),
        instructors=pairs(
            "SELECT instructor_id, first_name || ' ' || last_name FROM instructors "
            "ORDER BY instructor_id"
        ),
        hold_categories=tuple(
            str(r[0])
            for r in con.execute(
                "SELECT DISTINCT category FROM person_holds ORDER BY category"
            )
        ),
        institution=str(
            con.execute("SELECT value FROM meta WHERE key = 'institution'").fetchone()[
                0
            ]
        ),
        entry_cohorts=tuple(
            str(r[0])
            for r in con.execute(
                "SELECT DISTINCT (CAST(substr(entry_term, 1, 4) AS INTEGER) - 1) "
                "|| '-' "
                "|| substr(entry_term, 1, 4) FROM students ORDER BY 1"
            )
        ),
    )


# --- catalog structure --------------------------------------------------------


@dataclass(frozen=True)
class Param:
    """One typed parameter. ``kind`` names where its allowed values come from
    (the data for major, college, course, subject, term, instructor, ...;
    ``choices`` for a fixed list)."""

    name: str
    label: str
    kind: str
    required: bool = False
    default: Any = None
    choices: tuple[Any, ...] = ()
    choice_labels: dict[Any, str] = field(default_factory=dict)
    # How "How this was answered" shows the parameter to a reader: a template
    # with ``{}`` for the plain value (default "<label>: {}"), or None to leave
    # it out (the number of rows shown says nothing about the answer).
    shown: str | None = "{label}: {}"
    # Reader words for a value, where the record's words read badly on screen.
    shown_labels: dict[Any, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Column:
    """One result column. ``kind`` is text, count, gpa, pct, points, money,
    hours, or average. ``entity`` names the parameter kind a later step may
    take from this column (major, college, course, term, instructor)."""

    key: str
    label: str
    kind: str = "text"
    entity: str | None = None


@dataclass
class Result:
    rows: list[dict[str, Any]]
    notes: list[str] = field(default_factory=list)
    # The columns of this result when they depend on the parameters (the
    # general analysis); None means the analysis's own columns.
    columns: tuple[Column, ...] | None = None
    # Withheld cells (row index, column key) that are themselves under 10
    # students. A withheld cell not listed protects a neighbour and may be
    # large, so an answer never calls it "fewer than 10".
    small: set[tuple[int, str]] = field(default_factory=set)


Runner = Callable[[sqlite3.Connection, dict[str, Any], Vocab], Result]


@dataclass(frozen=True)
class Analysis:
    id: str
    title: str
    description: str
    params: tuple[Param, ...]
    fields_read: tuple[str, ...]
    columns: tuple[Column, ...]
    run: Runner
    instructor_level: bool = False

    def param(self, name: str) -> Param | None:
        return next((p for p in self.params if p.name == name), None)

    def column(self, key: str) -> Column | None:
        return next((c for c in self.columns if c.key == key), None)


# --- shared helpers -------------------------------------------------------------


def _suppressed(students: int) -> bool:
    return students < MINIMUM_CELL_SIZE


def _withheld_note(count: int, noun: str = "group") -> list[str]:
    if count <= 0:
        return []
    plural = noun if count == 1 else noun + "s"
    return [
        f"{count} {plural} with {SUPPRESSED_DISPLAY} students "
        f"{'is' if count == 1 else 'are'} withheld and not ranked."
    ]


_STAT_KEYS = ("graded", "dfw", "dfw_rate")
PROTECT_NOTE = (
    f"to protect groups of {SUPPRESSED_DISPLAY} students (with one more where "
    "needed, so a withheld figure cannot be worked out from a total)."
)


def _complementary(sizes: list[int]) -> set[int]:
    """The rows whose statistics are withheld: every group under the minimum,
    then, while the withheld groups add up to fewer than the minimum, the next
    smallest other row too. Otherwise the withheld rows could be recovered by
    subtracting the visible rows from a total shown elsewhere (a course's
    all-terms row, a group's whole)."""
    return set(_general.complementary(dict(enumerate(sizes))))


def _within_college(sizes: dict[str, int], v: Vocab) -> set[str]:
    """Majors withheld when each college's majors are one partition (the
    college total is published): ``complementary`` per college."""
    by_college: dict[str, dict[str, int]] = {}
    for major, n in sizes.items():
        by_college.setdefault(v.major_college.get(major, ""), {})[major] = n
    hidden: set[str] = set()
    for part in by_college.values():
        hidden |= _general.complementary(part)
    return hidden


def _term_partition_hidden(
    con: sqlite3.Connection, sql: str, args: tuple[Any, ...]
) -> dict[str, set[str]]:
    """key -> its withheld terms, from ``sql`` returning (key, term,
    students) over every term: a key's terms are one partition (its all-terms
    figure is published)."""
    parts: dict[str, dict[str, int]] = {}
    for key, term, n in con.execute(sql, args):
        parts.setdefault(str(key), {})[str(term)] = int(n)
    return {k: _general.complementary(sizes) for k, sizes in parts.items()}


def _major_term_hidden(con: sqlite3.Connection, v: Vocab) -> set[tuple[str, str]]:
    """(major, term) cells of enrolled headcount that are withheld: in each
    term a college's majors are one partition, and across terms a major's
    terms are another (the college's term total and the major's all-terms
    figures are both published)."""
    counts: dict[tuple[str, str], int] = {}
    for major, term, n in con.execute(
        """SELECT ap.major_code, r.term_code, COUNT(*)
           FROM student_term_records r JOIN academic_programs ap USING (program_code)
           GROUP BY 1, 2"""
    ):
        counts[(str(major), str(term))] = int(n)
    hidden: set[tuple[str, str]] = set()
    by_term: dict[str, dict[str, int]] = {}
    by_major: dict[str, dict[str, int]] = {}
    for (major, term), n in counts.items():
        by_term.setdefault(term, {})[major] = n
        by_major.setdefault(major, {})[term] = n
    for term, sizes in by_term.items():
        hidden |= {(m, term) for m in _within_college(sizes, v)}
    for major, sizes in by_major.items():
        hidden |= {(major, t) for t in _general.complementary(sizes)}
    return hidden


def _college_term_hidden(con: sqlite3.Connection) -> set[tuple[str, str]]:
    """(college, term) cells withheld: in each term the colleges are one
    partition of the university's total."""
    by_term: dict[str, dict[str, int]] = {}
    for college, term, n in con.execute(
        """SELECT ap.college_code, r.term_code, COUNT(*)
           FROM student_term_records r JOIN academic_programs ap USING (program_code)
           GROUP BY 1, 2"""
    ):
        by_term.setdefault(str(term), {})[str(college)] = int(n)
    return {
        (c, term)
        for term, sizes in by_term.items()
        for c in _general.complementary(sizes)
    }


def _withhold(row: dict[str, Any], keys: tuple[str, ...] = _STAT_KEYS) -> None:
    for key in keys:
        row[key] = SUPPRESSED_DISPLAY


def _filters(clauses: list[tuple[str, Any]]) -> tuple[str, tuple[Any, ...]]:
    """AND-joined constant SQL fragments with their bound values."""
    sql = "".join(f" AND {fragment}" for fragment, _ in clauses)
    return sql, tuple(value for _, value in clauses)


def _top(rows: list[dict[str, Any]], top: Any) -> list[dict[str, Any]]:
    return rows[: int(top)] if top else rows


ORDER_LOW_HIGH = ("lowest_first", "highest_first")
ORDER_LABELS = {"lowest_first": "lowest first", "highest_first": "highest first"}
TOP_CHOICES = (5, 10, 20, 50)
SEASONS = ("Fall", "Spring", "Summer")
GROUPS = ("first_generation", "pell", "residency", "entry_cohort", "entry_type")
GROUP_LABELS = {
    "first_generation": "first-generation status",
    "pell": "Pell status",
    "residency": "residency",
    "entry_cohort": "entry cohort (academic year of entry)",
    "entry_type": "entry type (first-time or transfer)",
}


def _order_param(default: str = "lowest_first") -> Param:
    return Param(
        "order",
        "Order",
        "choice",
        default=default,
        choices=ORDER_LOW_HIGH,
        choice_labels=ORDER_LABELS,
        shown="Ranked: {}",
    )


def _top_param(default: int | None = 10) -> Param:
    return Param(
        "top", "Rows shown", "choice", default=default, choices=TOP_CHOICES, shown=None
    )


# --- 1. average GPA by major ----------------------------------------------------


def _gpa_by_major(con: sqlite3.Connection, p: dict[str, Any], v: Vocab) -> Result:
    where, args = _filters(
        [
            (f, val)
            for f, val in (
                ("ap.college_code = ?", p.get("college")),
                ("ap.major_code = ?", p.get("major")),
            )
            if val
        ]
    )
    rows = con.execute(
        LATEST_RECORD_CTE
        + f"""
        SELECT ap.major_code, ap.name, ap.college_code, COUNT(*), AVG(cumulative_gpa)
        FROM last JOIN academic_programs ap USING (program_code)
        WHERE 1 = 1 {where}
        GROUP BY ap.major_code""",
        args,
    ).fetchall()
    minimum = int(p.get("min_students") or 20)
    sizes = {
        str(code): int(n)
        for code, n in con.execute(
            LATEST_RECORD_CTE
            + """
        SELECT ap.major_code, COUNT(*) FROM last
        JOIN academic_programs ap USING (program_code) GROUP BY 1"""
        )
    }
    hidden = _within_college(sizes, v)
    kept, withheld, small = [], 0, 0
    for code, name, college, n, avg in rows:
        if _suppressed(n) or code in hidden:
            withheld += 1
            continue
        if n < minimum and not p.get("major"):
            small += 1
            continue
        kept.append(
            {
                "major": code,
                "major_name": name,
                "college": v.colleges[college],
                "students": n,
                "avg_gpa": round(avg, 3),
                "_sort": avg,
            }
        )
    descending = p.get("order") == "highest_first"
    kept.sort(key=lambda r: (-r["_sort"] if descending else r["_sort"], r["major"]))
    notes = _withheld_note(withheld, "major")
    if small:
        notes.append(
            f"Only majors with at least {minimum} students are ranked "
            f"({small} smaller {'major is' if small == 1 else 'majors are'} left out)."
        )
    return Result(_top(kept, p.get("top")), notes)


# --- 2. average GPA by college --------------------------------------------------


def _gpa_by_college(con: sqlite3.Connection, p: dict[str, Any], v: Vocab) -> Result:
    rows = con.execute(
        LATEST_RECORD_CTE
        + """
        SELECT ap.college_code, COUNT(*), AVG(cumulative_gpa)
        FROM last JOIN academic_programs ap USING (program_code)
        GROUP BY ap.college_code"""
    ).fetchall()
    # A college holding a major under the minimum is withheld too, so the
    # small major cannot be recovered from the college and its other majors.
    small_major_colleges = {
        str(r[0])
        for r in con.execute(
            LATEST_RECORD_CTE
            + """
        SELECT ap.college_code FROM last JOIN academic_programs ap USING (program_code)
        GROUP BY ap.major_code HAVING COUNT(*) < ?""",
            (MINIMUM_CELL_SIZE,),
        )
    }
    kept, withheld = [], 0
    for college, n, avg in rows:
        if p.get("college") and college != p["college"]:
            continue
        if _suppressed(n) or college in small_major_colleges:
            withheld += 1
            continue
        kept.append(
            {
                "college": college,
                "college_name": v.colleges[college],
                "students": n,
                "avg_gpa": round(avg, 3),
                "_sort": avg,
            }
        )
    descending = p.get("order") == "highest_first"
    kept.sort(key=lambda r: (-r["_sort"] if descending else r["_sort"], r["college"]))
    return Result(kept, _withheld_note(withheld, "college"))


# --- 3. D, F or withdrawal rate by course ---------------------------------------


def _dfw_by_course(con: sqlite3.Connection, p: dict[str, Any], v: Vocab) -> Result:
    clauses: list[tuple[str, Any]] = []
    if p.get("major_required"):
        clauses.append(
            (
                "s.course_id IN (SELECT pr.course_id FROM program_requirements pr "
                "WHERE pr.program_code = ?)",
                v.major_program[p["major_required"]],
            )
        )
    if p.get("subject"):
        clauses.append(("c.subject_code = ?", p["subject"]))
    if p.get("level"):
        clauses.append(("c.course_level = ?", int(p["level"])))
    if p.get("term_from"):
        clauses.append(("s.term_code >= ?", p["term_from"]))
    if p.get("term_to"):
        clauses.append(("s.term_code <= ?", p["term_to"]))
    where, args = _filters(clauses)
    min_sections = int(p.get("min_sections") or 1)
    min_terms = int(p.get("min_terms") or 1)
    rows = con.execute(
        f"""
        SELECT s.course_id, c.title, COUNT(DISTINCT s.section_id),
               COUNT(DISTINCT s.term_code),
               SUM(g.grade IN {DFW_SQL}), COUNT(*), COUNT(DISTINCT r.student_id)
        FROM sections s JOIN courses c ON c.course_id = s.course_id
        JOIN section_registrations r ON r.section_id = s.section_id
        JOIN final_grades g ON g.registration_id = r.registration_id
        WHERE g.grade IN {GRADED_SQL} AND c.grade_mode = 'standard' {where}
        GROUP BY s.course_id
        HAVING COUNT(DISTINCT s.section_id) >= ?
               AND COUNT(DISTINCT s.term_code) >= ?""",
        (*args, min_sections, min_terms),
    ).fetchall()
    # A term window other than all terms: a course is shown only when none of
    # its terms had 1 to 9 students, so two windows cannot be subtracted to
    # isolate a small term.
    windowed = bool(p.get("term_from") or p.get("term_to")) and (
        (p.get("term_from") or min(v.terms)) != min(v.terms)
        or (p.get("term_to") or max(v.terms)) != max(v.terms)
    )
    small_terms: set[str] = set()
    if windowed:
        small_terms = {
            str(r[0])
            for r in con.execute(
                f"""
            SELECT s.course_id FROM sections s
            JOIN courses c ON c.course_id = s.course_id
            JOIN section_registrations r ON r.section_id = s.section_id
            JOIN final_grades g ON g.registration_id = r.registration_id
            WHERE g.grade IN {GRADED_SQL} AND c.grade_mode = 'standard'
            GROUP BY s.course_id, s.term_code
            HAVING COUNT(DISTINCT r.student_id) < ?""",
                (MINIMUM_CELL_SIZE,),
            )
        }
    kept, withheld = [], 0
    for course, title, sections, terms, dfw, graded, students in rows:
        if _suppressed(students) or course in small_terms:
            withheld += 1
            continue
        kept.append(
            {
                "course": course,
                "title": title,
                "sections": sections,
                "terms": terms,
                "graded": graded,
                "dfw": int(dfw),
                "dfw_rate": pct(int(dfw), graded),
                "_sort": int(dfw) / graded,
            }
        )
    descending = p.get("order", "highest_first") == "highest_first"
    kept.sort(key=lambda r: (-r["_sort"] if descending else r["_sort"], r["course"]))
    notes = _withheld_note(withheld, "course")
    if windowed and small_terms:
        notes.append(
            "With a term range, a course is shown only when every term it was taught "
            f"had at least {MINIMUM_CELL_SIZE} students, so two ranges cannot be "
            "subtracted to reveal a small class."
        )
    if min_sections > 1 or min_terms > 1:
        notes.append(
            f"Only courses with at least {min_sections} sections in at least "
            f"{min_terms} terms are ranked."
        )
    return Result(_top(kept, p.get("top")), notes)


# --- 4. a course's DFW trend by term ---------------------------------------------


def _course_dfw_trend(con: sqlite3.Connection, p: dict[str, Any], v: Vocab) -> Result:
    course = p["course"]
    rows = con.execute(
        f"""
        SELECT s.term_code, COUNT(DISTINCT s.section_id), SUM(g.grade IN {DFW_SQL}),
               COUNT(*),
               COUNT(DISTINCT r.student_id)
        FROM sections s JOIN courses c ON c.course_id = s.course_id
        JOIN section_registrations r ON r.section_id = s.section_id
        JOIN final_grades g ON g.registration_id = r.registration_id
        WHERE s.course_id = ? AND g.grade IN {GRADED_SQL} AND c.grade_mode = 'standard'
        GROUP BY s.term_code ORDER BY s.term_code""",
        (course,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    hidden = _complementary([int(r[4]) for r in rows])
    withheld = len(hidden)
    for index, (term, sections, dfw, graded, _students) in enumerate(rows):
        row: dict[str, Any] = {
            "course": course,
            "term": term,
            "term_name": v.terms[term],
            "sections": sections,
            "graded": graded,
            "dfw": int(dfw),
            "dfw_rate": pct(int(dfw), graded),
        }
        if index in hidden:
            _withhold(row)
        out.append(row)
    total = con.execute(
        f"""
        SELECT COUNT(DISTINCT s.section_id), COUNT(DISTINCT s.term_code),
               COALESCE(SUM(g.grade IN {DFW_SQL}), 0), COUNT(*),
                      COUNT(DISTINCT r.student_id)
        FROM sections s JOIN courses c ON c.course_id = s.course_id
        JOIN section_registrations r ON r.section_id = s.section_id
        JOIN final_grades g ON g.registration_id = r.registration_id
        WHERE s.course_id = ? AND g.grade IN {GRADED_SQL}
               AND c.grade_mode = 'standard'""",
        (course,),
    ).fetchone()
    if total[3]:
        all_row: dict[str, Any] = {
            "course": course,
            "term": "All terms",
            "term_name": "All terms",
            "sections": total[0],
        }
        if _suppressed(total[4]):
            all_row.update(
                graded=SUPPRESSED_DISPLAY,
                dfw=SUPPRESSED_DISPLAY,
                dfw_rate=SUPPRESSED_DISPLAY,
            )
        else:
            all_row.update(
                graded=total[3],
                dfw=int(total[2]),
                dfw_rate=pct(int(total[2]), total[3]),
            )
        out.append(all_row)
    notes = []
    if withheld:
        notes.append(
            f"Rates are withheld for {withheld} "
            f"{'term' if withheld == 1 else 'terms'} " + PROTECT_NOTE
        )
    return Result(out, notes)


# --- 5. instructors who taught a course ------------------------------------------


def _course_instructors(con: sqlite3.Connection, p: dict[str, Any], v: Vocab) -> Result:
    course = p["course"]
    teaching = con.execute(
        """
        SELECT si.instructor_id, i.academic_rank, COUNT(DISTINCT s.section_id),
               COUNT(DISTINCT s.term_code), MIN(s.term_code), MAX(s.term_code)
        FROM sections s JOIN section_instructors si ON si.section_id = s.section_id
        JOIN instructors i ON i.instructor_id = si.instructor_id
        WHERE s.course_id = ?
        GROUP BY si.instructor_id""",
        (course,),
    ).fetchall()
    grades = _instructor_stats(con, course)
    out: list[dict[str, Any]] = []
    withheld = 0
    for inst, rank, sections, terms, first, last in teaching:
        row: dict[str, Any] = {
            "instructor": inst,
            "name": f"{v.instructors[inst]} (fictional)",
            "rank": rank,
            "sections": sections,
            "terms": terms,
            "first_term": v.terms[first],
            "last_term": v.terms[last],
        }
        dfw, graded, hide = grades.get(inst, (0, 0, True))
        row.update(graded=graded, dfw=dfw, dfw_rate=pct(dfw, graded))
        if hide:
            withheld += 1
            _withhold(row)
        out.append(row)
    out.sort(key=lambda r: (-r["sections"], r["instructor"]))
    notes = ["Instructor names are fictional."]
    if withheld:
        notes.append(
            f"D, F or withdrawal rates are withheld for {withheld} "
            f"{'instructor' if withheld == 1 else 'instructors'} " + PROTECT_NOTE
        )
    return Result(out, notes)


def _instructor_stats(
    con: sqlite3.Connection, course: str
) -> dict[str, tuple[int, int, bool]]:
    """instructor -> (dfw, graded, withheld) for one course, with the
    complementary rule applied in instructor-id order. Both instructor
    analyses use it, so a row withheld in one is never shown in the other."""
    rows = con.execute(
        f"""
        SELECT si.instructor_id, SUM(g.grade IN {DFW_SQL}), COUNT(*),
               COUNT(DISTINCT r.student_id)
        FROM sections s JOIN courses c ON c.course_id = s.course_id
        JOIN section_instructors si ON si.section_id = s.section_id
        JOIN section_registrations r ON r.section_id = s.section_id
        JOIN final_grades g ON g.registration_id = r.registration_id
        WHERE s.course_id = ? AND g.grade IN {GRADED_SQL} AND c.grade_mode = 'standard'
        GROUP BY si.instructor_id ORDER BY si.instructor_id""",
        (course,),
    ).fetchall()
    hidden = _complementary([int(r[3]) for r in rows])
    return {str(r[0]): (int(r[1]), int(r[2]), i in hidden) for i, r in enumerate(rows)}


def course_summary(con: sqlite3.Connection, course: str, v: Vocab) -> Result:
    """The course as a whole (no instructor rows): what staff and reviewers
    receive in place of ``course_instructors``."""
    sections, terms, instructors = con.execute(
        """
        SELECT COUNT(DISTINCT s.section_id), COUNT(DISTINCT s.term_code),
               COUNT(DISTINCT si.instructor_id)
        FROM sections s JOIN section_instructors si ON si.section_id = s.section_id
        WHERE s.course_id = ?""",
        (course,),
    ).fetchone()
    dfw, graded, students = con.execute(
        f"""
        SELECT COALESCE(SUM(g.grade IN {DFW_SQL}), 0), COUNT(*),
               COUNT(DISTINCT r.student_id)
        FROM sections s JOIN courses c ON c.course_id = s.course_id
        JOIN section_registrations r ON r.section_id = s.section_id
        JOIN final_grades g ON g.registration_id = r.registration_id
        WHERE s.course_id = ? AND g.grade IN {GRADED_SQL}
               AND c.grade_mode = 'standard'""",
        (course,),
    ).fetchone()
    row: dict[str, Any] = {
        "course": course,
        "title": v.courses[course],
        "sections": sections,
        "terms": terms,
        "instructors": instructors,
    }
    if _suppressed(students):
        row.update(
            graded=SUPPRESSED_DISPLAY,
            dfw=SUPPRESSED_DISPLAY,
            dfw_rate=SUPPRESSED_DISPLAY,
        )
    else:
        row.update(graded=graded, dfw=int(dfw), dfw_rate=pct(int(dfw), graded))
    return Result([row] if sections else [])


COURSE_SUMMARY_COLUMNS = (
    Column("course", "Course", entity="course"),
    Column("title", "Title"),
    Column("sections", "Sections", "count"),
    Column("terms", "Terms", "count"),
    Column("instructors", "Instructors", "count"),
    Column("graded", "Graded registrations", "count"),
    Column("dfw", "D, F, or W", "count"),
    Column("dfw_rate", "D, F or withdrawal rate (%)", "pct"),
)


# --- 6. an instructor's teaching history ------------------------------------------


def _instructor_history(con: sqlite3.Connection, p: dict[str, Any], v: Vocab) -> Result:
    inst = p["instructor"]
    teaching = con.execute(
        """
        SELECT s.course_id, COUNT(DISTINCT s.section_id), COUNT(DISTINCT s.term_code)
        FROM sections s JOIN section_instructors si ON si.section_id = s.section_id
        WHERE si.instructor_id = ?
        GROUP BY s.course_id""",
        (inst,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    withheld = 0
    for course, sections, terms in teaching:
        row: dict[str, Any] = {
            "course": course,
            "title": v.courses[course],
            "sections": sections,
            "terms": terms,
        }
        dfw, graded, hide = _instructor_stats(con, course).get(inst, (0, 0, True))
        row.update(graded=graded, dfw=dfw, dfw_rate=pct(dfw, graded))
        if hide:
            withheld += 1
            _withhold(row)
        out.append(row)
    out.sort(key=lambda r: (-r["sections"], r["course"]))
    notes = [f"Instructor: {v.instructor_label(inst)}. Instructor names are fictional."]
    if withheld:
        notes.append(
            f"D, F or withdrawal rates are withheld for {withheld} "
            f"{'course' if withheld == 1 else 'courses'} " + PROTECT_NOTE
        )
    return Result(_top(out, p.get("top")), notes)


# --- 7. equity gap for a course or a major by student group ----------------------

_GROUP_SQL = {
    "first_generation": "CASE st.first_generation WHEN 1 THEN 'first-generation "
    "students' "
    "ELSE 'continuing-generation students' END",
    "pell": "CASE st.pell_recipient WHEN 1 THEN 'Pell recipients' "
    "ELSE 'students without Pell' END",
    "residency": "CASE st.residency WHEN 'in_state' THEN 'in-state students' "
    "WHEN 'out_of_state' THEN 'out-of-state students' "
    "ELSE 'international students' END",
    "entry_cohort": "'students who entered in ' "
    "|| (CAST(substr(st.entry_term, 1, 4) AS INTEGER) - 1) "
    "|| '-' || substr(st.entry_term, 1, 4)",
    "entry_type": "CASE st.entry_type WHEN 'transfer' THEN 'transfer students' "
    "ELSE 'first-time students' END",
}
_GROUP_REFERENCE = {
    "first_generation": "continuing-generation students",
    "pell": "students without Pell",
    "residency": "in-state students",
    "entry_type": "first-time students",
}


def _equity_gap(con: sqlite3.Connection, p: dict[str, Any], v: Vocab) -> Result:
    group = p.get("group") or "first_generation"
    if not p.get("course") and not p.get("major"):
        raise AnalysisError("the equity gap needs a course or a major")
    expr = _GROUP_SQL[group]
    if p.get("course"):
        scope_sql, scope_args = "s.course_id = ?", (p["course"],)
        scope_join = ""
    else:
        # Registrations a student took while in the major (their program in
        # that term's student_term_records row).
        scope_join = (
            "JOIN student_term_records t ON t.student_id = r.student_id "
            "AND t.term_code = r.term_code"
        )
        scope_sql, scope_args = "t.program_code = ?", (v.major_program[p["major"]],)
    rows = con.execute(
        f"""
        SELECT {expr} AS grp, COUNT(DISTINCT r.student_id), COUNT(*),
               SUM(g.grade IN {DFW_SQL})
        FROM final_grades g
        JOIN section_registrations r ON r.registration_id = g.registration_id
        JOIN sections s ON s.section_id = r.section_id
        JOIN courses c ON c.course_id = s.course_id
        JOIN students st ON st.student_id = r.student_id
        {scope_join}
        WHERE {scope_sql} AND g.grade IN {GRADED_SQL} AND c.grade_mode = 'standard'
        GROUP BY grp""",
        scope_args,
    ).fetchall()
    stats = [
        (str(label), int(n), int(graded), int(dfw)) for label, n, graded, dfw in rows
    ]
    stats.sort(key=lambda s: s[0])
    hidden = _complementary([s[1] for s in stats])
    visible = [s for i, s in enumerate(stats) if i not in hidden]
    reference = _GROUP_REFERENCE.get(group)
    ref = next((s for s in visible if s[0] == reference), None)
    if ref is None and visible:
        ref = max(visible, key=lambda s: (s[1], s[0]))
    out: list[dict[str, Any]] = []
    for label, n, graded, dfw in sorted(visible, key=lambda s: (-(s[3] / s[2]), s[0])):
        rate = pct(dfw, graded)
        gap = round(rate - pct(ref[3], ref[2]), 1) if ref is not None else 0.0
        out.append(
            {
                "group": label,
                "students": n,
                "graded": graded,
                "dfw": dfw,
                "dfw_rate": rate,
                "gap_points": gap,
            }
        )
    notes: list[str] = []
    if len(stats) > len(visible):
        count = len(stats) - len(visible)
        notes.append(
            f"{count} {'group is' if count == 1 else 'groups are'} withheld "
            + PROTECT_NOTE
        )
    if ref is not None:
        notes.append(f"Gaps are in percentage points against {ref[0]}.")
    return Result(out, notes)


# --- 8. headcount by major over time (growth) -------------------------------------


def _headcount_growth(con: sqlite3.Connection, p: dict[str, Any], v: Vocab) -> Result:
    start, end = sorted(
        (p.get("start_term") or "202110", p.get("end_term") or "202610")
    )
    if start == end:
        raise AnalysisError("growth needs two different terms")
    clauses = [
        (f, val)
        for f, val in (
            ("ap.major_code = ?", p.get("major")),
            ("ap.college_code = ?", p.get("college")),
        )
        if val
    ]
    where, args = _filters(clauses)
    rows = con.execute(
        f"""
        SELECT ap.major_code, ap.name, SUM(r.term_code = ?), SUM(r.term_code = ?)
        FROM student_term_records r JOIN academic_programs ap USING (program_code)
        WHERE r.term_code IN (?, ?) {where}
        GROUP BY ap.major_code""",
        (start, end, start, end, *args),
    ).fetchall()
    minimum = int(p.get("min_start") or 40)
    hidden_cells = _major_term_hidden(con, v)
    kept, withheld, small = [], 0, 0
    for code, name, a, b in rows:
        a, b = int(a), int(b)
        if (
            _suppressed(a)
            or _suppressed(b)
            or (code, start) in hidden_cells
            or (code, end) in hidden_cells
        ):
            withheld += 1
            continue
        if a < minimum and not p.get("major"):
            small += 1
            continue
        kept.append(
            {
                "major": code,
                "major_name": name,
                "start_term": v.terms[start],
                "start_headcount": a,
                "end_term": v.terms[end],
                "end_headcount": b,
                "change": b - a,
                "growth": pct(b - a, a),
                "_sort": b / a,
            }
        )
    slowest = p.get("order") == "slowest_first"
    kept.sort(key=lambda r: (r["_sort"] if slowest else -r["_sort"], r["major"]))
    notes = _withheld_note(withheld, "major")
    if small:
        notes.append(
            f"Only majors with at least {minimum} students in {v.terms[start]} are "
            f"ranked ({small} smaller {'major is' if small == 1 else 'majors are'} "
            "left out)."
        )
    return Result(_top(kept, p.get("top")), notes)


# --- 9. enrollment by term --------------------------------------------------------


def _scope_clauses(
    p: dict[str, Any], v: Vocab, alias: str = "r"
) -> list[tuple[str, Any]]:
    clauses: list[tuple[str, Any]] = []
    if p.get("major"):
        clauses.append((f"{alias}.program_code = ?", v.major_program[p["major"]]))
    if p.get("college"):
        clauses.append(
            (
                f"{alias}.program_code IN (SELECT program_code FROM academic_programs "
                "WHERE college_code = ?)",
                p["college"],
            )
        )
    if p.get("season"):
        clauses.append(
            (
                f"{alias}.term_code IN (SELECT term_code FROM academic_periods "
                "WHERE season = ?)",
                p["season"],
            )
        )
    return clauses


def _scope_hidden(con: sqlite3.Connection, p: dict[str, Any], v: Vocab) -> set[str]:
    """Terms withheld for a major or college scope (its headcount in the term
    is a withheld cell of a partition whose total is published)."""
    if p.get("major"):
        return {t for m, t in _major_term_hidden(con, v) if m == p["major"]}
    if p.get("college"):
        return {t for c, t in _college_term_hidden(con) if c == p["college"]}
    return set()


def _enrollment_parts_hidden(
    con: sqlite3.Connection, p: dict[str, Any], v: Vocab
) -> set[str]:
    """Terms withheld for a major or college because, in the term, the
    withheld rows of its partition (a college's majors, or the colleges)
    hold fewer than 10 students in some part (all, new, or continuing):
    each row is sized by its smallest part, so the withheld rows' new and
    continuing counts cannot be worked out from the parent either."""
    if not (p.get("major") or p.get("college")):
        return set()
    level = "major_code" if p.get("major") else "college_code"
    sizes: dict[tuple[str, str], dict[str, int]] = {}
    for unit, college, term, n, new, cont in con.execute(
        f"""SELECT ap.{level}, ap.college_code, r.term_code, COUNT(*),
               SUM(st.entry_term = r.term_code), SUM(st.entry_term < r.term_code)
        FROM student_term_records r JOIN students st ON st.student_id = r.student_id
        JOIN academic_programs ap ON ap.program_code = r.program_code
        GROUP BY 1, 2, 3"""
    ):
        part = (str(college) if p.get("major") else "", str(term))
        smallest = min(int(n), int(new), int(cont))
        bucket = sizes.setdefault(part, {})
        bucket[str(unit)] = min(bucket.get(str(unit), smallest), smallest)
    target = p.get("major") or p.get("college")
    return {
        term
        for (_, term), part in sizes.items()
        if target in _general.complementary(part)
    }


def _enrollment_by_term(con: sqlite3.Connection, p: dict[str, Any], v: Vocab) -> Result:
    where, args = _filters(_scope_clauses(p, v))
    rows = con.execute(
        f"""
        SELECT r.term_code, COUNT(*), SUM(st.entry_term = r.term_code),
               SUM(st.entry_term < r.term_code)
        FROM student_term_records r JOIN students st ON st.student_id = r.student_id
        WHERE 1 = 1 {where}
        GROUP BY r.term_code ORDER BY r.term_code""",
        args,
    ).fetchall()
    out: list[dict[str, Any]] = []
    withheld = 0
    hidden = _scope_hidden(con, p, v) | _enrollment_parts_hidden(con, p, v)
    for term, n, new, cont in rows:
        row: dict[str, Any] = {"term": term, "term_name": v.terms[term]}
        values = {"students": int(n), "new_students": int(new), "continuing": int(cont)}
        if term in hidden or any(_suppressed(value) for value in values.values()):
            # Students = new + continuing, so one withheld part withholds all.
            withheld += 1
            _withhold(row, tuple(values))
        else:
            row.update(values)
        out.append(row)
    notes = (
        [f"Counts of {SUPPRESSED_DISPLAY} students are withheld."] if withheld else []
    )
    return Result(out, notes)


# --- 10. spring-to-spring continuing registration change --------------------------


def _continuing_change(con: sqlite3.Connection, p: dict[str, Any], v: Vocab) -> Result:
    term = p.get("term") or max(t for t, s in v.term_season.items() if s == "Spring")
    prior = f"{int(term[:4]) - 1}{term[4:]}"
    if prior not in v.terms:
        raise AnalysisError(f"{v.terms.get(term, term)} has no term a year earlier")

    def continuing(code: str) -> int:
        return int(
            con.execute(
                """
            SELECT COUNT(DISTINCT r.student_id) FROM section_registrations r
            JOIN students st ON st.student_id = r.student_id
            WHERE r.term_code = ? AND st.entry_term < ?""",
                (code, code),
            ).fetchone()[0]
        )

    cur, prev = continuing(term), continuing(prior)
    row: dict[str, Any] = {
        "term": term,
        "term_name": v.terms[term],
        "prior_term": prior,
        "prior_term_name": v.terms[prior],
    }
    small: set[tuple[int, str]] = set()
    if _suppressed(cur) or _suppressed(prev):
        row.update(
            continuing=SUPPRESSED_DISPLAY,
            prior_continuing=SUPPRESSED_DISPLAY,
            change=SUPPRESSED_DISPLAY,
            change_pct=SUPPRESSED_DISPLAY,
        )
        # Only the term that is itself under 10 is; the other is withheld
        # so it cannot be subtracted from the change.
        small = {
            (0, key)
            for key, n in (("continuing", cur), ("prior_continuing", prev))
            if _suppressed(n)
        }
    else:
        row.update(
            continuing=cur,
            prior_continuing=prev,
            change=cur - prev,
            change_pct=round(100.0 * (cur / prev - 1), 1),
        )
    return Result(
        [row],
        [
            "A continuing student registered in the term and entered the "
            "university before it."
        ],
        small=small,
    )


# --- 11. withdrawal rate by modality by term --------------------------------------


def _withdrawal_by_modality(
    con: sqlite3.Connection, p: dict[str, Any], v: Vocab
) -> Result:
    clauses: list[tuple[str, Any]] = []
    if p.get("term"):
        clauses.append(("s.term_code = ?", p["term"]))
    where, args = _filters(clauses)
    rows = con.execute(
        f"""
        SELECT s.term_code, s.modality, SUM(g.grade = 'W'), COUNT(*),
               COUNT(DISTINCT r.student_id)
        FROM final_grades g
        JOIN section_registrations r ON r.registration_id = g.registration_id
        JOIN sections s ON s.section_id = r.section_id
        JOIN courses c ON c.course_id = s.course_id
        JOIN academic_periods ap ON ap.term_code = s.term_code
        WHERE c.grade_mode = 'standard' AND g.grade IN {GRADED_SQL}
          AND (ap.season != 'Summer' OR s.term_code = ?) {where}
        GROUP BY 1, 2""",
        (p.get("term") or "", *args),
    ).fetchall()
    by_term: dict[str, dict[str, tuple[int, int, int]]] = {}
    for term, modality, w, n, students in rows:
        by_term.setdefault(term, {})[modality] = (int(w), int(n), int(students))
    out: list[dict[str, Any]] = []
    withheld = 0
    for term in sorted(by_term):
        stats = by_term[term]
        row: dict[str, Any] = {"term": term, "term_name": v.terms[term]}
        rates: dict[str, float] = {}
        for modality in ("online", "in_person", "hybrid"):
            w, n, students = stats.get(modality, (0, 0, 0))
            if modality != "hybrid":
                if _suppressed(students):
                    row[f"{modality}_w"] = SUPPRESSED_DISPLAY
                    row[f"{modality}_graded"] = SUPPRESSED_DISPLAY
                else:
                    row[f"{modality}_w"] = w
                    row[f"{modality}_graded"] = n
            if _suppressed(students):
                withheld += 1
                row[f"{modality}_rate"] = SUPPRESSED_DISPLAY
            else:
                rates[modality] = pct(w, n)
                row[f"{modality}_rate"] = rates[modality]
        if "online" in rates and "in_person" in rates:
            gap = rates["online"] - rates["in_person"]
            row["gap_points"], row["_sort"] = round(gap, 1), gap
        else:
            row["gap_points"], row["_sort"] = SUPPRESSED_DISPLAY, None
        out.append(row)
    if p.get("order") == "largest_gap":
        ranked = sorted(
            (r for r in out if r["_sort"] is not None),
            key=lambda r: (-r["_sort"], r["term"]),
        )
        out = ranked + [r for r in out if r["_sort"] is None]
    notes = [
        "The withdrawal rate is W grades divided by graded registrations. Fall and "
        "spring terms only unless a term is named."
    ]
    if withheld:
        notes.append(f"Rates for groups of {SUPPRESSED_DISPLAY} students are withheld.")
    return Result(_top(out, p.get("top")), notes)


# --- 11b. withdrawal rate by course and teaching mode -----------------------------


def _withdrawal_by_course_modality(
    con: sqlite3.Connection, p: dict[str, Any], v: Vocab
) -> Result:
    """Each course's online W rate against its in-person W rate, over the fall
    and spring terms, ranked by the online rate. A course is ranked only when
    its online sections had at least ``min_online`` students (never under
    10); an in-person rate over fewer than 10 students is withheld."""
    clauses: list[tuple[str, Any]] = []
    if p.get("subject"):
        clauses.append(("c.subject_code = ?", p["subject"]))
    where, args = _filters(clauses)
    rows = con.execute(
        f"""
        SELECT s.course_id, c.title, s.modality, SUM(g.grade = 'W'), COUNT(*),
               COUNT(DISTINCT r.student_id)
        FROM final_grades g
        JOIN section_registrations r ON r.registration_id = g.registration_id
        JOIN sections s ON s.section_id = r.section_id
        JOIN courses c ON c.course_id = s.course_id
        JOIN academic_periods ap ON ap.term_code = s.term_code
        WHERE c.grade_mode = 'standard' AND g.grade IN {GRADED_SQL}
          AND ap.season != 'Summer'
          AND s.modality IN ('online', 'in_person') {where}
        GROUP BY 1, 2, 3""",
        args,
    ).fetchall()
    by_course: dict[str, dict[str, Any]] = {}
    for course, title, modality, w, n, students in rows:
        entry = by_course.setdefault(course, {"title": title})
        entry[modality] = (int(w), int(n), int(students))
    minimum = max(int(p.get("min_online") or MINIMUM_CELL_SIZE), MINIMUM_CELL_SIZE)
    kept: list[dict[str, Any]] = []
    withheld = 0
    for course, entry in by_course.items():
        if "online" not in entry:
            continue  # never taught online: nothing to rank
        w, n, students = entry["online"]
        if students < minimum:
            if _suppressed(students):
                withheld += 1
            continue
        row: dict[str, Any] = {
            "course": course,
            "title": entry["title"],
            "online_students": students,
            "online_w": w,
            "online_graded": n,
            "online_rate": pct(w, n),
            "_sort": w / n,
        }
        if "in_person" not in entry:
            # Never taught in person: nothing to compare, nothing withheld.
            row["in_person_rate"] = None
            row["gap_points"] = None
            kept.append(row)
            continue
        iw, inn, istudents = entry["in_person"]
        if _suppressed(istudents):
            row["in_person_rate"] = SUPPRESSED_DISPLAY
            row["gap_points"] = SUPPRESSED_DISPLAY
        else:
            row["in_person_rate"] = pct(iw, inn)
            row["gap_points"] = round(row["online_rate"] - row["in_person_rate"], 1)
        kept.append(row)
    descending = p.get("order", "highest_first") == "highest_first"
    kept.sort(key=lambda r: (-r["_sort"] if descending else r["_sort"], r["course"]))
    notes = [
        "The withdrawal rate is W grades divided by graded registrations, fall and "
        "spring terms only. Hybrid sections are left out.",
        f"Only courses whose online sections had at least {minimum} students are "
        "ranked.",
    ]
    notes += _withheld_note(withheld, "course")
    return Result(_top(kept, p.get("top")), notes)


# --- 12. probation and suspension rates by major ----------------------------------


def _standing_by_major(con: sqlite3.Connection, p: dict[str, Any], v: Vocab) -> Result:
    clauses: list[tuple[str, Any]] = []
    if p.get("term"):
        clauses.append(("a.term_code = ?", p["term"]))
    if p.get("college"):
        clauses.append(("ap.college_code = ?", p["college"]))
    if p.get("major"):
        clauses.append(("ap.major_code = ?", p["major"]))
    where, args = _filters(clauses)
    rows = con.execute(
        f"""
        SELECT ap.major_code, ap.name, COUNT(*), COUNT(DISTINCT a.student_id),
               SUM(a.standing IN ('Academic Probation', 'Continued Probation')),
               SUM(a.standing = 'Academic Suspension')
        FROM academic_standings a
        JOIN student_term_records t ON t.student_id = a.student_id
               AND t.term_code = a.term_code
        JOIN academic_programs ap ON ap.program_code = t.program_code
        WHERE 1 = 1 {where}
        GROUP BY ap.major_code""",
        args,
    ).fetchall()
    scope_sql, scope_args = (
        ("a.term_code = ?", (p["term"],))
        if p.get("term")
        else (
            "1 = 1",
            (),
        )
    )
    sizes = {
        str(m): int(n)
        for m, n in con.execute(
            f"""SELECT ap.major_code, COUNT(DISTINCT a.student_id)
            FROM academic_standings a
            JOIN student_term_records t ON t.student_id = a.student_id
                   AND t.term_code = a.term_code
            JOIN academic_programs ap ON ap.program_code = t.program_code
            WHERE {scope_sql} GROUP BY 1""",
            scope_args,
        )
    }
    hidden = _within_college(sizes, v)
    if p.get("term"):
        by_major = _term_partition_hidden(
            con,
            """SELECT ap.major_code, a.term_code, COUNT(DISTINCT a.student_id)
            FROM academic_standings a
            JOIN student_term_records t ON t.student_id = a.student_id
                   AND t.term_code = a.term_code
            JOIN academic_programs ap ON ap.program_code = t.program_code
            GROUP BY 1, 2""",
            (),
        )
        hidden |= {m for m, terms in by_major.items() if p["term"] in terms}
    kept, withheld = [], 0
    for code, name, records, students, probation, suspension in rows:
        if _suppressed(students) or code in hidden:
            withheld += 1
            continue
        kept.append(
            {
                "major": code,
                "major_name": name,
                "records": records,
                "probation": int(probation),
                "probation_rate": pct(int(probation), records),
                "suspension": int(suspension),
                "suspension_rate": pct(int(suspension), records),
                "_sort": int(probation) / records,
            }
        )
    descending = p.get("order", "highest_first") == "highest_first"
    kept.sort(key=lambda r: (-r["_sort"] if descending else r["_sort"], r["major"]))
    notes = _withheld_note(withheld, "major")
    notes.append(
        "Each student is counted once per term. Probation includes continued probation."
    )
    return Result(_top(kept, p.get("top")), notes)


# --- 13. graduation counts by major and year --------------------------------------


def _graduation_hidden(
    con: sqlite3.Connection, v: Vocab
) -> tuple[
    dict[tuple[str, str], int], set[tuple[str, str]], set[str], set[tuple[str, str]]
]:
    """(counts per (major, year), withheld (major, year), withheld major
    totals, withheld (college, year)). Every partition with a published total
    is protected: a college's majors in a year and over all years, a major's
    years, the colleges in a year, and a college's years."""
    counts: dict[tuple[str, str], int] = {}
    for major, year, n in con.execute(
        """SELECT ap.major_code, t.academic_year, COUNT(DISTINCT sap.student_id)
        FROM student_academic_programs sap
        JOIN academic_programs ap ON ap.program_code = sap.program_code
        JOIN academic_periods t ON t.term_code = sap.end_term
        WHERE sap.status = 'graduated' GROUP BY 1, 2"""
    ):
        counts[(str(major), str(year))] = int(n)
    by_year: dict[str, dict[str, int]] = {}
    by_major: dict[str, dict[str, int]] = {}
    totals: dict[str, int] = {}
    college_year: dict[tuple[str, str], int] = {}
    for (major, year), n in counts.items():
        by_year.setdefault(year, {})[major] = n
        by_major.setdefault(major, {})[year] = n
        totals[major] = totals.get(major, 0) + n
        key = (v.major_college.get(major, ""), year)
        college_year[key] = college_year.get(key, 0) + n
    hidden_my: set[tuple[str, str]] = set()
    for year, sizes in by_year.items():
        hidden_my |= {(m, year) for m in _within_college(sizes, v)}
    for major, sizes in by_major.items():
        hidden_my |= {(major, y) for y in _general.complementary(sizes)}
    hidden_totals = _within_college(totals, v)
    hidden_cy: set[tuple[str, str]] = set()
    cy_by_year: dict[str, dict[str, int]] = {}
    cy_by_college: dict[str, dict[str, int]] = {}
    for (college, year), n in college_year.items():
        cy_by_year.setdefault(year, {})[college] = n
        cy_by_college.setdefault(college, {})[year] = n
    for year, sizes in cy_by_year.items():
        hidden_cy |= {(c, year) for c in _general.complementary(sizes)}
    for college, sizes in cy_by_college.items():
        hidden_cy |= {(college, y) for y in _general.complementary(sizes)}
    return counts, hidden_my, hidden_totals, hidden_cy


def _graduations(con: sqlite3.Connection, p: dict[str, Any], v: Vocab) -> Result:
    counts, hidden_my, hidden_totals, hidden_cy = _graduation_hidden(con, v)
    major, college, year = p.get("major"), p.get("college"), p.get("academic_year")
    by_year = p.get("group_by") == "year"
    small: set[tuple[int, str]] = set()

    def in_scope(m: str) -> bool:
        return (not major or m == major) and (
            not college or v.major_college.get(m) == college
        )

    out: list[dict[str, Any]] = []
    withheld = 0
    if by_year:
        years = sorted({y for _, y in counts if not year or y == year})
        for y in years:
            n = sum(c for (m, yy), c in counts.items() if yy == y and in_scope(m))
            if major:
                hide = (major, y) in hidden_my
            elif college:
                hide = (college, y) in hidden_cy
            else:
                hide = False
            if n == 0:
                continue
            if hide or _suppressed(n):
                withheld += 1
                if _suppressed(n):
                    small.add((len(out), "graduates"))
                out.append({"academic_year": y, "graduates": SUPPRESSED_DISPLAY})
            else:
                out.append({"academic_year": y, "graduates": n})
    else:
        majors = sorted({m for m, _ in counts if in_scope(m)})
        for m in majors:
            if year:
                n = counts.get((m, year), 0)
                hide = (m, year) in hidden_my
            else:
                n = sum(c for (mm, _), c in counts.items() if mm == m)
                hide = m in hidden_totals
            if n == 0:
                continue
            if hide or _suppressed(n):
                withheld += 1
                continue
            out.append(
                {
                    "major": m,
                    "major_name": v.majors[m],
                    "academic_year": year or "All years",
                    "graduates": n,
                    "_sort": n,
                }
            )
        out.sort(key=lambda r: (-r["_sort"], r["major"]))
    notes = ["A graduate is counted in the academic year of the term they graduated."]
    if withheld:
        notes.append(
            f"Counts of {SUPPRESSED_DISPLAY} graduates are withheld"
            f"{'' if by_year else ' and not ranked'}, with more where needed so a "
            "withheld count cannot be worked out from a total."
        )
    if not by_year:
        return Result(_top(out, p.get("top")), notes)
    return Result(out, notes, small=small)


# --- 14. holds by office ---------------------------------------------------------


def _holds_by_office(con: sqlite3.Connection, p: dict[str, Any], v: Vocab) -> Result:
    clauses: list[tuple[str, Any]] = []
    if p.get("active_only") == "yes":
        clauses.append(("h.end_date IS NULL AND 1 = ?", 1))
    if p.get("term"):
        clauses.append(("h.term_code = ?", p["term"]))
    if p.get("category"):
        clauses.append(("h.category = ?", p["category"]))
    where, args = _filters(clauses)
    rows = con.execute(
        f"""
        SELECT h.responsible_office, COUNT(*), COUNT(DISTINCT h.student_id),
               COALESCE(SUM(h.amount), 0), SUM(h.amount > 0)
        FROM person_holds h WHERE 1 = 1 {where}
        GROUP BY h.responsible_office""",
        args,
    ).fetchall()
    hidden: set[str] = set()
    if p.get("term"):
        by_office = _term_partition_hidden(
            con,
            """SELECT h.responsible_office, h.term_code, COUNT(DISTINCT h.student_id)
            FROM person_holds h GROUP BY 1, 2""",
            (),
        )
        hidden = {o for o, terms in by_office.items() if p["term"] in terms}
    kept, withheld = [], 0
    for office, holds, students, amount, with_amount in rows:
        if _suppressed(students) or office in hidden:
            withheld += 1
            continue
        with_amount = int(with_amount or 0)
        kept.append(
            {
                "office": office,
                "holds": holds,
                "students": students,
                "total_amount": round(float(amount), 2),
                "average_amount": round(float(amount) / with_amount, 2)
                if with_amount
                else 0.0,
                "_sort": holds,
            }
        )
    kept.sort(key=lambda r: (-r["_sort"], r["office"]))
    notes = _withheld_note(withheld, "office")
    notes.append(
        "Amounts are in dollars. The average is over holds that carry an amount "
        "(financial and library holds)."
    )
    return Result(kept, notes)


# --- 15. advising appointment coverage by major ---------------------------------


def _advising_coverage(con: sqlite3.Connection, p: dict[str, Any], v: Vocab) -> Result:
    term = p.get("term") or max(t for t, s in v.term_season.items() if s != "Summer")
    clauses: list[tuple[str, Any]] = []
    if p.get("college"):
        clauses.append(("ap.college_code = ?", p["college"]))
    if p.get("major"):
        clauses.append(("ap.major_code = ?", p["major"]))
    where, args = _filters(clauses)
    rows = con.execute(
        f"""
        SELECT ap.major_code, ap.name, COUNT(*),
               SUM(EXISTS (SELECT 1 FROM student_appointments a
                           WHERE a.student_id = t.student_id
                                  AND a.term_code = t.term_code
                             AND a.status = 'completed'))
        FROM student_term_records t JOIN academic_programs ap USING (program_code)
        WHERE t.term_code = ? {where}
        GROUP BY ap.major_code""",
        (term, *args),
    ).fetchall()
    cells = _major_term_hidden(con, v)
    sizes = {
        str(m): int(n)
        for m, n in con.execute(
            """SELECT ap.major_code, COUNT(*) FROM student_term_records t
            JOIN academic_programs ap USING (program_code) WHERE t.term_code = ?
            GROUP BY 1""",
            (term,),
        )
    }
    hidden = _within_college(sizes, v) | {m for m, t in cells if t == term}
    kept, withheld = [], 0
    for code, name, students, advised in rows:
        if _suppressed(students) or code in hidden:
            withheld += 1
            continue
        kept.append(
            {
                "major": code,
                "major_name": name,
                "term_name": v.terms[term],
                "students": students,
                "advised": int(advised),
                "coverage": pct(int(advised), students),
                "_sort": int(advised) / students,
            }
        )
    descending = p.get("order") == "highest_first"
    kept.sort(key=lambda r: (-r["_sort"] if descending else r["_sort"], r["major"]))
    notes = _withheld_note(withheld, "major")
    notes.append(
        "Coverage is the share of enrolled students with at least one completed "
        "advising appointment in the term."
    )
    return Result(_top(kept, p.get("top")), notes)


# --- 16. credit hours by term ------------------------------------------------------


def _credit_hours(con: sqlite3.Connection, p: dict[str, Any], v: Vocab) -> Result:
    where, args = _filters(_scope_clauses(p, v))
    small: set[tuple[int, str]] = set()
    rows = con.execute(
        f"""
        SELECT r.term_code, COUNT(*), SUM(r.attempted_hours), SUM(r.earned_hours)
        FROM student_term_records r WHERE 1 = 1 {where}
        GROUP BY r.term_code ORDER BY r.term_code""",
        args,
    ).fetchall()
    out: list[dict[str, Any]] = []
    withheld = 0
    hidden = _scope_hidden(con, p, v)
    for term, n, attempted, earned in rows:
        row: dict[str, Any] = {"term": term, "term_name": v.terms[term]}
        if _suppressed(n) or term in hidden:
            withheld += 1
            if _suppressed(n):
                small.add((len(out), "students"))
            row.update(
                students=SUPPRESSED_DISPLAY,
                attempted_hours=SUPPRESSED_DISPLAY,
                earned_hours=SUPPRESSED_DISPLAY,
                average_attempted=SUPPRESSED_DISPLAY,
            )
        else:
            row.update(
                students=n,
                attempted_hours=int(attempted),
                earned_hours=int(earned),
                average_attempted=round(int(attempted) / n, 1),
            )
        out.append(row)
    notes = (
        [f"Terms with {SUPPRESSED_DISPLAY} students are withheld."] if withheld else []
    )
    return Result(out, notes, small=small)


# --- the registry ------------------------------------------------------------------

_P_MAJOR = Param("major", "Major", "major")
_P_COLLEGE = Param("college", "College", "college")
_P_SEASON = Param("season", "Season", "choice", choices=SEASONS)
_P_COURSE_REQUIRED = Param("course", "Course", "course", required=True)

_TERM_COLS = (Column("term", "Term code", entity="term"), Column("term_name", "Term"))
_DFW_COLS = (
    Column("graded", "Graded registrations", "count"),
    Column("dfw", "D, F, or W", "count"),
    Column("dfw_rate", "D, F or withdrawal rate (%)", "pct"),
)


def _measure_by_group(con: sqlite3.Connection, p: dict[str, Any], v: Vocab) -> Result:
    try:
        rows, notes, columns = _general.run(con, p, v)
    except _general.GeneralError as exc:
        raise AnalysisError(str(exc)) from None
    entity = {"major": "major", "college": "college", "term": "term"}
    return Result(
        rows,
        notes,
        tuple(
            Column(key, label, kind, entity.get(key)) for key, label, kind in columns
        ),
    )


def _grouping_param(name: str, label: str) -> Param:
    return Param(
        name,
        label,
        "choice",
        choices=_general.GROUPING_KEYS,
        choice_labels={k: g.noun for k, g in _general.GROUPINGS.items()},
        shown=label + ": {}",
    )


def _filter_param(key: str) -> Param:
    g = _general.GROUPINGS[key]
    if key in ("major", "college"):
        return Param(key, g.label, key)
    if key == "entry_cohort":
        return Param(key, g.label, "entry_cohort", shown="Only students who {}")
    return Param(
        key,
        g.label,
        "choice",
        choices=tuple(k for k in g.values if k != _general.NOT_RECORDED),
        choice_labels=dict(g.values),
        shown="Only: {}",
    )


MEASURE_BY_GROUP = Analysis(
    _general.ANALYSIS_ID,
    "A measure by group",
    "One reviewed measure (headcount, average GPA, dropout, stop-out, first-year "
    "retention, 4- and 6-year graduation, time to degree, D, F or withdrawal and "
    "withdrawal rates, probation, credits, major changes, advising, holds, Pell, "
    "first-generation, international, part-time and on-campus shares; and for "
    "graduates the survey knowledge rate, employment, median starting salary, "
    "graduate school, medical school acceptance, and alumni giving) by up to two "
    "groupings (major, college, class level, term, entry cohort, residency, "
    "first-generation, Pell, gender, race and ethnicity, age at entry, admit type, "
    "full or part time, housing, athletes, honors, modality, final GPA band), with "
    "filters on any grouping value and the term window.",
    (
        Param(
            "measure",
            "Measure",
            "choice",
            required=True,
            choices=_general.MEASURE_KEYS,
            choice_labels={k: m.label for k, m in _general.MEASURES.items()},
            shown="Measure: {}",
        ),
        _grouping_param("group_by", "Grouped by"),
        _grouping_param("then_by", "Then by"),
        *(_filter_param(k) for k in _general.GROUPING_KEYS if k != "term"),
        Param("term_from", "From term", "term"),
        Param("term_to", "To term", "term"),
        Param(
            "order",
            "Order",
            "choice",
            choices=("highest_first", "lowest_first", "natural"),
            choice_labels={
                "highest_first": "highest first",
                "lowest_first": "lowest first",
                "natural": "in natural order",
            },
            shown="Ranked: {}",
        ),
        _top_param(None),
    ),
    (
        "students (pseudonymous id counted, never returned)",
        "student_profiles.gender",
        "student_profiles.race_ethnicity",
        "student_profiles.age_band_at_entry",
        "student_profiles.athlete",
        "student_profiles.honors",
        "students.residency",
        "students.first_generation",
        "students.pell_recipient",
        "students.entry_term",
        "students.entry_type",
        "students.enrollment_status",
        "student_term_records (program, class level, hours, GPA)",
        "student_term_enrollment (status, load, housing)",
        "subsequent_enrollment.student_id (matched or not)",
        "academic_standings.standing",
        "student_academic_programs.status",
        "student_appointments.status",
        "person_holds.term_code",
        "final_grades.grade",
        "sections.modality",
        "first_destination (outcome, starting salary)",
        "graduate_enrollment.enrollment_begin_date",
        "medical_school_applications.accepted",
        "alumni_gifts (amount)",
    ),
    (
        Column("major", "Major code", entity="major"),
        Column("major_name", "Major"),
        Column("college", "College code", entity="college"),
        Column("college_name", "College"),
        Column("term", "Term code", entity="term"),
        Column("term_name", "Term"),
        Column("group", "Group"),
        Column("group_2", "Group"),
        Column("students", "Students", "count"),
        Column("denominator", "Denominator", "count"),
        Column("numerator", "Numerator", "count"),
        Column("value", "Value", "pct"),
    ),
    _measure_by_group,
)

ANALYSES: tuple[Analysis, ...] = (
    Analysis(
        "gpa_by_major",
        "Average GPA by major",
        "Average cumulative GPA of each major's students, ranked (each student counted "
        "once, in the program of their latest term).",
        (
            _order_param("lowest_first"),
            _P_COLLEGE,
            _P_MAJOR,
            Param(
                "min_students",
                "Minimum students to rank",
                "choice",
                default=20,
                choices=(10, 20, 50, 100),
                shown="Only majors with at least {} students",
            ),
            _top_param(10),
        ),
        (
            "student_term_records.program_code",
            "student_term_records.term_code",
            "student_term_records.cumulative_gpa",
            "academic_programs.major_code",
            "academic_programs.name",
            "academic_programs.college_code",
        ),
        (
            Column("major", "Major code", entity="major"),
            Column("major_name", "Major"),
            Column("college", "College"),
            Column("students", "Students", "count"),
            Column("avg_gpa", "Average cumulative GPA", "gpa"),
        ),
        _gpa_by_major,
    ),
    Analysis(
        "gpa_by_college",
        "Average GPA by college",
        "Average cumulative GPA of each college's students, ranked.",
        (_order_param("lowest_first"), _P_COLLEGE),
        (
            "student_term_records.program_code",
            "student_term_records.term_code",
            "student_term_records.cumulative_gpa",
            "academic_programs.college_code",
            "colleges.name",
        ),
        (
            Column("college", "College code", entity="college"),
            Column("college_name", "College"),
            Column("students", "Students", "count"),
            Column("avg_gpa", "Average cumulative GPA", "gpa"),
        ),
        _gpa_by_college,
    ),
    Analysis(
        "dfw_by_course",
        "D, F or withdrawal rate by course",
        "Share of graded registrations ending in D+, D, F, or W, by course, "
        "ranked. Filters: "
        "courses a major requires, subject, level, term range, a minimum track record.",
        (
            Param("major_required", "Required by major", "major"),
            Param("subject", "Subject", "subject"),
            Param(
                "level",
                "Course level",
                "choice",
                choices=("1000", "2000", "3000", "4000"),
            ),
            Param("term_from", "From term", "term"),
            Param("term_to", "To term", "term"),
            Param(
                "min_sections",
                "Minimum sections",
                "choice",
                default=8,
                choices=(1, 2, 4, 8, 16),
                shown="Only courses with at least {} sections",
            ),
            Param(
                "min_terms",
                "Minimum terms",
                "choice",
                default=4,
                choices=(1, 2, 4, 8),
                shown="Only courses taught in at least {} terms",
            ),
            _order_param("highest_first"),
            _top_param(10),
        ),
        (
            "final_grades.grade",
            "section_registrations.section_id",
            "sections.course_id",
            "sections.term_code",
            "courses.grade_mode",
            "courses.course_level",
            "courses.subject_code",
            "program_requirements.course_id",
        ),
        (
            Column("course", "Course", entity="course"),
            Column("title", "Title"),
            Column("sections", "Sections", "count"),
            Column("terms", "Terms", "count"),
            *_DFW_COLS,
        ),
        _dfw_by_course,
    ),
    Analysis(
        "course_dfw_trend",
        "A course's D, F or withdrawal trend by term",
        "One course's D, F or withdrawal rate in each term it was offered, "
        "and over all terms.",
        (_P_COURSE_REQUIRED,),
        (
            "final_grades.grade",
            "section_registrations.section_id",
            "sections.course_id",
            "sections.term_code",
            "courses.grade_mode",
        ),
        (
            Column("course", "Course", entity="course"),
            *_TERM_COLS,
            Column("sections", "Sections", "count"),
            *_DFW_COLS,
        ),
        _course_dfw_trend,
    ),
    Analysis(
        "course_instructors",
        "Instructors who taught a course",
        "Each instructor of record for one course: sections, terms, "
        "and D, F or withdrawal rate. "
        "Executive and admin roles only. Names are fictional.",
        (_P_COURSE_REQUIRED,),
        (
            "section_instructors.instructor_id",
            "instructors.first_name",
            "instructors.last_name",
            "instructors.academic_rank",
            "sections.course_id",
            "sections.term_code",
            "final_grades.grade",
        ),
        (
            Column("instructor", "Instructor", entity="instructor"),
            Column("name", "Name (fictional)"),
            Column("rank", "Rank"),
            Column("sections", "Sections", "count"),
            Column("terms", "Terms", "count"),
            Column("first_term", "First term"),
            Column("last_term", "Last term"),
            *_DFW_COLS,
        ),
        _course_instructors,
        instructor_level=True,
    ),
    Analysis(
        "instructor_history",
        "An instructor's teaching history",
        "The courses one instructor taught: sections, terms, "
        "and D, F or withdrawal rate. "
        "Executive and "
        "admin roles only. Names are fictional.",
        (
            Param("instructor", "Instructor", "instructor", required=True),
            _top_param(20),
        ),
        (
            "section_instructors.instructor_id",
            "sections.course_id",
            "sections.term_code",
            "courses.title",
            "final_grades.grade",
        ),
        (
            Column("course", "Course", entity="course"),
            Column("title", "Title"),
            Column("sections", "Sections", "count"),
            Column("terms", "Terms", "count"),
            *_DFW_COLS,
        ),
        _instructor_history,
        instructor_level=True,
    ),
    Analysis(
        "equity_gap",
        "Equity gap by student group",
        "D, F or withdrawal rate in a course, or in a major's registrations, "
        "by student group "
        "(first-generation, Pell, residency, entry cohort, entry type), with the "
        "gap in "
        "points against a reference group.",
        (
            Param("course", "Course", "course"),
            _P_MAJOR,
            Param(
                "group",
                "Student group",
                "choice",
                default="first_generation",
                choices=GROUPS,
                choice_labels=GROUP_LABELS,
            ),
        ),
        (
            "final_grades.grade",
            "sections.course_id",
            "students.first_generation",
            "students.pell_recipient",
            "students.residency",
            "students.entry_term",
            "students.entry_type",
            "student_term_records.program_code",
        ),
        (
            Column("group", "Group"),
            Column("students", "Students", "count"),
            Column("graded", "Graded registrations", "count"),
            Column("dfw", "D, F, or W", "count"),
            Column("dfw_rate", "D, F or withdrawal rate (%)", "pct"),
            Column("gap_points", "Gap (points)", "points"),
        ),
        _equity_gap,
    ),
    Analysis(
        "headcount_growth",
        "Headcount by major over time",
        "Each major's headcount in two terms and the growth between them, ranked.",
        (
            Param("start_term", "From term", "term", default="202110"),
            Param("end_term", "To term", "term", default="202610"),
            _P_MAJOR,
            _P_COLLEGE,
            Param(
                "order",
                "Order",
                "choice",
                default="fastest_first",
                choices=("fastest_first", "slowest_first"),
                choice_labels={
                    "fastest_first": "fastest growth first",
                    "slowest_first": "slowest growth first",
                },
                shown="Ranked: {}",
            ),
            Param(
                "min_start",
                "Minimum starting headcount to rank",
                "choice",
                default=40,
                choices=(10, 20, 40, 100),
                shown="Only majors with at least {} students at the start",
            ),
            _top_param(10),
        ),
        (
            "student_term_records.program_code",
            "student_term_records.term_code",
            "academic_programs.major_code",
        ),
        (
            Column("major", "Major code", entity="major"),
            Column("major_name", "Major"),
            Column("start_term", "From term"),
            Column("start_headcount", "Headcount then", "count"),
            Column("end_term", "To term"),
            Column("end_headcount", "Headcount now", "count"),
            Column("change", "Change", "count"),
            Column("growth", "Growth (%)", "pct"),
        ),
        _headcount_growth,
    ),
    Analysis(
        "enrollment_by_term",
        "Enrollment by term",
        "Students enrolled in each term, new and continuing, optionally for one major, "
        "one college, or one season.",
        (_P_MAJOR, _P_COLLEGE, _P_SEASON),
        (
            "student_term_records.term_code",
            "student_term_records.program_code",
            "students.entry_term",
        ),
        (
            *_TERM_COLS,
            Column("students", "Students enrolled", "count"),
            Column("new_students", "New entrants", "count"),
            Column("continuing", "Continuing", "count"),
        ),
        _enrollment_by_term,
    ),
    Analysis(
        "continuing_registration_change",
        "Continuing registration change",
        "Continuing students registered in a term against the same term a year earlier "
        "(spring to spring by default).",
        (Param("term", "Term", "term"),),
        (
            "section_registrations.term_code",
            "section_registrations.student_id (counted)",
            "students.entry_term",
        ),
        (
            *_TERM_COLS,
            Column("prior_term", "Prior term code"),
            Column("prior_term_name", "Prior term"),
            Column("continuing", "Continuing registered", "count"),
            Column("prior_continuing", "A year earlier", "count"),
            Column("change", "Change", "count"),
            Column("change_pct", "Change (%)", "pct"),
        ),
        _continuing_change,
    ),
    Analysis(
        "withdrawal_by_modality",
        "Withdrawal rate by modality",
        "W grades over graded registrations, online against in person (and hybrid), by "
        "term, with the online gap in points.",
        (
            Param("term", "Term", "term"),
            Param(
                "order",
                "Order",
                "choice",
                default="by_term",
                choices=("by_term", "largest_gap"),
                choice_labels={
                    "by_term": "in term order",
                    "largest_gap": "largest online gap first",
                },
                shown="Shown {}",
            ),
            _top_param(None),
        ),
        (
            "final_grades.grade",
            "sections.modality",
            "sections.term_code",
            "courses.grade_mode",
            "academic_periods.season",
        ),
        (
            *_TERM_COLS,
            Column("online_w", "Online W", "count"),
            Column("online_graded", "Online graded", "count"),
            Column("online_rate", "Online W rate (%)", "pct"),
            Column("in_person_w", "In-person W", "count"),
            Column("in_person_graded", "In-person graded", "count"),
            Column("in_person_rate", "In-person W rate (%)", "pct"),
            Column("hybrid_rate", "Hybrid W rate (%)", "pct"),
            Column("gap_points", "Online gap (points)", "points"),
        ),
        _withdrawal_by_modality,
    ),
    Analysis(
        "withdrawal_by_course_modality",
        "Withdrawal rate by course and teaching mode",
        "Each course's W rate in its online sections against its in-person "
        "sections (fall and spring terms), ranked by the online rate.",
        (
            Param("subject", "Subject", "subject"),
            Param(
                "min_online",
                "Minimum online students to rank",
                "choice",
                default=30,
                choices=(10, 20, 30, 50),
                shown="Only courses with at least {} students online",
            ),
            _order_param("highest_first"),
            _top_param(10),
        ),
        (
            "final_grades.grade",
            "sections.modality",
            "sections.course_id",
            "sections.term_code",
            "courses.grade_mode",
            "courses.subject_code",
            "academic_periods.season",
        ),
        (
            Column("course", "Course", entity="course"),
            Column("title", "Title"),
            Column("online_students", "Online students", "count"),
            Column("online_w", "Online W", "count"),
            Column("online_graded", "Online graded", "count"),
            Column("online_rate", "Online W rate (%)", "pct"),
            Column("in_person_rate", "In-person W rate (%)", "pct"),
            Column("gap_points", "Online gap (points)", "points"),
        ),
        _withdrawal_by_course_modality,
    ),
    Analysis(
        "standing_by_major",
        "Probation and suspension rates by major",
        "Share of each major's students on academic probation or suspension, "
        "counted once "
        "per term, ranked.",
        (
            Param("term", "Term", "term"),
            _P_COLLEGE,
            _P_MAJOR,
            _order_param("highest_first"),
            _top_param(10),
        ),
        (
            "academic_standings.standing",
            "academic_standings.term_code",
            "student_term_records.program_code",
        ),
        (
            Column("major", "Major code", entity="major"),
            Column("major_name", "Major"),
            Column("records", "Students (once per term)", "count"),
            Column("probation", "On probation", "count"),
            Column("probation_rate", "Probation rate (%)", "pct"),
            Column("suspension", "Suspended", "count"),
            Column("suspension_rate", "Suspension rate (%)", "pct"),
        ),
        _standing_by_major,
    ),
    Analysis(
        "graduations",
        "Graduation counts by major and year",
        "Graduates by major (ranked) or by academic year.",
        (
            _P_MAJOR,
            _P_COLLEGE,
            Param("academic_year", "Academic year", "academic_year"),
            Param(
                "group_by",
                "Group by",
                "choice",
                default="major",
                choices=("major", "year"),
                choice_labels={"major": "major", "year": "academic year"},
                shown="Grouped by {}",
            ),
            _top_param(10),
        ),
        (
            "student_academic_programs.status",
            "student_academic_programs.end_term",
            "student_academic_programs.program_code",
            "academic_periods.academic_year",
        ),
        (
            Column("major", "Major code", entity="major"),
            Column("major_name", "Major"),
            Column("academic_year", "Academic year"),
            Column("graduates", "Graduates", "count"),
        ),
        _graduations,
    ),
    Analysis(
        "holds_by_office",
        "Holds by office",
        "Holds and the students they affect, by responsible office, with amounts owed.",
        (
            Param(
                "active_only",
                "Active holds only",
                "choice",
                default="no",
                choices=("yes", "no"),
                shown="{}",
                shown_labels={"yes": "Active holds only", "no": "All holds"},
            ),
            Param("term", "Term placed", "term"),
            Param("category", "Category", "category"),
        ),
        (
            "person_holds.responsible_office",
            "person_holds.category",
            "person_holds.amount",
            "person_holds.term_code",
            "person_holds.end_date",
        ),
        (
            Column("office", "Office"),
            Column("holds", "Holds", "count"),
            Column("students", "Students", "count"),
            Column("total_amount", "Total owed ($)", "money"),
            Column("average_amount", "Average owed ($)", "money"),
        ),
        _holds_by_office,
    ),
    Analysis(
        "advising_coverage",
        "Advising appointment coverage by major",
        "Share of each major's enrolled students with a completed advising "
        "appointment in "
        "a term, ranked (lowest coverage first by default).",
        (
            Param("term", "Term", "advising_term"),
            _P_COLLEGE,
            _P_MAJOR,
            _order_param("lowest_first"),
            _top_param(10),
        ),
        (
            "student_term_records.program_code",
            "student_term_records.term_code",
            "student_appointments.term_code",
            "student_appointments.status",
        ),
        (
            Column("major", "Major code", entity="major"),
            Column("major_name", "Major"),
            Column("term_name", "Term"),
            Column("students", "Students enrolled", "count"),
            Column("advised", "With a completed appointment", "count"),
            Column("coverage", "Coverage (%)", "pct"),
        ),
        _advising_coverage,
    ),
    Analysis(
        "credit_hours_by_term",
        "Credit hours by term",
        "Credit hours attempted and earned in each term, optionally for one major, one "
        "college, or one season.",
        (_P_MAJOR, _P_COLLEGE, _P_SEASON),
        (
            "student_term_records.attempted_hours",
            "student_term_records.earned_hours",
            "student_term_records.term_code",
            "student_term_records.program_code",
        ),
        (
            *_TERM_COLS,
            Column("students", "Students enrolled", "count"),
            Column("attempted_hours", "Hours attempted", "hours"),
            Column("earned_hours", "Hours earned", "hours"),
            Column("average_attempted", "Average hours attempted", "average"),
        ),
        _credit_hours,
    ),
    MEASURE_BY_GROUP,
)

ANALYSIS_BY_ID: dict[str, Analysis] = {a.id: a for a in ANALYSES}


# --- the catalog: analyses + the allowed values of this database ---------------


class Catalog:
    """The analyses together with the allowed values of one school database."""

    def __init__(self, vocab: Vocab) -> None:
        self.vocab = vocab
        self.analyses = ANALYSIS_BY_ID
        self._hash: str | None = None
        # Course titles, major names, and instructor names, masked before the
        # refusal check so a title such as "Theories of Counseling" is not
        # read as a question about counseling.
        self.title_names: tuple[str, ...] = tuple(
            dict.fromkeys(
                [
                    *vocab.courses.values(),
                    *vocab.majors.values(),
                    *vocab.instructors.values(),
                ]
            )
        )

        # Every name the catalog knows, kept when a question is masked for
        # person names (explore/privacy.py ``mask_names``): the titles above,
        # colleges, subjects and terms.
        self.known_names: tuple[str, ...] = tuple(
            dict.fromkeys(
                [
                    *self.title_names,
                    *vocab.colleges.values(),
                    *vocab.subjects.values(),
                    *vocab.terms.values(),
                ]
            )
        )

    def allowed(self, param: Param) -> tuple[Any, ...]:
        v = self.vocab
        if param.kind == "choice":
            return param.choices
        if param.kind == "major":
            return tuple(v.majors)
        if param.kind == "college":
            return tuple(v.colleges)
        if param.kind == "subject":
            return tuple(v.subjects)
        if param.kind == "course":
            return tuple(v.courses)
        if param.kind == "term":
            return tuple(v.terms)
        if param.kind == "advising_term":
            return tuple(t for t, s in v.term_season.items() if s != "Summer")
        if param.kind == "instructor":
            return tuple(v.instructors)
        if param.kind == "academic_year":
            return v.academic_years
        if param.kind == "category":
            return v.hold_categories
        if param.kind == "entry_cohort":
            return v.entry_cohorts
        raise ValueError(f"unknown parameter kind {param.kind!r}")

    def is_allowed(self, param: Param, value: Any) -> bool:
        if isinstance(value, (bool, float)) or not isinstance(value, (str, int)):
            return False
        # A number written as a string (or the reverse) is the same choice;
        # comparing text keeps is_allowed and normalize in agreement.
        return str(value) in {str(a) for a in self.allowed(param)}

    def normalize(self, param: Param, value: Any) -> Any:
        for allowed in self.allowed(param):
            if str(allowed) == str(value):
                return allowed
        raise AnalysisError(f"{value!r} is not an allowed {param.label.lower()}")

    def plain(self, param: Param, value: Any) -> str:
        v = self.vocab
        if param.kind == "major":
            return f"{v.majors[value]} ({value})"
        if param.kind == "college":
            return v.colleges[value]
        if param.kind == "subject":
            return f"{v.subjects[value]} ({value})"
        if param.kind == "course":
            return v.course_label(value)
        if param.kind in ("term", "advising_term"):
            return v.terms[value]
        if param.kind == "instructor":
            return v.instructor_label(value)
        if param.kind == "category":
            return str(value).replace("_", " ")
        if param.kind == "entry_cohort":
            return f"entered {value}"
        return str(param.choice_labels.get(value, value))

    def shown(self, param: Param, value: Any) -> str | None:
        """The parameter as a reader sees it in "How this was answered":
        names without their codes ("Major: Mechanical Engineering",
        "Instructor: Alicia Shelby (fictional)"), thresholds as a rule ("Only
        majors with at least 20 students"), and nothing for the number of
        rows shown. ``plain`` stays the exact record (codes included)."""
        if param.shown is None:
            return None
        v = self.vocab
        if value in param.shown_labels:
            text = param.shown_labels[value]
        elif param.kind == "major":
            text = v.majors[value]
        elif param.kind == "subject":
            text = v.subjects[value]
        elif param.kind == "instructor":
            text = f"{v.instructors.get(value, value)} (fictional)"
        else:
            text = self.plain(param, value)
        return param.shown.replace("{label}", param.label).replace("{}", text)

    def spec(self, instructors: bool = True) -> dict[str, Any]:
        """What the model planner receives: ids, titles, descriptions,
        parameter names and their allowed values, and the result columns a
        later step may take a parameter from. Values enumerated from the data
        are listed once under ``value_lists`` (value -> plain label) and a
        parameter names its list; fixed choices are listed inline."""
        value_lists: dict[str, Any] = {}
        out = []
        for analysis in ANALYSES:
            params = []
            for param in analysis.params:
                entry: dict[str, Any] = {
                    "name": param.name,
                    "label": param.label,
                    "required": param.required,
                    "default": param.default,
                }
                if param.kind == "choice":
                    entry["allowed"] = list(param.choices)
                elif param.kind == "instructor" and not instructors:
                    entry["allowed_list"] = None
                    entry["note"] = (
                        "instructor names are given to the executive and admin "
                        "roles only"
                    )
                else:
                    if param.kind not in value_lists:
                        value_lists[param.kind] = {
                            str(a): self.plain(param, a) for a in self.allowed(param)
                        }
                    entry["allowed_list"] = param.kind
                params.append(entry)
            out.append(
                {
                    "id": analysis.id,
                    "title": analysis.title,
                    "description": analysis.description,
                    "params": params,
                    "chainable_columns": {
                        c.key: c.entity for c in analysis.columns if c.entity
                    },
                }
            )
        return {"analyses": out, "value_lists": value_lists}

    @property
    def hash(self) -> str:
        """sha256 of the canonical spec, computed once per catalog."""
        if self._hash is None:
            canonical = json.dumps(self.spec(), sort_keys=True, separators=(",", ":"))
            self._hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return self._hash


_CATALOG_CACHE: dict[tuple[str, int, int], Catalog] = {}
_CATALOG_LOCK = threading.Lock()


def catalog_for(con: sqlite3.Connection, path: Path | None = None) -> Catalog:
    """The catalog for a school database, cached per file (path, size, mtime)."""
    db = (path or school_db_path()).expanduser()
    try:
        stat = db.stat()
        key = (str(db.resolve()), stat.st_size, stat.st_mtime_ns)
    except OSError:
        raise SchoolDataMissing() from None
    with _CATALOG_LOCK:
        catalog = _CATALOG_CACHE.get(key)
        if catalog is None:
            catalog = Catalog(load_vocab(con))
            _CATALOG_CACHE.clear()
            _CATALOG_CACHE[key] = catalog
        return catalog
