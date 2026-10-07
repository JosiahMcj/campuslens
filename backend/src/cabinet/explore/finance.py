"""The university's own budget: budget against actual, revenue and the
tuition discount, read from the finance tables of the school database
(``fiscal_years``, ``cost_centers``, ``budget_lines``, ``revenue_lines``,
``tuition_revenue``; see data/school/SCHEMA.md).

These are institutional figures, not student data, so no small-cell rule
applies to them. They are, however, for the people who run the budget: only
``BUDGET_ROLES`` (the president, the finance office and the administrator)
may read them, in Explore, on the Data page and on the Finance overview.
Everyone else is refused with a 403 and a ``data.refused`` event.

The runners here return plain rows (dicts) and notes; ``catalog`` wraps them
as analyses. Nothing a person types reaches SQL: the fiscal year is checked
against the years in the data and every other choice selects a constant.
"""

from __future__ import annotations

import sqlite3
from typing import Any

BUDGET_ROLES = ("executive", "admin", "finance")
BUDGET_TABLES = (
    "fiscal_years",
    "cost_centers",
    "budget_lines",
    "revenue_lines",
    "tuition_revenue",
)
BUDGET_MISSING = "The university's budget records are not in this school database yet."
BUDGET_REFUSAL = (
    "The university budget is shown to the president, the finance office and the "
    "administrator only."
)

CATEGORY_LABELS = {
    "salaries_benefits": "Salaries and benefits",
    "operations": "Operations",
    "scholarships": "Funded scholarships",
    "facilities": "Facilities",
    "technology": "Technology",
    "travel": "Travel",
}
FUND_LABELS = {
    "operating": "Operating fund",
    "auxiliary": "Auxiliary fund (housing and dining)",
    "restricted": "Restricted fund (gifts and grants)",
}
SOURCE_LABELS = {
    "gross_tuition": "Gross tuition",
    "institutional_aid": "Institutional aid (tuition discount)",
    "fees": "Student fees",
    "housing": "Housing",
    "dining": "Dining",
    "gifts": "Gifts",
    "grants": "Grants",
    "endowment_draw": "Endowment draw",
    "other": "Other (athletics, conferences, rentals)",
}
SOURCE_ORDER = tuple(SOURCE_LABELS)
BY_CHOICES = ("division", "department", "category", "fund")
BY_LABELS = {
    "division": "division",
    "department": "department or office",
    "category": "expense category",
    "fund": "fund",
}


class BudgetMissing(Exception):
    """The school database has no finance tables."""


def has_budget(con: sqlite3.Connection) -> bool:
    names = {
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN "
            "(?, ?, ?, ?, ?)",
            BUDGET_TABLES,
        )
    }
    return names == set(BUDGET_TABLES)


def fiscal_years(con: sqlite3.Connection) -> tuple[str, ...]:
    """The fiscal years in the data, oldest first (empty without them)."""
    if not has_budget(con):
        return ()
    return tuple(
        str(r[0])
        for r in con.execute("SELECT fiscal_year FROM fiscal_years ORDER BY 1")
    )


def _year(con: sqlite3.Connection, p: dict[str, Any]) -> str:
    if not has_budget(con):
        raise BudgetMissing(BUDGET_MISSING)
    years = fiscal_years(con)
    year = str(p.get("fiscal_year") or years[-1])
    if year not in years:
        raise BudgetMissing(f"There is no {year} in the budget records.")
    return year


def _status_note(con: sqlite3.Connection, year: str) -> list[str]:
    row = con.execute(
        "SELECT status, start_date, end_date FROM fiscal_years WHERE fiscal_year = ?",
        (year,),
    ).fetchone()
    note = [f"{year} runs from {row[1]} to {row[2]} (July to June)."]
    if row[0] == "preliminary":
        note.append(
            f"{year} is preliminary: the books are not closed and June is estimated."
        )
    return note


def _variance_pct(budget: int, actual: int) -> float:
    return round(100.0 * (actual - budget) / budget, 1) if budget else 0.0


def budget_vs_actual(
    con: sqlite3.Connection, p: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Expense budget against actual for one fiscal year, by division, cost
    center, category or fund; optionally only the lines over budget."""
    year = _year(con, p)
    by = str(p.get("by") or "division")
    key_sql = {
        "division": "c.division",
        "department": "c.name",
        "category": "b.category",
        "fund": "b.fund",
    }[by]
    rows = con.execute(
        f"SELECT {key_sql}, SUM(b.budget_amount), SUM(b.actual_amount)"
        " FROM budget_lines b JOIN cost_centers c USING (cost_center_id)"
        f" WHERE b.fiscal_year = ? GROUP BY {key_sql}",
        (year,),
    ).fetchall()
    labels = {"category": CATEGORY_LABELS, "fund": FUND_LABELS}.get(by, {})
    out: list[dict[str, Any]] = []
    for key, budget, actual in rows:
        budget, actual = int(budget), int(actual)
        out.append(
            {
                "group": labels.get(str(key), str(key)),
                "fiscal_year": year,
                "budget": budget,
                "actual": actual,
                "variance": actual - budget,
                "variance_pct": _variance_pct(budget, actual),
            }
        )
    over_only = p.get("over_budget") == "yes"
    if over_only:
        out = [r for r in out if r["variance"] > 0]
    order = p.get("order") or ("highest_first" if over_only else "")
    if order in ("highest_first", "lowest_first"):
        out.sort(
            key=lambda r: (
                -r["variance_pct"] if order == "highest_first" else r["variance_pct"]
            )
        )
    else:
        out.sort(key=lambda r: -r["budget"])
    top = p.get("top")
    shown = len(out)
    if top:
        out = out[: int(top)]
    notes = _status_note(con, year)
    notes.append(
        "Expenses only (revenue is separate). The variance is actual less budget: "
        "positive means over budget. Institutional figures, not student data."
    )
    if over_only:
        notes.append(f"{shown} of {len(rows)} {BY_LABELS[by]}s spent over budget.")
    if not over_only:
        total_b = sum(int(r[1]) for r in rows)
        total_a = sum(int(r[2]) for r in rows)
        out.append(
            {
                "group": "All expenses",
                "fiscal_year": year,
                "budget": total_b,
                "actual": total_a,
                "variance": total_a - total_b,
                "variance_pct": _variance_pct(total_b, total_a),
                "_total": True,
            }
        )
    return out, notes


def revenue_by_source(
    con: sqlite3.Connection, p: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Revenue by source for one fiscal year, budget and actual, with the
    tuition discount subtracted, and net revenue."""
    year = _year(con, p)
    rows = {
        str(source): (int(b), int(a))
        for source, b, a in con.execute(
            "SELECT source, SUM(budget_amount), SUM(actual_amount) FROM revenue_lines"
            " WHERE fiscal_year = ? GROUP BY source",
            (year,),
        )
    }
    out: list[dict[str, Any]] = []
    total_b = total_a = 0
    for source in SOURCE_ORDER:
        if source not in rows:
            continue
        b, a = rows[source]
        sign = -1 if source == "institutional_aid" else 1
        b, a = sign * b, sign * a
        total_b += b
        total_a += a
        out.append(
            {
                "group": SOURCE_LABELS[source],
                "fiscal_year": year,
                "budget": b,
                "actual": a,
                "variance": a - b,
                "variance_pct": _variance_pct(abs(b), abs(a)) * sign,
            }
        )
    out.append(
        {
            "group": "Net revenue",
            "fiscal_year": year,
            "budget": total_b,
            "actual": total_a,
            "variance": total_a - total_b,
            "variance_pct": _variance_pct(total_b, total_a),
            "_total": True,
        }
    )
    notes = _status_note(con, year)
    notes.append(
        "Institutional aid is the university's own grants against tuition (the "
        "tuition discount), so it is subtracted. The variance is actual less budget."
    )
    return out, notes


def tuition_discount(
    con: sqlite3.Connection, p: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Gross tuition, institutional aid, net tuition (with its budget) and the
    discount rate, each fiscal year or one."""
    if not has_budget(con):
        raise BudgetMissing(BUDGET_MISSING)
    year = p.get("fiscal_year")
    if year is not None:
        year = _year(con, p)
    rows = con.execute(
        "SELECT t.fiscal_year, t.student_terms, t.gross_tuition, t.institutional_aid,"
        " t.net_tuition, t.discount_rate,"
        " (SELECT SUM(CASE r.source WHEN 'gross_tuition' THEN r.budget_amount"
        "  WHEN 'institutional_aid' THEN -r.budget_amount ELSE 0 END)"
        "  FROM revenue_lines r WHERE r.fiscal_year = t.fiscal_year)"
        " FROM tuition_revenue t WHERE (? IS NULL OR t.fiscal_year = ?)"
        " ORDER BY t.fiscal_year",
        (year, year),
    ).fetchall()
    out = [
        {
            "fiscal_year": fy,
            "student_terms": int(n),
            "gross_tuition": int(gross),
            "institutional_aid": int(aid),
            "net_tuition": int(net),
            "net_tuition_budget": int(net_b or 0),
            "discount_rate": round(100.0 * float(rate), 1),
        }
        for fy, n, gross, aid, net, rate, net_b in rows
    ]
    notes = [
        "Net tuition is gross tuition less institutional aid; the discount rate is "
        "institutional aid as a share of gross tuition. Gross tuition is the tuition "
        "billed to students (fall and spring) plus summer credit hours, so it moves "
        "with enrollment. Student terms count each student once per fall or spring "
        "term.",
    ]
    if out and out[-1]["fiscal_year"] == fiscal_years(con)[-1]:
        notes.append(f"{out[-1]['fiscal_year']} is preliminary.")
    return out, notes
