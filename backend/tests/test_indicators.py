"""Tests for the M8 support indicators (CONTRACTS.md M8, cabinet.indicators).

Each rule is tested on hand-built records, firing and not firing at its
boundary. M8 on the real fixture equals the planted value in data/VERIFY.md.
The evidence-drawer payload carries rule ids and reasons and no name-like
field, and no prompt an AI employee ever sees contains a student id, with M8
present.
"""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cabinet.analysts import (
    CHIEF_OF_STAFF,
    ENROLLMENT_ANALYST,
    STUDENT_SUCCESS_ANALYST,
    build_prompt,
    chief_received,
)
from cabinet.api import create_app
from cabinet.fixture import (
    Advising,
    CurrentTerm,
    Enrollment,
    Fixture,
    Hold,
    InSessionTerm,
    PriorYearTerm,
    Profile,
    StudentRecord,
    Terms,
    load_fixture,
)
from cabinet.indicators import (
    CLOSING_SOON_DAYS,
    RULES,
    SMALL_BALANCE_LIMIT,
    IndicatorRule,
    RuleContext,
    evaluate_indicators,
)
from cabinet.metrics import M3_AMOUNT_LIMIT, findings
from cabinet.permissions import findings_for_role
from cabinet.questions import DEFAULT_QUESTION, received_for
from conftest import make_authenticated_client

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO_ROOT / "data" / "fixture.json"
VERIFY_PATH = REPO_ROOT / "data" / "VERIFY.md"

AS_OF = date(2026, 11, 20)
IN_SESSION_START = date(2026, 8, 24)
REGISTRATION_CLOSE = date(2026, 12, 18)

CONTEXT = RuleContext(
    as_of=AS_OF,
    in_session_start=IN_SESSION_START,
    registration_close_date=REGISTRATION_CLOSE,
)


def make_hold(
    *,
    category: str = "financial",
    amount: str = "500.00",
    office: str = "Bursar",
    resolved: bool = False,
) -> Hold:
    return Hold(
        category=category,
        amount=Decimal(amount),
        responsible_office=office,
        hold_date=date(2026, 10, 1),
        resolved=resolved,
    )


def make_student(
    student_id: str,
    *,
    continuing: bool = True,
    status: str = "not_registered",
    holds: tuple[Hold, ...] = (),
    last_appointment: date | None = None,
) -> StudentRecord:
    return StudentRecord(
        profile=Profile(
            student_id=student_id,
            program="BA-BIBL",
            class_level="sophomore",
            continuing=continuing,
        ),
        enrollment=Enrollment(
            term="202720",
            registration_status=status,
            registered_credit_hours=0 if status != "registered" else 12,
            registration_date=(
                date(2026, 11, 1) if status == "registered" else None
            ),
        ),
        holds=holds,
        advising=Advising(
            advisor_id="ADV-001",
            last_appointment_date=last_appointment,
            appointment_status="completed" if last_appointment else "none",
        ),
    )


def make_fixture(
    students: list[StudentRecord],
    *,
    in_session_start: date | None = IN_SESSION_START,
    registration_close: date | None = REGISTRATION_CLOSE,
) -> Fixture:
    terms = Terms(
        current=CurrentTerm(
            term="202720",
            name="Spring 2027",
            start_date=date(2027, 1, 11),
            registration_open_date=date(2026, 11, 2),
            registration_close_date=registration_close,
        ),
        prior_year=PriorYearTerm(
            term="202620",
            name="Spring 2026",
            start_date=date(2026, 1, 12),
            registration_open_date=date(2025, 11, 3),
            registration_close_date=date(2025, 12, 19),
            prior_year_equivalent_date=date(2025, 11, 20),
        ),
        in_session=InSessionTerm(
            term="202710",
            name="Fall 2026",
            start_date=in_session_start,
            end_date=date(2026, 12, 11),
        ),
    )
    return Fixture(
        students=tuple(students),
        prior_year_students=(),
        terms=terms,
        as_of=AS_OF,
    )


def rule(rule_id: str) -> IndicatorRule:
    return next(r for r in RULES if r.rule_id == rule_id)


def fires(rule_id: str, record: StudentRecord, context: RuleContext = CONTEXT) -> bool:
    return rule(rule_id).fires(record, context)


def parse_verify_ids(text: str) -> dict[str, list[str]]:
    pattern = re.compile(
        r"<!--\s*ids:([A-Z0-9_]+)\s*-->\s*\n((?:[A-Z]{3}-\d{4}(?:\s+|$))+)",
        re.MULTILINE,
    )
    return {match.group(1): match.group(2).split() for match in pattern.finditer(text)}


# --- the registry is data, readable in one screen -------------------------------


def test_registry_shape() -> None:
    assert [r.rule_id for r in RULES] == ["I1", "I2", "I3", "I4"]
    for entry in RULES:
        assert entry.title
        assert entry.fields_read
        # The reason names every field the rule reads, so a reviewer can
        # check the prose against the predicate without opening the code.
        for field in entry.fields_read:
            assert field in entry.reason, f"{entry.rule_id}: {field}"


def test_small_balance_limit_is_shared_with_m3() -> None:
    assert M3_AMOUNT_LIMIT == SMALL_BALANCE_LIMIT


# --- I1: continuing, not registered, unresolved financial hold under $1,000 -----


def test_i1_fires_under_the_boundary() -> None:
    student = make_student("STU-9001", holds=(make_hold(amount="999.99"),))
    assert fires("I1", student)


def test_i1_does_not_fire_at_exactly_1000() -> None:
    student = make_student("STU-9001", holds=(make_hold(amount="1000.00"),))
    assert not fires("I1", student)


def test_i1_ignores_resolved_and_non_financial_holds() -> None:
    resolved = make_student("STU-9001", holds=(make_hold(resolved=True),))
    academic = make_student(
        "STU-9002", holds=(make_hold(category="academic", office="Registrar"),)
    )
    assert not fires("I1", resolved)
    assert not fires("I1", academic)


def test_i1_requires_continuing_and_not_registered() -> None:
    registered = make_student(
        "STU-9001", status="registered", holds=(make_hold(),)
    )
    new_student = make_student("STU-9002", continuing=False, holds=(make_hold(),))
    assert not fires("I1", registered)
    assert not fires("I1", new_student)


# --- I2: continuing, not registered, no advising appointment this term ----------


def test_i2_fires_with_no_appointment_ever() -> None:
    assert fires("I2", make_student("STU-9001", last_appointment=None))


def test_i2_fires_the_day_before_the_term_starts() -> None:
    student = make_student("STU-9001", last_appointment=date(2026, 8, 23))
    assert fires("I2", student)


def test_i2_does_not_fire_on_the_first_day_of_the_term() -> None:
    student = make_student("STU-9001", last_appointment=IN_SESSION_START)
    assert not fires("I2", student)


def test_i2_requires_not_registered() -> None:
    student = make_student("STU-9001", status="registered", last_appointment=None)
    assert not fires("I2", student)


def test_i2_without_in_session_start_only_never_seen_counts() -> None:
    context = RuleContext(
        as_of=AS_OF, in_session_start=None, registration_close_date=REGISTRATION_CLOSE
    )
    assert fires("I2", make_student("STU-9001", last_appointment=None), context)
    dated = make_student("STU-9002", last_appointment=date(2026, 8, 23))
    assert not fires("I2", dated, context)


# --- I3: two or more unresolved holds across different offices ------------------


def test_i3_fires_with_two_unresolved_holds_at_different_offices() -> None:
    student = make_student(
        "STU-9001",
        holds=(
            make_hold(office="Bursar"),
            make_hold(category="academic", office="Registrar"),
        ),
    )
    assert fires("I3", student)


def test_i3_does_not_fire_for_two_holds_at_one_office() -> None:
    student = make_student(
        "STU-9001",
        holds=(make_hold(office="Bursar"), make_hold(office="Bursar")),
    )
    assert not fires("I3", student)


def test_i3_ignores_resolved_holds() -> None:
    student = make_student(
        "STU-9001",
        holds=(
            make_hold(office="Bursar"),
            make_hold(category="academic", office="Registrar", resolved=True),
        ),
    )
    assert not fires("I3", student)


def test_i3_applies_to_any_student() -> None:
    # I3 names no registration status: a registered student with unresolved
    # holds at two offices fires it.
    student = make_student(
        "STU-9001",
        status="registered",
        holds=(
            make_hold(office="Bursar"),
            make_hold(category="library", office="Library"),
        ),
    )
    assert fires("I3", student)


# --- I4: continuing, not registered, registration closes within 14 days ---------


def _context_close(close: date) -> RuleContext:
    return RuleContext(
        as_of=AS_OF, in_session_start=IN_SESSION_START, registration_close_date=close
    )


def test_i4_fires_at_exactly_14_days_out() -> None:
    context = _context_close(date(2026, 12, 4))  # AS_OF + 14
    assert CLOSING_SOON_DAYS == 14
    assert fires("I4", make_student("STU-9001"), context)


def test_i4_does_not_fire_at_15_days_out() -> None:
    context = _context_close(date(2026, 12, 5))  # AS_OF + 15
    assert not fires("I4", make_student("STU-9001"), context)


def test_i4_fires_when_registration_closes_today_but_not_after() -> None:
    assert fires("I4", make_student("STU-9001"), _context_close(AS_OF))
    closed = _context_close(date(2026, 11, 19))  # already closed
    assert not fires("I4", make_student("STU-9001"), closed)


def test_i4_requires_not_registered_and_dates() -> None:
    registered = make_student("STU-9001", status="registered")
    assert not fires("I4", registered, _context_close(date(2026, 12, 4)))
    no_close = RuleContext(
        as_of=AS_OF, in_session_start=IN_SESSION_START, registration_close_date=None
    )
    assert not fires("I4", make_student("STU-9002"), no_close)


# --- M8 on the real fixture equals the planted value in VERIFY.md ---------------


def test_m8_on_fixture_matches_verify_md() -> None:
    fixture = load_fixture(FIXTURE_PATH)
    report = evaluate_indicators(fixture)
    verify_ids = parse_verify_ids(VERIFY_PATH.read_text(encoding="utf-8"))

    assert report.value == 22
    assert report.row_ids == sorted(verify_ids["M8"])
    counts = {o.rule.rule_id: len(o.row_ids) for o in report.outcomes}
    assert counts == {"I1": 18, "I2": 12, "I3": 0, "I4": 0}
    # I1's population is M3's and I2's is M4's, as VERIFY.md records.
    assert {o.rule.rule_id: o.row_ids for o in report.outcomes}["I1"] == sorted(
        verify_ids["M3"]
    )
    assert {o.rule.rule_id: o.row_ids for o in report.outcomes}["I2"] == sorted(
        verify_ids["M4"]
    )
    # row_rules is exactly the union, per student, of the rules that fired.
    assert sorted(report.row_rules) == report.row_ids
    for outcome in report.outcomes:
        for student_id in outcome.row_ids:
            assert outcome.rule.rule_id in report.row_rules[student_id]


def test_m8_finding_shape_on_fixture() -> None:
    fixture = load_fixture(FIXTURE_PATH)
    out = findings(fixture, fixture_path=FIXTURE_PATH)
    m8 = out["M8"]
    assert m8["value"] == 22
    assert m8["display"] == "22"
    assert m8["reason"] is None
    assert [entry["id"] for entry in m8["rules"]] == ["I1", "I2", "I3", "I4"]
    for entry in m8["rules"]:
        for key in ("title", "reason", "fields_read", "count", "row_ids"):
            assert key in entry
    assert m8["row_rules"]["STU-0120"] == ["I1", "I2"]
    assert m8["row_rules"]["STU-0138"] == ["I2"]
    # The rule ids behind every listed student resolve to a rule in the table.
    table = {entry["id"] for entry in m8["rules"]}
    for rule_ids in m8["row_rules"].values():
        assert set(rule_ids) <= table
    json.dumps(m8, ensure_ascii=False)


def test_m8_null_only_for_an_empty_fixture() -> None:
    empty = make_fixture([], registration_close=None, in_session_start=None)
    report = evaluate_indicators(empty)
    assert report.value is None
    assert report.reason is not None
    assert report.row_ids == []
    # The per-rule table still renders, at zero.
    assert [len(o.row_ids) for o in report.outcomes] == [0, 0, 0, 0]
    finding = findings(empty, fixture_path=FIXTURE_PATH)["M8"]
    assert finding["display"] == "--"

    # Students but no fires: a real 0, never `--`.
    calm = make_fixture([make_student("STU-9001", status="registered")])
    finding = findings(calm, fixture_path=FIXTURE_PATH)["M8"]
    assert finding["value"] == 0
    assert finding["display"] == "0"


def test_m8_one_rule_firing_is_enough_and_no_sum_exists() -> None:
    # Two rules fire for this student (I1 and I2); the count is 1 student,
    # never 2, and there is no per-student total anywhere.
    student = make_student(
        "STU-9001",
        holds=(make_hold(amount="100.00"),),
        last_appointment=None,
    )
    report = evaluate_indicators(make_fixture([student]))
    assert report.value == 1
    assert report.row_rules == {"STU-9001": ["I1", "I2"]}


# --- the aggregate boundary: no per-student detail reaches an AI employee -------


def _findings_obj() -> dict[str, Any]:
    return findings(load_fixture(FIXTURE_PATH), fixture_path=FIXTURE_PATH)


def test_m8_aggregate_for_roles_strips_row_detail() -> None:
    obj = _findings_obj()
    success = findings_for_role(STUDENT_SUCCESS_ANALYST, obj)
    assert "M8" in success
    assert success["M8"]["value"] == 22
    chief = findings_for_role(CHIEF_OF_STAFF, obj)
    assert "M8" in chief
    enrollment = findings_for_role(ENROLLMENT_ANALYST, obj)
    assert "M8" not in enrollment
    for scoped in (success, chief):
        dumped = json.dumps(scoped)
        assert "row_ids" not in dumped
        assert "row_rules" not in dumped
        assert "STU-" not in dumped
        assert "PRI-" not in dumped
    # The per-rule counts stay: they are aggregates, like M5's office counts.
    counts = {entry["id"]: entry["count"] for entry in success["M8"]["rules"]}
    assert counts == {"I1": 18, "I2": 12, "I3": 0, "I4": 0}


def test_prompts_never_contain_a_student_id_with_m8_present() -> None:
    """Extends the model-never-receives-row-ids guarantee to M8: the Student
    Success Analyst receives M8, the Chief of Staff's aggregate includes it,
    and neither prompt carries a student id."""
    obj = _findings_obj()
    received = received_for(DEFAULT_QUESTION, STUDENT_SUCCESS_ANALYST, obj)
    assert "M8" in received
    _system, user = build_prompt(received, STUDENT_SUCCESS_ANALYST)
    assert '"M8"' in user
    assert "STU-" not in user
    assert "PRI-" not in user

    analyst_texts = {
        ENROLLMENT_ANALYST: "Registration is down 4.8 % [M1].",
        STUDENT_SUCCESS_ANALYST: "18 students carry a small financial hold [M3].",
    }
    chief_payload = chief_received(obj, analyst_texts)
    assert "M8" in chief_payload["findings"]
    _system, chief_user = build_prompt(chief_payload, CHIEF_OF_STAFF)
    assert "STU-" not in chief_user
    assert "PRI-" not in chief_user


# --- the evidence-drawer payload ------------------------------------------------


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return make_authenticated_client(create_app())


def _walk_keys(node: Any) -> list[str]:
    keys: list[str] = []
    if isinstance(node, dict):
        for key, item in node.items():
            keys.append(str(key))
            keys.extend(_walk_keys(item))
    elif isinstance(node, list):
        for item in node:
            keys.extend(_walk_keys(item))
    return keys


def test_findings_endpoint_m8_drawer_payload(client: TestClient) -> None:
    response = client.get("/findings")
    assert response.status_code == 200
    m8 = response.json()["M8"]
    # Per pseudonymous student id: which rules fired, and each rule's reason.
    assert m8["row_rules"]["STU-0120"] == ["I1", "I2"]
    reasons = {entry["id"]: entry["reason"] for entry in m8["rules"]}
    assert set(reasons) == {"I1", "I2", "I3", "I4"}
    assert all(isinstance(text, str) and text for text in reasons.values())
    assert set(m8["row_rules"]) <= set(m8["row_ids"])
    # Pseudonymous ids only: no name-like key or value anywhere in M8.
    for key in _walk_keys(m8):
        assert "name" not in key.lower()
    dumped = json.dumps(m8)
    assert "counseling" not in dumped
    assert "@" not in dumped


# --- the words that may never appear --------------------------------------------

# Students are people who may need support: these words are banned from the
# rule registry, the drawer component, and the new prose (CONTRACTS.md M8).
_BANNED_RE = re.compile(r"\b(?:risk|scores?|predict\w*|likely)\b", re.IGNORECASE)


def test_banned_words_absent_from_code_and_new_prose() -> None:
    code_files = [
        REPO_ROOT / "backend" / "src" / "cabinet" / "indicators.py",
        REPO_ROOT / "ui" / "src" / "components" / "EvidenceDrawer.tsx",
    ]
    for path in code_files:
        match = _BANNED_RE.search(path.read_text(encoding="utf-8"))
        assert match is None, f"{path}: {match.group(0)!r}"

    doc_files = [
        REPO_ROOT / "CONTRACTS.md",
        REPO_ROOT / "SCHEMA.md",
        REPO_ROOT / "docs" / "PRIVACY.md",
        REPO_ROOT / "README.md",
        REPO_ROOT / "DEMO-SCRIPT.md",
    ]
    for path in doc_files:
        text = path.read_text(encoding="utf-8")
        # "predict" is banned from the whole document; the other two words
        # predate M8 in these files (e.g. "never risk scores"), so they are
        # checked only in the new prose: lines that mention M8 or the
        # support indicators.
        assert "predict" not in text.lower(), path
        # A paragraph counts as new prose from its first M8 line to the next
        # blank line, so every line of it is scanned, not only the ones that
        # name M8.
        lines = text.splitlines()
        keep: list[str] = []
        in_para = False
        for line in lines:
            if not line.strip():
                in_para = False
                continue
            if "m8" in line.lower() or "support indicator" in line.lower():
                in_para = True
            if in_para:
                keep.append(line)
        new_prose = "\n".join(keep)
        assert new_prose, f"{path}: no M8 prose found"
        match = _BANNED_RE.search(new_prose)
        assert match is None, f"{path}: {match.group(0)!r}"
