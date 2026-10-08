"""Tests for the student billing tables (data/school/billing.py).

Billing is exercised through the real generator, which calls build_billing
at the end of generation: a school is generated at scale 0.01 with billing
and once more with build_billing skipped. The tests assert the three billing
tables exist and are populated, that their references, amounts, and dates are
sound, that the on-time payment rate sits in its designed band (a billed term
counts as on time when it is on a payment plan or fully paid within 30 days
after its due date), and that billing changed no existing table.
"""

from __future__ import annotations

import importlib
import json
import sqlite3
import subprocess
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHOOL = REPO_ROOT / "data" / "school"
GENERATE = SCHOOL / "generate.py"

BILLING_TABLES = ("student_charges", "student_payments", "payment_plans")


@pytest.fixture(scope="module")
def billed_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A generated school, billing included (the generator's own path)."""
    out = tmp_path_factory.mktemp("school-billed") / "school.db"
    proc = subprocess.run(
        [sys.executable, str(GENERATE), "--scale", "0.01", "--out", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert out.exists()
    return out


@pytest.fixture(scope="module")
def plain_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The same school generated with build_billing skipped."""
    out = tmp_path_factory.mktemp("school-plain") / "school.db"
    sys.path.insert(0, str(SCHOOL))
    billing = importlib.import_module("billing")
    generate = importlib.import_module("generate")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(billing, "build_billing", lambda con, seed: None)
    try:
        generate.write_db(generate.build(0.01), out)
    finally:
        monkeypatch.undo()
    return out


def billed_and_due(
    con: sqlite3.Connection,
) -> tuple[dict[tuple[str, str], int], dict[tuple[str, str], str]]:
    """Billed cents and the due date of every billed (student, term) pair."""
    billed: dict[tuple[str, str], int] = defaultdict(int)
    due: dict[tuple[str, str], str] = {}
    for student_id, term_code, amount, due_date in con.execute(
        "SELECT student_id, term_code, amount, due_date FROM student_charges"
    ):
        billed[(student_id, term_code)] += round(amount * 100)
        due[(student_id, term_code)] = due_date
    return billed, due


def test_billing_tables_exist_and_are_nonempty(billed_db: Path) -> None:
    con = sqlite3.connect(str(billed_db))
    try:
        for table in BILLING_TABLES:
            rows = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert rows > 0, f"{table} is empty"
    finally:
        con.close()


def test_billing_referential_integrity(billed_db: Path) -> None:
    con = sqlite3.connect(str(billed_db))
    try:
        for table in BILLING_TABLES:
            unknown_students = con.execute(
                f"SELECT COUNT(*) FROM {table} WHERE student_id NOT IN "
                "(SELECT student_id FROM students)"
            ).fetchone()[0]
            unknown_terms = con.execute(
                f"SELECT COUNT(*) FROM {table} WHERE term_code NOT IN "
                "(SELECT term_code FROM academic_periods)"
            ).fetchone()[0]
            unenrolled = con.execute(
                f"SELECT COUNT(*) FROM {table} WHERE NOT EXISTS "
                "(SELECT 1 FROM student_term_enrollment e "
                f"WHERE e.student_id = {table}.student_id "
                f"AND e.term_code = {table}.term_code AND e.status = 'enrolled')"
            ).fetchone()[0]
            assert unknown_students == 0, f"{table}: unknown student_id"
            assert unknown_terms == 0, f"{table}: unknown term_code"
            assert unenrolled == 0, f"{table}: pair not enrolled"
    finally:
        con.close()


def test_billing_amounts_methods_and_dates(billed_db: Path) -> None:
    con = sqlite3.connect(str(billed_db))
    try:
        bad_charges = con.execute(
            "SELECT COUNT(*) FROM student_charges WHERE amount <= 0 "
            "OR category NOT IN ('tuition', 'housing', 'fees', 'meal_plan')"
        ).fetchone()[0]
        bad_payments = con.execute(
            "SELECT COUNT(*) FROM student_payments WHERE amount <= 0 "
            "OR method NOT IN ('card', 'ach', 'aid_disbursement', 'payment_plan', "
            "'third_party')"
        ).fetchone()[0]
        bad_plans = con.execute(
            "SELECT COUNT(*) FROM payment_plans WHERE installments < 1"
        ).fetchone()[0]
        assert bad_charges == 0
        assert bad_payments == 0
        assert bad_plans == 0
        for table, column in (
            ("student_charges", "due_date"),
            ("student_payments", "paid_on"),
            ("payment_plans", "enrolled_on"),
        ):
            dates = [r[0] for r in con.execute(f"SELECT {column} FROM {table}")]
            assert dates
            for value in dates:
                assert date.fromisoformat(str(value)), f"{table}.{column}: {value}"
        for table in ("student_charges", "student_payments"):
            unrounded = [
                amount
                for (amount,) in con.execute(f"SELECT amount FROM {table}")
                if abs(amount * 100 - round(amount * 100)) > 1e-6
            ]
            assert unrounded == [], f"{table}: amounts not rounded to cents"
    finally:
        con.close()


def test_on_time_payment_rate_in_band(billed_db: Path) -> None:
    con = sqlite3.connect(str(billed_db))
    try:
        billed, due = billed_and_due(con)
        assert len(billed) > 0
        paid_by_deadline: dict[tuple[str, str], int] = defaultdict(int)
        for student_id, term_code, amount, paid_on in con.execute(
            "SELECT student_id, term_code, amount, paid_on FROM student_payments"
        ):
            key = (student_id, term_code)
            deadline = (date.fromisoformat(due[key]) + timedelta(days=30)).isoformat()
            if paid_on <= deadline:
                paid_by_deadline[key] += round(amount * 100)
        plans = {
            (student_id, term_code)
            for student_id, term_code in con.execute(
                "SELECT student_id, term_code FROM payment_plans"
            )
        }
    finally:
        con.close()
    on_time = sum(
        1
        for key, billed_cents in billed.items()
        if key in plans or paid_by_deadline.get(key, 0) >= billed_cents - 1
    )
    rate = on_time / len(billed)
    assert 0.80 <= rate <= 0.95, f"on-time payment rate {rate:.4f} outside 0.80-0.95"


def test_billing_leaves_existing_tables_unchanged(
    billed_db: Path, plain_db: Path
) -> None:
    con_billed = sqlite3.connect(str(billed_db))
    con_plain = sqlite3.connect(str(plain_db))
    try:
        tables = {
            "billed": {
                r[0]
                for r in con_billed.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            },
            "plain": {
                r[0]
                for r in con_plain.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            },
        }
        assert tables["billed"] - tables["plain"] == set(BILLING_TABLES)
        for table in sorted(tables["plain"]):
            billed_rows = con_billed.execute(
                f"SELECT COUNT(*) FROM '{table}'"
            ).fetchone()[0]
            plain_rows = con_plain.execute(
                f"SELECT COUNT(*) FROM '{table}'"
            ).fetchone()[0]
            assert billed_rows == plain_rows, (
                f"{table}: {billed_rows} vs {plain_rows} rows"
            )
    finally:
        con_billed.close()
        con_plain.close()


def test_billing_is_deterministic_across_runs(billed_db: Path, tmp_path: Path) -> None:
    """The same seed builds the same billing rows (its own random stream)."""
    second = tmp_path / "again.db"
    proc = subprocess.run(
        [sys.executable, str(GENERATE), "--scale", "0.01", "--out", str(second)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    con_first = sqlite3.connect(str(billed_db))
    con_second = sqlite3.connect(str(second))
    try:
        for table in BILLING_TABLES:
            first_rows = con_first.execute(
                f"SELECT * FROM {table} ORDER BY rowid"
            ).fetchall()
            second_rows = con_second.execute(
                f"SELECT * FROM {table} ORDER BY rowid"
            ).fetchall()
            assert first_rows == second_rows, f"{table}: rows differ between runs"
    finally:
        con_first.close()
        con_second.close()


def test_checker_accepts_billing(tmp_path: Path) -> None:
    """The school checker passes its billing check alongside the rest.

    Generated at scale 0.02, the scale the school tests use: at 0.01 the
    planted online-withdrawal pattern is inside its noise band regardless of
    billing.
    """
    db = tmp_path / "school.db"
    proc = subprocess.run(
        [sys.executable, str(GENERATE), "--scale", "0.02", "--out", str(db)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    proc = subprocess.run(
        [sys.executable, str(SCHOOL / "check.py"), "--db", str(db), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report: dict[str, Any] = json.loads(proc.stdout)
    checks = {c["name"]: c for c in report["checks"]}
    assert checks["billing"]["ok"], checks["billing"]["detail"]
    assert checks["schema"]["ok"], checks["schema"]["detail"]
    # the 24 simulated, 4 outcome and 2 support-program tables
    assert len(report["counts"]) == 30
