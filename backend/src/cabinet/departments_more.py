"""More department overviews: Admissions, Financial Aid, Advising, Academic
Affairs, Institutional Research, Career Services, Advancement, International
Student Services and Athletics.

Imported only by ``cabinet.departments`` (at its end), which owns the shared
helpers: the same ``protect()`` complementary suppression, the same minimum
group size, the same tile shape. Every builder here takes the read-only
school connection and the latest enrolled term and returns ``{"tiles",
"tables"}`` of aggregates over tables the school database already has. No
student id, name or row leaves this module.

Table kinds (each protects every student count in it):

- ``_counts``: one count per row.
- ``_matrix``: counts in a grid; ``protect`` runs over the whole grid.
- ``_rates``: a count and a share. The share is withheld whenever either the
  group or the part is withheld, so a shown share never reveals a small cell.
- ``_valued``: a count and one measure (a median salary, an average GPA)
  withheld with its count.
"""

# ruff: noqa: E501  (the tile notes and table titles are sentences)
from __future__ import annotations

import sqlite3
import statistics
from typing import Any

from cabinet.counseling import MINIMUM_CELL_SIZE
from cabinet.departments import (
    WITHHELD,
    _count,
    _money,
    _share,
    _shown,
    _tile,
    protect,
)

Rows = list[tuple[str, int]]


def _column(key: str, label: str) -> dict[str, str]:
    return {"key": key, "label": label}


def _counts(
    key: str, title: str, first: str, count_label: str, rows: Rows
) -> dict[str, Any]:
    mask = protect([[n] for _, n in rows])
    return {
        "key": key,
        "title": title,
        "columns": [_column("group", first), _column("students", count_label)],
        "rows": [
            {"group": label, "students": _shown(n, w)}
            for (label, n), (w,) in zip(rows, mask, strict=True)
        ],
    }


def _matrix(
    key: str,
    title: str,
    first: str,
    columns: list[str],
    rows: list[tuple[str, list[int]]],
) -> dict[str, Any]:
    mask = protect([cells for _, cells in rows])
    return {
        "key": key,
        "title": title,
        "columns": [_column("group", first)]
        + [_column(f"c{i}", label) for i, label in enumerate(columns)],
        "rows": [
            {
                "group": label,
                **{f"c{i}": _shown(n, row_mask[i]) for i, n in enumerate(cells)},
            }
            for (label, cells), row_mask in zip(rows, mask, strict=True)
        ],
    }


def _rates(
    key: str,
    title: str,
    first: str,
    count_label: str,
    rate_label: str,
    rows: list[tuple[str, int, int]],
) -> dict[str, Any]:
    """Rows of (label, group size, part of it); shows the size and the share."""
    mask = protect([[whole, part] for _, whole, part in rows])
    out = []
    for (label, whole, part), (whole_hidden, part_hidden) in zip(
        rows, mask, strict=True
    ):
        hidden = whole_hidden or part_hidden
        out.append(
            {
                "group": label,
                "students": _shown(whole, whole_hidden),
                "rate": WITHHELD if hidden else _share(part, whole),
            }
        )
    return {
        "key": key,
        "title": title,
        "columns": [
            _column("group", first),
            _column("students", count_label),
            _column("rate", rate_label),
        ],
        "rows": out,
    }


def _valued(
    key: str,
    title: str,
    first: str,
    count_label: str,
    value_label: str,
    rows: list[tuple[str, int, str]],
) -> dict[str, Any]:
    mask = protect([[n] for _, n, _ in rows])
    return {
        "key": key,
        "title": title,
        "columns": [
            _column("group", first),
            _column("students", count_label),
            _column("value", value_label),
        ],
        "rows": [
            {
                "group": label,
                "students": _shown(n, w),
                "value": WITHHELD if w else value,
            }
            for (label, n, value), (w,) in zip(rows, mask, strict=True)
        ],
    }


# --- shared reads ---------------------------------------------------------------

LEVELS = "CASE r.class_level WHEN 'Freshman' THEN 1 WHEN 'Sophomore' THEN 2 WHEN 'Junior' THEN 3 ELSE 4 END"
ENROLLED = (
    "FROM student_term_records r JOIN student_term_enrollment e"
    " ON e.student_id = r.student_id AND e.term_code = r.term_code"
    " AND e.status = 'enrolled'"
    " JOIN students s ON s.student_id = r.student_id"
    " JOIN academic_programs ap ON ap.program_code = r.program_code"
    " JOIN colleges c ON c.college_code = ap.college_code"
)


def _fall_terms(con: sqlite3.Connection, term: str) -> list[tuple[str, str]]:
    """(code, name) of every fall term up to the latest enrolled term."""
    return [
        (str(code), str(name))
        for code, name in con.execute(
            "SELECT term_code, name FROM academic_periods WHERE season = 'Fall'"
            " AND sequence <= (SELECT sequence FROM academic_periods WHERE term_code = ?)"
            " ORDER BY sequence",
            (term,),
        )
    ]


def _latest_fall(con: sqlite3.Connection, term: str) -> tuple[str, str]:
    return _fall_terms(con, term)[-1]


def _enrolled(con: sqlite3.Connection, term: str) -> int:
    (n,) = con.execute(
        "SELECT COUNT(*) FROM student_term_enrollment WHERE term_code = ?"
        " AND status = 'enrolled'",
        (term,),
    ).fetchone()
    return int(n)


def _by_college(
    con: sqlite3.Connection, term: str, part_sql: str
) -> list[tuple[str, int, int]]:
    """(college, enrolled, of them matching ``part_sql``) for the term."""
    return [
        (str(name), int(whole), int(part or 0))
        for name, whole, part in con.execute(
            f"SELECT c.name, COUNT(*), SUM({part_sql}) {ENROLLED}"
            " WHERE r.term_code = ? GROUP BY c.college_code ORDER BY c.name",
            (term,),
        )
    ]


def _graduate_college_sql() -> str:
    """Graduates with the college of the program they graduated from."""
    return (
        " JOIN student_academic_programs sp ON sp.student_id = s.student_id"
        " AND sp.status = 'graduated'"
        " JOIN academic_programs ap ON ap.program_code = sp.program_code"
        " JOIN colleges c ON c.college_code = ap.college_code"
    )


RESIDENCY = {
    "in_state": "In state",
    "out_of_state": "Out of state",
    "international": "International",
}


# --- Admissions -----------------------------------------------------------------


def admissions(con: sqlite3.Connection, term: str) -> dict[str, Any]:
    fall, fall_name = _latest_fall(con, term)
    total, first_time, transfer, outside = con.execute(
        "SELECT COUNT(*), SUM(entry_type = 'first_time'), SUM(entry_type = 'transfer'),"
        " SUM(residency != 'in_state') FROM students WHERE entry_term = ?",
        (fall,),
    ).fetchone()
    tiles = [
        _tile(
            "entering_class",
            f"New students, {fall_name}",
            _count(int(total)),
            "First-time and transfer students who entered this fall.",
        ),
        _tile(
            "first_time",
            "First-time students",
            _count(int(first_time or 0)),
            f"Of the {fall_name} entering class.",
        ),
        _tile(
            "transfer",
            "Transfer students",
            _count(int(transfer or 0)),
            f"Of the {fall_name} entering class.",
        ),
        _tile(
            "outside_state",
            "From outside the state",
            _share(int(outside or 0), int(total)),
            "Out-of-state and international students, as a share of the entering class.",
        ),
    ]
    falls = _fall_terms(con, term)
    by_type: list[tuple[str, list[int]]] = []
    by_residency: list[tuple[str, list[int]]] = []
    for code, name in falls:
        by_type.append(
            (
                name,
                [
                    int(n or 0)
                    for n in con.execute(
                        "SELECT SUM(entry_type = 'first_time'), SUM(entry_type = 'transfer')"
                        " FROM students WHERE entry_term = ?",
                        (code,),
                    ).fetchone()
                ],
            )
        )
        by_residency.append(
            (
                name,
                [
                    int(n or 0)
                    for n in con.execute(
                        "SELECT SUM(residency = 'in_state'), SUM(residency = 'out_of_state'),"
                        " SUM(residency = 'international') FROM students WHERE entry_term = ?",
                        (code,),
                    ).fetchone()
                ],
            )
        )
    college_rows = [
        (str(name), [int(a or 0), int(b or 0)])
        for name, a, b in con.execute(
            "SELECT c.name, SUM(s.entry_type = 'first_time'), SUM(s.entry_type = 'transfer')"
            " FROM students s JOIN student_academic_programs sp"
            " ON sp.student_id = s.student_id AND sp.start_term = s.entry_term"
            " JOIN academic_programs ap ON ap.program_code = sp.program_code"
            " JOIN colleges c ON c.college_code = ap.college_code"
            " WHERE s.entry_term = ? GROUP BY c.college_code ORDER BY c.name",
            (fall,),
        )
    ]
    return {
        "tiles": tiles,
        "tables": [
            _matrix(
                "entering_by_type",
                "Entering classes by admit type",
                "Fall term",
                ["First-time", "Transfer"],
                by_type,
            ),
            _matrix(
                "entering_by_residency",
                "Entering classes by residency",
                "Fall term",
                list(RESIDENCY.values()),
                by_residency,
            ),
            _matrix(
                "entering_by_college",
                f"{fall_name} entering class by college",
                "College",
                ["First-time", "Transfer"],
                college_rows,
            ),
        ],
    }


# --- Financial Aid --------------------------------------------------------------


def financial_aid(con: sqlite3.Connection, term: str) -> dict[str, Any]:
    enrolled = _enrolled(con, term)
    pell, first_gen_pell, pell_hold = con.execute(
        "SELECT SUM(s.pell_recipient), SUM(s.pell_recipient AND s.first_generation),"
        " SUM(s.pell_recipient AND EXISTS (SELECT 1 FROM person_holds h"
        " WHERE h.student_id = s.student_id AND h.category = 'financial'"
        " AND h.end_date IS NULL))"
        " FROM student_term_enrollment e JOIN students s ON s.student_id = e.student_id"
        " WHERE e.term_code = ? AND e.status = 'enrolled'",
        (term,),
    ).fetchone()
    (financial_holds,) = con.execute(
        "SELECT COUNT(DISTINCT student_id) FROM person_holds"
        " WHERE category = 'financial' AND end_date IS NULL"
    ).fetchone()
    pell = int(pell or 0)
    tiles = [
        _tile(
            "pell_students",
            "Pell recipients enrolled",
            _count(pell),
            "Enrolled this term and awarded a Pell grant.",
        ),
        _tile(
            "pell_share",
            "Pell share of enrolled students",
            _share(pell, enrolled),
            "Pell recipients as a share of everyone enrolled this term.",
        ),
        _tile(
            "pell_first_gen",
            "Pell recipients who are first-generation",
            _count(int(first_gen_pell or 0)),
            "Both Pell-eligible and first in their family to attend college.",
        ),
        _tile(
            "pell_with_hold",
            "Pell recipients with an open account hold",
            _count(int(pell_hold or 0)),
            f"Of {_count(int(financial_holds))} students with an open account hold in all.",
        ),
    ]
    levels = [
        (str(level), int(whole), int(part or 0))
        for level, whole, part in con.execute(
            f"SELECT r.class_level, COUNT(*), SUM(s.pell_recipient) {ENROLLED}"
            f" WHERE r.term_code = ? GROUP BY r.class_level ORDER BY {LEVELS}",
            (term,),
        )
    ]
    gen = [
        (
            "First-generation" if g else "Not first-generation",
            [int(a or 0), int(b or 0)],
        )
        for g, a, b in con.execute(
            f"SELECT s.first_generation, SUM(s.pell_recipient), SUM(NOT s.pell_recipient) {ENROLLED}"
            " WHERE r.term_code = ? GROUP BY s.first_generation ORDER BY s.first_generation DESC",
            (term,),
        )
    ]
    return {
        "tiles": tiles,
        "tables": [
            _rates(
                "pell_by_level",
                "Pell recipients by class level",
                "Class level",
                "Enrolled",
                "Pell share",
                levels,
            ),
            _rates(
                "pell_by_college",
                "Pell recipients by college",
                "College",
                "Enrolled",
                "Pell share",
                _by_college(con, term, "s.pell_recipient"),
            ),
            _matrix(
                "pell_by_first_gen",
                "Pell status by first-generation status",
                "Group",
                ["Pell recipients", "Not Pell"],
                gen,
            ),
        ],
    }


# --- Advising and Student Success ----------------------------------------------


def advising(con: sqlite3.Connection, term: str) -> dict[str, Any]:
    enrolled = _enrolled(con, term)
    covered = _advised_by_college(con, term)
    advised = sum(part for _, _, part in covered)
    appointments = con.execute(
        "SELECT COUNT(DISTINCT student_id), SUM(status = 'completed'), SUM(status = 'no_show'),"
        " COUNT(*) FROM student_appointments WHERE term_code = ?",
        (term,),
    ).fetchone()
    met, completed, no_show, booked = (int(v or 0) for v in appointments)
    (changed,) = con.execute(
        "SELECT COUNT(DISTINCT student_id) FROM student_academic_programs WHERE status = 'changed'"
    ).fetchone()
    tiles = [
        _tile(
            "advised_share",
            "Enrolled students with an advisor",
            _share(advised, enrolled),
            "Students with a current primary advisor, as a share of those enrolled this term.",
        ),
        _tile(
            "met_advisor",
            "Students who met an advisor this term",
            _count(met),
            "At least one advising appointment recorded for the term.",
        ),
        _tile(
            "no_show_share",
            "Appointments missed",
            _share(no_show, booked),
            "No-shows as a share of the term's booked appointments.",
        ),
        _tile(
            "major_changers",
            "Students who have changed major",
            _count(int(changed)),
            "Over the six years of records.",
        ),
    ]
    kinds = [
        "registration_advising",
        "academic_planning",
        "academic_recovery",
        "degree_audit",
    ]
    labels = {
        "registration_advising": "Registration advising",
        "academic_planning": "Academic planning",
        "academic_recovery": "Academic recovery",
        "degree_audit": "Degree audit",
    }
    appointment_rows = []
    for kind in kinds:
        counts = con.execute(
            "SELECT SUM(status = 'completed'), SUM(status = 'no_show'), SUM(status = 'cancelled')"
            " FROM student_appointments WHERE term_code = ? AND appointment_type = ?",
            (term, kind),
        ).fetchone()
        appointment_rows.append((labels[kind], [int(v or 0) for v in counts]))
    groups = []
    for label, column, value in (
        ("First-generation", "first_generation", 1),
        ("Not first-generation", "first_generation", 0),
        ("Pell recipients", "pell_recipient", 1),
        ("Not Pell", "pell_recipient", 0),
        ("Transfer students", "entry_type", "transfer"),
        ("First-time students", "entry_type", "first_time"),
    ):
        whole, left = con.execute(
            f"SELECT COUNT(*), SUM(enrollment_status IN ('stopped_out', 'withdrawn'))"
            f" FROM students WHERE {column} = ?",
            (value,),
        ).fetchone()
        groups.append((label, int(whole), int(left or 0)))
    left_college = [
        (str(name), int(n))
        for name, n in con.execute(
            "SELECT c.name, COUNT(DISTINCT sp.student_id) FROM student_academic_programs sp"
            " JOIN academic_programs ap ON ap.program_code = sp.program_code"
            " JOIN colleges c ON c.college_code = ap.college_code"
            " WHERE sp.status = 'changed' GROUP BY c.college_code ORDER BY c.name"
        )
    ]
    return {
        "tiles": tiles,
        "tables": [
            _rates(
                "advising_coverage",
                "Advising coverage by college",
                "College",
                "Enrolled",
                "With an advisor",
                covered,
            ),
            _matrix(
                "appointments",
                "This term's appointments by type",
                "Appointment type",
                ["Completed", "No-show", "Cancelled"],
                appointment_rows,
            ),
            _rates(
                "left_without_degree",
                "Left without a degree (stopped out or withdrew), by group",
                "Group",
                "Students",
                "Left",
                groups,
            ),
            _counts(
                "major_changes",
                "Students who changed major, by the college they left",
                "College",
                "Students",
                left_college,
            ),
        ],
    }


def _advised_by_college(
    con: sqlite3.Connection, term: str
) -> list[tuple[str, int, int]]:
    return _by_college(
        con,
        term,
        "EXISTS (SELECT 1 FROM student_advisor_relationships a"
        " WHERE a.student_id = r.student_id AND a.start_term <= r.term_code"
        " AND (a.end_term IS NULL OR a.end_term >= r.term_code))",
    )


# --- Academic Affairs (Provost) --------------------------------------------------

REGISTRATIONS = (
    "FROM final_grades g JOIN section_registrations sr ON sr.registration_id = g.registration_id"
    " JOIN grade_scale gs ON gs.grade = g.grade"
    " JOIN sections sec ON sec.section_id = sr.section_id"
    " JOIN courses co ON co.course_id = sec.course_id"
    " JOIN subjects sub ON sub.subject_code = co.subject_code"
    " JOIN colleges c ON c.college_code = sub.college_code"
)


def academic_affairs(con: sqlite3.Connection, term: str) -> dict[str, Any]:
    regs, dfw = con.execute(
        f"SELECT COUNT(*), SUM(gs.is_dfw) {REGISTRATIONS} WHERE sr.term_code = ?",
        (term,),
    ).fetchone()
    sections, average = con.execute(
        "SELECT COUNT(*), AVG(n) FROM (SELECT sec.section_id, COUNT(sr.registration_id) AS n"
        " FROM sections sec LEFT JOIN section_registrations sr ON sr.section_id = sec.section_id"
        " WHERE sec.term_code = ? GROUP BY sec.section_id)",
        (term,),
    ).fetchone()
    employed = _employed_instructors(con, term)
    tiles = [
        _tile(
            "dfw_rate",
            "D, F or withdrawal rate this term",
            _share(int(dfw or 0), int(regs)),
            "Course registrations ending in a D, an F or a withdrawal, as a share of all registrations.",
        ),
        _tile(
            "sections",
            "Sections this term",
            f"{int(sections):,}",
            "Every section offered in the term.",
        ),
        _tile(
            "average_section",
            "Average section size",
            f"{float(average or 0):.1f}",
            "Students registered per section, withdrawals included.",
        ),
        _tile(
            "instructors",
            "Instructors teaching or on staff",
            f"{sum(n for _, n in employed):,}",
            "Employed this term. Instructors are staff, not students.",
        ),
    ]
    by_college = [
        (str(name), int(whole), int(part or 0))
        for name, whole, part in con.execute(
            f"SELECT c.name, COUNT(*), SUM(gs.is_dfw) {REGISTRATIONS}"
            " WHERE sr.term_code = ? GROUP BY c.college_code ORDER BY c.name",
            (term,),
        )
    ]
    hardest = [
        (f"{cid}  {title}", int(whole), int(part or 0))
        for cid, title, whole, part in con.execute(
            f"SELECT co.course_id, co.title, COUNT(*), SUM(gs.is_dfw) {REGISTRATIONS}"
            " GROUP BY co.course_id HAVING COUNT(*) >= 30"
            " ORDER BY 1.0 * SUM(gs.is_dfw) / COUNT(*) DESC, co.course_id LIMIT 10"
        )
    ]
    sizes = [
        {
            "group": {
                "in_person": "In person",
                "online": "Online",
                "hybrid": "Hybrid",
            }.get(str(m), str(m)),
            "sections": f"{int(n):,}",
            "average": f"{float(a or 0):.1f}",
        }
        for m, n, a in con.execute(
            "SELECT modality, COUNT(*), AVG(k) FROM (SELECT sec.section_id, sec.modality,"
            " COUNT(sr.registration_id) AS k FROM sections sec"
            " LEFT JOIN section_registrations sr ON sr.section_id = sec.section_id"
            " WHERE sec.term_code = ? GROUP BY sec.section_id) GROUP BY modality ORDER BY modality",
            (term,),
        )
    ]
    staff = [{"group": rank, "sections": f"{n:,}"} for rank, n in employed]
    return {
        "tiles": tiles,
        "tables": [
            _rates(
                "dfw_by_college",
                "D, F and withdrawal rate by college, this term",
                "College",
                "Registrations",
                "D, F or W",
                by_college,
            ),
            _rates(
                "hardest_courses",
                "Courses with the highest D, F and withdrawal rates (all terms, 30 or more registrations)",
                "Course",
                "Registrations",
                "D, F or W",
                hardest,
            ),
            {
                "key": "section_sizes",
                "title": "Sections this term, by how they meet",
                "columns": [
                    _column("group", "Format"),
                    _column("sections", "Sections"),
                    _column("average", "Average size"),
                ],
                "rows": sizes,
            },
            {
                "key": "instructors_by_rank",
                "title": "Instructors employed this term, by rank",
                "columns": [
                    _column("group", "Rank"),
                    _column("sections", "Instructors"),
                ],
                "rows": staff,
            },
        ],
    }


def _employed_instructors(con: sqlite3.Connection, term: str) -> Rows:
    return [
        (str(rank), int(n))
        for rank, n in con.execute(
            "SELECT academic_rank, COUNT(*) FROM instructors WHERE hire_term <= ?"
            " AND (leave_term IS NULL OR ? < leave_term) GROUP BY academic_rank"
            " ORDER BY COUNT(*) DESC",
            (term, term),
        )
    ]


# --- Institutional Research ------------------------------------------------------


def institutional_research(con: sqlite3.Connection, term: str) -> dict[str, Any]:
    enrolled = _enrolled(con, term)
    falls = _fall_terms(con, term)
    retention = _retention_by_cohort(con, falls)
    last = retention[-1] if retention else None
    (degrees,) = con.execute(
        "SELECT COUNT(*) FROM students WHERE enrollment_status = 'graduated' AND exit_term IN"
        " (SELECT term_code FROM academic_periods WHERE academic_year ="
        " (SELECT academic_year FROM academic_periods WHERE sequence ="
        " (SELECT MAX(sequence) FROM academic_periods WHERE season = 'Spring'"
        " AND sequence <= (SELECT sequence FROM academic_periods WHERE term_code = ?))))",
        (term,),
    ).fetchone()
    full_time = con.execute(
        "SELECT SUM(academic_load = 'full_time') FROM student_term_enrollment"
        " WHERE term_code = ? AND status = 'enrolled'",
        (term,),
    ).fetchone()[0]
    tiles = [
        _tile("enrolled", "Students enrolled this term", _count(enrolled), ""),
        _tile(
            "full_time_share",
            "Enrolled full time",
            _share(int(full_time or 0), enrolled),
            "Share of enrolled students with a full-time load.",
        ),
        _tile(
            "retention",
            "First-year retention, latest cohort",
            "—" if last is None else _share(last[2], last[1]),
            ""
            if last is None
            else f"First-time students who entered in {last[0]} and came back the next fall.",
        ),
        _tile(
            "degrees",
            "Degrees awarded, latest academic year",
            _count(int(degrees)),
            "Graduates with an end date in the academic year of the latest spring term.",
        ),
    ]
    trend = []
    for code, name in falls:
        whole, ft = con.execute(
            "SELECT COUNT(*), SUM(academic_load = 'full_time') FROM student_term_enrollment"
            " WHERE term_code = ? AND status = 'enrolled'",
            (code,),
        ).fetchone()
        trend.append((name, int(whole), int(ft or 0)))
    awarded = [
        (str(year), int(n))
        for year, n in con.execute(
            "SELECT p.academic_year, COUNT(*) FROM students s JOIN academic_periods p"
            " ON p.term_code = s.exit_term WHERE s.enrollment_status = 'graduated'"
            " GROUP BY p.academic_year ORDER BY p.academic_year"
        )
    ]
    return {
        "tiles": tiles,
        "tables": [
            _rates(
                "enrollment_trend",
                "Fall enrollment by year",
                "Fall term",
                "Enrolled",
                "Full time",
                trend,
            ),
            _rates(
                "retention_by_cohort",
                "First-year retention by entering fall cohort",
                "Entering cohort",
                "Entered",
                "Returned",
                [(n, w, p) for n, w, p in retention],
            ),
            _counts(
                "degrees_by_year",
                "Degrees awarded by academic year",
                "Academic year",
                "Graduates",
                awarded,
            ),
        ],
    }


def _retention_by_cohort(
    con: sqlite3.Connection, falls: list[tuple[str, str]], where: str = "1 = 1"
) -> list[tuple[str, int, int]]:
    """(cohort name, first-time entrants, those enrolled the next fall)."""
    codes = {code for code, _ in falls}
    out = []
    for code, name in falls:
        if str(int(code) + 100) not in codes:
            continue
        whole, back = con.execute(
            "SELECT COUNT(*), SUM(e1.status = 'enrolled') FROM students s"
            " JOIN student_profiles pr ON pr.student_id = s.student_id"
            " LEFT JOIN student_term_enrollment e1 ON e1.student_id = s.student_id"
            " AND e1.term_code = ?"
            f" WHERE s.entry_type = 'first_time' AND s.entry_term = ? AND {where}",
            (str(int(code) + 100), code),
        ).fetchone()
        out.append((name, int(whole), int(back or 0)))
    return out


# --- Career Services -------------------------------------------------------------

OUTCOMES = {
    "employed_full_time": "Employed full time",
    "employed_part_time": "Employed part time",
    "graduate_school": "Graduate or professional school",
    "military_service": "Military or volunteer service",
    "seeking": "Still seeking",
    "not_seeking": "Not seeking",
}


def _median_salary(values: list[int]) -> str:
    if len(values) < MINIMUM_CELL_SIZE:
        return WITHHELD
    return f"${int(round(statistics.median(values) / 500.0)) * 500:,}"


def careers(con: sqlite3.Connection, term: str) -> dict[str, Any]:
    answered, positive, school = con.execute(
        "SELECT COUNT(*), SUM(outcome IN ('employed_full_time', 'employed_part_time',"
        " 'graduate_school', 'military_service')), SUM(outcome = 'graduate_school')"
        " FROM first_destination"
    ).fetchone()
    salaries = [
        int(v)
        for (v,) in con.execute(
            "SELECT starting_salary FROM first_destination WHERE starting_salary IS NOT NULL"
        )
    ]
    (applicants, accepted) = con.execute(
        "SELECT COUNT(*), SUM(accepted) FROM medical_school_applications"
    ).fetchone()
    tiles = [
        _tile(
            "respondents",
            "Graduates who answered the first-destination survey",
            _count(int(answered)),
            "Bachelor's graduates six months after graduating.",
        ),
        _tile(
            "positive_outcome",
            "Employed, in school or serving",
            _share(int(positive or 0), int(answered)),
            "Share of respondents.",
        ),
        _tile(
            "median_salary",
            "Median starting salary",
            _median_salary(salaries),
            "Full-time employed respondents who reported a salary. Rounded to $500.",
        ),
        _tile(
            "medical_accepted",
            "Accepted to medical school",
            _share(int(accepted or 0), int(applicants)),
            f"Of {_count(int(applicants))} applicants.",
        ),
    ]
    outcome_rows = [
        (OUTCOMES[str(o)], int(n))
        for o, n in con.execute(
            "SELECT outcome, COUNT(*) FROM first_destination GROUP BY outcome ORDER BY COUNT(*) DESC"
        )
    ]
    by_college: dict[str, list[int]] = {}
    pay: dict[str, list[int]] = {}
    rows = con.execute(
        "SELECT c.name, f.outcome, f.starting_salary FROM first_destination f"
        " JOIN students s ON s.student_id = f.student_id" + _graduate_college_sql()
    )
    for name, outcome, salary in rows:
        entry = by_college.setdefault(str(name), [0, 0, 0])
        entry[0] += 1
        entry[1] += outcome == "employed_full_time"
        if salary is not None:
            entry[2] += 1
            pay.setdefault(str(name), []).append(int(salary))
    college_rows = sorted(by_college.items())
    mask = protect([cells for _, cells in college_rows])
    college_table = {
        "key": "outcomes_by_college",
        "title": "First destination by college",
        "columns": [
            _column("group", "College"),
            _column("students", "Respondents"),
            _column("employed", "Employed full time"),
            _column("salary", "Median starting salary"),
        ],
        "rows": [
            {
                "group": name,
                "students": _shown(cells[0], m[0]),
                "employed": WITHHELD if m[0] or m[1] else _share(cells[1], cells[0]),
                "salary": WITHHELD
                if m[0] or m[1] or m[2]
                else _median_salary(pay.get(name, [])),
            }
            for (name, cells), m in zip(college_rows, mask, strict=True)
        ],
    }
    program_rows = [
        (str(t).replace("_", " ").capitalize(), int(n))
        for t, n in con.execute(
            "SELECT program_type, COUNT(*) FROM graduate_enrollment GROUP BY program_type ORDER BY COUNT(*) DESC"
        )
    ]
    med = [
        (str(year), int(n), int(a or 0))
        for year, n, a in con.execute(
            "SELECT entering_year, COUNT(*), SUM(accepted) FROM medical_school_applications GROUP BY entering_year ORDER BY entering_year"
        )
    ]
    return {
        "tiles": tiles,
        "tables": [
            _counts(
                "first_destination",
                "What graduates did first",
                "Outcome",
                "Respondents",
                outcome_rows,
            ),
            college_table,
            _counts(
                "graduate_programs",
                "Graduates found in graduate or professional school, by program type",
                "Program",
                "Graduates",
                program_rows,
            ),
            _rates(
                "medical_school",
                "Medical school applications by entering year",
                "Entering year",
                "Applicants",
                "Accepted",
                med,
            ),
        ],
    }


# --- Advancement ---------------------------------------------------------------


def _dollars(amount: float, donors: int) -> str:
    return _money(round(amount / 100.0) * 100, donors)


def advancement(con: sqlite3.Connection, term: str) -> dict[str, Any]:
    (alumni,) = con.execute(
        "SELECT COUNT(*) FROM students WHERE enrollment_status = 'graduated'"
    ).fetchone()
    donors, total, gifts = con.execute(
        "SELECT COUNT(DISTINCT student_id), COALESCE(SUM(amount), 0), COUNT(*) FROM alumni_gifts"
    ).fetchone()
    (year,) = con.execute("SELECT MAX(fiscal_year) FROM alumni_gifts").fetchone()
    year_donors, year_total = con.execute(
        "SELECT COUNT(DISTINCT student_id), COALESCE(SUM(amount), 0) FROM alumni_gifts WHERE fiscal_year = ?",
        (year,),
    ).fetchone()
    tiles = [
        _tile(
            "alumni_donors",
            "Alumni who have given",
            _count(int(donors)),
            "Graduates with at least one gift on record.",
        ),
        _tile(
            "participation",
            "Alumni giving participation",
            _share(int(donors), int(alumni)),
            "Donors as a share of all graduates.",
        ),
        _tile(
            "year_giving",
            f"Given in fiscal {year}",
            _dollars(float(year_total), int(year_donors)),
            f"By {_count(int(year_donors))} alumni. Rounded to the nearest $100.",
        ),
        _tile(
            "average_gift",
            "Average gift",
            _money(float(total) / int(gifts), int(donors)) if gifts else "—",
            "Total given divided by the number of gifts.",
        ),
    ]
    by_year = [
        (str(y), int(d), _dollars(float(t), int(d)))
        for y, d, t in con.execute(
            "SELECT fiscal_year, COUNT(DISTINCT student_id), SUM(amount) FROM alumni_gifts GROUP BY fiscal_year ORDER BY fiscal_year"
        )
    ]
    designation = [
        (str(x).replace("_", " ").capitalize(), int(d), _dollars(float(t), int(d)))
        for x, d, t in con.execute(
            "SELECT designation, COUNT(DISTINCT student_id), SUM(amount) FROM alumni_gifts GROUP BY designation ORDER BY SUM(amount) DESC"
        )
    ]
    college = [
        (str(name), int(whole), int(part or 0))
        for name, whole, part in con.execute(
            "SELECT c.name, COUNT(*), SUM(EXISTS (SELECT 1 FROM alumni_gifts g WHERE g.student_id = s.student_id))"
            " FROM students s"
            + _graduate_college_sql()
            + " WHERE s.enrollment_status = 'graduated'"
            " GROUP BY c.college_code ORDER BY c.name"
        )
    ]
    return {
        "tiles": tiles,
        "tables": [
            _valued(
                "giving_by_year",
                "Giving by fiscal year",
                "Fiscal year",
                "Donors",
                "Given",
                by_year,
            ),
            _valued(
                "giving_by_designation",
                "Giving by designation",
                "Designation",
                "Donors",
                "Given",
                designation,
            ),
            _rates(
                "participation_by_college",
                "Giving participation by college",
                "College",
                "Graduates",
                "Have given",
                college,
            ),
        ],
    }


# --- International Student Services ----------------------------------------------


def international(con: sqlite3.Connection, term: str) -> dict[str, Any]:
    enrolled = _enrolled(con, term)
    now, full_time = con.execute(
        "SELECT COUNT(*), SUM(e.academic_load = 'full_time') FROM student_term_enrollment e"
        " JOIN students s ON s.student_id = e.student_id"
        " WHERE e.term_code = ? AND e.status = 'enrolled' AND s.residency = 'international'",
        (term,),
    ).fetchone()
    fall, fall_name = _latest_fall(con, term)
    (entering,) = con.execute(
        "SELECT COUNT(*) FROM students WHERE residency = 'international' AND entry_term = ?",
        (fall,),
    ).fetchone()
    (financial,) = con.execute(
        "SELECT COUNT(DISTINCT h.student_id) FROM person_holds h JOIN students s ON s.student_id = h.student_id"
        " WHERE s.residency = 'international' AND h.end_date IS NULL"
    ).fetchone()
    tiles = [
        _tile(
            "international_enrolled",
            "International students enrolled",
            _count(int(now)),
            "Enrolled this term.",
        ),
        _tile(
            "international_share",
            "International share of enrolled students",
            _share(int(now), enrolled),
            "",
        ),
        _tile(
            "new_international",
            f"New international students, {fall_name}",
            _count(int(entering)),
            "Entered this fall, first-time and transfer.",
        ),
        _tile(
            "international_holds",
            "International students with an open hold",
            _count(int(financial)),
            "Any office's hold still open.",
        ),
    ]
    trend = []
    for code, name in _fall_terms(con, term):
        whole, intl = con.execute(
            "SELECT COUNT(*), SUM(s.residency = 'international') FROM student_term_enrollment e"
            " JOIN students s ON s.student_id = e.student_id WHERE e.term_code = ? AND e.status = 'enrolled'",
            (code,),
        ).fetchone()
        trend.append((name, int(whole), int(intl or 0)))
    levels = [
        (str(level), int(n))
        for level, n in con.execute(
            f"SELECT r.class_level, COUNT(*) {ENROLLED} WHERE r.term_code = ? AND s.residency = 'international'"
            f" GROUP BY r.class_level ORDER BY {LEVELS}",
            (term,),
        )
    ]
    return {
        "tiles": tiles,
        "tables": [
            _rates(
                "international_by_college",
                "International students by college, this term",
                "College",
                "Enrolled",
                "International",
                _by_college(con, term, "s.residency = 'international'"),
            ),
            _rates(
                "international_trend",
                "International students each fall",
                "Fall term",
                "Enrolled",
                "International",
                trend,
            ),
            _counts(
                "international_by_level",
                "International students by class level, this term",
                "Class level",
                "Students",
                levels,
            ),
        ],
    }


# --- Athletics ---------------------------------------------------------------------


def athletics(con: sqlite3.Connection, term: str) -> dict[str, Any]:
    enrolled = _enrolled(con, term)
    groups = {}
    for flag in (1, 0):
        n, mean, good = con.execute(
            "SELECT COUNT(*), AVG(r.cumulative_gpa), SUM(st.standing = 'Good Standing')"
            " FROM student_term_records r JOIN student_profiles pr ON pr.student_id = r.student_id"
            " JOIN student_term_enrollment e ON e.student_id = r.student_id AND e.term_code = r.term_code"
            " AND e.status = 'enrolled'"
            " LEFT JOIN academic_standings st ON st.student_id = r.student_id AND st.term_code = r.term_code"
            " WHERE r.term_code = ? AND pr.athlete = ?",
            (term, flag),
        ).fetchone()
        groups[flag] = (int(n), mean, int(good or 0))
    falls = _fall_terms(con, term)
    ret_a = _retention_by_cohort(con, falls, "pr.athlete = 1")
    ret_n = _retention_by_cohort(con, falls, "pr.athlete = 0")

    def pooled(rows: list[tuple[str, int, int]]) -> tuple[int, int]:
        return sum(w for _, w, _ in rows), sum(p for _, _, p in rows)

    (aw, ap), (nw, np_) = pooled(ret_a), pooled(ret_n)
    a_n, a_gpa, a_good = groups[1]
    n_n, n_gpa, n_good = groups[0]

    def gpa(n: int, value: Any) -> str:
        return (
            WITHHELD
            if n < MINIMUM_CELL_SIZE or value is None
            else f"{float(value):.2f}"
        )

    tiles = [
        _tile(
            "athletes",
            "Athletes enrolled",
            _count(a_n),
            "Intercollegiate athletes enrolled this term.",
        ),
        _tile(
            "athlete_share",
            "Athletes as a share of enrolled students",
            _share(a_n, enrolled),
            "",
        ),
        _tile(
            "athlete_gpa",
            "Athletes' average GPA",
            gpa(a_n, a_gpa),
            f"Cumulative. Other students: {gpa(n_n, n_gpa)}.",
        ),
        _tile(
            "athlete_retention",
            "Athletes' first-year retention",
            _share(ap, aw),
            f"First-time athletes who came back the next fall. Other students: {_share(np_, nw)}.",
        ),
    ]
    compare = [
        ("Athletes", a_n, gpa(a_n, a_gpa)),
        ("Other students", n_n, gpa(n_n, n_gpa)),
    ]
    retention_rows = [
        ("Athletes", aw, ap),
        ("Other students", nw, np_),
    ]
    standing_rows = [
        ("Athletes", a_n, a_good),
        ("Other students", n_n, n_good),
    ]
    levels = [
        (str(level), int(whole), int(part or 0))
        for level, whole, part in con.execute(
            f"SELECT r.class_level, COUNT(*), SUM(pr.athlete) {ENROLLED}"
            " JOIN student_profiles pr ON pr.student_id = r.student_id"
            f" WHERE r.term_code = ? GROUP BY r.class_level ORDER BY {LEVELS}",
            (term,),
        )
    ]
    return {
        "tiles": tiles,
        "tables": [
            _valued(
                "gpa_comparison",
                "Cumulative GPA, athletes and other students",
                "Group",
                "Enrolled",
                "Average GPA",
                compare,
            ),
            _rates(
                "retention_comparison",
                "First-year retention, first-time students of every cohort",
                "Group",
                "Entered",
                "Returned",
                retention_rows,
            ),
            _rates(
                "standing_comparison",
                "Good academic standing this term",
                "Group",
                "Enrolled",
                "In good standing",
                standing_rows,
            ),
            _rates(
                "athletes_by_level",
                "Athletes by class level, this term",
                "Class level",
                "Enrolled",
                "Athletes",
                levels,
            ),
        ],
    }
