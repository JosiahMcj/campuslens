"""Tests for the corrective retry after a validation failure.

When a provider's output fails validation, the analyst and Chief of Staff
runners call the provider once more (``CABINET_VALIDATION_RETRIES``, default
1, 0 turns it off) with the same inputs plus one plain-words correction, and
validate again. Covers: invalid then valid is shown with one retry recorded on
the audit trail; invalid twice is unavailable; retries=0 is unavailable after
one try; the correction states the reason and carries no finding value the
role did not receive; the replay provider never takes the retry path; a
recording is written only for the validated answer, under the original key;
and the chat provider sends exactly the original messages plus the
correction.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cabinet.analysts import (
    CHIEF_OF_STAFF,
    ENROLLMENT_ANALYST,
    STUDENT_SUCCESS_ANALYST,
    OutputRejected,
    chief_received,
    correction_message,
    run_analyst,
    run_chief_of_staff,
    validate_explanation,
    validation_retries_from_env,
)
from cabinet.api import APPROVED_QUESTION, create_app
from cabinet.audit import AuditLog
from cabinet.fixture import load_fixture
from cabinet.metrics import findings as compute_findings
from cabinet.provider import (
    ChatProvider,
    Explanation,
    FakeProvider,
    RecordingProvider,
    ReplayProvider,
    replay_filename,
)
from cabinet.questions import DEFAULT_QUESTION, received_for
from conftest import make_authenticated_client

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO_ROOT / "data" / "fixture.json"

# The Student Success Analyst receives M3, M4, M5, M8. This answer cites M7
# (credit hours, an Enrollment finding) and quotes its value.
CITES_M7 = "Registered credit hours are down 2.7% versus last year [M7]."
NOT_JSON = "Here is the briefing: spring registration is down [M1]."


@pytest.fixture(scope="module")
def findings_obj() -> dict[str, Any]:
    return compute_findings(load_fixture(FIXTURE_PATH), fixture_path=FIXTURE_PATH)


@pytest.fixture
def log(tmp_path: Path) -> AuditLog:
    return AuditLog(tmp_path / "events.jsonl")


class ScriptedProvider(FakeProvider):
    """FakeProvider that first answers with scripted invalid texts per role,
    then with the stub's valid answer. Remembers every call."""

    def __init__(self, bad: dict[str, list[str]]) -> None:
        super().__init__()
        self.bad = {role: list(texts) for role, texts in bad.items()}
        self.calls: list[dict[str, Any]] = []

    def _answer(self, findings: dict[str, Any], role: str) -> Explanation:
        queue = self.bad.get(role)
        if queue:
            return Explanation(
                text=queue.pop(0), provider=self.name, model_label=self.model_label
            )
        return super().explain(findings, role)

    def explain(self, findings: dict[str, Any], role: str) -> Explanation:
        self.calls.append({"kind": "explain", "role": role, "findings": findings})
        return self._answer(findings, role)

    def correct(
        self, findings: dict[str, Any], role: str, correction: str
    ) -> Explanation:
        self.calls.append(
            {
                "kind": "correct",
                "role": role,
                "findings": findings,
                "correction": correction,
            }
        )
        return self._answer(findings, role)


def _produced(log: AuditLog) -> list[dict[str, Any]]:
    return log.events("finding.produced")


# --- the analyst runner --------------------------------------------------------


def test_invalid_then_valid_is_shown_with_one_retry_recorded(
    findings_obj: dict[str, Any], log: AuditLog
) -> None:
    provider = ScriptedProvider({STUDENT_SUCCESS_ANALYST: [CITES_M7]})
    result = run_analyst(STUDENT_SUCCESS_ANALYST, findings_obj, provider, log)

    assert result.available is True
    assert result.validation_retries == 1
    assert "[M7]" not in str(result.text)
    received = received_for(DEFAULT_QUESTION, STUDENT_SUCCESS_ANALYST, findings_obj)
    # What is shown is the validated second answer.
    validate_explanation(str(result.text), received)
    assert [call["kind"] for call in provider.calls] == ["explain", "correct"]
    # The corrective try gets exactly the same inputs as the first.
    assert provider.calls[1]["findings"] == provider.calls[0]["findings"] == received

    produced = _produced(log)
    assert len(produced) == 1
    assert produced[0]["payload"]["validation_retries"] == 1
    # One grant only: the retry is not a new task.
    assert len(log.events("data.granted")) == 1


def test_valid_first_try_makes_no_retry(
    findings_obj: dict[str, Any], log: AuditLog
) -> None:
    provider = ScriptedProvider({})
    result = run_analyst(ENROLLMENT_ANALYST, findings_obj, provider, log)
    assert result.available is True
    assert result.validation_retries == 0
    assert [call["kind"] for call in provider.calls] == ["explain"]
    assert _produced(log)[0]["payload"]["validation_retries"] == 0


def test_invalid_twice_is_unavailable(
    findings_obj: dict[str, Any], log: AuditLog
) -> None:
    provider = ScriptedProvider({STUDENT_SUCCESS_ANALYST: [CITES_M7, CITES_M7]})
    result = run_analyst(STUDENT_SUCCESS_ANALYST, findings_obj, provider, log)

    assert result.available is False
    assert result.text is None
    assert result.claims == []
    assert result.validation_retries == 1
    assert result.reason == (
        "the model's output failed validation: claim cites finding(s) M7 the "
        "analyst did not receive"
    )
    assert [call["kind"] for call in provider.calls] == ["explain", "correct"]
    assert _produced(log) == []


def test_retries_zero_is_unavailable_after_one_try(
    findings_obj: dict[str, Any], log: AuditLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CABINET_VALIDATION_RETRIES", "0")
    provider = ScriptedProvider({STUDENT_SUCCESS_ANALYST: [CITES_M7]})
    result = run_analyst(STUDENT_SUCCESS_ANALYST, findings_obj, provider, log)

    assert result.available is False
    assert result.validation_retries == 0
    assert [call["kind"] for call in provider.calls] == ["explain"]
    assert _produced(log) == []


def test_correction_states_the_reason_and_no_unreceived_value(
    findings_obj: dict[str, Any], log: AuditLog
) -> None:
    provider = ScriptedProvider({STUDENT_SUCCESS_ANALYST: [CITES_M7]})
    run_analyst(STUDENT_SUCCESS_ANALYST, findings_obj, provider, log)
    correction = provider.calls[1]["correction"]

    assert correction.startswith(
        "Your previous answer cited M7, which you did not receive. "
        "Use only the findings you were given."
    )
    received = received_for(DEFAULT_QUESTION, STUDENT_SUCCESS_ANALYST, findings_obj)
    not_received = [
        finding_id
        for finding_id in findings_obj
        if re.fullmatch(r"M\d+", finding_id) and finding_id not in received
    ]
    assert "M7" in not_received
    for finding_id in not_received:
        finding = findings_obj[finding_id]
        for value in (finding.get("display"), finding.get("value")):
            if value not in (None, "", "--"):
                assert str(value) not in correction
    # Beyond finding IDs, the correction carries no number at all: no data is
    # added, not even the value the model quoted.
    assert re.search(r"\d", re.sub(r"\bM\d+\b", "", correction)) is None


def test_correction_for_other_reasons_quotes_the_validator(
    findings_obj: dict[str, Any],
) -> None:
    received = received_for(DEFAULT_QUESTION, ENROLLMENT_ANALYST, findings_obj)
    with pytest.raises(OutputRejected) as caught:
        validate_explanation("Spring registration is 99 students [M2].", received)
    correction = correction_message(caught.value, ENROLLMENT_ANALYST)
    assert correction.startswith(
        "Your previous answer was rejected because numeral '99' does not exist "
        "in the cited findings (M2)."
    )
    assert "Write your answer again." in correction


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, 1), ("", 1), ("0", 0), ("1", 1), ("2", 2), ("99", 3), ("-1", 1), ("x", 1)],
)
def test_retry_setting(
    raw: str | None, expected: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    if raw is None:
        monkeypatch.delenv("CABINET_VALIDATION_RETRIES", raising=False)
    else:
        monkeypatch.setenv("CABINET_VALIDATION_RETRIES", raw)
    assert validation_retries_from_env() == expected


# --- replay and recording ------------------------------------------------------


def test_replay_never_takes_the_retry_path(
    findings_obj: dict[str, Any], log: AuditLog, tmp_path: Path
) -> None:
    """A recording was validated when it was made; one that fails today is
    unavailable with no second call. ReplayProvider offers no ``correct``."""
    assert not hasattr(ReplayProvider, "correct")
    replay_dir = tmp_path / "replay"
    replay_dir.mkdir()
    received = received_for(DEFAULT_QUESTION, STUDENT_SUCCESS_ANALYST, findings_obj)
    path = replay_dir / replay_filename(received, STUDENT_SUCCESS_ANALYST)
    path.write_text(
        json.dumps({"provider": "chat", "model_label": "live", "text": CITES_M7}),
        encoding="utf-8",
    )
    replay = ReplayProvider(replay_dirs=[replay_dir])
    result = run_analyst(STUDENT_SUCCESS_ANALYST, findings_obj, replay, log)
    assert result.available is False
    assert result.validation_retries == 0
    assert result.recorded is True


def test_a_recorded_answer_never_retries_even_if_the_provider_could(
    findings_obj: dict[str, Any], log: AuditLog
) -> None:
    class RecordedScripted(ScriptedProvider):
        def explain(self, findings: dict[str, Any], role: str) -> Explanation:
            answer = super().explain(findings, role)
            return Explanation(
                text=answer.text,
                provider=answer.provider,
                model_label=answer.model_label,
                recorded=True,
            )

    provider = RecordedScripted({STUDENT_SUCCESS_ANALYST: [CITES_M7]})
    result = run_analyst(STUDENT_SUCCESS_ANALYST, findings_obj, provider, log)
    assert result.available is False
    assert [call["kind"] for call in provider.calls] == ["explain"]


def test_recording_holds_only_the_validated_answer_under_the_original_key(
    findings_obj: dict[str, Any], log: AuditLog, tmp_path: Path
) -> None:
    replay_dir = tmp_path / "record"
    inner = ScriptedProvider({STUDENT_SUCCESS_ANALYST: [CITES_M7]})
    recorder = RecordingProvider(inner, replay_dir=replay_dir)
    result = run_analyst(STUDENT_SUCCESS_ANALYST, findings_obj, recorder, log)
    assert result.available is True
    assert result.validation_retries == 1

    received = received_for(DEFAULT_QUESTION, STUDENT_SUCCESS_ANALYST, findings_obj)
    files = sorted(replay_dir.iterdir())
    assert files == [replay_dir / replay_filename(received, STUDENT_SUCCESS_ANALYST)]
    recorded = json.loads(files[0].read_text(encoding="utf-8"))
    assert recorded["text"] == result.text
    assert "[M7]" not in recorded["text"]


def test_recording_provider_without_a_correctable_inner_never_retries(
    findings_obj: dict[str, Any], log: AuditLog, tmp_path: Path
) -> None:
    class ExplainOnly:
        name = "explain-only"
        model_label = "explain-only-1"

        def __init__(self) -> None:
            self.calls = 0

        def explain(self, findings: dict[str, Any], role: str) -> Explanation:
            self.calls += 1
            return Explanation(
                text=CITES_M7, provider=self.name, model_label=self.model_label
            )

    inner = ExplainOnly()
    recorder = RecordingProvider(inner, replay_dir=tmp_path / "record")
    result = run_analyst(STUDENT_SUCCESS_ANALYST, findings_obj, recorder, log)
    assert result.available is False
    assert inner.calls == 1
    assert not (tmp_path / "record").exists()


# --- the Chief of Staff --------------------------------------------------------


def _analyst_texts(findings_obj: dict[str, Any]) -> dict[str, str]:
    fake = FakeProvider()
    return {
        role: fake.explain(
            received_for(DEFAULT_QUESTION, role, findings_obj), role
        ).text
        for role in (ENROLLMENT_ANALYST, STUDENT_SUCCESS_ANALYST)
    }


def test_chief_invalid_then_valid_is_shown_with_one_retry(
    findings_obj: dict[str, Any], log: AuditLog
) -> None:
    provider = ScriptedProvider({CHIEF_OF_STAFF: [NOT_JSON]})
    result = run_chief_of_staff(
        findings_obj, _analyst_texts(findings_obj), provider, log, "task-chief"
    )
    assert result.available is True
    assert result.validation_retries == 1
    assert result.executive_summary
    correction = provider.calls[1]["correction"]
    assert correction.startswith(
        "Your previous answer was rejected because the Chief of Staff's answer "
        "is not a JSON object only"
    )
    assert '{"executive_summary": "...", "limitations": "..."}' in correction
    received = chief_received(findings_obj, _analyst_texts(findings_obj))
    assert provider.calls[1]["findings"] == received
    # One aggregate grant only.
    assert len(log.events("data.granted")) == 1


def test_chief_invalid_twice_is_unavailable(
    findings_obj: dict[str, Any], log: AuditLog
) -> None:
    provider = ScriptedProvider({CHIEF_OF_STAFF: [NOT_JSON, NOT_JSON]})
    result = run_chief_of_staff(
        findings_obj, _analyst_texts(findings_obj), provider, log, "task-chief"
    )
    assert result.available is False
    assert result.executive_summary is None
    assert result.limitations is None
    assert result.text is None
    assert result.validation_retries == 1
    assert str(result.reason).startswith("the model's output failed validation:")


def test_chief_retries_zero_is_unavailable_after_one_try(
    findings_obj: dict[str, Any], log: AuditLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CABINET_VALIDATION_RETRIES", "0")
    provider = ScriptedProvider({CHIEF_OF_STAFF: [NOT_JSON]})
    result = run_chief_of_staff(
        findings_obj, _analyst_texts(findings_obj), provider, log, "task-chief"
    )
    assert result.available is False
    assert result.validation_retries == 0
    assert [call["kind"] for call in provider.calls] == ["explain"]


# --- the chat provider's corrective request ------------------------------------


def test_chat_correct_sends_the_same_messages_plus_the_correction(
    findings_obj: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CABINET_LLM_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("CABINET_LLM_MODEL", "test-model")
    monkeypatch.setenv("CABINET_LLM_API_KEY", "test-key")
    sent: list[dict[str, Any]] = []

    def fake_post(
        self: ChatProvider,
        url: str,
        key: str,
        payload: dict[str, Any],
        timeout: float = 55,
    ) -> dict[str, Any]:
        sent.append(payload)
        return {
            "choices": [
                {"finish_reason": "stop", "message": {"content": "ok [M1]."}}
            ]
        }

    monkeypatch.setattr(ChatProvider, "_post_with_retry", fake_post)
    received = received_for(DEFAULT_QUESTION, ENROLLMENT_ANALYST, findings_obj)
    chat = ChatProvider()
    chat.explain(received, ENROLLMENT_ANALYST)
    chat.correct(received, ENROLLMENT_ANALYST, "Use only the findings you were given.")

    first, second = sent
    assert second["messages"][:2] == first["messages"]
    assert second["messages"][2] == {
        "role": "user",
        "content": "Use only the findings you were given.",
    }
    assert len(second["messages"]) == 3
    assert {k: v for k, v in second.items() if k != "messages"} == {
        k: v for k, v in first.items() if k != "messages"
    }


# --- through the API -----------------------------------------------------------


def test_ask_records_the_retry_counts_on_the_existing_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = ScriptedProvider(
        {STUDENT_SUCCESS_ANALYST: [CITES_M7], CHIEF_OF_STAFF: [NOT_JSON]}
    )
    monkeypatch.setattr("cabinet.api.provider_from_env", lambda: provider)
    client: TestClient = make_authenticated_client(create_app())

    response = client.post("/ask", json={"question": APPROVED_QUESTION})
    assert response.status_code == 200
    sections = response.json()["briefing"]["sections"]
    for section in ("1", "2", "3", "7"):
        assert sections[section]["kind"] == "available", section

    events = client.get("/events").json()["events"]
    types = {event["type"] for event in events}
    assert not any("retry" in event_type for event_type in types)
    produced = {
        event["actor"]: event["payload"]["validation_retries"]
        for event in events
        if event["type"] == "finding.produced"
    }
    assert produced == {ENROLLMENT_ANALYST: 0, STUDENT_SUCCESS_ANALYST: 1}
    (briefing,) = [e for e in events if e["type"] == "briefing.produced"]
    assert briefing["payload"]["validation_retries"] == {
        ENROLLMENT_ANALYST: 0,
        STUDENT_SUCCESS_ANALYST: 1,
        CHIEF_OF_STAFF: 1,
    }


def test_ask_section_is_unavailable_after_two_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = ScriptedProvider({STUDENT_SUCCESS_ANALYST: [CITES_M7, CITES_M7]})
    monkeypatch.setattr("cabinet.api.provider_from_env", lambda: provider)
    client: TestClient = make_authenticated_client(create_app())

    response = client.post("/ask", json={"question": APPROVED_QUESTION})
    assert response.status_code == 200
    sections = response.json()["briefing"]["sections"]
    assert sections["3"]["kind"] == "unavailable"
    assert "M7" in sections["3"]["reason"]
    assert "2.7" not in json.dumps(sections["3"])
    events = client.get("/events").json()["events"]
    (briefing,) = [e for e in events if e["type"] == "briefing.produced"]
    assert briefing["payload"]["validation_retries"][STUDENT_SUCCESS_ANALYST] == 1
