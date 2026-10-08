"""Synthetic student billing for Demonstration University: charges, payments,
and payment plans.

Stands in for the Ellucian Ethos student accounts resources: one
`student-charges` row per billed amount (tuition by load, housing and a meal
plan on campus, fees), one `student-payments` row per payment event, and one
`payment-plans` row per enrolled installment plan. Every enrolled fall and
spring term inside the calendar (Fall 2020 to Spring 2026) is billed and then
paid in full by the due date, in full within 30 days after it, partially with
a balance left outstanding, or carried on an installment plan. Pell
recipients and first-generation students are somewhat more often late or
partial payers; theology and ministry majors somewhat more often pay
partially.

build_billing(con, seed) runs at the very end of generation (generate.py
calls it after every other table is written) and draws from its own seeded
random stream (seed + 7001), so every table the simulator produced is
unchanged, row for row, whether or not billing is built. Stdlib only
(sqlite3, random, datetime).
"""

from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta

# Billing parameters, in cents, per enrolled term.
TUITION_FULL_TIME = (1_600_000, 1_900_000)  # $16,000-$19,000 full time
TUITION_PART_TIME = (800_000, 950_000)      # half for part time
HOUSING = (340_000, 360_000)                # about $3,500 for on-campus students
FEES = (58_000, 62_000)                     # about $600
MEAL_PLAN = (225_000, 255_000)              # about $2,400 for on-campus students
DUE_DAYS_AFTER_CENSUS = 21                  # term due date: census date + 21 days
PAID_DAYS_BEFORE_DUE = 28                   # on-time payers pay inside this window
LATE_DAYS = 30                              # ... or within this many days after it
PLAN_RATE = 0.20                            # about a fifth use a payment plan
PLAN_RATE_BONUS = 0.03                      # slightly more among Pell/first-generation
PARTIAL_SHARE = (0.40, 0.85)                # share of the bill paid when paying partially
SECOND_PARTIAL_CHANCE = 0.5                 # and a further cut of the remainder within 30 days

# Who pays how: shares of billed terms without a plan.
ON_TIME = 0.84                              # in full by the due date
LATE = 0.06                                 # in full within 30 days after it
# The rest pay partially and leave a balance. Pell recipients and
# first-generation students shift toward late and partial; theology and
# ministry majors (Biblical Studies, Theology, Christian and Youth Ministry,
# Christian Studies) shift toward partial.
PELL_ON_TIME_PENALTY = 0.05
FIRST_GEN_ON_TIME_PENALTY = 0.05
THEOLOGY_ON_TIME_PENALTY = 0.03
PELL_LATE_BONUS = 0.01
FIRST_GEN_LATE_BONUS = 0.02
THEOLOGY_LATE_BONUS = 0.01

# How full and first partial payments are made.
PELL_METHODS = (("aid_disbursement", 0.45), ("card", 0.27), ("ach", 0.25),
                ("third_party", 0.03))
OTHER_METHODS = (("card", 0.55), ("ach", 0.36), ("aid_disbursement", 0.05),
                 ("third_party", 0.04))
PARTIAL_METHODS = ("card", "ach")
PLAN_INSTALLMENTS = (4, 4, 5)  # monthly, weighted toward four

# Theology and ministry majors, by subject of their program.
THEOLOGY_SUBJECTS = ("BIBL", "THEO", "MINS")

BILLING_DDL = """
CREATE TABLE IF NOT EXISTS student_charges (
    charge_id TEXT PRIMARY KEY,
    student_id TEXT NOT NULL REFERENCES students(student_id),
    term_code TEXT NOT NULL REFERENCES academic_periods(term_code),
    category TEXT NOT NULL CHECK (category IN
        ('tuition', 'housing', 'fees', 'meal_plan')),
    amount REAL NOT NULL,
    due_date TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS student_payments (
    payment_id TEXT PRIMARY KEY,
    student_id TEXT NOT NULL REFERENCES students(student_id),
    term_code TEXT NOT NULL REFERENCES academic_periods(term_code),
    amount REAL NOT NULL,
    paid_on TEXT NOT NULL,
    method TEXT NOT NULL CHECK (method IN
        ('card', 'ach', 'aid_disbursement', 'payment_plan', 'third_party'))
);
CREATE TABLE IF NOT EXISTS payment_plans (
    student_id TEXT NOT NULL REFERENCES students(student_id),
    term_code TEXT NOT NULL REFERENCES academic_periods(term_code),
    installments INTEGER NOT NULL CHECK (installments >= 1),
    enrolled_on TEXT NOT NULL,
    PRIMARY KEY (student_id, term_code)
);
"""


def _weighted(rng: random.Random, weights: tuple[tuple[str, float], ...]) -> str:
    u = rng.random()
    for value, weight in weights:
        u -= weight
        if u < 0.0:
            return value
    return weights[-1][0]


def build_billing(con: sqlite3.Connection, seed: int) -> None:
    """Create and fill student_charges, student_payments, and payment_plans."""
    rng = random.Random(seed + 7001)
    con.executescript(BILLING_DDL)

    starts: dict[str, str] = {}
    dues: dict[str, str] = {}
    for term_code, start_iso, census_iso in con.execute(
            "SELECT term_code, start_date, census_date FROM academic_periods"):
        starts[term_code] = start_iso
        dues[term_code] = (date.fromisoformat(census_iso)
                           + timedelta(days=DUE_DAYS_AFTER_CENSUS)).isoformat()

    placeholders = ", ".join("?" * len(THEOLOGY_SUBJECTS))
    ministry_programs = [r[0] for r in con.execute(
        f"SELECT program_code FROM academic_programs WHERE subject_code IN ({placeholders})",
        THEOLOGY_SUBJECTS)]
    history: dict[str, list[tuple[str, str | None]]] = {}
    if ministry_programs:
        for student_id, start_term, end_term in con.execute(
                f"SELECT student_id, start_term, end_term FROM student_academic_programs "
                f"WHERE program_code IN ({', '.join('?' * len(ministry_programs))})",
                ministry_programs):
            history.setdefault(student_id, []).append((start_term, end_term))
    flags: dict[str, tuple[int, int]] = {
        row[0]: (row[1], row[2]) for row in con.execute(
            "SELECT student_id, pell_recipient, first_generation FROM students")}

    charges: list[tuple[str, str, str, str, float, str]] = []
    payments: list[tuple[str, str, str, float, str, str]] = []
    plans: list[tuple[str, str, int, str]] = []
    enrolled = con.execute(
        "SELECT e.student_id, e.term_code, e.academic_load, e.housing "
        "FROM student_term_enrollment e "
        "JOIN academic_periods p ON p.term_code = e.term_code "
        "WHERE e.status = 'enrolled' "
        "ORDER BY e.student_id, e.term_code").fetchall()

    for student_id, term_code, load, housing in enrolled:
        pell, first_gen = flags.get(student_id, (0, 0))
        on_campus = housing == "on_campus"
        bill_cents = 0
        for category, cents in (
                ("tuition", rng.randint(*TUITION_FULL_TIME if load == "full_time"
                                        else TUITION_PART_TIME)),
                ("housing", rng.randint(*HOUSING)) if on_campus else (None, None),
                ("fees", rng.randint(*FEES)),
                ("meal_plan", rng.randint(*MEAL_PLAN)) if on_campus else (None, None)):
            if category is None:
                continue
            charges.append((f"SC-{len(charges) + 1:07d}", student_id, term_code,
                            category, cents / 100.0, dues[term_code]))
            bill_cents += cents

        in_ministry = any(start_term <= term_code and (end_term is None
                         or term_code <= end_term)
                         for start_term, end_term in history.get(student_id, ()))
        plan_rate = PLAN_RATE + (PLAN_RATE_BONUS if pell or first_gen else 0.0)

        if rng.random() < plan_rate:
            installments = rng.choice(PLAN_INSTALLMENTS)
            enrolled_on = (date.fromisoformat(starts[term_code])
                           - timedelta(days=rng.randint(5, 25)))
            plans.append((student_id, term_code, installments, enrolled_on.isoformat()))
            base_cents = bill_cents // installments
            for i in range(installments):
                amount = base_cents if i < installments - 1 \
                    else bill_cents - base_cents * (installments - 1)
                paid_on = enrolled_on + timedelta(days=28 * i + rng.randint(-3, 6))
                payments.append((f"SP-{len(payments) + 1:07d}", student_id, term_code,
                                 amount / 100.0, paid_on.isoformat(), "payment_plan"))
            continue

        on_time_share = (ON_TIME
                         - (PELL_ON_TIME_PENALTY if pell else 0.0)
                         - (FIRST_GEN_ON_TIME_PENALTY if first_gen else 0.0)
                         - (THEOLOGY_ON_TIME_PENALTY if in_ministry else 0.0))
        late_share = (LATE
                      + (PELL_LATE_BONUS if pell else 0.0)
                      + (FIRST_GEN_LATE_BONUS if first_gen else 0.0)
                      + (THEOLOGY_LATE_BONUS if in_ministry else 0.0))
        methods = PELL_METHODS if pell else OTHER_METHODS
        due_date = date.fromisoformat(dues[term_code])
        draw = rng.random()

        if draw < on_time_share:
            paid_on = due_date - timedelta(days=rng.randint(0, PAID_DAYS_BEFORE_DUE))
            payments.append((f"SP-{len(payments) + 1:07d}", student_id, term_code,
                             bill_cents / 100.0, paid_on.isoformat(),
                             _weighted(rng, methods)))
        elif draw < on_time_share + late_share:
            paid_on = due_date + timedelta(days=rng.randint(1, LATE_DAYS))
            payments.append((f"SP-{len(payments) + 1:07d}", student_id, term_code,
                             bill_cents / 100.0, paid_on.isoformat(),
                             _weighted(rng, methods)))
        else:
            first_cents = int(bill_cents * rng.uniform(*PARTIAL_SHARE))
            paid_on = due_date + timedelta(days=rng.randint(-10, 10))
            payments.append((f"SP-{len(payments) + 1:07d}", student_id, term_code,
                             first_cents / 100.0, paid_on.isoformat(),
                             _weighted(rng, methods)))
            if rng.random() < SECOND_PARTIAL_CHANCE:
                second_cents = int((bill_cents - first_cents) * rng.uniform(0.3, 0.9))
                if second_cents > 0:
                    paid_on = due_date + timedelta(days=rng.randint(12, LATE_DAYS))
                    payments.append((f"SP-{len(payments) + 1:07d}", student_id, term_code,
                                     second_cents / 100.0, paid_on.isoformat(),
                                     rng.choice(PARTIAL_METHODS)))

    con.executemany("INSERT INTO student_charges VALUES (?,?,?,?,?,?)", charges)
    con.executemany("INSERT INTO student_payments VALUES (?,?,?,?,?,?)", payments)
    con.executemany("INSERT INTO payment_plans VALUES (?,?,?,?)", plans)
    con.commit()
