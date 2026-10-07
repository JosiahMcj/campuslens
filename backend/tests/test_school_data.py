"""Tests for the Demonstration University generator and checker (data/school/).

The generator runs at --scale 0.02 into tmp dirs (never the repo's var/).
These tests assert the schema, determinism (same seed, same canonical hash),
the realism checks, the direction of every planted pattern, and that the
checker fails on tampered rows. One test also generates the full scale
and requires every planted value in VERIFY.md exactly; it takes about 45 s,
so it runs only with CABINET_SCHOOL_FULL=1 (``make school-data`` makes the
same exact comparison through check.py).
"""

from __future__ import annotations

import json
import os
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
    "pattern_salary_by_major",
    "pattern_salary_rises_with_gpa",
    "pattern_med_acceptance_gpa",
    "pattern_ministry_gives_most",
    "pattern_athletes_give_more",
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
    "survey_only_eligible_graduates",
    "salary_only_for_full_time",
    "graduate_school_consistent",
    "medical_school_consistent",
    "gifts_from_alumni",
    "outcome_tables_plausible",
]
OUTCOME_TABLES = (
    "first_destination",
    "graduate_enrollment",
    "medical_school_applications",
    "alumni_gifts",
)
# The canonical hash of the 24 tables that existed before graduate outcomes
# were added, at scale 0.02, as the generator wrote them before (commit
# 37b93fe). Graduate outcomes draw from their own stream, so these rows must
# not change.
ORIGINAL_TABLES_SHA256_SCALE_002 = (
    "5616b6642f851026a8780f6e62fedf788f5590cddacafa2a382683e851d4cf5d"
)
# The same at full scale: the whole-database hash VERIFY.md recorded then.
ORIGINAL_TABLES_SHA256_FULL = (
    "4df6c6378721b0c1cb487ee51528a580f24626f363f9da2ab6df11d43d0cb234"
)


def generate(out: Path, scale: float = 0.02) -> None:
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
    """Two independent generations at scale 0.02 and their check reports."""
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
    # 28 documented tables and the 2 support-program tables (interventions.py).
    assert len(counts) == 30
    for table in OUTCOME_TABLES:
        assert counts[table] > 0, table
    assert counts["academic_periods"] == 17
    assert counts["colleges"] == 7
    assert counts["academic_programs"] == 60
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
    assert meta["scale"] == "0.02"
    assert meta["fictional"] == "true"


def test_same_seed_gives_identical_canonical_hash(small: dict[str, Any]) -> None:
    report_a, report_b = small["reports"]
    assert re.fullmatch(r"[0-9a-f]{64}", report_a["canonical_sha256"])
    assert report_a["canonical_sha256"] == report_b["canonical_sha256"]
    assert report_a["planted"] == report_b["planted"]


def test_existing_tables_are_unchanged_by_graduate_outcomes(
    small: dict[str, Any],
) -> None:
    """The 24 original tables hash exactly as before outcomes were added."""
    report_a, report_b = small["reports"]
    assert report_a["original_tables_sha256"] == ORIGINAL_TABLES_SHA256_SCALE_002
    assert report_b["original_tables_sha256"] == ORIGINAL_TABLES_SHA256_SCALE_002
    assert report_a["canonical_sha256"] != report_a["original_tables_sha256"]


def test_outcome_rows_agree_with_graduates(small: dict[str, Any]) -> None:
    """Spot checks beside check.py's: salaries only for full-time employment,
    survey respondents are bachelor's graduates, every medical enrollment is
    an accepted applicant, and gifts come from graduates."""
    con = sqlite3.connect(small["db"])
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM first_destination WHERE starting_salary IS NOT "
            "NULL AND outcome != 'employed_full_time'"
        ).fetchone() == (0,)
        assert con.execute(
            "SELECT COUNT(*) FROM first_destination fd JOIN students s "
            "USING (student_id) WHERE s.enrollment_status != 'graduated'"
        ).fetchone() == (0,)
        assert con.execute(
            "SELECT COUNT(*) FROM alumni_gifts g JOIN students s USING (student_id) "
            "WHERE s.enrollment_status != 'graduated'"
        ).fetchone() == (0,)
        assert con.execute(
            "SELECT COUNT(*) FROM graduate_enrollment ge WHERE program_type = "
            "'medical' AND NOT EXISTS (SELECT 1 FROM medical_school_applications m "
            "WHERE m.student_id = ge.student_id AND m.accepted = 1)"
        ).fetchone() == (0,)
        salaries = con.execute(
            "SELECT MIN(starting_salary), MAX(starting_salary) FROM first_destination"
        ).fetchone()
    finally:
        con.close()
    assert 20_000 <= salaries[0] < salaries[1] <= 150_000


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
    "salary_only_for_full_time": (
        "UPDATE first_destination SET starting_salary = 50000 WHERE rowid = "
        "(SELECT MIN(rowid) FROM first_destination WHERE outcome = 'seeking')"
    ),
    "survey_only_eligible_graduates": (
        "INSERT INTO first_destination SELECT student_id, '202110', '2021-06-12', "
        "'seeking', NULL, NULL FROM students WHERE enrollment_status = 'active' "
        "LIMIT 1"
    ),
    "graduate_school_consistent": (
        "UPDATE first_destination SET outcome = 'graduate_school', "
        "employer_sector = NULL, starting_salary = NULL WHERE rowid = (SELECT "
        "MIN(rowid) FROM first_destination WHERE outcome = 'seeking' AND "
        "student_id NOT IN (SELECT student_id FROM graduate_enrollment))"
    ),
    "medical_school_consistent": (
        "INSERT INTO medical_school_applications SELECT student_id, 2024, 'MD', 0 "
        "FROM students WHERE enrollment_status = 'withdrawn' LIMIT 1"
    ),
    "gifts_from_alumni": (
        "UPDATE alumni_gifts SET gift_date = '2019-01-02' WHERE gift_id = 1"
    ),
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


@pytest.mark.skipif(
    os.environ.get("CABINET_SCHOOL_FULL") != "1",
    reason="full scale takes about 45 s: CABINET_SCHOOL_FULL=1, or make school-data",
)
def test_full_scale_matches_verify_md_exactly(tmp_path: Path) -> None:
    db = tmp_path / "school.db"
    generate(db, scale=1.0)
    rc, report = check(db)
    failed = [c for c in report["checks"] if not c["ok"]]
    assert rc == 0, failed
    exact = by_name(report)["planted_exact"]
    assert exact["ok"] and "match VERIFY.md" in exact["detail"]
    assert report["original_tables_sha256"] == ORIGINAL_TABLES_SHA256_FULL


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
        [python39, str(GENERATE), "--scale", "0.02", "--out", str(db)],
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
    # The original 24 tables still hash to the value recorded before
    # graduate outcomes were added.
    assert expected.pop("original_tables_sha256") == ORIGINAL_TABLES_SHA256_FULL
    assert set(expected) == set(small["reports"][0]["planted"])
    assert expected["spring_registration"]["change_pct"] == -4.8
    assert expected["lowest_gpa_major"]["major"] == "MEEN"
    assert expected["hardest_required_course"]["course"] == "MEEN 3310"
    assert expected["starting_salary"]["highest_major"] == "CSCI"
    assert expected["alumni_giving"]["highest_major"] == "MINS"
    assert 40 <= expected["medical_school"]["rate_pct"] <= 50
    assert 5 <= expected["alumni_giving"]["participation_pct"] <= 12
