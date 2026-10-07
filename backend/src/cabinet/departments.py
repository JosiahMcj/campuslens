"""Department overviews: one aggregate page per department account.

- ``GET /departments/overview?department=<id>`` — the overview of one
  department, computed in code from the school database (the same
  read-only Demonstration University data Explore reads,
  ``CABINET_SCHOOL_DB``). A department account (finance, registrar,
  studentlife) reads its own department only: naming another is a logged
  403. The president and the admin read every department (the parameter
  picks one; default Finance). Without the school data the answer is 503
  with the same plain sentence Explore gives.

Every figure is a count, a share or a sum over a group. No student id, name
or row ever leaves this module, and any group of fewer than
``MINIMUM_CELL_SIZE`` students is withheld ("Fewer than 10"), like the
counseling aggregate. A withheld cell is never alone in a row or column of
a table (``protect``: complementary suppression), so nothing can be
recovered by subtraction. The overviews are pure functions of the school data,
so one computed overview is cached per (database file, size, mtime,
department) in this process.

``tile_snapshot`` is what the inbox stores when someone sends an alert about
one overview figure: the figure as it reads right now, recomputed here
from the data (never from the client).
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from cabinet.auth import (
    DEPARTMENT_ROLES,
    ROLE_FINANCE,
    ROLE_REGISTRAR,
    ROLE_STUDENT_LIFE,
)
from cabinet.counseling import MINIMUM_CELL_SIZE
from cabinet.explore.catalog import (
    SchoolDataMissing,
    connect_readonly,
    school_db_path,
)

router = APIRouter()

# Department id -> the name every screen shows for it.
DEPARTMENT_NAMES: dict[str, str] = {
    ROLE_FINANCE: "Finance — Student Accounts",
    ROLE_REGISTRAR: "Registrar",
    ROLE_STUDENT_LIFE: "Student Life",
}
DEPARTMENTS: tuple[str, ...] = DEPARTMENT_ROLES

WITHHELD = f"Fewer than {MINIMUM_CELL_SIZE}"

_cache: dict[tuple[str, int, int, str], dict[str, Any]] = {}
_cache_lock = threading.Lock()


def _count(n: int) -> str:
    """A student count as shown: withheld below the minimum group size."""
    return WITHHELD if n < MINIMUM_CELL_SIZE else f"{n:,}"


def protect(matrix: list[list[int]]) -> list[list[bool]]:
    """Which cells of a table of student counts to withhold.

    A cell under ``MINIMUM_CELL_SIZE`` is withheld. Then complementary
    suppression: a row or a column with exactly one withheld cell (and
    another cell beside it) could be recovered by subtracting the shown
    cells from a total shown elsewhere (a tile, the enrolled count), so the
    smallest cell still shown in that row or column is withheld too. This
    repeats until no row or column has a lone withheld cell. A list of
    groups is a one-column table: pass ``[[n] for n in counts]``.
    """
    mask = [[n < MINIMUM_CELL_SIZE for n in row] for row in matrix]
    lines: list[list[tuple[int, int]]] = [
        [(r, c) for c in range(len(row))] for r, row in enumerate(matrix)
    ]
    width = max((len(row) for row in matrix), default=0)
    lines += [
        [(r, c) for r in range(len(matrix)) if c < len(matrix[r])]
        for c in range(width)
    ]
    changed = True
    while changed:
        changed = False
        for line in lines:
            if len(line) < 2:
                continue
            hidden = [cell for cell in line if mask[cell[0]][cell[1]]]
            if len(hidden) != 1:
                continue
            shown = [cell for cell in line if not mask[cell[0]][cell[1]]]
            r, c = min(shown, key=lambda cell: matrix[cell[0]][cell[1]])
            mask[r][c] = True
            changed = True
    return mask


def _shown(n: int, withheld: bool) -> str:
    return WITHHELD if withheld else f"{n:,}"


def _money(amount: float, students: int) -> str:
    return WITHHELD if students < MINIMUM_CELL_SIZE else f"${amount:,.0f}"


def _share(part: int, whole: int) -> str:
    if whole < MINIMUM_CELL_SIZE or part < MINIMUM_CELL_SIZE:
        return WITHHELD
    return f"{100 * part / whole:.0f}%"


def _tile(key: str, label: str, display: str, note: str) -> dict[str, str]:
    return {"key": key, "label": label, "display": display, "note": note}


def _latest_term(con: sqlite3.Connection) -> tuple[str, str]:
    row = con.execute(
        "SELECT e.term_code, p.name FROM student_term_enrollment e"
        " JOIN academic_periods p ON p.term_code = e.term_code"
        " WHERE e.status = 'enrolled' ORDER BY p.sequence DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise SchoolDataMissing()
    return str(row[0]), str(row[1])


def _finance(con: sqlite3.Connection, term: str) -> dict[str, Any]:
    students, holds, balance = con.execute(
        "SELECT COUNT(DISTINCT student_id), COUNT(*), COALESCE(SUM(amount), 0)"
        " FROM person_holds WHERE category = 'financial' AND end_date IS NULL"
    ).fetchone()
    placed = con.execute(
        "SELECT COUNT(*), COUNT(DISTINCT student_id) FROM person_holds"
        " WHERE category = 'financial' AND term_code = ?",
        (term,),
    ).fetchone()
    average = balance / students if students else 0.0
    tiles = [
        _tile(
            "open_account_holds",
            "Students with an open account hold",
            _count(students),
            "A balance hold that has not been cleared. It blocks registration.",
        ),
        _tile(
            "open_balance",
            "Balance on open account holds",
            _money(balance, students),
            f"Across {holds:,} open holds." if students >= MINIMUM_CELL_SIZE else "",
        ),
        _tile(
            "average_balance",
            "Average balance per student with a hold",
            _money(average, students),
            "The open balance divided by the students who owe it.",
        ),
        _tile(
            "placed_this_term",
            "Account holds placed this term",
            WITHHELD if placed[1] < MINIMUM_CELL_SIZE else f"{placed[0]:,}",
            "Placed during the current term, cleared or not.",
        ),
    ]
    offices = con.execute(
        "SELECT responsible_office, COUNT(*), COUNT(DISTINCT student_id),"
        " COALESCE(SUM(amount), 0) FROM person_holds WHERE end_date IS NULL"
        " GROUP BY responsible_office ORDER BY COUNT(DISTINCT student_id) DESC"
    ).fetchall()
    office_mask = protect([[int(row[2])] for row in offices])
    by_office = []
    for (office, n_holds, n_students, amount), (withheld,) in zip(
        offices, office_mask, strict=True
    ):
        by_office.append(
            {
                "office": office,
                "students": _shown(int(n_students), withheld),
                "holds": WITHHELD if withheld else f"{n_holds:,}",
                "balance": "—"
                if not amount
                else (WITHHELD if withheld else f"${amount:,.0f}"),
            }
        )
    band_counts: list[tuple[str, int]] = []
    for label, low, high in (
        ("Under $500", 0, 500),
        ("$500 to $1,999", 500, 2000),
        ("$2,000 to $4,999", 2000, 5000),
        ("$5,000 or more", 5000, None),
    ):
        sql = (
            "SELECT COUNT(*) FROM (SELECT student_id, SUM(amount) AS owed"
            " FROM person_holds WHERE category = 'financial' AND end_date IS NULL"
            " GROUP BY student_id) WHERE owed >= ?"
        )
        args: list[float] = [low]
        if high is not None:
            sql += " AND owed < ?"
            args.append(high)
        (n,) = con.execute(sql, args).fetchone()
        band_counts.append((label, int(n)))
    band_mask = protect([[n] for _, n in band_counts])
    bands = [
        {"band": label, "students": _shown(n, withheld)}
        for (label, n), (withheld,) in zip(band_counts, band_mask, strict=True)
    ]
    return {
        "tiles": tiles,
        "tables": [
            {
                "key": "open_holds_by_office",
                "title": "Open holds by responsible office",
                "columns": [
                    {"key": "office", "label": "Office"},
                    {"key": "students", "label": "Students"},
                    {"key": "holds", "label": "Open holds"},
                    {"key": "balance", "label": "Balance"},
                ],
                "rows": by_office,
            },
            {
                "key": "balance_bands",
                "title": "Students with an open account hold, by balance owed",
                "columns": [
                    {"key": "band", "label": "Balance owed"},
                    {"key": "students", "label": "Students"},
                ],
                "rows": bands,
            },
        ],
    }


def _registrar(con: sqlite3.Connection, term: str) -> dict[str, Any]:
    enrolled, full_time = con.execute(
        "SELECT COUNT(*), SUM(academic_load = 'full_time')"
        " FROM student_term_enrollment WHERE term_code = ? AND status = 'enrolled'",
        (term,),
    ).fetchone()
    (registrar_holds,) = con.execute(
        "SELECT COUNT(DISTINCT student_id) FROM person_holds"
        " WHERE category = 'registrar' AND end_date IS NULL"
    ).fetchone()
    (not_good,) = con.execute(
        "SELECT COUNT(*) FROM academic_standings"
        " WHERE term_code = ? AND standing != 'Good Standing'",
        (term,),
    ).fetchone()
    tiles = [
        _tile("enrolled", "Students enrolled this term", _count(int(enrolled)), ""),
        _tile(
            "full_time_share",
            "Enrolled full time",
            _share(int(full_time or 0), int(enrolled)),
            "Share of this term's enrolled students carrying a full-time load.",
        ),
        _tile(
            "registrar_holds",
            "Students with an open Registrar hold",
            _count(int(registrar_holds)),
            "Missing documents or records the Registrar needs before registration.",
        ),
        _tile(
            "not_good_standing",
            "Students not in good standing",
            _count(int(not_good)),
            "On probation, continued probation or suspension at the end of the term.",
        ),
    ]
    level_rows = [
        (str(level), int(n))
        for level, n in con.execute(
            "SELECT r.class_level, COUNT(*) FROM student_term_records r"
            " JOIN student_term_enrollment e ON e.student_id = r.student_id"
            " AND e.term_code = r.term_code"
            " WHERE r.term_code = ? AND e.status = 'enrolled'"
            " GROUP BY r.class_level ORDER BY CASE r.class_level"
            " WHEN 'Freshman' THEN 1 WHEN 'Sophomore' THEN 2"
            " WHEN 'Junior' THEN 3 WHEN 'Senior' THEN 4 ELSE 5 END",
            (term,),
        )
    ]
    levels = [
        {"level": level, "students": _shown(n, withheld)}
        for (level, n), (withheld,) in zip(
            level_rows, protect([[n] for _, n in level_rows]), strict=True
        )
    ]
    standing_rows = [
        (str(standing), int(n))
        for standing, n in con.execute(
            "SELECT standing, COUNT(*) FROM academic_standings WHERE term_code = ?"
            " GROUP BY standing ORDER BY COUNT(*) DESC",
            (term,),
        )
    ]
    standings = [
        {"standing": standing, "students": _shown(n, withheld)}
        for (standing, n), (withheld,) in zip(
            standing_rows, protect([[n] for _, n in standing_rows]), strict=True
        )
    ]
    return {
        "tiles": tiles,
        "tables": [
            {
                "key": "enrolled_by_level",
                "title": "Enrolled this term, by class level",
                "columns": [
                    {"key": "level", "label": "Class level"},
                    {"key": "students", "label": "Students"},
                ],
                "rows": levels,
            },
            {
                "key": "standing",
                "title": "Academic standing this term",
                "columns": [
                    {"key": "standing", "label": "Standing"},
                    {"key": "students", "label": "Students"},
                ],
                "rows": standings,
            },
        ],
    }


def _student_life(con: sqlite3.Connection, term: str) -> dict[str, Any]:
    enrolled, on_campus, first_gen = con.execute(
        "SELECT COUNT(*), SUM(e.housing = 'on_campus'), SUM(s.first_generation)"
        " FROM student_term_enrollment e JOIN students s"
        " ON s.student_id = e.student_id"
        " WHERE e.term_code = ? AND e.status = 'enrolled'",
        (term,),
    ).fetchone()
    (stopped_out,) = con.execute(
        "SELECT COUNT(*) FROM student_term_enrollment"
        " WHERE term_code = ? AND status = 'stopped_out'",
        (term,),
    ).fetchone()
    booked, no_show = con.execute(
        "SELECT SUM(status != 'cancelled'), SUM(status = 'no_show')"
        " FROM student_appointments WHERE term_code = ?",
        (term,),
    ).fetchone()
    tiles = [
        _tile(
            "on_campus",
            "Enrolled students living on campus",
            _count(int(on_campus or 0)),
            f"Of {_count(int(enrolled))} enrolled this term.",
        ),
        _tile(
            "first_generation_share",
            "First-generation students",
            _share(int(first_gen or 0), int(enrolled)),
            "Share of this term's enrolled students.",
        ),
        _tile(
            "stopped_out",
            "Students who stopped out this term",
            _count(int(stopped_out)),
            "Did not return and did not graduate or transfer.",
        ),
        _tile(
            "advising_no_show",
            "Advising no-show rate",
            _share(int(no_show or 0), int(booked or 0)),
            "Missed appointments as a share of those not cancelled, this term.",
        ),
    ]
    appointment_rows = [
        (str(kind), [int(done or 0), int(missed or 0), int(cancelled or 0)])
        for kind, done, missed, cancelled in con.execute(
            "SELECT appointment_type, SUM(status = 'completed'),"
            " SUM(status = 'no_show'), SUM(status = 'cancelled')"
            " FROM student_appointments WHERE term_code = ?"
            " GROUP BY appointment_type ORDER BY COUNT(*) DESC",
            (term,),
        )
    ]
    appointment_mask = protect([counts for _, counts in appointment_rows])
    appointments = [
        {
            "type": kind.replace("_", " ").capitalize(),
            "completed": _shown(counts[0], hidden[0]),
            "no_show": _shown(counts[1], hidden[1]),
            "cancelled": _shown(counts[2], hidden[2]),
        }
        for (kind, counts), hidden in zip(
            appointment_rows, appointment_mask, strict=True
        )
    ]
    housing_rows = [
        (str(place), [int(ft or 0), int(pt or 0)])
        for place, ft, pt in con.execute(
            "SELECT housing, SUM(academic_load = 'full_time'),"
            " SUM(academic_load = 'part_time') FROM student_term_enrollment"
            " WHERE term_code = ? AND status = 'enrolled' AND housing IS NOT NULL"
            " GROUP BY housing ORDER BY housing DESC",
            (term,),
        )
    ]
    housing_mask = protect([counts for _, counts in housing_rows])
    housing = [
        {
            "housing": "On campus" if place == "on_campus" else "Off campus",
            "full_time": _shown(counts[0], hidden[0]),
            "part_time": _shown(counts[1], hidden[1]),
        }
        for (place, counts), hidden in zip(housing_rows, housing_mask, strict=True)
    ]
    return {
        "tiles": tiles,
        "tables": [
            {
                "key": "advising_appointments",
                "title": "Advising appointments this term",
                "columns": [
                    {"key": "type", "label": "Appointment"},
                    {"key": "completed", "label": "Completed"},
                    {"key": "no_show", "label": "No-show"},
                    {"key": "cancelled", "label": "Cancelled"},
                ],
                "rows": appointments,
            },
            {
                "key": "housing",
                "title": "Where enrolled students live",
                "columns": [
                    {"key": "housing", "label": "Housing"},
                    {"key": "full_time", "label": "Full time"},
                    {"key": "part_time", "label": "Part time"},
                ],
                "rows": housing,
            },
        ],
    }


_BUILDERS = {
    ROLE_FINANCE: _finance,
    ROLE_REGISTRAR: _registrar,
    ROLE_STUDENT_LIFE: _student_life,
}


def overview(department: str, db: Path | None = None) -> dict[str, Any]:
    """One department's overview. Raises SchoolDataMissing without the
    school data and KeyError for an unknown department."""
    builder = _BUILDERS[department]
    path = (db or school_db_path()).expanduser()
    try:
        stat = path.stat()
    except OSError:
        raise SchoolDataMissing() from None
    key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns, department)
    with _cache_lock:
        cached = _cache.get(key)
    if cached is not None:
        return cached
    con = connect_readonly(path)
    try:
        term, term_name = _latest_term(con)
        body = builder(con, term)
        meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
    except sqlite3.Error:
        raise SchoolDataMissing() from None
    finally:
        con.close()
    result = {
        "department": department,
        "name": DEPARTMENT_NAMES[department],
        "term": {"code": term, "name": term_name},
        "fictional": meta.get("fictional") == "true",
        "institution": meta.get("institution", ""),
        "minimum_cell_size": MINIMUM_CELL_SIZE,
        **body,
    }
    with _cache_lock:
        _cache[key] = result
    return result


def tile_snapshot(department: str, tile_key: str) -> dict[str, Any] | None:
    """One overview figure as it reads now (for an inbox alert), or None
    when the department or the figure does not exist."""
    if department not in _BUILDERS:
        return None
    data = overview(department)
    tile = next((t for t in data["tiles"] if t["key"] == tile_key), None)
    if tile is None:
        return None
    return {
        "department": department,
        "department_name": data["name"],
        "term": data["term"]["name"],
        "label": tile["label"],
        "display": tile["display"],
        "note": tile["note"],
    }


def may_read(role: str, department: str) -> bool:
    """A department account reads its own overview; the president and the
    admin read every one (the route table admits only these roles)."""
    if role in DEPARTMENT_ROLES:
        return role == department
    return role in ("executive", "admin")


@router.get("/departments/overview")
def get_overview(
    request: Request, department: str | None = Query(default=None)
) -> JSONResponse:
    user = request.scope["cabinet_user"]
    role = str(user["role"])
    wanted = department or (role if role in DEPARTMENT_ROLES else ROLE_FINANCE)
    if wanted not in _BUILDERS:
        return JSONResponse(
            status_code=422,
            content={
                "detail": f"unknown department {wanted!r}; expected one of "
                + ", ".join(DEPARTMENTS)
            },
        )
    if not may_read(role, wanted):
        detail = "a department account reads its own department's overview only"
        request.app.state.auth.audit_append(
            int(user["institution_id"]),
            "data.refused",
            actor=str(user["id"]),
            payload={
                "reason": detail,
                "method": request.method,
                "path": request.url.path,
            },
        )
        return JSONResponse(status_code=403, content={"detail": detail})
    try:
        return JSONResponse(content=overview(wanted))
    except SchoolDataMissing as exc:
        return JSONResponse(
            status_code=503, content={"available": False, "message": exc.args[0]}
        )
