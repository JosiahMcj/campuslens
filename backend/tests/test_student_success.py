"""Tests for the Student Success Analyst (ROADMAP §3 layer 4).

Mirrors the Enrollment Analyst tests: the analyst receives only its permitted
findings (M3, M4, M5) and every call logs ``data.granted`` listing exactly
those fields; the fake provider's output (including M5's per-office counts)
passes the numeral test; invented or misattributed numbers are rejected and
never shown; a cache hit writes no events; replay is byte-identical with the
network blocked; and handing this role the Enrollment task is refused by the
gate before any provider call (the ``data.refused`` demo).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cabinet.analysts import (
    ENROLLMENT_ANALYST,
    STUDENT_SUCCESS_ANALYST,
    OutputRejected,
    run_analyst,
    validate_explanation,
)
from cabinet.api import create_app
from cabinet.audit import AuditLog
from cabinet.fixture import load_fixture
from cabinet.metrics import findings as compute_findings
from cabinet.permissions import ROLE_TASK_FIELDS, findings_for_role
from cabinet.provider import (
    Explanation,
    FakeProvider,
    replay_filename,
)
from conftest import make_authenticated_client

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO_ROOT / "data" / "fixture.json"
SUCCESS_FIELDS = list(ROLE_TASK_FIELDS[STUDENT_SUCCESS_ANALYST])
ENROLLMENT_FIELDS = list(ROLE_TASK_FIELDS[ENROLLMENT_ANALYST])
# The enrollment task's fields this role may not read (status-only boundary).
MISTAKEN_REFUSED_FIELDS = [
    "enrollment.registered_credit_hours",
    "enrollment.registration_date",
]


@pytest.fixture(scope="module")
def findings_obj() -> dict[str, Any]:
    return compute_findings(load_fixture(FIXTURE_PATH), fixture_path=FIXTURE_PATH)


@pytest.fixture
def received(findings_obj: dict[str, Any]) -> dict[str, Any]:
    return findings_for_role(STUDENT_SUCCESS_ANALYST, findings_obj)


@pytest.fixture
def log(tmp_path: Path) -> AuditLog:
    return AuditLog(tmp_path / "events.jsonl")


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return make_authenticated_client(create_app())


class StubProvider:
    """Returns a fixed text and remembers what findings it received."""

    name = "stub"
    model_label = "stub-1"

    def __init__(self, text: str) -> None:
        self.text = text
        self.received: dict[str, Any] | None = None

    def explain(self, findings: dict[str, Any], role: str) -> Explanation:
        self.received = findings
        return Explanation(
            text=self.text, provider=self.name, model_label=self.model_label
        )


class CountingProvider(FakeProvider):
    """FakeProvider that counts explain calls."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def explain(self, findings: dict[str, Any], role: str) -> Explanation:
        self.calls += 1
        return super().explain(findings, role)


# --- scoping and audit --------------------------------------------------------


def test_analyst_receives_only_m3_m4_m5(
    findings_obj: dict[str, Any], log: AuditLog
) -> None:
    provider = StubProvider("18 students carry a small financial hold [M3].")
    result = run_analyst(STUDENT_SUCCESS_ANALYST, findings_obj, provider, log)
    assert result.available is True
    assert provider.received is not None
    assert sorted(provider.received) == ["M3", "M4", "M5"]


def test_analyst_receives_no_row_ids(
    findings_obj: dict[str, Any], log: AuditLog
) -> None:
    """The model never sees a row ID; the evidence drawer reads them from
    /findings, not from the analyst's scoped findings."""
    provider = StubProvider("12 students have not met with an advisor [M4].")
    result = run_analyst(STUDENT_SUCCESS_ANALYST, findings_obj, provider, log)
    assert result.available is True, result.reason
    dumped = json.dumps(provider.received)
    assert "row_ids" not in dumped
    assert "hold_row_ids" not in dumped
    assert "STU-" not in dumped
    assert "PRI-" not in dumped


def test_every_call_logs_data_granted_with_exact_fields(
    findings_obj: dict[str, Any], log: AuditLog
) -> None:
    provider = StubProvider("18 students carry a small financial hold [M3].")
    run_analyst(STUDENT_SUCCESS_ANALYST, findings_obj, provider, log, task_id="task-a")
    run_analyst(STUDENT_SUCCESS_ANALYST, findings_obj, provider, log, task_id="task-b")

    granted = log.events("data.granted")
    assert len(granted) == 2
    for event in granted:
        assert event["actor"] == STUDENT_SUCCESS_ANALYST
        assert event["payload"]["granted_fields"] == SUCCESS_FIELDS
        # The student-success grant contains no enrollment detail beyond
        # status and term, and no counseling.
        assert "enrollment.registered_credit_hours" not in event["payload"][
            "granted_fields"
        ]
        assert "enrollment.registration_date" not in event["payload"]["granted_fields"]
        assert not any(
            f.startswith("counseling.") for f in event["payload"]["granted_fields"]
        )

    produced = log.events("finding.produced")
    assert len(produced) == 2
    assert produced[0]["payload"]["findings"] == ["M3", "M4", "M5"]


# --- the numeral test ----------------------------------------------------------


def test_numeral_test_passes_on_fake_provider_output(
    findings_obj: dict[str, Any], received: dict[str, Any], log: AuditLog
) -> None:
    """FakeProvider output must pass validation against the real fixture,
    including M5's per-office counts."""
    result = run_analyst(STUDENT_SUCCESS_ANALYST, findings_obj, FakeProvider(), log)
    assert result.available is True, result.reason
    assert result.text is not None
    # M5 is a table: the stub names each office with its count.
    assert "Bursar 24" in result.text
    assert "Registrar 2" in result.text
    claims = validate_explanation(result.text, received)
    assert {fid for c in claims for fid in c.finding_ids} <= {"M3", "M4", "M5"}


def test_numeral_test_rejects_an_invented_number(
    findings_obj: dict[str, Any], received: dict[str, Any], log: AuditLog
) -> None:
    """"19 students" is not in the findings; the output is rejected, logged,
    and returned as unavailable — never shown."""
    provider = StubProvider("19 students carry an unresolved financial hold [M3].")
    result = run_analyst(STUDENT_SUCCESS_ANALYST, findings_obj, provider, log)
    assert result.available is False
    assert result.reason is not None and "19" in result.reason
    assert result.text is None  # unvalidated text never leaves the analyst
    assert log.events("finding.produced") == []

    with pytest.raises(OutputRejected, match="19"):
        validate_explanation(
            "19 students carry an unresolved financial hold [M3].", received
        )


def test_numeral_test_rejects_an_m1_m2_number_cited_under_m3(
    received: dict[str, Any],
) -> None:
    """Per-claim attribution: 42 is M2's number and −4.8 % is M1's; a claim
    citing M3 can use neither, even though another analyst received them."""
    with pytest.raises(OutputRejected, match="'42'"):
        validate_explanation(
            "42 continuing students have not registered [M3].", received
        )
    with pytest.raises(OutputRejected, match="'4.8 %'"):
        validate_explanation(
            "Registration is down 4.8 % because of holds [M3].", received
        )
    # The real numbers, cited under their own findings, pass.
    claims = validate_explanation(
        "18 students carry an unresolved financial hold under the threshold "
        "[M3]. 12 students have not met with an advisor this term [M4].",
        received,
    )
    assert [c.finding_ids for c in claims] == [["M3"], ["M4"]]


def test_claim_must_carry_a_received_finding_id(received: dict[str, Any]) -> None:
    # An ID the student success analyst did not receive.
    with pytest.raises(OutputRejected, match="M2"):
        validate_explanation("42 students have not registered [M2].", received)


def test_rejects_risk_and_score_language_about_students(
    received: dict[str, Any],
) -> None:
    """Students are people who may need support, never risk scores or
    financial units."""
    for text in (
        "18 students are at risk of missing registration [M3].",
        "The risk score for these 12 students [M4].",
    ):
        with pytest.raises(OutputRejected, match="never risk scores"):
            validate_explanation(text, received)


# --- the API ------------------------------------------------------------------


def test_briefing_student_success_with_fake_provider(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    response = client.get("/briefing/student-success")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["provider"] == "fake"
    assert body["model_label"]
    assert "model" not in body  # the model id is never exposed
    assert isinstance(body["text"], str) and body["text"]
    assert body["claims"], "expected at least one claim"
    for claim in body["claims"]:
        assert claim["text"]
        assert claim["finding_ids"]
        assert set(claim["finding_ids"]) <= {"M3", "M4", "M5"}

    granted = client.get("/events", params={"type": "data.granted"}).json()["events"]
    assert granted[-1]["payload"]["granted_fields"] == SUCCESS_FIELDS
    produced = client.get("/events", params={"type": "finding.produced"}).json()[
        "events"
    ]
    assert produced[-1]["payload"]["findings"] == ["M3", "M4", "M5"]
    # With no prior question, the briefing assigned its own task first.
    assert produced[-1]["payload"]["task_id"] == "briefing-student_success_analyst"


def test_briefing_student_success_reuses_the_questions_task(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same task_id rule as enrollment: reuse the latest question's task for
    this role, so finding.produced shares a task_id with its task.assigned."""
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    client.post(
        "/ask", json={"question": "What should I know about spring registration?"}
    )
    assert client.get("/briefing/student-success").status_code == 200

    events = client.get("/events").json()["events"]
    success_task = next(
        e["payload"]["task_id"]
        for e in events
        if e["type"] == "task.assigned"
        and e["payload"]["role"] == STUDENT_SUCCESS_ANALYST
    )
    # The student success run happened inside /ask; the per-role route
    # above was a cache hit and produced nothing new.
    produced = [
        e
        for e in events
        if e["type"] == "finding.produced" and e["actor"] == STUDENT_SUCCESS_ANALYST
    ]
    assert len(produced) == 1
    assert produced[0]["payload"]["task_id"] == success_task


def test_briefing_student_success_without_config_is_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Same 503 behaviour as the enrollment route: no CABINET_LLM_* config ->
    503 {available: false, reason}; metrics and the audit log still work."""
    for var in (
        "CABINET_LLM_BASE_URL",
        "CABINET_LLM_MODEL",
        "CABINET_LLM_LABEL",
        "CABINET_LLM_API_KEY",
        "CABINET_LLM_API_KEY_FILE",
        "CABINET_LLM_API_KEY_VAR",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(
        "cabinet.provider.LOCAL_ENV_PATH", tmp_path / "no-cabinet-local-env"
    )
    monkeypatch.setenv("CABINET_PROVIDER", "chat")
    response = client.get("/briefing/student-success")
    assert response.status_code == 503
    body = response.json()
    assert body["available"] is False
    assert "CABINET_LLM_BASE_URL" in body["reason"]
    assert client.get("/findings").status_code == 200
    assert client.get("/events").status_code == 200


def test_briefing_student_success_cache_hit_writes_no_events(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = CountingProvider()
    monkeypatch.setattr("cabinet.api.provider_from_env", lambda: provider)

    first = client.get("/briefing/student-success")
    assert first.status_code == 200
    events_after_first = client.get("/events").json()["events"]

    second = client.get("/briefing/student-success")
    assert second.status_code == 200
    assert second.json() == first.json()
    assert provider.calls == 1
    # The cache hit wrote nothing: no second grant, no second finding.
    assert client.get("/events").json()["events"] == events_after_first

    # The refresh route forces a new run.
    assert client.post("/briefing/student-success/refresh").status_code == 200
    assert provider.calls == 2


def test_briefing_student_success_replay_roundtrip_sockets_blocked(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    received: dict[str, Any],
) -> None:
    """Record through the API with the fake provider, then replay it with the
    network blocked: the replayed explanation is byte-identical to the
    recorded one and the recording file is untouched."""
    replay_dir = tmp_path / "replay"
    monkeypatch.setenv("CABINET_REPLAY_DIR", str(replay_dir))
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    monkeypatch.setenv("CABINET_RECORD", "1")
    recorded = client.get("/briefing/student-success")
    assert recorded.status_code == 200
    assert recorded.json()["recorded"] is False

    recording = replay_dir / replay_filename(received, STUDENT_SUCCESS_ANALYST)
    assert recording.exists()
    recording_bytes = recording.read_bytes()

    def no_network(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("replay must never touch the network")

    # The provider's only network path is urllib (ChatProvider); block it.
    monkeypatch.setattr("urllib.request.urlopen", no_network)
    monkeypatch.setenv("CABINET_PROVIDER", "replay")
    monkeypatch.delenv("CABINET_RECORD", raising=False)
    replayed = client.get("/briefing/student-success")
    assert replayed.status_code == 200
    assert replayed.json()["text"] == recorded.json()["text"]
    assert replayed.content.decode("utf-8").replace(
        '"recorded":true', '"recorded":false'
    ) == recorded.content.decode("utf-8")
    assert replayed.json()["recorded"] is True
    assert replayed.json()["provider"] == "fake"
    assert recording.read_bytes() == recording_bytes


# --- the mistaken-task refusal --------------------------------------------------


def test_mistaken_enrollment_task_is_refused_before_any_provider_call(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Handing the Student Success Analyst the Enrollment task — the
    enrollment task's field list, which includes fields this role may not
    read — is refused by the gate before any provider call, logged as
    ``data.refused`` naming the refused fields."""
    provider = CountingProvider()
    monkeypatch.setattr("cabinet.api.provider_from_env", lambda: provider)

    response = client.post(
        "/governance/request",
        json={
            "role": STUDENT_SUCCESS_ANALYST,
            "fields": ENROLLMENT_FIELDS,
            "task_id": "task-enrollment_analyst-mistaken",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["granted"] is False
    assert body["role"] == STUDENT_SUCCESS_ANALYST
    assert body["refused_fields"] == MISTAKEN_REFUSED_FIELDS

    # The provider was never called: the gate ran first.
    assert provider.calls == 0

    # The refusal is logged, naming the refused fields; nothing was granted
    # or produced under that task.
    event = body["event"]
    assert event["type"] == "data.refused"
    assert event["actor"] == STUDENT_SUCCESS_ANALYST
    assert event["payload"]["task_id"] == "task-enrollment_analyst-mistaken"
    assert event["payload"]["refused_fields"] == MISTAKEN_REFUSED_FIELDS
    for field in MISTAKEN_REFUSED_FIELDS:
        assert field in event["payload"]["reason"]
    events = client.get("/events").json()["events"]
    assert not any(
        e["type"] in ("data.granted", "finding.produced")
        and e["payload"].get("task_id") == "task-enrollment_analyst-mistaken"
        for e in events
    )
