"""Tests for the Demonstration University generator and checker (data/school/).

The generator runs at --scale 0.05 into tmp dirs (never the repo's var/).
These tests assert the schema, determinism (same seed, same canonical hash),
the realism checks, the direction of every planted pattern, and that the
checker fails on tampered rows. One test also generates the full scale
(a few seconds) and requires every planted value in VERIFY.md exactly.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHOOL = REPO_ROOT / "data" / "school"
GENERATE = SCHOOL / "generate.py"
CHECK = SCHOOL / "check.py"
VERIFY_MD = SCHOOL / "VERIFY.md"

PATTERNS = [
    "pattern_low_gpa_major",
    "pattern_hard_course",
    "pattern_instructor_contrast",
    "pattern_instructor_change",
    "pattern_equity_gap",
    "pattern_spring_decline",
    "pattern_fastest_growth",
    "pattern_online_withdrawals",
]
REALISM = [
    "holds_block_registration",
    "history_spans_ordered",
    "advisor_relationships_within_employment",
    "schema",
    "foreign_keys",
    "privacy",
    "grade_rows",
    "withdrawn_status",
    "gpa_range",
    "term_gpa_recomputed",
    "cumulative_gpa_recomputed",
    "class_level_recomputed",
    "standing_recomputed",
    "no_time_conflicts",
    "enrollment_within_capacity",
    "registration_term_matches_section",
    "term_program_matches_history",
    "one_section_per_course",
    "registration_dates",
    "instructors_employed",
    "advisors_employed",
    "holds_dates",
    "prerequisites_mostly_respected",
    "graduation_after_enough_credits",
    "no_registration_after_exit",
    "mean_gpa_plausible",
    "dfw_mostly_5_to_25",
]


def generate(out: Path, scale: float = 0.05) -> None:
    proc = subprocess.run(
        [sys.executable, str(GENERATE), "--scale", str(scale), "--out", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert out.exists()


def check(db: Path) -> tuple[int, dict[str, Any]]:
    proc = subprocess.run(
        [sys.executable, str(CHECK), "--db", str(db), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    report: dict[str, Any] = json.loads(proc.stdout)
    return proc.returncode, report


def by_name(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["name"]: c for c in report["checks"]}


@pytest.fixture(scope="module")
def small(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """Two independent generations at scale 0.05 and their check reports."""
    first = tmp_path_factory.mktemp("school-a") / "school.db"
    second = tmp_path_factory.mktemp("school-b") / "school.db"
    generate(first)
    generate(second)
    rc_a, report_a = check(first)
    rc_b, report_b = check(second)
    return {
        "db": first,
        "rc": (rc_a, rc_b),
        "reports": (report_a, report_b),
    }


def test_schema_tables_and_counts(small: dict[str, Any]) -> None:
    report: dict[str, Any] = small["reports"][0]
    assert by_name(report)["schema"]["ok"], by_name(report)["schema"]["detail"]
    counts: dict[str, int] = report["counts"]
    assert len(counts) == 21
    assert counts["academic_periods"] == 17
    assert counts["colleges"] == 6
    assert counts["academic_programs"] == 40
    assert counts["courses"] == 906
    assert counts["students"] > 200
    assert counts["section_registrations"] == counts["final_grades"]
    assert counts["sections"] == counts["section_instructors"]
    con = sqlite3.connect(small["db"])
    try:
        terms = [r[0] for r in con.execute("SELECT term_code FROM academic_periods")]
        meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
    finally:
        con.close()
    assert terms[0] == "202110"
    assert terms[-1] == "202620"
    assert meta["seed"] == "20261005"
    assert meta["scale"] == "0.05"
    assert meta["fictional"] == "true"


def test_same_seed_gives_identical_canonical_hash(small: dict[str, Any]) -> None:
    report_a, report_b = small["reports"]
    assert re.fullmatch(r"[0-9a-f]{64}", report_a["canonical_sha256"])
    assert report_a["canonical_sha256"] == report_b["canonical_sha256"]
    assert report_a["planted"] == report_b["planted"]


def test_realism_checks_pass(small: dict[str, Any]) -> None:
    checks = by_name(small["reports"][0])
    for name in REALISM:
        assert name in checks, f"missing check {name}"
        assert checks[name]["ok"], f"{name}: {checks[name]['detail']}"


def test_planted_patterns_hold_at_reduced_scale(small: dict[str, Any]) -> None:
    checks = by_name(small["reports"][0])
    for name in PATTERNS:
        assert name in checks, f"missing pattern {name}"
        assert checks[name]["ok"], f"{name}: {checks[name]['detail']}"
    planted = small["reports"][0]["planted"]
    assert planted["equity_gap"]["gap_points"] > 5
    assert planted["spring_registration"]["change_pct"] < 0


def test_checker_passes_overall(small: dict[str, Any]) -> None:
    assert small["rc"] == (0, 0)
    assert small["reports"][0]["ok"] is True


TAMPERING = {
    "term_gpa_recomputed": (
        "UPDATE final_grades SET grade = 'A', quality_points = 12.0 "
        "WHERE registration_id = (SELECT MIN(registration_id) FROM final_grades "
        "WHERE grade = 'F' AND credit_hours = 3)"
    ),
    "enrollment_within_capacity": (
        "UPDATE sections SET capacity = 0 "
        "WHERE section_id = (SELECT MIN(section_id) FROM sections)"
    ),
    "no_time_conflicts": (
        "UPDATE sections SET meeting_days = 'MTWRF', start_time = '08:00', "
        "end_time = '21:00' WHERE section_id IN (SELECT r.section_id FROM "
        "section_registrations r WHERE r.student_id = (SELECT student_id FROM "
        "section_registrations GROUP BY student_id, term_code "
        "HAVING COUNT(*) >= 2 ORDER BY student_id, term_code LIMIT 1))"
    ),
    "standing_recomputed": (
        "UPDATE academic_standings SET standing = 'Academic Suspension' "
        "WHERE rowid = (SELECT MIN(rowid) FROM academic_standings "
        "WHERE standing = 'Good Standing')"
    ),
    "instructors_employed": (
        "UPDATE instructors SET leave_term = '202110' WHERE instructor_id = 'I-0004'"
    ),
    "graduation_after_enough_credits": (
        "UPDATE academic_programs SET credits_required = 400"
    ),
    "holds_block_registration": (
        "INSERT INTO person_holds (student_id, category, description, amount, "
        "responsible_office, term_code, start_date, end_date) "
        "SELECT student_id, 'financial', 'Tampered', 10.0, 'Student Accounts', "
        "term_code, '2000-01-01', NULL FROM section_registrations LIMIT 1"
    ),
}


@pytest.mark.parametrize("check_name", sorted(TAMPERING))
def test_checker_fails_on_tampered_rows(
    small: dict[str, Any], tmp_path: Path, check_name: str
) -> None:
    tampered = tmp_path / "tampered.db"
    shutil.copy(small["db"], tampered)
    con = sqlite3.connect(tampered)
    try:
        assert con.execute(TAMPERING[check_name]).rowcount > 0
        con.commit()
    finally:
        con.close()
    rc, report = check(tampered)
    assert rc == 1
    failed = {c["name"] for c in report["checks"] if not c["ok"]}
    assert check_name in failed
    assert report["canonical_sha256"] != small["reports"][0]["canonical_sha256"]


def test_full_scale_matches_verify_md_exactly(tmp_path: Path) -> None:
    db = tmp_path / "school.db"
    generate(db, scale=1.0)
    rc, report = check(db)
    failed = [c for c in report["checks"] if not c["ok"]]
    assert rc == 0, failed
    exact = by_name(report)["planted_exact"]
    assert exact["ok"] and "match VERIFY.md" in exact["detail"]


def find_python39() -> str | None:
    """A Python 3.9 interpreter, if this machine has one (macOS ships one)."""
    for name in ("python3.9", "/usr/bin/python3"):
        path = shutil.which(name)
        if path is None:
            continue
        proc = subprocess.run(
            [path, "-c", "import sys; print(sys.version_info[:2] == (3, 9))"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.stdout.strip() == "True":
            return path
    return None


PYTHON39 = find_python39()


@pytest.mark.skipif(PYTHON39 is None, reason="no Python 3.9 interpreter to compare")
def test_python39_generates_the_same_rows(
    small: dict[str, Any], tmp_path: Path
) -> None:
    db = tmp_path / "school39.db"
    python39 = PYTHON39
    assert python39 is not None
    proc = subprocess.run(
        [python39, str(GENERATE), "--scale", "0.05", "--out", str(db)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    _rc, report = check(db)
    assert report["canonical_sha256"] == small["reports"][0]["canonical_sha256"]


def test_verify_md_documents_every_planted_fact(small: dict[str, Any]) -> None:
    text = VERIFY_MD.read_text(encoding="utf-8")
    match = re.search(r"<!-- planted-values -->\s*```json\n(.*?)```", text, re.S)
    assert match, "VERIFY.md has no planted-values block"
    expected: dict[str, Any] = json.loads(match.group(1))
    assert re.fullmatch(r"[0-9a-f]{64}", expected.pop("canonical_sha256"))
    assert set(expected) == set(small["reports"][0]["planted"])
    assert expected["spring_registration"]["change_pct"] == -4.8
    assert expected["lowest_gpa_major"]["major"] == "MEEN"
    assert expected["hardest_required_course"]["course"] == "MEEN 3310"
