"""Tests for the governance slice (ROADMAP §5).

Covers: the §5 permission table, the field-request gate (both refusals, and
proof that a stub model call is never invoked on refusal), the append-only
audit log, the approved-question check, the API endpoints, and idempotent
double approve — including across an API restart over the same audit file.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cabinet.api import (
    APPROVED_QUESTION,
    DEMO_DECISION_ID,
    OUT_OF_SCOPE_REFUSAL,
    build_decisions,
    create_app,
    is_approved_question,
)
from cabinet.audit import EVENT_TYPES, AuditLog
from cabinet.permissions import (
    AGGREGATE_ONLY,
    READ,
    REFUSED,
    ROLE_PERMISSIONS,
    FieldRequestRefused,
    access_for,
    findings_for_role,
    request_fields,
    run_task_with_model,
)
from cabinet.provider import Explanation, FakeProvider, ProviderUnavailable
from conftest import make_authenticated_client

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO_ROOT / "data" / "fixture.json"


@pytest.fixture
def log(tmp_path: Path) -> AuditLog:
    return AuditLog(tmp_path / "events.jsonl")


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return make_authenticated_client(create_app())


# --- the §5 permission table -----------------------------------------------


def test_permission_table_matches_roadmap_s5() -> None:
    chief = ROLE_PERMISSIONS["chief_of_staff"]
    enrollment = ROLE_PERMISSIONS["enrollment_analyst"]
    success = ROLE_PERMISSIONS["student_success_analyst"]

    # Chief of Staff: aggregates only, counseling refused.
    assert chief["profile.program"] == AGGREGATE_ONLY
    assert chief["enrollment.registration_status"] == AGGREGATE_ONLY
    assert chief["holds.amount"] == AGGREGATE_ONLY
    assert chief["advising.advisor_id"] == AGGREGATE_ONLY
    assert chief["comparison.baseline"] == AGGREGATE_ONLY
    assert chief["counseling.counseling_notes"] == REFUSED
    assert chief["counseling.chaplain_contact"] == REFUSED

    # Enrollment Analyst: profile/enrollment/comparison read, holds/advising refused.
    assert enrollment["profile.continuing"] == READ
    assert enrollment["enrollment.registration_date"] == READ
    assert enrollment["comparison.prior_year_equivalent_date"] == READ
    assert enrollment["holds.amount"] == REFUSED
    assert enrollment["advising.last_appointment_date"] == REFUSED
    assert enrollment["counseling.counseling_notes"] == REFUSED

    # Student Success Analyst: holds/advising read, counseling refused, and
    # enrollment "read (status only)" made literal: registration_status and
    # term are readable; credit hours and the registration date are refused.
    assert success["holds.amount"] == READ
    assert success["advising.appointment_status"] == READ
    assert success["enrollment.registration_status"] == READ
    assert success["enrollment.term"] == READ
    assert success["enrollment.registered_credit_hours"] == REFUSED
    assert success["enrollment.registration_date"] == REFUSED
    assert success["counseling.chaplain_contact"] == REFUSED

    # Counseling fields are refused to every role, always.
    for role_table in ROLE_PERMISSIONS.values():
        assert role_table["counseling.counseling_notes"] == REFUSED
        assert role_table["counseling.chaplain_contact"] == REFUSED


def test_hold_spelling_is_normalized() -> None:
    assert access_for("enrollment_analyst", "hold.amount") == REFUSED
    assert access_for("student_success_analyst", "hold.amount") == READ


# --- the gate: grant, refusal (a), and the never-called model ---------------


def test_gate_grants_and_logs(log: AuditLog) -> None:
    granted = request_fields(
        "enrollment_analyst",
        ["profile.continuing", "enrollment.registration_status"],
        "task-1",
        log,
    )
    assert granted == ["profile.continuing", "enrollment.registration_status"]
    events = log.events("data.granted")
    assert len(events) == 1
    assert events[0]["actor"] == "enrollment_analyst"
    assert events[0]["payload"]["granted_fields"] == granted
    assert events[0]["payload"]["task_id"] == "task-1"


def test_refusal_a_enrollment_analyst_requests_hold_amount(log: AuditLog) -> None:
    """Refusal (a) from ROADMAP §5: refused before any model call, logged."""
    model_calls: list[list[str]] = []

    def stub_model(granted: list[str]) -> str:
        model_calls.append(granted)
        return "explanation"

    with pytest.raises(FieldRequestRefused) as excinfo:
        run_task_with_model(
            "enrollment_analyst",
            ["enrollment.registration_status", "holds.amount"],
            "task-refusal-a",
            log,
            stub_model,
        )

    # The stub "model call" was never invoked.
    assert model_calls == []

    exc = excinfo.value
    assert exc.refused_fields == ["holds.amount"]
    assert "enrollment_analyst" in exc.reason
    assert "holds.amount" in exc.reason

    refused_events = log.events("data.refused")
    assert len(refused_events) == 1
    payload = refused_events[0]["payload"]
    assert refused_events[0]["actor"] == "enrollment_analyst"
    assert payload["requested_fields"] == [
        "enrollment.registration_status",
        "holds.amount",
    ]
    assert payload["refused_fields"] == ["holds.amount"]
    assert isinstance(payload["reason"], str) and payload["reason"]
    # The whole request is refused: nothing was granted.
    assert log.events("data.granted") == []


def test_gate_refuses_counseling_for_every_role(log: AuditLog) -> None:
    for role in ("chief_of_staff", "enrollment_analyst", "student_success_analyst"):
        with pytest.raises(FieldRequestRefused):
            request_fields(role, ["counseling.counseling_notes"], "task-x", log)
    assert len(log.events("data.refused")) == 3


def test_gate_refuses_unknown_field_and_role(log: AuditLog) -> None:
    with pytest.raises(FieldRequestRefused):
        request_fields("enrollment_analyst", ["student.gpa"], "task-x", log)
    with pytest.raises(FieldRequestRefused):
        request_fields("nobody", ["profile.program"], "task-x", log)


def test_student_success_analyst_enrollment_is_status_only(log: AuditLog) -> None:
    """ROADMAP §5 "read (status only)" made literal: the Student Success
    Analyst reads enrollment.registration_status and enrollment.term only;
    registered_credit_hours and registration_date are refused at the gate."""
    granted = request_fields(
        "student_success_analyst",
        ["enrollment.registration_status", "enrollment.term"],
        "task-s5",
        log,
    )
    assert granted == ["enrollment.registration_status", "enrollment.term"]
    for field in (
        "enrollment.registered_credit_hours",
        "enrollment.registration_date",
    ):
        with pytest.raises(FieldRequestRefused):
            request_fields("student_success_analyst", [field], "task-s5", log)
    assert len(log.events("data.refused")) == 2
    assert len(log.events("data.granted")) == 1


def test_granted_request_calls_model(log: AuditLog) -> None:
    model_calls: list[list[str]] = []

    def stub_model(granted: list[str]) -> str:
        model_calls.append(granted)
        return "explanation"

    result = run_task_with_model(
        "student_success_analyst",
        ["holds.amount", "advising.last_appointment_date"],
        "task-ok",
        log,
        stub_model,
    )
    assert result == "explanation"
    assert model_calls == [["holds.amount", "advising.last_appointment_date"]]


# --- findings_for_role --------------------------------------------------------


def _findings_obj() -> dict[str, Any]:
    from cabinet.fixture import load_fixture
    from cabinet.metrics import findings

    return findings(load_fixture(FIXTURE_PATH), fixture_path=FIXTURE_PATH)


def test_findings_for_role_scoping() -> None:
    obj = _findings_obj()
    assert sorted(findings_for_role("enrollment_analyst", obj)) == ["M1", "M2", "M7"]
    assert sorted(findings_for_role("student_success_analyst", obj)) == [
        "M3",
        "M4",
        "M5",
        "M8",
    ]
    chief = findings_for_role("chief_of_staff", obj)
    assert sorted(chief) == ["M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8"]
    # No role receives row IDs: the evidence drawer gets them from
    # /findings, never through a model.
    for role in ("chief_of_staff", "enrollment_analyst", "student_success_analyst"):
        scoped = json.dumps(findings_for_role(role, obj))
        assert "row_ids" not in scoped
        assert "hold_row_ids" not in scoped
        assert "row_rules" not in scoped
        assert "STU-" not in scoped
        assert "PRI-" not in scoped
        # Nor any Financial Aid review queue field (the queue's rows, facts,
        # and notes never reach a model; test_aid_queue.py runs both
        # questions after a note is saved).
        assert "facts" not in scoped
        assert "aid_review" not in scoped
        assert "advising_appointment_status" not in scoped
    with pytest.raises(ValueError, match="unknown role"):
        findings_for_role("nobody", obj)


# --- the audit log ------------------------------------------------------------


def test_audit_log_is_append_only_and_restart_safe(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    first = AuditLog(path)
    e1 = first.append("question.asked", actor="executive", payload={"q": "?"})
    e2 = first.append("task.assigned", actor="chief_of_staff", payload={})
    assert (e1["id"], e2["id"]) == (1, 2)

    # A new instance over the same file recovers the next id.
    second = AuditLog(path)
    e3 = second.append("data.granted", actor="enrollment_analyst", payload={})
    assert e3["id"] == 3
    assert [e["id"] for e in second.events()] == [1, 2, 3]
    assert second.events("data.granted") == [e3]

    # One JSON object per line, with the required fields (prev_hash/hash are
    # the integrity chain).
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    for line in lines:
        event = json.loads(line)
        assert set(event) == {
            "id", "ts", "type", "actor", "payload", "prev_hash", "hash"
        }
        assert event["type"] in EVENT_TYPES
        assert event["ts"].endswith("+00:00")

    # Unknown event types are refused; there is no update or delete API.
    with pytest.raises(ValueError, match="unknown audit event type"):
        second.append("data.updated", actor="x", payload={})
    assert not hasattr(second, "update")
    assert not hasattr(second, "delete")


# --- the API -------------------------------------------------------------------


def test_approved_question_matching() -> None:
    assert is_approved_question(APPROVED_QUESTION)
    assert is_approved_question("  what should I know about spring registration?  ")
    assert is_approved_question("WHAT SHOULD I KNOW ABOUT SPRING REGISTRATION")
    assert is_approved_question("What should I know about spring registration")
    assert not is_approved_question("Which students are in counseling?")
    assert not is_approved_question("")


def test_findings_endpoint(client: TestClient) -> None:
    response = client.get("/findings")
    assert response.status_code == 200
    body = response.json()
    assert sorted(k for k in body if k != "meta") == [
        "M1",
        "M2",
        "M3",
        "M4",
        "M5",
        "M6",
        "M7",
        "M8",
    ]
    # The executive view keeps row IDs for the evidence drawer.
    assert body["M2"]["value"] == 42
    assert len(body["M2"]["row_ids"]) == 42
    assert body["M1"]["display"] == "−4.8 %"


def test_ask_approved_question(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    response = client.post("/ask", json={"question": APPROVED_QUESTION})
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True
    assert {task["role"] for task in body["tasks"]} == {
        "enrollment_analyst",
        "student_success_analyst",
        "chief_of_staff",
    }
    assert body["findings"]["enrollment_analyst"] == {
        "M1": "−4.8 %",
        "M2": "42",
        "M7": "−2.7 %",
    }
    assert body["findings"]["student_success_analyst"] == {
        "M3": "18",
        "M4": "12",
        "M5": "28 unresolved holds",
        "M8": "22",
    }
    # One /ask runs the whole cabinet and returns the full briefing.
    assert set(body["briefing"]["sections"]) == {"1", "2", "3", "4", "5", "6", "7"}

    events = client.get("/events").json()["events"]
    types = [e["type"] for e in events]
    # /ask dispatches both analysts, runs them (each run grants and, on
    # success, produces), then assigns and grants the Chief of Staff and logs
    # briefing.produced exactly once.
    assert types == [
        "question.asked",
        "task.assigned",
        "task.assigned",
        "data.granted",
        "finding.produced",
        "data.granted",
        "finding.produced",
        "task.assigned",
        "data.granted",
        "briefing.produced",
    ]
    assert [e["id"] for e in events] == body["event_ids"]


def test_ask_out_of_scope_question_is_refused(client: TestClient) -> None:
    """Refusal (b) from ROADMAP §5: refused with a sentence, logged."""
    response = client.post(
        "/ask", json={"question": "Which students are in counseling?"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is False
    assert body["refusal"] == OUT_OF_SCOPE_REFUSAL
    assert "spring registration" in body["refusal"]
    assert "counseling" in body["refusal"]

    events = client.get("/events").json()["events"]
    assert [e["type"] for e in events] == ["question.asked", "data.refused"]
    assert events[1]["actor"] == "chief_of_staff"
    assert events[1]["payload"]["reason"] == OUT_OF_SCOPE_REFUSAL


def test_governance_request_endpoint_shows_refusal_a(client: TestClient) -> None:
    refused = client.post(
        "/governance/request",
        json={"role": "enrollment_analyst", "fields": ["holds.amount"]},
    )
    assert refused.status_code == 200
    body = refused.json()
    assert body["granted"] is False
    assert body["refused_fields"] == ["holds.amount"]
    assert body["event"]["type"] == "data.refused"

    granted = client.post(
        "/governance/request",
        json={"role": "student_success_analyst", "fields": ["holds.amount"]},
    )
    assert granted.status_code == 200
    assert granted.json()["granted"] is True

    refused_events = client.get("/events", params={"type": "data.refused"}).json()[
        "events"
    ]
    assert len(refused_events) == 1
    assert refused_events[0]["payload"]["refused_fields"] == ["holds.amount"]


def test_events_type_filter_and_unknown_type(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    client.post("/ask", json={"question": APPROVED_QUESTION})
    granted = client.get("/events", params={"type": "data.granted"}).json()["events"]
    # Two analyst grants plus the Chief of Staff's aggregate grant.
    assert len(granted) == 3
    assert all(e["type"] == "data.granted" for e in granted)
    assert client.get("/events", params={"type": "bogus"}).status_code == 422


def test_governance_request_rejects_unknown_role_and_empty_fields(
    client: TestClient,
) -> None:
    """Unknown roles and empty field lists are client errors: 422, and no
    audit event — the gate never runs."""
    events_before = client.get("/events").json()["events"]

    unknown = client.post(
        "/governance/request",
        json={"role": "nobody", "fields": ["profile.program"]},
    )
    assert unknown.status_code == 422

    empty = client.post(
        "/governance/request", json={"role": "enrollment_analyst", "fields": []}
    )
    assert empty.status_code == 422

    assert client.get("/events").json()["events"] == events_before


def test_double_approve_creates_exactly_one_task(client: TestClient) -> None:
    first = client.post("/decisions/approve", json={"decision_id": DEMO_DECISION_ID})
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["created"] is True
    task = first_body["task"]
    assert task["status"] == "Waiting for the message to be sent"
    assert task["office"] == "Financial Aid"
    assert task["id"]

    second = client.post("/decisions/approve", json={"decision_id": DEMO_DECISION_ID})
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["created"] is False
    assert second_body["task"] == task

    created = client.get("/events", params={"type": "task.created"}).json()["events"]
    approved = client.get("/events", params={"type": "decision.approved"}).json()[
        "events"
    ]
    assert len(created) == 1
    assert len(approved) == 1

    decisions = client.get("/decisions").json()["decisions"]
    assert decisions[0]["id"] == DEMO_DECISION_ID
    assert decisions[0]["approved"] is True


def test_approve_is_idempotent_across_restart() -> None:
    """A new app instance over the same database creates no second task."""
    first_client = make_authenticated_client(create_app())
    first = first_client.post(
        "/decisions/approve", json={"decision_id": DEMO_DECISION_ID}
    ).json()
    assert first["created"] is True

    restarted_client = make_authenticated_client(create_app())
    second = restarted_client.post(
        "/decisions/approve", json={"decision_id": DEMO_DECISION_ID}
    ).json()
    assert second["created"] is False
    assert second["task"] == first["task"]

    events = restarted_client.get("/events", params={"type": "task.created"}).json()[
        "events"
    ]
    assert len(events) == 1


def test_approve_is_idempotent_under_concurrency(tmp_path: Path) -> None:
    """20 concurrent approves (a double-click will do this) create one task."""
    from concurrent.futures import ThreadPoolExecutor

    client = make_authenticated_client(create_app())

    def approve() -> dict[str, Any]:
        response = client.post(
            "/decisions/approve", json={"decision_id": DEMO_DECISION_ID}
        )
        assert response.status_code == 200
        return response.json()  # type: ignore[no-any-return]

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(lambda _: approve(), range(20)))

    tasks = {json.dumps(r["task"], sort_keys=True) for r in results}
    assert len(tasks) == 1
    assert sum(1 for r in results if r["created"]) == 1
    created = client.get("/events", params={"type": "task.created"}).json()["events"]
    approved = client.get("/events", params={"type": "decision.approved"}).json()[
        "events"
    ]
    assert len(created) == 1
    assert len(approved) == 1


def test_approve_unknown_decision_is_404(client: TestClient) -> None:
    response = client.post("/decisions/approve", json={"decision_id": "D-nope"})
    assert response.status_code == 404


# --- review fixes --------------------------------------------------------------


def test_torn_final_audit_line_is_truncated_on_open(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A process killed mid-write leaves a partial last line. On open the torn
    tail is truncated (a copy kept in a sibling .torn-<ts> file) BEFORE any
    append, so the next append starts on its own line instead of being glued
    onto the fragment — which is what used to make the *next* restart raise
    "tampering" and keep the API from starting."""
    path = tmp_path / "events.jsonl"
    log = AuditLog(path)
    log.append("question.asked", actor="executive", payload={"q": "?"})
    log.append("task.assigned", actor="chief_of_staff", payload={})
    torn = '{"id": 3, "ts": "2026-09-24T00:00:00+00:00", "type": "da'
    with path.open("a", encoding="utf-8") as handle:
        handle.write(torn)

    with caplog.at_level(logging.WARNING):
        restarted = AuditLog(path)
    assert any("truncated" in r.getMessage() for r in caplog.records)

    # The removed bytes are kept, reviewable, next to the log.
    torn_copies = list(tmp_path.glob("events.jsonl.torn-*"))
    assert len(torn_copies) == 1
    assert torn_copies[0].read_text(encoding="utf-8") == torn

    # The log now ends with a newline and serves the intact events.
    assert path.read_bytes().endswith(b"\n")
    assert [e["id"] for e in restarted.events()] == [1, 2]

    event = restarted.append("data.granted", actor="enrollment_analyst", payload={})
    assert event["id"] == 3


def test_torn_tail_then_append_then_restart_is_clean(tmp_path: Path) -> None:
    """The full failure sequence from the audit: torn tail -> open -> append
    -> close -> open again. All good events readable, new ids continue, no
    exception — before the fix, the second open raised 'tampering' because
    the append had been glued onto the torn fragment."""
    path = tmp_path / "events.jsonl"
    first = AuditLog(path)
    first.append("question.asked", actor="executive", payload={"q": "?"})
    first.append("task.assigned", actor="chief_of_staff", payload={})
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"id": 3, "ts": "2026-09-24T00:00:00+00:00", "type": "da')

    second = AuditLog(path)  # truncates the torn tail
    second.append("data.granted", actor="enrollment_analyst", payload={})
    second.append("finding.produced", actor="enrollment_analyst", payload={})
    del second

    third = AuditLog(path)  # must not raise
    events = third.events()
    assert [e["id"] for e in events] == [1, 2, 3, 4]
    assert [e["type"] for e in events] == [
        "question.asked",
        "task.assigned",
        "data.granted",
        "finding.produced",
    ]
    appended = third.append("task.created", actor="chief_of_staff", payload={})
    assert appended["id"] == 5


def test_malformed_middle_audit_line_raises_with_line_number(
    tmp_path: Path,
) -> None:
    """A bad line that is NOT the last one means tampering (the log is
    append-only): a clear error naming the line number."""
    path = tmp_path / "events.jsonl"
    log = AuditLog(path)
    log.append("question.asked", actor="executive", payload={"q": "?"})
    log.append("task.assigned", actor="chief_of_staff", payload={})
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text(lines[0] + "\nnot json\n" + lines[1] + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="malformed audit line 2"):
        AuditLog(path)


def test_second_ask_event_ids_are_exactly_its_own(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ask, briefing, ask again: the second ask's event_ids are the events it
    wrote — ascending, disjoint from the first ask's, none from the briefing."""
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    first = client.post("/ask", json={"question": APPROVED_QUESTION}).json()
    assert client.get("/briefing/enrollment").status_code == 200
    second = client.post("/ask", json={"question": APPROVED_QUESTION}).json()

    ids = second["event_ids"]
    assert ids == sorted(ids)
    # The second ask reuses the cached analyst and chief runs: it writes only
    # question.asked, the three task.assigned, and briefing.produced.
    assert len(ids) == 5
    assert set(ids).isdisjoint(first["event_ids"])
    events = client.get("/events").json()["events"]
    # The second ask's ids are exactly the last five events, in order.
    assert ids == [e["id"] for e in events[-5:]]
    assert [e["type"] for e in events[-5:]] == [
        "question.asked",
        "task.assigned",
        "task.assigned",
        "task.assigned",
        "briefing.produced",
    ]


def test_ask_then_briefing_event_sequence(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """finding.produced belongs to the analyst run that produced it, under the
    same task_id as that task's task.assigned and data.granted; the per-role
    briefing route then reuses the run's cache and writes nothing."""
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    client.post("/ask", json={"question": APPROVED_QUESTION})
    assert client.get("/briefing/enrollment").status_code == 200

    events = client.get("/events").json()["events"]
    assert [e["type"] for e in events] == [
        "question.asked",
        "task.assigned",
        "task.assigned",
        "data.granted",
        "finding.produced",
        "data.granted",
        "finding.produced",
        "task.assigned",
        "data.granted",
        "briefing.produced",
    ]
    enrollment_task = events[1]["payload"]["task_id"]
    assert events[1]["payload"]["role"] == "enrollment_analyst"
    # The enrollment run's grant and product carry the question's task_id.
    assert events[3]["payload"]["task_id"] == enrollment_task
    assert events[4]["payload"]["task_id"] == enrollment_task
    success_task = events[2]["payload"]["task_id"]
    assert events[2]["payload"]["role"] == "student_success_analyst"
    assert events[5]["payload"]["task_id"] == success_task
    assert events[6]["payload"]["task_id"] == success_task
    # The Chief of Staff's task and aggregate grant close the run.
    assert events[7]["payload"]["role"] == "chief_of_staff"
    assert events[8]["payload"]["task_id"] == events[7]["payload"]["task_id"]

    # A page load hits the cache: no new events at all.
    assert client.get("/briefing/enrollment").status_code == 200
    assert client.get("/events").json()["events"] == events


class _CountingProvider(FakeProvider):
    """FakeProvider that counts explain calls."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def explain(self, findings: dict[str, Any], role: str) -> Explanation:
        self.calls += 1
        return super().explain(findings, role)


class _DownProvider:
    name = "down"
    model_label = "down-1"

    def __init__(self) -> None:
        self.calls = 0

    def explain(self, findings: dict[str, Any], role: str) -> Explanation:
        self.calls += 1
        raise ProviderUnavailable("provider is down", provider=self.name)


def test_briefing_is_cached_and_refresh_forces_a_new_run(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _CountingProvider()
    monkeypatch.setattr("cabinet.api.provider_from_env", lambda: provider)

    first = client.get("/briefing/enrollment")
    assert first.status_code == 200
    second = client.get("/briefing/enrollment")
    assert second.status_code == 200
    assert second.json() == first.json()
    assert provider.calls == 1

    # With no prior question, the briefing assigned its own task first; the
    # grant and the produced finding share that task_id.
    events = client.get("/events").json()["events"]
    assert [e["type"] for e in events] == [
        "task.assigned",
        "data.granted",
        "finding.produced",
    ]
    task_id = events[0]["payload"]["task_id"]
    assert task_id == "briefing-enrollment_analyst"
    assert events[1]["payload"]["task_id"] == task_id
    assert events[2]["payload"]["task_id"] == task_id

    # ?refresh=1 and the POST refresh endpoint each force a new run.
    refreshed = client.get("/briefing/enrollment", params={"refresh": "1"})
    assert refreshed.status_code == 200
    assert provider.calls == 2
    assert client.post("/briefing/enrollment/refresh").status_code == 200
    assert provider.calls == 3


def test_briefing_failures_are_not_cached(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _DownProvider()
    monkeypatch.setattr("cabinet.api.provider_from_env", lambda: provider)
    assert client.get("/briefing/enrollment").status_code == 503
    assert client.get("/briefing/enrollment").status_code == 503
    assert provider.calls == 2
    assert client.get("/events", params={"type": "finding.produced"}).json()[
        "events"
    ] == []


def test_page_load_gets_write_no_audit_events(client: TestClient) -> None:
    """Opening the page runs nothing: GET /findings and GET /briefing are the
    only calls the UI makes on load, and neither touches the audit log — the
    analysts and the Chief of Staff run only inside POST /ask."""
    assert client.get("/findings").status_code == 200
    assert client.get("/briefing").status_code == 404  # none produced yet
    assert client.get("/events").json()["events"] == []


def test_decision_text_is_built_from_the_findings(client: TestClient) -> None:
    decision = client.get("/decisions").json()["decisions"][0]
    assert "$1,000 balance threshold" in decision["title"]
    assert "eligibility review" in decision["title"]
    assert "for the 18 continuing students" in decision["text"]
    assert "$1,000" in decision["text"]
    # PROPOSAL.md: a review is authorized; eligibility decisions are out of
    # scope, and approving sends nothing.
    assert "review only" in decision["text"]
    assert decision["follow_up"]["office"] == "Financial Aid"


def test_decision_text_follows_the_findings_values() -> None:
    """No hardcoded counts: M3 = 17 in the findings -> 17 in the decision."""
    modified = json.loads(json.dumps(_findings_obj()))
    modified["M3"]["value"] = 17
    modified["M3"]["display"] = "17"
    text = build_decisions(modified)[0]["text"]
    assert "for the 17 continuing students" in text
    assert "the 18 continuing" not in text
