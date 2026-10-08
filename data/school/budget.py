"""The university's own finances for Demonstration University: fiscal years,
cost centers, the operating budget against actuals, and revenue.

Stands in for a Colleague or Banner Finance general ledger and budget, the
data Ellucian Ethos serves as ``fiscal-years``, ``accounting-string-
component-values`` (cost centers), ``budget-phase-line-items`` (the
adopted budget) and ``ledger-activities`` (actuals). Five tables:

- ``fiscal_years``: FY2021 (July 2020 to June 2021) to FY2026.
- ``cost_centers``: each college's dean's office, each academic department
  (one per subject), the library, and the administrative and auxiliary
  offices, each in a division.
- ``budget_lines``: one row per fiscal year, cost center, fund and expense
  category, with the adopted budget and the actual, in whole dollars.
- ``revenue_lines``: one row per fiscal year, revenue source and fund, budget
  and actual. ``institutional_aid`` is the tuition discount: a reduction of
  gross tuition, recorded as a positive amount, never an expense.
- ``tuition_revenue``: the tuition drivers per fiscal year (student terms,
  credit hours, gross tuition, institutional aid, net tuition, discount
  rate), the same figures as the tuition rows of ``revenue_lines``.

How it ties to the rest of the school. Gross tuition is the tuition billed
to students in the fiscal year's fall and spring terms (``student_charges``)
plus summer credit hours at a per-hour rate, so it moves with enrollment.
Housing and dining revenue is the housing and meal-plan charges, so it moves
with the students living on campus. Without the billing tables (an older
database) the same figures come from enrollment at the billing module's
average rates. Each academic department's salaries follow the instructors
employed in its subject that year, by rank.

Planted stories (what an analyst should find):

- The tuition discount rate creeps up from about 46 % in FY2021 and jumps
  above 51 % in FY2026, when enrollment softens (fewer billed student terms
  than the year before). Net tuition revenue falls short of budget in FY2026.
- Athletics spends over its budget two years running: FY2025 and FY2026
  (operations, travel and athletic scholarships).

The tables are written after every other table from their own seeded random
stream (seed + 9101). They only read the other tables, so every existing
row is unchanged. Amounts are whole dollars. Stdlib only, Python 3.9.
"""

from __future__ import annotations

import random
import sqlite3
from typing import Dict, List, Optional, Tuple

FISCAL_YEARS = ("FY2021", "FY2022", "FY2023", "FY2024", "FY2025", "FY2026")
FUNDS = ("operating", "auxiliary", "restricted")
CATEGORIES = (
    "salaries_benefits", "operations", "scholarships", "facilities", "technology", "travel",
)
REVENUE_SOURCES = (
    "gross_tuition", "institutional_aid", "fees", "housing", "dining", "gifts", "grants",
    "endowment_draw", "other",
)

# The billing module's average charges, for a database without billing.
TUITION_FULL_TIME = 17_500
TUITION_PART_TIME = 8_750
HOUSING_TERM = 3_500
MEAL_PLAN_TERM = 2_400
FEES_TERM = 600
SUMMER_RATE_FY2021 = 590  # dollars per summer credit hour, +3 % a year

# Discount rate: a slow upward drift, plus a jump when enrollment softens
# (the fiscal year's billed student terms below the best year so far).
DISCOUNT_BASE = 0.462
DISCOUNT_DRIFT = 0.004
DISCOUNT_SOFTENING = 1.2

# Revenue not driven by students, in dollars at scale 1.0, by fiscal year
# (GIFTS: gifts from friends, churches and foundations; alumni gifts are added
# from alumni_gifts).
GIFTS = (14_200_000, 15_800_000, 21_400_000, 17_100_000, 17_900_000, 16_500_000)
GIFTS_RESTRICTED_SHARE = 0.6
GRANTS = (9_400_000, 9_700_000, 10_100_000, 10_300_000, 10_700_000, 10_900_000)
ENDOWMENT_DRAW = (12_600_000, 13_100_000, 13_900_000, 14_400_000, 15_000_000, 15_600_000)
ENDOWMENT_RESTRICTED_SHARE = 0.55
OTHER_REVENUE = 4_200_000  # athletics tickets, conferences, rentals
OPERATING_MARGIN = 0.015  # the budget plans a small surplus
RESTRICTED_SPEND = 0.97  # of restricted revenue

# Annual salary by rank (FY2021 dollars), before 30 % benefits; +2.5 % a year.
RANK_SALARY = {
    "Professor": 98_000, "Associate Professor": 82_000, "Assistant Professor": 72_000,
    "Instructor": 58_000, "Lecturer": 55_000, "Adjunct Instructor": 22_000,
}
BENEFITS = 1.30
RAISE = 0.025
# Departments whose faculty bring in grants (restricted operations).
GRANT_SUBJECTS = ("BIOL", "CHEM", "PHYS", "ENVS", "CSCI", "NURS", "PUBH", "EDUC", "PSYC")

# Administrative and auxiliary cost centers: id, name, division, fund, and
# base amounts by category at scale 1.0 (dollars, FY2021).
OFFICES: Tuple[Tuple[str, str, str, str, Dict[str, int]], ...] = (
    ("CC-300", "University Library", "Academic Affairs", "operating",
     {"salaries_benefits": 4_500_000, "operations": 3_800_000, "technology": 900_000}),
    ("CC-400", "Student Life", "Student Life", "operating",
     {"salaries_benefits": 6_100_000, "operations": 3_000_000, "travel": 200_000}),
    ("CC-410", "Chapel and Spiritual Life", "Spiritual Life", "operating",
     {"salaries_benefits": 1_400_000, "operations": 800_000, "travel": 150_000}),
    ("CC-500", "Athletics", "Athletics", "operating",
     {"salaries_benefits": 9_200_000, "operations": 6_000_000, "travel": 3_200_000,
      "facilities": 1_500_000}),
    ("CC-600", "Admissions", "Enrollment Management", "operating",
     {"salaries_benefits": 3_500_000, "operations": 4_000_000, "travel": 800_000,
      "technology": 600_000}),
    ("CC-610", "Financial Aid", "Enrollment Management", "operating",
     {"salaries_benefits": 1_800_000, "operations": 300_000, "technology": 300_000}),
    ("CC-700", "Facilities", "Facilities", "operating",
     {"salaries_benefits": 9_000_000, "facilities": 22_000_000, "operations": 3_000_000}),
    ("CC-710", "Information Technology", "Information Technology", "operating",
     {"salaries_benefits": 7_000_000, "technology": 9_000_000, "operations": 1_000_000}),
    ("CC-800", "Advancement", "Advancement", "operating",
     {"salaries_benefits": 4_000_000, "operations": 2_000_000, "travel": 600_000}),
    ("CC-900", "Administration", "Administration", "operating",
     {"salaries_benefits": 12_000_000, "operations": 8_000_000, "technology": 1_000_000,
      "travel": 300_000}),
    ("CC-950", "Housing", "Auxiliary Services", "auxiliary",
     {"salaries_benefits": 3_000_000, "facilities": 22_000_000, "operations": 2_000_000}),
    ("CC-960", "Dining", "Auxiliary Services", "auxiliary",
     {"operations": 26_000_000, "salaries_benefits": 1_000_000}),
)
# Restricted spending: funded scholarships (endowment and gifts), athletic
# scholarships funded by boosters, restricted chapel programs, and grant work
# (spread over GRANT_SUBJECTS). Weights within restricted revenue.
RESTRICTED: Tuple[Tuple[str, str, float], ...] = (
    ("CC-610", "scholarships", 0.56),
    ("CC-500", "scholarships", 0.10),
    ("CC-410", "operations", 0.04),
)
RESTRICTED_GRANT_SHARE = 0.30
DEAN_BASE = {"salaries_benefits": 1_300_000, "operations": 350_000, "travel": 90_000,
             "technology": 120_000}

# Planted: Athletics over budget (actual / budget - 1, before noise).
ATHLETICS_OVER = {"FY2025": {"operations": 0.10, "travel": 0.13, "scholarships": 0.09,
                             "salaries_benefits": 0.03},
                  "FY2026": {"operations": 0.14, "travel": 0.16, "scholarships": 0.12,
                             "salaries_benefits": 0.04}}
NOISE_SD = 0.018
NOISE_CAP = 0.045

BUDGET_DDL = """
CREATE TABLE fiscal_years (
    fiscal_year TEXT PRIMARY KEY,
    academic_year TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('closed', 'preliminary'))
);
CREATE TABLE cost_centers (
    cost_center_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    division TEXT NOT NULL,
    college_code TEXT REFERENCES colleges(college_code),
    subject_code TEXT REFERENCES subjects(subject_code)
);
CREATE TABLE budget_lines (
    fiscal_year TEXT NOT NULL REFERENCES fiscal_years(fiscal_year),
    cost_center_id TEXT NOT NULL REFERENCES cost_centers(cost_center_id),
    fund TEXT NOT NULL CHECK (fund IN ('operating', 'auxiliary', 'restricted')),
    category TEXT NOT NULL CHECK (category IN ('salaries_benefits', 'operations',
        'scholarships', 'facilities', 'technology', 'travel')),
    budget_amount INTEGER NOT NULL,
    actual_amount INTEGER NOT NULL,
    PRIMARY KEY (fiscal_year, cost_center_id, fund, category)
);
CREATE TABLE revenue_lines (
    fiscal_year TEXT NOT NULL REFERENCES fiscal_years(fiscal_year),
    source TEXT NOT NULL CHECK (source IN ('gross_tuition', 'institutional_aid', 'fees',
        'housing', 'dining', 'gifts', 'grants', 'endowment_draw', 'other')),
    fund TEXT NOT NULL CHECK (fund IN ('operating', 'auxiliary', 'restricted')),
    budget_amount INTEGER NOT NULL,
    actual_amount INTEGER NOT NULL,
    PRIMARY KEY (fiscal_year, source, fund)
);
CREATE TABLE tuition_revenue (
    fiscal_year TEXT PRIMARY KEY REFERENCES fiscal_years(fiscal_year),
    student_terms INTEGER NOT NULL,
    credit_hours INTEGER NOT NULL,
    gross_tuition INTEGER NOT NULL,
    institutional_aid INTEGER NOT NULL,
    net_tuition INTEGER NOT NULL,
    discount_rate REAL NOT NULL
);
"""


def _round_k(x: float) -> int:
    """A budget figure: whole thousands of dollars."""
    return int(round(x / 1000.0)) * 1000


def _has_table(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone() is not None


class _Drivers:
    """What the school's own records say about one fiscal year."""

    def __init__(self) -> None:
        self.student_terms = 0
        self.credit_hours = 0
        self.tuition = 0.0
        self.fees = 0.0
        self.housing = 0.0
        self.dining = 0.0


def _drivers(con: sqlite3.Connection) -> Dict[str, _Drivers]:
    year_of = {code: year for code, year in con.execute(
        "SELECT term_code, academic_year FROM academic_periods")}
    fy_of = {code: f"FY{int(year[-4:])}" for code, year in year_of.items()}
    out = {fy: _Drivers() for fy in FISCAL_YEARS}
    for term, n, hours, ft, pt, on_campus in con.execute(
            "SELECT term_code, COUNT(*), SUM(census_hours), SUM(academic_load = 'full_time'),"
            " SUM(academic_load = 'part_time'), SUM(housing = 'on_campus')"
            " FROM student_term_enrollment WHERE status = 'enrolled'"
            " GROUP BY term_code ORDER BY term_code"):
        d = out.get(fy_of.get(term, ""))
        if d is None:
            continue
        d.student_terms += int(n)
        d.credit_hours += int(hours or 0)
        d.tuition += TUITION_FULL_TIME * int(ft or 0) + TUITION_PART_TIME * int(pt or 0)
        d.fees += FEES_TERM * int(n)
        d.housing += HOUSING_TERM * int(on_campus or 0)
        d.dining += MEAL_PLAN_TERM * int(on_campus or 0)
    if _has_table(con, "student_charges"):
        billed: Dict[str, Dict[str, float]] = {fy: {} for fy in FISCAL_YEARS}
        for term, category, amount in con.execute(
                "SELECT term_code, category, SUM(amount) FROM student_charges"
                " GROUP BY term_code, category ORDER BY term_code, category"):
            fy = fy_of.get(term)
            if fy in billed:
                billed[fy][category] = billed[fy].get(category, 0.0) + float(amount)
        for fy, sums in billed.items():
            if sums:
                d = out[fy]
                d.tuition = sums.get("tuition", 0.0)
                d.fees = sums.get("fees", 0.0)
                d.housing = sums.get("housing", 0.0)
                d.dining = sums.get("meal_plan", 0.0)
    # Summer sessions: billed per credit hour (not in student_charges).
    for term, hours in con.execute(
            "SELECT r.term_code, SUM(r.attempted_hours) FROM student_term_records r"
            " JOIN academic_periods p ON p.term_code = r.term_code"
            " WHERE p.season = 'Summer' GROUP BY r.term_code ORDER BY r.term_code"):
        fy = fy_of.get(term)
        if fy not in out:
            continue
        i = FISCAL_YEARS.index(fy)
        out[fy].credit_hours += int(hours or 0)
        out[fy].tuition += int(hours or 0) * SUMMER_RATE_FY2021 * (1.03 ** i)
    return out


def _faculty_cost(con: sqlite3.Connection) -> Dict[Tuple[str, str], float]:
    """(fiscal year, subject) -> salaries and benefits of the instructors
    employed in that subject in the fiscal year's fall term."""
    rows = con.execute(
        "SELECT subject_code, academic_rank, hire_term, leave_term FROM instructors"
        " ORDER BY instructor_id").fetchall()
    out: Dict[Tuple[str, str], float] = {}
    for i, fy in enumerate(FISCAL_YEARS):
        fall = f"{fy[2:]}10"
        for subject, rank, hire, leave in rows:
            if hire <= fall and (leave is None or leave > fall):
                key = (fy, subject)
                out[key] = out.get(key, 0.0) + RANK_SALARY.get(rank, 60_000) * BENEFITS * (
                    (1 + RAISE) ** i)
    return out


def _noise(rng: random.Random) -> float:
    return max(-NOISE_CAP, min(NOISE_CAP, rng.gauss(0.0, NOISE_SD)))


def budget_rows(con: sqlite3.Connection, seed: int) -> Dict[str, List[tuple]]:
    """Every row of the five tables (see the module docstring)."""
    rng = random.Random(seed + 9101)
    meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
    scale = float(meta.get("scale", "1.0"))
    drivers = _drivers(con)
    # Gifts: every alumni gift of the fiscal year (alumni_gifts, when the
    # outcomes tables exist) plus gifts from friends, churches and
    # foundations (GIFTS, the larger part).
    alumni: Dict[str, float] = {}
    if _has_table(con, "alumni_gifts"):
        alumni = {f"FY{int(fy)}": float(total) for fy, total in con.execute(
            "SELECT fiscal_year, SUM(amount) FROM alumni_gifts GROUP BY fiscal_year"
            " ORDER BY fiscal_year")}
    faculty = _faculty_cost(con)
    colleges = [r[0] for r in con.execute("SELECT college_code FROM colleges ORDER BY 1")]
    college_names = dict(con.execute("SELECT college_code, name FROM colleges"))
    subjects = con.execute(
        "SELECT subject_code, name, college_code FROM subjects ORDER BY subject_code").fetchall()

    years = []
    for fy in FISCAL_YEARS:
        end = int(fy[2:])
        years.append((fy, f"{end - 1}-{end}", f"{end - 1}-07-01", f"{end}-06-30",
                      "preliminary" if fy == FISCAL_YEARS[-1] else "closed"))

    centers: List[tuple] = []
    for n, code in enumerate(colleges):
        name = college_names[code].replace("College of ", "")
        centers.append((f"CC-1{n:02d}", f"Dean's Office, {name}", "Academic Affairs",
                        code, None))
    for n, (subject, name, college) in enumerate(subjects):
        centers.append((f"CC-2{n:02d}", f"Department of {name}", "Academic Affairs",
                        college, subject))
    dept_of = {s: f"CC-2{n:02d}" for n, (s, _, _) in enumerate(subjects)}
    for cc, name, division, _fund, _base in OFFICES:
        centers.append((cc, name, division, None, None))

    revenue: List[tuple] = []
    tuition: List[tuple] = []
    lines: List[tuple] = []
    best_terms = 0
    prev: Optional[Dict[str, float]] = None
    prev_rate: Optional[float] = None
    for i, fy in enumerate(FISCAL_YEARS):
        d = drivers[fy]
        best_terms = max(best_terms, d.student_terms)
        softening = (best_terms - d.student_terms) / best_terms if best_terms else 0.0
        rate = DISCOUNT_BASE + DISCOUNT_DRIFT * i + DISCOUNT_SOFTENING * softening
        rate = min(0.60, max(0.40, rate + rng.gauss(0.0, 0.0015)))
        gross = int(round(d.tuition))
        aid = int(round(gross * rate))
        net = gross - aid
        tuition.append((fy, d.student_terms, d.credit_hours, gross, aid, net,
                        round(aid / gross, 4) if gross else 0.0))
        gifts = GIFTS[i] * scale + alumni.get(fy, 0.0)
        endow = ENDOWMENT_DRAW[i] * scale
        actual = {
            ("gross_tuition", "operating"): float(gross),
            ("institutional_aid", "operating"): float(aid),
            ("fees", "operating"): d.fees,
            ("housing", "auxiliary"): d.housing,
            ("dining", "auxiliary"): d.dining,
            ("gifts", "operating"): gifts * (1 - GIFTS_RESTRICTED_SHARE),
            ("gifts", "restricted"): gifts * GIFTS_RESTRICTED_SHARE,
            ("grants", "restricted"): GRANTS[i] * scale,
            ("endowment_draw", "operating"): endow * (1 - ENDOWMENT_RESTRICTED_SHARE),
            ("endowment_draw", "restricted"): endow * ENDOWMENT_RESTRICTED_SHARE,
            ("other", "operating"): OTHER_REVENUE * scale * (1.02 ** i),
        }
        # The budget is set before the year: student-driven revenue from last
        # year's actuals plus 1 %, aid at last year's rate plus 0.3 points.
        budget: Dict[Tuple[str, str], float] = {}
        for key, value in actual.items():
            source = key[0]
            if source == "endowment_draw":
                budget[key] = value  # set by the spending rule in advance
            elif prev is not None and source in ("gross_tuition", "fees", "housing", "dining"):
                budget[key] = prev[source] * 1.01
            elif source == "institutional_aid":
                continue
            else:
                budget[key] = value * (1 + rng.gauss(0.0, 0.03))
        planned_rate = (prev_rate + 0.003) if prev_rate is not None else rate
        budget[("institutional_aid", "operating")] = (
            budget[("gross_tuition", "operating")] * planned_rate)
        for (source, fund) in sorted(actual):
            revenue.append((fy, source, fund, _round_k(budget[(source, fund)]),
                            int(round(actual[(source, fund)]))))
        prev = {"gross_tuition": float(gross), "fees": d.fees, "housing": d.housing,
                "dining": d.dining}
        prev_rate = rate

        # Expenses: the budget spends unrestricted revenue less a planned
        # surplus, and restricted revenue as the donors and grantors direct.
        def rev(fund_set: Tuple[str, ...]) -> float:
            total = 0.0
            for (source, fund), value in budget.items():
                if fund in fund_set:
                    total += -value if source == "institutional_aid" else value
            return total

        unrestricted = rev(("operating", "auxiliary")) * (1 - OPERATING_MARGIN)
        restricted = rev(("restricted",)) * RESTRICTED_SPEND
        base: Dict[Tuple[str, str, str], float] = {}
        growth = 1.03 ** i
        for code in colleges:
            cc = centers[colleges.index(code)][0]
            for category, amount in DEAN_BASE.items():
                base[(cc, "operating", category)] = amount * scale * growth
        for subject, _name, _college in subjects:
            cc = dept_of[subject]
            pay = faculty.get((fy, subject), 0.0)
            base[(cc, "operating", "salaries_benefits")] = pay
            base[(cc, "operating", "operations")] = pay * 0.06
            base[(cc, "operating", "travel")] = pay * 0.015
            base[(cc, "operating", "technology")] = pay * 0.02
        for cc, _name, _division, fund, amounts in OFFICES:
            for category, amount in amounts.items():
                base[(cc, fund, category)] = amount * scale * growth
        fill = unrestricted / sum(base.values())
        planned = {key: value * fill for key, value in base.items()}
        for cc, category, share in RESTRICTED:
            planned[(cc, "restricted", category)] = restricted * share
        grant_pay = {s: faculty.get((fy, s), 0.0) for s in GRANT_SUBJECTS}
        grant_total = sum(grant_pay.values()) or 1.0
        for subject in GRANT_SUBJECTS:
            planned[(dept_of[subject], "restricted", "operations")] = (
                restricted * RESTRICTED_GRANT_SHARE * grant_pay[subject] / grant_total)
        for (cc, fund, category) in sorted(planned):
            plan = _round_k(planned[(cc, fund, category)])
            if plan <= 0:
                continue
            over = ATHLETICS_OVER.get(fy, {}).get(category, 0.0) if cc == "CC-500" else 0.0
            spent = int(round(plan * (1 + over + _noise(rng))))
            lines.append((fy, cc, fund, category, plan, spent))
    return {"fiscal_years": years, "cost_centers": centers, "budget_lines": lines,
            "revenue_lines": revenue, "tuition_revenue": tuition}


def write_tables(con: sqlite3.Connection, seed: int) -> None:
    """Create and fill the five tables; nothing else is written."""
    rows = budget_rows(con, seed)
    con.executescript(BUDGET_DDL)
    for table in ("fiscal_years", "cost_centers", "budget_lines", "revenue_lines",
                  "tuition_revenue"):
        data = rows[table]
        if data:
            marks = ",".join("?" * len(data[0]))
            con.executemany(f"INSERT INTO {table} VALUES ({marks})", data)
