"""Unit tests for the M1-M7 metric functions and the findings object.

Expected values are hand-computed from data/fixture.json per CONTRACTS.md; the
row-ID lists are parsed out of data/VERIFY.md and compared verbatim.
"""

from __future__ import annotations

import json
import re
from datetime import date
from fractions import Fraction
from pathlib import Path
from typing import Any

from cabinet.fixture import Fixture, load_fixture, parse_fixture
from cabinet.metrics import (
    _percent_display,
    findings,
    m1_registration_vs_prior_year,
    m2_unregistered_continuing,
    m3_financial_hold_under_1000,
    m4_no_advising_this_term,
    m5_unresolved_holds_by_office,
    m6_days_until_registration_closes,
    m7_credit_hours_vs_prior_year,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO_ROOT / "data" / "fixture.json"
VERIFY_PATH = REPO_ROOT / "data" / "VERIFY.md"

MINUS_SIGN = "\u2212"

TERMS: dict[str, Any] = {
    "current": {
        "term": "202720",
        "name": "Spring 2027",
        "start_date": "2027-01-11",
        "registration_open_date": "2026-11-02",
        "registration_close_date": "2026-12-18",
    },
    "prior_year": {
        "term": "202620",
        "name": "Spring 2026",
        "start_date": "2026-01-12",
        "registration_open_date": "2025-11-03",
        "registration_close_date": "2025-12-19",
        "prior_year_equivalent_date": "2025-11-20",
    },
    "in_session": {
        "term": "202710",
        "name": "Fall 2026",
        "start_date": "2026-08-24",
        "end_date": "2026-12-11",
    },
}

IN_SESSION_START = "2026-08-24"


def make_hold(
    *,
    category: str = "financial",
    amount: float = 100.0,
    office: str = "Bursar",
    resolved: bool = False,
    hold_date: str = "2026-10-01",
) -> dict[str, Any]:
    return {
        "category": category,
        "amount": amount,
        "responsible_office": office,
        "hold_date": hold_date,
        "resolved": resolved,
    }


def make_student(
    student_id: str,
    *,
    continuing: bool = True,
    status: str = "registered",
    hours: int = 12,
    registration_date: str | None = "2026-11-01",
    holds: list[dict[str, Any]] | None = None,
    last_appointment_date: str | None = None,
) -> dict[str, Any]:
    return {
        "profile": {
            "student_id": student_id,
            "program": "BA-BIBL",
            "class_level": "sophomore",
            "continuing": continuing,
        },
        "enrollment": {
            "term": "202720",
            "registration_status": status,
            "registered_credit_hours": hours,
            "registration_date": registration_date,
        },
        "holds": holds if holds is not None else [],
        "advising": {
            "advisor_id": "ADV-001",
            "last_appointment_date": last_appointment_date,
            "appointment_status": "completed" if last_appointment_date else "none",
        },
        "comparison": {
            "prior_year_equivalent_date": "2025-11-20",
            "prior_term_status": "registered",
            "baseline": "202620",
        },
        "counseling": {"counseling_notes": None, "chaplain_contact": False},
    }


def make_fixture(
    students: list[dict[str, Any]],
    prior_year_students: list[dict[str, Any]] | None = None,
    terms: dict[str, Any] | None = None,
) -> Fixture:
    return parse_fixture(
        {
            "terms": terms if terms is not None else TERMS,
            "students": students,
            "prior_year_students": (
                prior_year_students if prior_year_students is not None else []
            ),
        }
    )


def unregistered(student_id: str, **kwargs: Any) -> dict[str, Any]:
    """A continuing, not-registered student (an M2 row)."""
    kwargs.setdefault("status", "not_registered")
    kwargs.setdefault("registration_date", None)
    kwargs.setdefault("hours", 0)
    return make_student(student_id, **kwargs)


def parse_verify_ids(text: str) -> dict[str, list[str]]:
    """Parse '<!-- ids:KEY -->' markers in VERIFY.md; IDs are the following
    non-empty line(s) of space-separated STU-/PRI- tokens."""
    pattern = re.compile(
        r"<!--\s*ids:([A-Z0-9_]+)\s*-->\s*\n((?:[A-Z]{3}-\d{4}(?:\s+|$))+)",
        re.MULTILINE,
    )
    return {match.group(1): match.group(2).split() for match in pattern.finditer(text)}


# --- Planted values against the real fixture (CONTRACTS.md) ---


def test_as_of_is_derived_from_data_not_wall_clock() -> None:
    fixture = load_fixture(FIXTURE_PATH)
    assert fixture.as_of == date(2026, 11, 20)


def test_planted_values_real_fixture() -> None:
    fixture = load_fixture(FIXTURE_PATH)

    m1 = m1_registration_vs_prior_year(fixture)
    assert m1.value == Fraction(-6, 125)  # 119/125 - 1 = -0.048 exactly
    assert len(m1.numerator_ids) == 119
    assert len(m1.denominator_ids) == 125

    assert m2_unregistered_continuing(fixture).value == 42
    assert m3_financial_hold_under_1000(fixture).value == 18
    assert m4_no_advising_this_term(fixture).value == 12

    m6 = m6_days_until_registration_closes(fixture)
    assert m6.value == 28
    assert m6.closed is False

    m7 = m7_credit_hours_vs_prior_year(fixture)
    assert m7.value == Fraction(1821, 1872) - 1  # -17/624 ≈ -0.02724
    assert m7.numerator_sum == 1821
    assert m7.denominator_sum == 1872


def test_m5_grouping_real_fixture() -> None:
    fixture = load_fixture(FIXTURE_PATH)
    offices = m5_unresolved_holds_by_office(fixture)
    # Sorted by office.
    assert [o.office for o in offices] == [
        "Bursar",
        "Library",
        "Registrar",
        "Student Life",
    ]
    counts = {o.office: o.count for o in offices}
    assert counts == {"Bursar": 24, "Library": 1, "Registrar": 2, "Student Life": 1}
    assert sum(counts.values()) == 28  # 28 unresolved hold records
    for office in offices:
        assert office.count == len(office.hold_row_ids)
    # M5 counts hold records across ALL current-term students: the three D4
    # registered students with unresolved financial holds are in Bursar even
    # though they are outside M2/M3.
    bursar = next(o for o in offices if o.office == "Bursar")
    for registered_student in ("STU-0007", "STU-0034", "STU-0088"):
        assert registered_student in bursar.hold_row_ids
    # The $1,000.00 boundary is M3's, not M5's: STU-0142's hold counts here.
    assert "STU-0142" in bursar.hold_row_ids


def test_row_ids_match_verify_md() -> None:
    fixture = load_fixture(FIXTURE_PATH)
    verify_ids = parse_verify_ids(VERIFY_PATH.read_text(encoding="utf-8"))
    out = findings(fixture, fixture_path=FIXTURE_PATH)

    assert out["M1"]["row_ids"]["numerator"] == sorted(verify_ids["M1_NUM"])
    assert out["M1"]["row_ids"]["denominator"] == sorted(verify_ids["M1_DEN"])
    assert out["M2"]["row_ids"] == sorted(verify_ids["M2"])
    assert out["M3"]["row_ids"] == sorted(verify_ids["M3"])
    assert out["M4"]["row_ids"] == sorted(verify_ids["M4"])

    # M7 shares the M1 populations, so it carries the same row IDs.
    assert out["M7"]["row_ids"]["numerator"] == sorted(verify_ids["M1_NUM"])
    assert out["M7"]["row_ids"]["denominator"] == sorted(verify_ids["M1_DEN"])

    # Planted decoys stay out of the metric they imitate.
    m3_ids = set(out["M3"]["row_ids"])
    for key in ("D1", "D2", "D3", "D4"):
        assert m3_ids.isdisjoint(verify_ids[key])
    assert set(out["M4"]["row_ids"]).isdisjoint(verify_ids["D5"])


def test_findings_object_shape_and_displays() -> None:
    fixture = load_fixture(FIXTURE_PATH)
    out = findings(fixture, fixture_path=FIXTURE_PATH)

    assert set(out) == {"M1", "M2", "M3", "M4", "M5", "M6", "M7", "meta"}
    for finding_id in ("M1", "M2", "M3", "M4", "M5", "M6", "M7"):
        finding = out[finding_id]
        assert finding["id"] == finding_id
        for key in (
            "title",
            "value",
            "display",
            "reason",
            "comparison",
            "source_fields",
            "row_ids",
            "definition",
        ):
            assert key in finding, f"{finding_id} missing {key!r}"

    assert out["M1"]["value"] == float(Fraction(-6, 125))
    assert out["M1"]["display"] == f"{MINUS_SIGN}4.8 %"
    assert out["M1"]["reason"] is None
    assert out["M1"]["comparison"] == {
        "prior_year_registered_continuing": 125,
        "prior_year_equivalent_date": "2025-11-20",
    }

    assert out["M2"]["value"] == 42
    assert out["M2"]["display"] == "42"
    assert out["M3"]["value"] == 18
    assert out["M3"]["display"] == "18"
    # The $1,000 threshold is data, so the UI never hardcodes it.
    assert out["M3"]["comparison"] == {"threshold_usd": 1000}
    assert out["M4"]["value"] == 12
    assert out["M4"]["display"] == "12"

    assert out["M5"]["display"] == "28 unresolved holds"
    assert out["M5"]["value"][0] == {
        "office": "Bursar",
        "count": 24,
        "hold_row_ids": out["M5"]["value"][0]["hold_row_ids"],
    }

    assert out["M6"]["value"] == 28
    assert out["M6"]["display"] == "28"
    assert out["M6"]["closed"] is False

    assert out["M7"]["value"] == float(Fraction(1821, 1872) - 1)
    assert out["M7"]["display"] == f"{MINUS_SIGN}2.7 %"
    assert out["M7"]["comparison"] == {
        "prior_year_registered_credit_hours": 1872,
        "prior_year_equivalent_date": "2025-11-20",
    }

    assert out["meta"]["as_of"] == "2026-11-20"
    assert out["meta"]["fixture"] == str(FIXTURE_PATH)
    assert out["meta"]["terms"]["current"] == "202720"
    assert out["meta"]["terms"]["prior_year"] == "202620"
    assert out["meta"]["terms"]["in_session"] == "202710"

    # The whole object is JSON-serializable as the CLI prints it.
    json.dumps(out, ensure_ascii=False)


# --- The exact `--` rule: zero denominators render `--`, never `0 %` ---


def test_generate_fixture_prints_plain_percent_and_is_deterministic() -> None:
    """The generator's summary line printed "-4.8 %%" (a stray format
    escape in a plain print). It must print "-4.8 %", and the run must
    rewrite data/fixture.json byte-identically."""
    import subprocess
    import sys

    before = FIXTURE_PATH.read_bytes()
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "data" / "generate_fixture.py")],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "%%" not in result.stdout
    assert "M1 = -4.8 %" in result.stdout
    assert FIXTURE_PATH.read_bytes() == before


def test_m1_null_for_empty_prior_year_list() -> None:
    fixture = make_fixture([make_student("STU-9001")], [])
    m1 = m1_registration_vs_prior_year(fixture)
    assert m1.value is None
    assert m1.reason is not None
    assert m1.numerator_ids == ["STU-9001"]
    assert m1.denominator_ids == []
    assert findings(fixture, fixture_path=FIXTURE_PATH)["M1"]["display"] == "--"


def test_m7_null_for_empty_prior_year_list() -> None:
    fixture = make_fixture([make_student("STU-9001")], [])
    m7 = m7_credit_hours_vs_prior_year(fixture)
    assert m7.value is None
    assert m7.reason is not None
    assert findings(fixture, fixture_path=FIXTURE_PATH)["M7"]["display"] == "--"


def test_m7_null_when_prior_year_hour_sum_is_zero() -> None:
    # A registered prior-year student carrying 0 hours: denominator population
    # is non-empty but the credit-hour sum is 0.
    prior = make_student("PRI-9001", hours=0, registration_date="2025-11-10")
    fixture = make_fixture([make_student("STU-9001")], [prior])
    m7 = m7_credit_hours_vs_prior_year(fixture)
    assert m7.denominator_ids == ["PRI-9001"]
    assert m7.value is None


def test_zero_change_is_a_real_value_not_dash_dash() -> None:
    current = make_student("STU-9001")
    prior = make_student("PRI-9001", registration_date="2025-11-10")
    fixture = make_fixture([current], [prior])
    m1 = m1_registration_vs_prior_year(fixture)
    assert m1.value == 0
    out = findings(fixture, fixture_path=FIXTURE_PATH)
    assert out["M1"]["display"] == "0.0 %"
    assert out["M7"]["display"] == "0.0 %"


def test_m2_zero_not_null_when_all_continuing_registered() -> None:
    fixture = make_fixture([make_student("STU-9001"), make_student("STU-9002")])
    m2 = m2_unregistered_continuing(fixture)
    assert m2.value == 0
    assert m2.row_ids == []
    out = findings(fixture, fixture_path=FIXTURE_PATH)
    assert out["M2"]["value"] == 0
    assert out["M2"]["display"] == "0"


# --- Empty fixture: every metric hits its contracted `--` or empty-table rule ---


def test_empty_fixture() -> None:
    fixture = make_fixture([], [])
    assert fixture.as_of is None

    assert m1_registration_vs_prior_year(fixture).value is None
    assert m2_unregistered_continuing(fixture).value is None
    assert m3_financial_hold_under_1000(fixture).value is None
    assert m4_no_advising_this_term(fixture).value is None
    assert m5_unresolved_holds_by_office(fixture) == []
    assert m6_days_until_registration_closes(fixture).value is None
    assert m7_credit_hours_vs_prior_year(fixture).value is None

    out = findings(fixture, fixture_path=FIXTURE_PATH)
    for finding_id in ("M1", "M2", "M3", "M4", "M6", "M7"):
        finding = out[finding_id]
        assert finding["value"] is None, finding_id
        assert finding["display"] == "--", finding_id
        assert finding["reason"] is not None, finding_id
    # M5 is a table, not a scalar: an empty fixture renders the empty table.
    assert out["M5"]["value"] == []
    assert out["M5"]["display"] == "No unresolved holds"
    assert out["meta"]["as_of"] is None
    json.dumps(out, ensure_ascii=False)


# --- Planted boundaries ---


def test_percent_display_rounds_half_up_from_the_exact_value() -> None:
    """The display is computed with Decimal from the exact Fraction (never
    float formatting): the halfway value -0.0125 renders −1.3 %, so the
    display and the validator's allowed percent agree."""
    assert _percent_display(Fraction(-1, 80)) == f"{MINUS_SIGN}1.3 %"
    assert _percent_display(Fraction(1, 80)) == "1.3 %"
    assert _percent_display(Fraction(-6, 125)) == f"{MINUS_SIGN}4.8 %"
    assert _percent_display(Fraction(0)) == "0.0 %"


def test_m3_excludes_hold_of_exactly_1000_dollars() -> None:
    student = unregistered("STU-9001", holds=[make_hold(amount=1000.00)])
    fixture = make_fixture([student])
    m3 = m3_financial_hold_under_1000(fixture)
    # M2 is non-empty and no one qualifies: a real 0, not null.
    assert m3.value == 0
    assert m3.row_ids == []


def test_m3_counts_hold_of_999_99() -> None:
    student = unregistered("STU-9001", holds=[make_hold(amount=999.99)])
    fixture = make_fixture([student])
    m3 = m3_financial_hold_under_1000(fixture)
    assert m3.value == 1
    assert m3.row_ids == ["STU-9001"]


def test_m3_excludes_resolved_and_non_financial_holds() -> None:
    resolved = unregistered(
        "STU-9001", holds=[make_hold(amount=100.0, resolved=True)]
    )
    academic = unregistered(
        "STU-9002",
        holds=[make_hold(category="academic", amount=100.0, office="Registrar")],
    )
    fixture = make_fixture([resolved, academic])
    m3 = m3_financial_hold_under_1000(fixture)
    assert m2_unregistered_continuing(fixture).value == 2
    assert m3.value == 0


def test_m4_appointment_on_in_session_start_counts_as_this_term() -> None:
    student = unregistered("STU-9001", last_appointment_date=IN_SESSION_START)
    fixture = make_fixture([student])
    m4 = m4_no_advising_this_term(fixture)
    assert m4.value == 0  # excluded: the appointment is this term
    assert m4.row_ids == []


def test_m4_appointment_before_in_session_start_counts() -> None:
    student = unregistered("STU-9001", last_appointment_date="2026-08-23")
    fixture = make_fixture([student])
    m4 = m4_no_advising_this_term(fixture)
    assert m4.value == 1
    assert m4.row_ids == ["STU-9001"]


def test_m4_null_appointment_counts() -> None:
    student = unregistered("STU-9001", last_appointment_date=None)
    fixture = make_fixture([student])
    m4 = m4_no_advising_this_term(fixture)
    assert m4.value == 1


def test_m4_null_when_in_session_start_date_missing() -> None:
    terms = json.loads(json.dumps(TERMS))
    del terms["in_session"]["start_date"]
    fixture = make_fixture([unregistered("STU-9001")], [], terms)
    m4 = m4_no_advising_this_term(fixture)
    assert m4.value is None
    assert m4.reason is not None
    assert "start_date" in m4.reason


# --- M6 edge cases ---


def test_m6_past_close_date_clamps_to_zero_with_closed_flag() -> None:
    terms = json.loads(json.dumps(TERMS))
    terms["current"]["registration_close_date"] = "2026-11-18"
    student = make_student("STU-9001", registration_date="2026-11-20")
    fixture = make_fixture([student], [], terms)
    m6 = m6_days_until_registration_closes(fixture)
    assert m6.value == 0
    assert m6.closed is True
    out = findings(fixture, fixture_path=FIXTURE_PATH)
    assert out["M6"]["display"] == "0"  # 0 is a real value, never `--`
    assert out["M6"]["closed"] is True


def test_m6_null_when_close_date_missing() -> None:
    terms = json.loads(json.dumps(TERMS))
    del terms["current"]["registration_close_date"]
    fixture = make_fixture([make_student("STU-9001")], [], terms)
    m6 = m6_days_until_registration_closes(fixture)
    assert m6.value is None
    assert m6.reason is not None


# --- M5 grouping ---


def test_m5_grouping_counts_hold_records_not_students() -> None:
    students = [
        make_student(
            "STU-9001",
            holds=[
                make_hold(category="academic", office="Registrar"),
                make_hold(category="library", office="Registrar"),
            ],
        ),
        make_student("STU-9002", holds=[make_hold(office="Bursar", resolved=True)]),
        make_student("STU-9003", holds=[make_hold(office="Bursar")]),
    ]
    offices = m5_unresolved_holds_by_office(make_fixture(students))
    assert [(o.office, o.count) for o in offices] == [("Bursar", 1), ("Registrar", 2)]
    # One student, two unresolved hold records at the same office: contributes 2.
    assert offices[1].hold_row_ids == ["STU-9001", "STU-9001"]
    assert offices[0].hold_row_ids == ["STU-9003"]


# --- The counseling group is never read (ROADMAP §5) ---


def test_counseling_group_is_never_read() -> None:
    raw: dict[str, Any] = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    full = findings(parse_fixture(raw), fixture_path=FIXTURE_PATH)

    for record in raw["students"] + raw["prior_year_students"]:
        del record["counseling"]
    stripped = findings(parse_fixture(raw), fixture_path=FIXTURE_PATH)

    assert full == stripped
    # The typed record does not even model the group.
    fixture = parse_fixture(raw)
    assert not hasattr(fixture.students[0], "counseling")
