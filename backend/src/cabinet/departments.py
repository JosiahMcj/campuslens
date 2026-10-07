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
from datetime import date
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
from cabinet.explore import finance as fin
from cabinet.explore import general
from cabinet.explore.catalog import (
    SchoolDataMissing,
    catalog_for,
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
        [(r, c) for r in range(len(matrix)) if c < len(matrix[r])] for c in range(width)
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


def _dollars(amount: float) -> str:
    """An institutional figure: millions with one decimal ("$406.8 million")."""
    sign = "−" if amount < 0 else ""
    return f"{sign}${abs(amount) / 1e6:,.1f} million"


def _signed_pct(value: float) -> str:
    if value == 0:
        return "on budget"
    return f"{abs(value):.1f}% {'over' if value > 0 else 'under'}"


def _money_hundreds(amount: float, students: int) -> str:
    """A student-account dollar sum: withheld under the minimum group size,
    else rounded to the nearest $100 (as Explore rounds it)."""
    if students < MINIMUM_CELL_SIZE:
        return WITHHELD
    return f"${int(round(amount / 100.0)) * 100:,}"


def _university_budget(con: sqlite3.Connection) -> dict[str, Any] | None:
    """The university's own budget (no students in it, so no small-cell
    rule): this fiscal year's spending and revenue against budget by
    division and source, and the tuition discount rate each year."""
    if not fin.has_budget(con):
        return None
    year = fin.fiscal_years(con)[-1]
    spend, _ = fin.budget_vs_actual(con, {"fiscal_year": year})
    revenue, notes = fin.revenue_by_source(con, {"fiscal_year": year})
    tuition, _ = fin.tuition_discount(con, {})
    total = spend[-1]
    net = revenue[-1]
    this, before = tuition[-1], tuition[-2] if len(tuition) > 1 else tuition[-1]
    surplus = net["actual"] - total["actual"]
    tiles = [
        _tile(
            "budget_spending",
            f"Spending against budget, {year}",
            _dollars(total["actual"]),
            f"Budget {_dollars(total['budget'])}: "
            f"{_signed_pct(total['variance_pct'])}.",
        ),
        _tile(
            "budget_net_revenue",
            f"Net revenue, {year}",
            _dollars(net["actual"]),
            f"Budget {_dollars(net['budget'])}: {_signed_pct(net['variance_pct'])}. "
            + (
                f"A deficit of {_dollars(-surplus)}."
                if surplus < 0
                else f"A surplus of {_dollars(surplus)}."
            ),
        ),
        _tile(
            "budget_net_tuition",
            f"Net tuition revenue, {year}",
            _dollars(this["net_tuition"]),
            f"Gross tuition {_dollars(this['gross_tuition'])} less institutional aid "
            f"{_dollars(this['institutional_aid'])}.",
        ),
        _tile(
            "budget_discount_rate",
            f"Tuition discount rate, {year}",
            f"{this['discount_rate']:.1f}%",
            f"{before['discount_rate']:.1f}% in {before['fiscal_year']}. Institutional "
            "aid as a share of gross tuition.",
        ),
    ]
    by_division = [
        {
            "division": r["group"],
            "budget": f"${r['budget']:,}",
            "actual": f"${r['actual']:,}",
            "variance": ("+" if r["variance"] > 0 else "−" if r["variance"] < 0 else "")
            + f"${abs(r['variance']):,}",
            "variance_pct": _signed_pct(r["variance_pct"]),
        }
        for r in spend
    ]
    gross_revenue = sum(r["actual"] for r in revenue[:-1] if r["actual"] > 0)
    mix = [
        {
            "source": r["group"],
            "budget": ("−" if r["budget"] < 0 else "") + f"${abs(r['budget']):,}",
            "actual": ("−" if r["actual"] < 0 else "") + f"${abs(r['actual']):,}",
            "share": f"{100 * r['actual'] / gross_revenue:.1f}%"
            if r["actual"] > 0 and not r.get("_total")
            else "—",
        }
        for r in revenue
    ]
    trend = [
        {
            "fiscal_year": r["fiscal_year"],
            "student_terms": f"{r['student_terms']:,}",
            "gross": _dollars(r["gross_tuition"]),
            "aid": _dollars(r["institutional_aid"]),
            "net": _dollars(r["net_tuition"]),
            "rate": f"{r['discount_rate']:.1f}%",
        }
        for r in tuition
    ]
    money = [
        {"key": "budget", "label": "Budget"},
        {"key": "actual", "label": "Actual"},
    ]
    return {
        "key": "university_budget",
        "title": "University budget",
        "intro": (
            f"{year}, July to June. "
            + " ".join(n for n in notes if "preliminary" in n)
            + " The university's own books, not student records: shown to the "
            "president, the finance office and the administrator only."
        ).strip(),
        "tiles": tiles,
        "tables": [
            {
                "key": "budget_by_division",
                "total_last": True,
                "title": f"Spending against budget by division, {year}",
                "columns": [
                    {"key": "division", "label": "Division"},
                    *money,
                    {"key": "variance", "label": "Actual less budget"},
                    {"key": "variance_pct", "label": "Over or under"},
                ],
                "rows": by_division,
            },
            {
                "key": "revenue_mix",
                "total_last": True,
                "title": f"Revenue by source, {year}",
                "columns": [
                    {"key": "source", "label": "Source"},
                    *money,
                    {"key": "share", "label": "Share of gross revenue"},
                ],
                "rows": mix,
            },
            {
                "key": "discount_trend",
                "title": "Tuition discount rate by fiscal year",
                "columns": [
                    {"key": "fiscal_year", "label": "Fiscal year"},
                    {"key": "student_terms", "label": "Student terms"},
                    {"key": "gross", "label": "Gross tuition"},
                    {"key": "aid", "label": "Institutional aid"},
                    {"key": "net", "label": "Net tuition"},
                    {"key": "rate", "label": "Discount rate"},
                ],
                "rows": trend,
            },
        ],
    }


AGING_LABELS = {
    "1_30": "1 to 30 days",
    "31_60": "31 to 60 days",
    "61_90": "61 to 90 days",
    "over_90": "More than 90 days",
}


def _student_accounts(con: sqlite3.Connection, path: Path) -> dict[str, Any] | None:
    """The billing ledger (charges, payments, payment plans): computed with
    Explore's own measures, so the figures, the rounding and the small-group
    rules are the same in both places."""
    if not general.has_billing(con):
        return None
    v = catalog_for(con, path).vocab

    def run(p: dict[str, Any]) -> list[dict[str, Any]]:
        return general.run(con, p, v)[0]

    def one(measure: str) -> dict[str, Any] | None:
        rows = run({"measure": measure})
        return rows[0] if rows else None

    on_time = one("on_time_payment_rate")
    plan = one("payment_plan_share")
    (asof,) = con.execute("SELECT MAX(end_date) FROM academic_periods").fetchone()
    (last_due,) = con.execute("SELECT MAX(due_date) FROM student_charges").fetchone()
    regular = [t for t, season in v.term_season.items() if season != "Summer"]
    term_name = v.terms[regular[-1]]
    # One pass over the past-due balances (the past_due_* measures' own row
    # set and definitions, read once instead of once per figure).
    by_age: dict[str, tuple[set[str], float]] = {}
    everyone: set[str] = set()
    late_90: set[str] = set()
    owed = 0.0
    for sid, aging, days, amount in con.execute(
        general.UNIT_SQL["account"]
        + " SELECT b.sid, b.aging, b.days_past_due, b.balance FROM b"
        " WHERE b.past_due = 1",
        {"term_from": regular[0], "term_to": regular[-1]},
    ):
        ids, total = by_age.get(aging, (set(), 0.0))
        ids.add(sid)
        by_age[aging] = (ids, total + float(amount))
        everyone.add(sid)
        owed += float(amount)
        if days > 90:
            late_90.add(sid)

    def hundreds(amount: float) -> str:
        return f"${int(round(amount / 100.0)) * 100:,}"

    def pct_of(row: dict[str, Any] | None) -> str:
        return WITHHELD if row is None else f"{row['value']:.1f}%"

    tiles = [
        _tile(
            "accounts_past_due_balance",
            "Past-due balance on student accounts",
            _money_hundreds(owed, len(everyone)),
            f"Unpaid after the due date, as of {asof}. Nothing is written off in "
            "these records. Rounded to the nearest $100.",
        ),
        _tile(
            "accounts_past_due_students",
            "Students with a past-due balance",
            _count(len(everyone)),
            f"{_count(len(late_90))} of them more than 90 days past due.",
        ),
        _tile(
            "accounts_on_time",
            f"Paid on time, {term_name}",
            pct_of(on_time),
            "Paid in full by the due date, or on a payment plan.",
        ),
        _tile(
            "accounts_payment_plans",
            f"On a payment plan, {term_name}",
            pct_of(plan),
            (
                f"{plan['numerator']:,} of {plan['denominator']:,} students billed."
                if plan is not None
                else ""
            ),
        ),
    ]
    present = [code for code in AGING_LABELS if code in by_age]
    mask = dict(
        zip(
            present,
            (m[0] for m in protect([[len(by_age[c][0])] for c in present])),
            strict=True,
        )
    )
    aging_rows = []
    empty: list[str] = []
    for code, label in AGING_LABELS.items():
        if code not in by_age:
            empty.append(label.lower())
            aging_rows.append({"age": label, "students": "None", "balance": "—"})
            continue
        ids, amount = by_age[code]
        hidden = mask[code]
        aging_rows.append(
            {
                "age": label,
                "students": _shown(len(ids), hidden),
                "balance": WITHHELD if hidden else hundreds(amount),
            }
        )
    college_rows = []
    rates = {
        r.get("college"): r
        for r in run({"measure": "on_time_payment_rate", "group_by": "college"})
    }
    plans = {
        r.get("college"): r
        for r in run({"measure": "payment_plan_share", "group_by": "college"})
    }
    for code, name in sorted(v.colleges.items(), key=lambda kv: kv[1]):
        rate, share = rates.get(code), plans.get(code)
        college_rows.append(
            {
                "college": name,
                "billed": WITHHELD if rate is None else f"{rate['denominator']:,}",
                "on_time": pct_of(rate),
                "plan": pct_of(share),
            }
        )
    notes = [
        "Each billed term's balance is aged from its own due date. A student with "
        "balances of different ages is counted in each row."
    ]
    if empty and last_due:
        gap = (date.fromisoformat(asof) - date.fromisoformat(last_due)).days
        notes.append(
            f"No balance is {' or '.join(empty)} past due. Each term's charges fall "
            f"due on one date, and the latest, {last_due}, was {gap} days before "
            f"the records end ({asof}), so every unpaid balance is at least {gap} "
            "days past due."
        )
    return {
        "key": "student_accounts",
        "title": "Student accounts",
        "intro": (
            "Charges, payments and payment plans on students' accounts (the "
            "billing ledger), separate from the account holds above. Groups under "
            f"{MINIMUM_CELL_SIZE} students are withheld."
        ),
        "tiles": tiles,
        "tables": [
            {
                "key": "accounts_aging",
                "title": f"Past-due balances by days past due, as of {asof}",
                "columns": [
                    {"key": "age", "label": "Days past due"},
                    {"key": "students", "label": "Students"},
                    {"key": "balance", "label": "Balance"},
                ],
                "rows": aging_rows,
                "notes": notes,
            },
            {
                "key": "accounts_by_college",
                "title": f"On-time payment and payment plans by college, {term_name}",
                "columns": [
                    {"key": "college", "label": "College"},
                    {"key": "billed", "label": "Students billed"},
                    {"key": "on_time", "label": "Paid on time"},
                    {"key": "plan", "label": "On a payment plan"},
                ],
                "rows": college_rows,
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
        if department == ROLE_FINANCE:
            sections = [
                s
                for s in (_university_budget(con), _student_accounts(con, path))
                if s is not None
            ]
            body["sections"] = sections
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
    tiles = [*data["tiles"]]
    for section in data.get("sections", []):
        tiles.extend(section["tiles"])
    tile = next((t for t in tiles if t["key"] == tile_key), None)
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
