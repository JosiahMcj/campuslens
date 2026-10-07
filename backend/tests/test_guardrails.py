"""Regression tests for the 2026-10-06 leak audit.

Each section pins one fix: student ids in GET /findings by role, the /ask
question redaction, counseling free text and file modes at rest, the
Ellucian resource allow list, the production model endpoint and redirects,
and the small operational counts in the live model prompt.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from cabinet.api import create_app
from conftest import make_authenticated_client

# Any pseudonymous student id the demonstration fixture carries.
STUDENT_ID_RE = re.compile(r"\b(?:STU|PRI)-\d{3,}\b")


# --- 1. GET /findings: student ids for the executive and admin only -------------


@pytest.mark.parametrize("role", ["staff", "reviewer", "aid"])
def test_findings_carry_no_student_ids_for_non_row_roles(role: str) -> None:
    client = make_authenticated_client(create_app(), role=role)
    response = client.get("/findings")
    assert response.status_code == 200
    body = response.json()
    assert STUDENT_ID_RE.search(response.text) is None
    # The figures and counts are all still there, and say what was withheld.
    m1 = body["M1"]
    assert m1["rows_withheld"] is True
    assert m1["row_ids"] == {"numerator": [], "denominator": []}
    assert m1["row_counts"]["numerator"] > 0
    assert m1["row_counts"]["denominator"] > 0
    m2 = body["M2"]
    assert m2["row_ids"] == []
    assert m2["row_counts"] == m2["value"]
    offices = body["M5"]["value"]
    assert offices and all(o["hold_row_ids"] == [] for o in offices)
    assert all(o["count"] > 0 for o in offices)
    m8 = body["M8"]
    assert m8["row_rules"] == {}
    assert all(rule["row_ids"] == [] for rule in m8["rules"])
    assert m8["row_counts"] == m8["value"]


@pytest.mark.parametrize("role", ["admin", "executive"])
def test_findings_carry_student_ids_for_row_roles(role: str) -> None:
    client = make_authenticated_client(create_app(), role=role)
    response = client.get("/findings")
    assert response.status_code == 200
    body = response.json()
    assert STUDENT_ID_RE.search(response.text) is not None
    assert "rows_withheld" not in body["M2"]
    assert len(body["M2"]["row_ids"]) == body["M2"]["value"]
    assert body["M8"]["row_rules"]


def test_stripping_for_one_role_leaves_the_cached_findings_whole() -> None:
    app = create_app()
    staff = make_authenticated_client(app, role="staff")
    assert STUDENT_ID_RE.search(staff.get("/findings").text) is None
    executive = make_authenticated_client(app, role="executive")
    body: dict[str, Any] = executive.get("/findings").json()
    assert body["M2"]["row_ids"]
    assert any(o["hold_row_ids"] for o in body["M5"]["value"])
    assert json.dumps(body).count("rows_withheld") == 0


# --- 2. /ask: the typed question is redacted before the audit log -------------


def test_ask_redacts_student_ids_before_the_audit_log() -> None:
    from cabinet.questions import QUESTIONS

    app = create_app()
    executive = make_authenticated_client(app, role="executive")
    typed = "Why has S-100023 not registered? Also student 4471 and 20261234."
    response = executive.post("/ask", json={"question": typed})
    assert response.status_code == 200
    assert response.json()["accepted"] is False

    reviewer = make_authenticated_client(app, role="reviewer")
    events = reviewer.get("/events").json()["events"]
    text = json.dumps(events)
    for token in ("S-100023", "20261234"):
        assert token not in text
    asked = [e for e in events if e["type"] == "question.asked"]
    refused = [
        e
        for e in events
        if e["type"] == "data.refused" and "question" in e["payload"]
    ]
    assert asked and refused
    assert "[number withheld]" in asked[-1]["payload"]["question"]
    assert "[number withheld]" in refused[-1]["payload"]["question"]

    # An approved question is unchanged by the redaction, so matching and the
    # recorded question text stay exactly as before.
    from cabinet.explore.privacy import redact_question

    for question in QUESTIONS:
        assert redact_question(question.text) == question.text
