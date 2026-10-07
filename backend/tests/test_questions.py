"""Tests for the question registry and Q2 (ROADMAP: the second approved
executive question).

Covers: the registry and its matching rule; GET /questions; Q1's behavior
byte-for-byte (its received payloads, prompts, and replay hashes are the
pre-registry ones, so the committed golden run in data/golden/ still replays
identically); Q2 end to end through the fake provider (dispatch, sections,
events, decision, actions); the refusal naming both approved questions;
per-question golden keying; and the numeral validator applied to Q2 output.
"""

from __future__ import annotations

import json
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
    run_analyst,
    validate_explanation,
)
from cabinet.api import APPROVED_QUESTION, OUT_OF_SCOPE_REFUSAL, create_app
from cabinet.audit import AuditLog
from cabinet.fixture import load_fixture
from cabinet.metrics import findings as compute_findings
from cabinet.permissions import ROLE_FINDINGS, findings_for_role
from cabinet.provider import (
    GOLDEN_REPLAY_DIR,
    Explanation,
    ProviderUnavailable,
    ReplayProvider,
    replay_filename,
)
from cabinet.questions import (
    DEFAULT_QUESTION,
    QUESTION_KEY,
    QUESTIONS,
    SPRING_REGISTRATION,
    UNRESOLVED_HOLDS,
    UNRESOLVED_HOLDS_DECISION_ID,
    match_question,
    received_for,
)
from conftest import make_authenticated_client

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO_ROOT / "data" / "fixture.json"

Q2_TEXT = "Where are unresolved holds affecting continued enrollment?"


@pytest.fixture(scope="module")
def findings_obj() -> dict[str, Any]:
    return compute_findings(load_fixture(FIXTURE_PATH), fixture_path=FIXTURE_PATH)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return make_authenticated_client(create_app())


@pytest.fixture
def log(tmp_path: Path) -> AuditLog:
    return AuditLog(tmp_path / "events.jsonl")


# --- the registry and its matching rule ----------------------------------------


def test_registry_lists_both_approved_questions() -> None:
    assert [q.id for q in QUESTIONS] == ["spring-registration", "unresolved-holds"]
    assert QUESTIONS[0].text == APPROVED_QUESTION
    assert QUESTIONS[1].text == Q2_TEXT
    assert DEFAULT_QUESTION is SPRING_REGISTRATION
    # Dispatch is always a subset of the role's standing findings.
    for question in QUESTIONS:
        for role, finding_ids in question.dispatch.items():
            assert set(finding_ids) <= set(ROLE_FINDINGS[role])


def test_q2_dispatch() -> None:
    assert UNRESOLVED_HOLDS.dispatch[STUDENT_SUCCESS_ANALYST] == ("M3", "M5")
    assert UNRESOLVED_HOLDS.dispatch[ENROLLMENT_ANALYST] == ("M2",)
    # M6 is aggregate-only for the chief; the chief receives M2, M3, M5, M6.
    assert UNRESOLVED_HOLDS.dispatch[CHIEF_OF_STAFF] == ("M2", "M3", "M5", "M6")
    assert "M6" not in UNRESOLVED_HOLDS.dispatch[ENROLLMENT_ANALYST]
    assert "M6" not in UNRESOLVED_HOLDS.dispatch[STUDENT_SUCCESS_ANALYST]


def test_match_question_normalizes_like_before() -> None:
    assert match_question(APPROVED_QUESTION) is SPRING_REGISTRATION
    assert match_question("  what should I KNOW about spring registration? ") is (
        SPRING_REGISTRATION
    )
    assert match_question("What should I know about spring registration") is (
        SPRING_REGISTRATION
    )
    assert match_question(Q2_TEXT) is UNRESOLVED_HOLDS
    assert match_question(Q2_TEXT.lower().rstrip("?")) is UNRESOLVED_HOLDS
    assert match_question(Q2_TEXT.upper()) is UNRESOLVED_HOLDS
    assert match_question("Which students are in counseling?") is None
    assert match_question("") is None


def test_questions_endpoint(client: TestClient) -> None:
    response = client.get("/questions")
    assert response.status_code == 200
    assert response.json() == [
        {"id": "spring-registration", "text": APPROVED_QUESTION},
        {"id": "unresolved-holds", "text": Q2_TEXT},
    ]


# --- Q1 byte-for-byte: payloads, prompts, and the committed golden run ----------


def test_q1_received_is_the_pre_registry_payload(
    findings_obj: dict[str, Any]
) -> None:
    """Q1's received payload is exactly findings_for_role's: no QUESTION_KEY,
    same dispatch — so its replay hashes (and golden files) never move."""
    for role in (ENROLLMENT_ANALYST, STUDENT_SUCCESS_ANALYST, CHIEF_OF_STAFF):
        received = received_for(SPRING_REGISTRATION, role, findings_obj)
        assert received == findings_for_role(role, findings_obj)
        assert QUESTION_KEY not in received
    texts = {ENROLLMENT_ANALYST: "a [M1].", STUDENT_SUCCESS_ANALYST: "b [M3]."}
    assert chief_received(findings_obj, texts, SPRING_REGISTRATION) == (
        chief_received(findings_obj, texts)
    )


def test_q1_prompt_has_no_question_line_q2_prompt_has_it(
    findings_obj: dict[str, Any]
) -> None:
    """The user prompt gains "The president asked: …" only for Q2, so Q1's
    prompt bytes — and therefore its golden hashes — do not change."""
    for role in (ENROLLMENT_ANALYST, STUDENT_SUCCESS_ANALYST):
        q1 = received_for(SPRING_REGISTRATION, role, findings_obj)
        # Q1's prompt is byte-identical to the pre-registry build_prompt.
        assert build_prompt(q1, role) == build_prompt(
            findings_for_role(role, findings_obj), role
        )
        q2_system, q2_user = build_prompt(
            received_for(UNRESOLVED_HOLDS, role, findings_obj), role
        )
        assert q2_user.startswith(f"The president asked: {Q2_TEXT}\n")
        assert "The president asked" not in build_prompt(q1, role)[1]

    texts = {ENROLLMENT_ANALYST: "a [M1].", STUDENT_SUCCESS_ANALYST: "b [M3]."}
    chief_q1 = chief_received(findings_obj, texts, SPRING_REGISTRATION)
    chief_q2 = chief_received(findings_obj, texts, UNRESOLVED_HOLDS)
    assert "The president asked" not in build_prompt(chief_q1, CHIEF_OF_STAFF)[1]
    chief_q2_prompt = build_prompt(chief_q2, CHIEF_OF_STAFF)[1]
    assert chief_q2_prompt.startswith(f"The president asked: {Q2_TEXT}\n")
    # The chief's Q2 received carries the question id, keying its hash apart.
    assert chief_q2[QUESTION_KEY]["id"] == "unresolved-holds"
    assert replay_filename(chief_q2, CHIEF_OF_STAFF) != replay_filename(
        chief_q1, CHIEF_OF_STAFF
    )


def test_q1_replays_byte_identical_to_the_committed_golden_run(
    findings_obj: dict[str, Any]
) -> None:
    """The golden files committed for Q1 still key to Q1's received payloads
    and serve their text unchanged — the replay of the existing question is
    byte-identical to today's golden files."""
    replay = ReplayProvider(replay_dirs=[GOLDEN_REPLAY_DIR])
    analyst_texts: dict[str, str] = {}
    for role in (ENROLLMENT_ANALYST, STUDENT_SUCCESS_ANALYST):
        received = received_for(SPRING_REGISTRATION, role, findings_obj)
        golden_file = GOLDEN_REPLAY_DIR / replay_filename(received, role)
        assert golden_file.exists(), f"Q1 golden file moved: {golden_file}"
        recorded = json.loads(golden_file.read_text(encoding="utf-8"))
        explanation = replay.explain(received, role)
        assert explanation.text == recorded["text"]
        assert explanation.recorded is True
        analyst_texts[role] = explanation.text
        # The replayed text still passes validation against what Q1 receives.
        validate_explanation(explanation.text, received)

    received = chief_received(findings_obj, analyst_texts, SPRING_REGISTRATION)
    golden_file = GOLDEN_REPLAY_DIR / replay_filename(received, CHIEF_OF_STAFF)
    assert golden_file.exists(), f"Q1 chief golden file moved: {golden_file}"
    recorded = json.loads(golden_file.read_text(encoding="utf-8"))
    explanation = replay.explain(received, CHIEF_OF_STAFF)
    assert explanation.text == recorded["text"]


# --- Q2 end to end through the fake provider -------------------------------------


def test_ask_q2_runs_the_whole_cabinet(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    response = client.post("/ask", json={"question": Q2_TEXT})
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True
    assert body["question_id"] == "unresolved-holds"
    assert body["question"] == Q2_TEXT
    assert {task["role"] for task in body["tasks"]} == {
        ENROLLMENT_ANALYST,
        STUDENT_SUCCESS_ANALYST,
        CHIEF_OF_STAFF,
    }
    # The dispatch scopes each role's findings for this question.
    assert body["findings"][ENROLLMENT_ANALYST] == {"M2": "42"}
    assert body["findings"][STUDENT_SUCCESS_ANALYST] == {
        "M3": "18",
        "M5": "28 unresolved holds",
    }

    briefing = body["briefing"]
    assert briefing["question_id"] == "unresolved-holds"
    assert briefing["question"] == Q2_TEXT
    sections = briefing["sections"]
    assert set(sections) == {"1", "2", "3", "4", "5", "6", "7"}
    for section_id in ("1", "2", "3", "7"):
        assert sections[section_id]["kind"] == "available"
        assert sections[section_id]["claims"]
    # Section 2 ("current measure and historical comparison") carries the
    # enrollment claims; section 3 the student-success claims.
    for claim in sections["2"]["claims"]:
        assert set(claim["finding_ids"]) <= {"M2"}
    for claim in sections["3"]["claims"]:
        assert set(claim["finding_ids"]) <= {"M3", "M5"}
    for claim in sections["1"]["claims"] + sections["7"]["claims"]:
        assert set(claim["finding_ids"]) <= {"M2", "M3", "M5", "M6"}

    # Section 5: one action per office from M5's rows, plus Financial Aid for
    # the M3 count.
    actions = sections["5"]["actions"]
    assert [a["office"] for a in actions] == [
        "Bursar",
        "Library",
        "Registrar",
        "Student Life",
        "Financial Aid",
    ]
    assert "24 unresolved holds" in actions[0]["text"]
    assert "18 small-balance cases" in actions[-1]["text"]
    assert "$1,000" in actions[-1]["text"]

    # Section 6: the coordinated hold-resolution review led by the Bursar.
    decisions = sections["6"]["decisions"]
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision["id"] == UNRESOLVED_HOLDS_DECISION_ID
    assert "28 unresolved holds" in decision["text"]
    assert "$1,000" in decision["text"]
    assert "review only" in decision["text"]
    assert decision["follow_up"]["office"] == "Bursar"

    # The audit log: same shape as Q1's run, keyed to this question.
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
    assert events[0]["payload"]["question_id"] == "unresolved-holds"
    produced = events[-1]["payload"]
    assert produced["question_id"] == "unresolved-holds"
    assert produced["question_event_id"] == briefing["question_event_id"]
    # The analysts' tasks carry the question's dispatch, not the full split.
    assigned = {e["payload"]["role"]: e["payload"] for e in events[1:3]}
    assert assigned[ENROLLMENT_ANALYST]["findings"] == ["M2"]
    assert assigned[STUDENT_SUCCESS_ANALYST]["findings"] == ["M3", "M5"]
    produced_events = [
        e for e in events if e["type"] == "finding.produced"
    ]
    assert produced_events[0]["payload"]["findings"] == ["M2"]
    assert produced_events[1]["payload"]["findings"] == ["M3", "M5"]
    assert [e["id"] for e in events] == body["event_ids"]


def test_q2_then_q1_caches_are_keyed_per_question(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Asking Q2 does not disturb Q1's cached runs, and vice versa."""
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    q1_first = client.post("/ask", json={"question": APPROVED_QUESTION}).json()
    q2 = client.post("/ask", json={"question": Q2_TEXT}).json()
    q1_again = client.post("/ask", json={"question": APPROVED_QUESTION}).json()

    assert q1_again["briefing"]["sections"] == q1_first["briefing"]["sections"]
    assert q1_again["briefing"]["question_id"] == "spring-registration"
    assert q2["briefing"]["question_id"] == "unresolved-holds"
    # Q1's re-ask reused its cache: only question.asked, three task.assigned,
    # and briefing.produced.
    events = client.get("/events").json()["events"]
    assert [e["type"] for e in events[-5:]] == [
        "question.asked",
        "task.assigned",
        "task.assigned",
        "task.assigned",
        "briefing.produced",
    ]


def test_get_briefing_returns_the_latest_briefing_and_its_question(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    assert client.get("/briefing").status_code == 404
    asked = client.post("/ask", json={"question": Q2_TEXT}).json()
    served = client.get("/briefing")
    assert served.status_code == 200
    assert served.json() == asked["briefing"]
    assert served.json()["question_id"] == "unresolved-holds"
    assert served.json()["question"] == Q2_TEXT


def test_refusal_names_both_approved_questions(client: TestClient) -> None:
    response = client.post(
        "/ask", json={"question": "Which students are in counseling?"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is False
    assert body["refusal"] == OUT_OF_SCOPE_REFUSAL
    assert APPROVED_QUESTION in body["refusal"]
    assert Q2_TEXT in body["refusal"]
    assert "counseling" in body["refusal"]

    events = client.get("/events").json()["events"]
    assert [e["type"] for e in events] == ["question.asked", "data.refused"]
    assert events[0]["payload"]["question_id"] is None
    assert events[1]["payload"]["reason"] == OUT_OF_SCOPE_REFUSAL


# --- decisions per question -----------------------------------------------------


def test_decisions_follow_the_latest_question(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    # Before anything is asked: the default question's decision.
    decisions = client.get("/decisions").json()
    assert decisions["question_id"] == "spring-registration"
    assert decisions["decisions"][0]["id"] == "D-spring-registration-1"

    client.post("/ask", json={"question": Q2_TEXT})
    decisions = client.get("/decisions").json()
    assert decisions["question_id"] == "unresolved-holds"
    assert decisions["decisions"][0]["id"] == UNRESOLVED_HOLDS_DECISION_ID
    assert decisions["decisions"][0]["approved"] is False


def test_each_questions_decision_approves_idempotently(client: TestClient) -> None:
    for decision_id, office in (
        ("D-spring-registration-1", "Financial Aid"),
        (UNRESOLVED_HOLDS_DECISION_ID, "Bursar"),
    ):
        first = client.post("/decisions/approve", json={"decision_id": decision_id})
        assert first.status_code == 200
        assert first.json()["created"] is True
        assert first.json()["task"]["office"] == office
        assert first.json()["task"]["status"] == "Waiting for the message to be sent"
        second = client.post(
            "/decisions/approve", json={"decision_id": decision_id}
        )
        assert second.json()["created"] is False
        assert second.json()["task"] == first.json()["task"]

    created = client.get("/events", params={"type": "task.created"}).json()["events"]
    assert len(created) == 2


# --- per-question golden keying and validation on Q2 output ----------------------


def test_received_payloads_key_apart_per_question(
    findings_obj: dict[str, Any]
) -> None:
    for role in (ENROLLMENT_ANALYST, STUDENT_SUCCESS_ANALYST):
        q1 = received_for(SPRING_REGISTRATION, role, findings_obj)
        q2 = received_for(UNRESOLVED_HOLDS, role, findings_obj)
        assert replay_filename(q1, role) != replay_filename(q2, role)
        assert q2[QUESTION_KEY] == {
            "id": "unresolved-holds",
            "text": Q2_TEXT,
            "brief": UNRESOLVED_HOLDS.chief_brief,
        }
        # Row IDs are stripped for every question, like every role.
        assert "STU-" not in json.dumps(q2)


def test_numeral_validation_applies_to_q2_output(
    findings_obj: dict[str, Any], log: AuditLog
) -> None:
    """A provider that invents a number under Q2's dispatch is rejected
    exactly as under Q1: unavailable, never shown, nothing recorded."""

    class InventingProvider:
        name = "inventing"
        model_label = "inventing-1"

        def explain(self, findings: dict[str, Any], role: str) -> Explanation:
            return Explanation(
                text="There are 99 unresolved holds [M5].",
                provider=self.name,
                model_label=self.model_label,
            )

    result = run_analyst(
        STUDENT_SUCCESS_ANALYST,
        findings_obj,
        InventingProvider(),
        log,
        question=UNRESOLVED_HOLDS,
    )
    assert result.available is False
    assert "failed validation" in str(result.reason)
    assert log.events("finding.produced") == []

    class DownProvider:
        name = "down"
        model_label = "down-1"

        def explain(self, findings: dict[str, Any], role: str) -> Explanation:
            raise ProviderUnavailable("provider is down", provider=self.name)

    assert (
        run_analyst(
            ENROLLMENT_ANALYST,
            findings_obj,
            DownProvider(),
            log,
            question=UNRESOLVED_HOLDS,
        ).available
        is False
    )
