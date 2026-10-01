"""Tests for the Chief of Staff (ROADMAP §3 layers 4-5).

Covers: the Chief of Staff receives only aggregate findings (M1–M7, row IDs
stripped) plus the two analysts' validated explanations — never student rows,
never the counseling group; its two-section output is JSON-only and goes
through the same validation as the analysts (claim IDs, numeral check per
claim, direction, dates, risk wording, student IDs) with a structural
five-sentence cap on the executive summary; POST /ask runs the whole cabinet
with exactly one ``briefing.produced``; GET /briefing serves the produced
briefing without writing events; an unavailable analyst degrades the briefing
without invented text; and the whole /ask replays from recordings with the
network blocked.
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
    OutputRejected,
    chief_aggregate_fields,
    count_sentences,
    run_chief_of_staff,
    validate_chief_output,
)
from cabinet.api import APPROVED_QUESTION, create_app
from cabinet.audit import AuditLog
from cabinet.fixture import load_fixture
from cabinet.metrics import findings as compute_findings
from cabinet.permissions import findings_for_role
from cabinet.provider import Explanation, FakeProvider, ProviderUnavailable
from conftest import make_authenticated_client

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO_ROOT / "data" / "fixture.json"

VALID_SUMMARY = (
    "Spring registration is down 4.8 % versus the same point last year [M1]. "
    "42 continuing students have not yet registered [M2]."
)
VALID_LIMITATIONS = (
    "All records are fictional demonstration data, and the as-of date comes "
    "from the data, not today's date [M2]."
)
VALID_CHIEF_JSON = json.dumps(
    {"executive_summary": VALID_SUMMARY, "limitations": VALID_LIMITATIONS}
)

ANALYST_TEXTS = {
    ENROLLMENT_ANALYST: "Registration is down 4.8 % [M1].",
    STUDENT_SUCCESS_ANALYST: "18 students carry a small financial hold [M3].",
}


@pytest.fixture(scope="module")
def findings_obj() -> dict[str, Any]:
    return compute_findings(load_fixture(FIXTURE_PATH), fixture_path=FIXTURE_PATH)


@pytest.fixture
def chief_findings(findings_obj: dict[str, Any]) -> dict[str, Any]:
    return findings_for_role(CHIEF_OF_STAFF, findings_obj)


@pytest.fixture
def log(tmp_path: Path) -> AuditLog:
    return AuditLog(tmp_path / "events.jsonl")


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return make_authenticated_client(create_app())


class StubProvider:
    """Returns a fixed text and remembers what it received."""

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


class DownProvider:
    name = "down"
    model_label = "down-1"

    def explain(self, findings: dict[str, Any], role: str) -> Explanation:
        raise ProviderUnavailable("provider is down", provider=self.name)


class PartiallyDownProvider(FakeProvider):
    """FakeProvider that cannot answer for one role."""

    def __init__(self, down_role: str) -> None:
        super().__init__()
        self.down_role = down_role

    def explain(self, findings: dict[str, Any], role: str) -> Explanation:
        if role == self.down_role:
            raise ProviderUnavailable(
                f"provider is down for {self.down_role}", provider=self.name
            )
        return super().explain(findings, role)


# --- what the Chief of Staff receives ------------------------------------------


def test_chief_receives_aggregates_and_validated_texts_only(
    findings_obj: dict[str, Any], log: AuditLog
) -> None:
    """The exact received keys: the aggregate findings M1–M8 and the two
    analysts' validated explanations. No row IDs, no counseling fields."""
    provider = StubProvider(VALID_CHIEF_JSON)
    result = run_chief_of_staff(
        findings_obj, ANALYST_TEXTS, provider, log, "task-chief-1"
    )
    assert result.available is True, result.reason
    received = provider.received
    assert received is not None
    assert set(received) == {"findings", "analyst_explanations"}
    assert sorted(received["findings"]) == [
        "M1",
        "M2",
        "M3",
        "M4",
        "M5",
        "M6",
        "M7",
        "M8",
    ]
    assert sorted(received["analyst_explanations"]) == [
        ENROLLMENT_ANALYST,
        STUDENT_SUCCESS_ANALYST,
    ]
    dumped = json.dumps(received)
    assert "row_ids" not in dumped
    assert "hold_row_ids" not in dumped
    assert "row_rules" not in dumped
    assert "STU-" not in dumped
    assert "PRI-" not in dumped
    assert "counseling" not in dumped


def test_chief_grant_lists_aggregate_fields_before_the_model_call(
    findings_obj: dict[str, Any], log: AuditLog
) -> None:
    provider = StubProvider(VALID_CHIEF_JSON)
    run_chief_of_staff(findings_obj, ANALYST_TEXTS, provider, log, "task-chief-2")
    granted = log.events("data.granted")
    assert len(granted) == 1
    payload = granted[0]["payload"]
    assert granted[0]["actor"] == CHIEF_OF_STAFF
    assert payload["task_id"] == "task-chief-2"
    assert payload["level"] == "aggregate"
    assert payload["analyst_explanations"] == [
        ENROLLMENT_ANALYST,
        STUDENT_SUCCESS_ANALYST,
    ]
    # The aggregate fields behind M1–M7; never a counseling field.
    assert "enrollment.registration_status" in payload["granted_fields"]
    assert "holds.amount" in payload["granted_fields"]
    assert not any(f.startswith("counseling.") for f in payload["granted_fields"])
    # The grant precedes the model's answer: no finding.produced for the chief.
    assert log.events("finding.produced") == []


def test_chief_never_receives_the_counseling_group_even_if_asked(
    log: AuditLog,
) -> None:
    """The aggregate gate refuses a counseling field before any model call,
    exactly like the raw-field gate does for the analysts."""
    from cabinet.permissions import FieldRequestRefused, grant_aggregates

    with pytest.raises(FieldRequestRefused):
        grant_aggregates(
            CHIEF_OF_STAFF,
            ["profile.continuing", "counseling.counseling_notes"],
            "task-chief-x",
            log,
        )
    refused = log.events("data.refused")
    assert len(refused) == 1
    assert refused[0]["payload"]["refused_fields"] == ["counseling.counseling_notes"]


# --- JSON-only output ------------------------------------------------------------


def test_output_must_be_json_only(
    chief_findings: dict[str, Any],
) -> None:
    for bad in (
        "Here is the briefing you asked for.",
        f"```json\n{VALID_CHIEF_JSON}\n```",
        f"{VALID_CHIEF_JSON}\nHope this helps.",
        '["executive_summary", "limitations"]',
    ):
        with pytest.raises(OutputRejected):
            validate_chief_output(bad, chief_findings)


def test_output_must_have_exactly_the_two_keys(
    chief_findings: dict[str, Any],
) -> None:
    extra = json.dumps(
        {
            "executive_summary": VALID_SUMMARY,
            "limitations": VALID_LIMITATIONS,
            "notes": "an extra key",
        }
    )
    with pytest.raises(OutputRejected, match="exactly the keys"):
        validate_chief_output(extra, chief_findings)
    missing = json.dumps({"executive_summary": VALID_SUMMARY})
    with pytest.raises(OutputRejected, match="exactly the keys"):
        validate_chief_output(missing, chief_findings)
    non_string = json.dumps({"executive_summary": 5, "limitations": VALID_LIMITATIONS})
    with pytest.raises(OutputRejected, match="strings"):
        validate_chief_output(non_string, chief_findings)


def test_prose_or_extra_keys_are_never_shown(
    findings_obj: dict[str, Any], log: AuditLog
) -> None:
    for bad_text in (
        "The cabinet reviewed spring registration.",
        json.dumps(
            {
                "executive_summary": VALID_SUMMARY,
                "limitations": VALID_LIMITATIONS,
                "unvalidated": "anything could go here",
            }
        ),
    ):
        provider = StubProvider(bad_text)
        result = run_chief_of_staff(
            findings_obj, ANALYST_TEXTS, provider, log, "task-chief-bad"
        )
        assert result.available is False
        assert result.executive_summary is None
        assert result.limitations is None
        assert result.reason is not None


# --- the shared validation rules ---------------------------------------------------


def test_summary_uses_the_shared_numeral_direction_and_date_rules(
    chief_findings: dict[str, Any],
) -> None:
    def output(summary: str) -> str:
        return json.dumps(
            {"executive_summary": summary, "limitations": VALID_LIMITATIONS}
        )

    # An invented number is rejected.
    with pytest.raises(OutputRejected, match="'43'"):
        validate_chief_output(
            output("43 continuing students have not yet registered [M2]."),
            chief_findings,
        )
    # A flipped direction is rejected (M1 is −4.8 %).
    with pytest.raises(OutputRejected, match="direction"):
        validate_chief_output(
            output("Spring registration is up 4.8 % versus last year [M1]."),
            chief_findings,
        )
    # A date not in the findings is rejected.
    with pytest.raises(OutputRejected, match="date"):
        validate_chief_output(
            output("As of November 21, 2025, registration is down 4.8 % [M1]."),
            chief_findings,
        )
    # A claim without a finding ID is rejected.
    with pytest.raises(OutputRejected, match="finding ID"):
        validate_chief_output(
            output("Spring registration is down 4.8 % versus last year."),
            chief_findings,
        )


def test_risk_wording_and_student_ids_are_rejected_in_both_sections(
    chief_findings: dict[str, Any],
) -> None:
    with pytest.raises(OutputRejected, match="never risk scores"):
        validate_chief_output(
            json.dumps(
                {
                    "executive_summary": "42 students are at risk of missing "
                    "registration [M2].",
                    "limitations": VALID_LIMITATIONS,
                }
            ),
            chief_findings,
        )
    with pytest.raises(OutputRejected, match="STU-0007"):
        validate_chief_output(
            json.dumps(
                {
                    "executive_summary": VALID_SUMMARY,
                    "limitations": "Student STU-0007 illustrates the barrier [M3].",
                }
            ),
            chief_findings,
        )


def test_five_sentence_cap_is_structural(
    chief_findings: dict[str, Any],
) -> None:
    def summary_with(sentences: int) -> str:
        return " ".join(
            "Sentence number rests on the data [M2]." for _ in range(sentences)
        )

    five = json.dumps(
        {"executive_summary": summary_with(5), "limitations": VALID_LIMITATIONS}
    )
    claims, _ = validate_chief_output(five, chief_findings)
    assert len(claims) == 5

    six = json.dumps(
        {"executive_summary": summary_with(6), "limitations": VALID_LIMITATIONS}
    )
    with pytest.raises(OutputRejected, match="cap is 5"):
        validate_chief_output(six, chief_findings)


def test_sentence_cap_ignores_abbreviation_dots(
    chief_findings: dict[str, Any],
) -> None:
    """The M7 title's "vs." (and "e.g.", "U.S.", "Dr.", …) never reads as a
    sentence boundary: a five-sentence summary quoting the title is accepted,
    while six real sentences are still rejected."""
    five_with_title = " ".join(
        [
            "Registered credit hours vs. prior year are an optional measure [M7].",
            *["Sentence rests on the data [M2]."] * 4,
        ]
    )
    assert count_sentences(five_with_title) == 5
    accepted = json.dumps(
        {"executive_summary": five_with_title, "limitations": VALID_LIMITATIONS}
    )
    claims, _ = validate_chief_output(accepted, chief_findings)
    assert len(claims) == 5

    six_real = json.dumps(
        {
            "executive_summary": five_with_title + " One more real sentence [M2].",
            "limitations": VALID_LIMITATIONS,
        }
    )
    with pytest.raises(OutputRejected, match="cap is 5"):
        validate_chief_output(six_real, chief_findings)


def test_count_sentences_protects_the_abbreviation_list() -> None:
    text = (
        "Dr. Ada met Mr. Beam, e.g. the advisors, and Ms. Cole in the U.S. "
        "office [M2]. No. 42 came later [M2]."
    )
    assert count_sentences(text) == 2


def test_fake_provider_output_passes_validation(
    findings_obj: dict[str, Any], log: AuditLog
) -> None:
    result = run_chief_of_staff(
        findings_obj, ANALYST_TEXTS, FakeProvider(), log, "task-chief-fake"
    )
    assert result.available is True, result.reason
    assert result.executive_summary is not None
    assert result.limitations is not None
    assert len(result.summary_claims) <= 5
    for claim in result.summary_claims + result.limitation_claims:
        assert claim.finding_ids


def test_provider_down_is_unavailable_and_not_shown(
    findings_obj: dict[str, Any], log: AuditLog
) -> None:
    result = run_chief_of_staff(
        findings_obj, ANALYST_TEXTS, DownProvider(), log, "task-chief-down"
    )
    assert result.available is False
    assert result.reason == "provider is down"
    assert result.executive_summary is None
    # The aggregate grant is still logged; nothing is produced.
    assert len(log.events("data.granted")) == 1
    assert log.events("finding.produced") == []
    assert log.events("briefing.produced") == []


# --- POST /ask: the whole cabinet in one call -----------------------------------


def test_ask_runs_the_whole_cabinet(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    response = client.post("/ask", json={"question": APPROVED_QUESTION})
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True

    briefing = body["briefing"]
    sections = briefing["sections"]
    assert set(sections) == {"1", "2", "3", "4", "5", "6", "7"}
    for section_id in ("1", "2", "3", "7"):
        assert sections[section_id]["kind"] == "available"
        assert sections[section_id]["claims"]
    assert sections["1"]["provenance"]["source"] == CHIEF_OF_STAFF
    assert sections["7"]["provenance"]["source"] == CHIEF_OF_STAFF
    assert sections["2"]["provenance"]["source"] == ENROLLMENT_ANALYST
    assert sections["3"]["provenance"]["source"] == STUDENT_SUCCESS_ANALYST
    # Sections 4–6 are computed in code, never model-written.
    assert set(sections["4"]["findings"]) == {
        "M1",
        "M2",
        "M3",
        "M4",
        "M5",
        "M6",
        "M7",
        "M8",
    }
    assert sections["5"]["actions"]
    assert all(a["office"] for a in sections["5"]["actions"])
    assert len(sections["6"]["decisions"]) == 1
    # The operational actions and the leadership decision stay separate.
    assert "5" in sections and "6" in sections

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
    # Exactly one briefing.produced, once per run.
    produced = [e for e in events if e["type"] == "briefing.produced"]
    assert len(produced) == 1
    payload = produced[0]["payload"]
    assert payload["question_id"] == briefing["question_id"]
    assert payload["question_event_id"] == briefing["question_event_id"]
    assert payload["sections"] == [1, 2, 3, 4, 5, 6, 7]
    assert set(payload["sources"]) == {"1", "2", "3", "7"}
    assert payload["chief_task_id"] == (
        f"task-{CHIEF_OF_STAFF}-{briefing['question_event_id']}"
    )
    assert set(payload["analyst_task_ids"]) == {
        ENROLLMENT_ANALYST,
        STUDENT_SUCCESS_ANALYST,
    }
    # The chief's grant is aggregate-level and names the analyst explanations.
    chief_grant = [e for e in events if e["type"] == "data.granted"][-1]
    assert chief_grant["actor"] == CHIEF_OF_STAFF
    assert chief_grant["payload"]["level"] == "aggregate"
    assert chief_grant["payload"]["analyst_explanations"] == [
        ENROLLMENT_ANALYST,
        STUDENT_SUCCESS_ANALYST,
    ]
    assert [e["id"] for e in events] == body["event_ids"]


def test_get_briefing_404_then_serves_the_produced_briefing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    missing = client.get("/briefing")
    assert missing.status_code == 404
    assert "ask the approved question" in missing.json()["reason"]

    asked = client.post("/ask", json={"question": APPROVED_QUESTION}).json()
    served = client.get("/briefing")
    assert served.status_code == 200
    assert served.json() == asked["briefing"]

    # A reload writes no events.
    events_after_ask = client.get("/events").json()["events"]
    client.get("/briefing")
    assert client.get("/events").json()["events"] == events_after_ask


def test_ask_degrades_when_an_analyst_is_unavailable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One analyst down: its section is marked unavailable, the Chief of
    Staff run is skipped (sections 1 and 7 unavailable — the UI falls back to
    the computed headline), nothing is invented, and briefing.produced is
    still logged exactly once."""
    monkeypatch.setattr(
        "cabinet.api.provider_from_env",
        lambda: PartiallyDownProvider(STUDENT_SUCCESS_ANALYST),
    )
    response = client.post("/ask", json={"question": APPROVED_QUESTION})
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True
    sections = body["briefing"]["sections"]
    assert sections["2"]["kind"] == "available"
    assert sections["3"]["kind"] == "unavailable"
    assert sections["3"]["reason"]
    assert sections["1"]["kind"] == "unavailable"
    assert sections["7"]["kind"] == "unavailable"
    # Sections 4–6 are computed and always render.
    assert sections["4"]["findings"]
    assert sections["5"]["actions"]
    assert sections["6"]["decisions"]

    events = client.get("/events").json()["events"]
    types = [e["type"] for e in events]
    # No chief task was assigned, no aggregate grant was written.
    assert not any(
        e["type"] == "task.assigned" and e["payload"].get("role") == CHIEF_OF_STAFF
        for e in events
    )
    assert not any(
        e["actor"] == CHIEF_OF_STAFF and e["type"] == "data.granted" for e in events
    )
    assert types.count("briefing.produced") == 1
    produced = [e for e in events if e["type"] == "briefing.produced"][0]
    assert produced["payload"]["chief_task_id"] is None
    assert produced["payload"]["sources"]["3"]["available"] is False
    assert produced["payload"]["sources"]["1"]["available"] is False


def test_ask_degrades_when_the_whole_provider_is_down(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("cabinet.api.provider_from_env", lambda: DownProvider())
    response = client.post("/ask", json={"question": APPROVED_QUESTION})
    assert response.status_code == 200
    sections = response.json()["briefing"]["sections"]
    for section_id in ("1", "2", "3", "7"):
        assert sections[section_id]["kind"] == "unavailable"
    assert sections["5"]["actions"]
    events = client.get("/events").json()["events"]
    assert [e["type"] for e in events].count("briefing.produced") == 1


# --- replay: the whole /ask from recordings, network blocked ----------------------


def test_ask_replay_roundtrip_with_urlopen_blocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    replay_dir = tmp_path / "replay"
    monkeypatch.setenv("CABINET_REPLAY_DIR", str(replay_dir))
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    monkeypatch.setenv("CABINET_RECORD", "1")
    record_client = make_authenticated_client(
        create_app()
    )
    recorded = record_client.post("/ask", json={"question": APPROVED_QUESTION})
    assert recorded.status_code == 200
    recorded_sections = recorded.json()["briefing"]["sections"]

    # Three recordings: both analysts plus the Chief of Staff.
    recordings = sorted(replay_dir.glob("*.json"))
    assert len(recordings) == 3
    roles = {json.loads(p.read_text(encoding="utf-8"))["role"] for p in recordings}
    assert roles == {ENROLLMENT_ANALYST, STUDENT_SUCCESS_ANALYST, CHIEF_OF_STAFF}

    def no_network(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("replay must never touch the network")

    # The provider's only network path is urllib (ChatProvider); block it.
    monkeypatch.setattr("urllib.request.urlopen", no_network)
    monkeypatch.setenv("CABINET_PROVIDER", "replay")
    monkeypatch.delenv("CABINET_RECORD", raising=False)
    # A fresh app: empty caches, so every section is served from the
    # recordings, not from the recording run's in-process cache.
    replay_client = make_authenticated_client(
        create_app()
    )
    replayed = replay_client.post("/ask", json={"question": APPROVED_QUESTION})
    assert replayed.status_code == 200
    replayed_sections = replayed.json()["briefing"]["sections"]
    for section_id in ("1", "2", "3", "7"):
        assert replayed_sections[section_id]["kind"] == "available"
        assert (
            replayed_sections[section_id]["text"]
            == recorded_sections[section_id]["text"]
        )
        assert replayed_sections[section_id]["provenance"]["recorded"] is True
    events = replay_client.get("/events").json()["events"]
    # Exactly one briefing.produced from the replay run (the record run's
    # events share the institution's chain, so scope to this run's ids).
    replay_event_ids = set(replayed.json()["event_ids"])
    assert [
        e["type"] for e in events if e["id"] in replay_event_ids
    ].count("briefing.produced") == 1


# --- record-golden: the third golden file -----------------------------------------


def test_record_golden_records_the_chief_and_keeps_existing_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    """record_golden runs both analysts and then the Chief of Staff on their
    validated texts, and never overwrites an existing golden file."""
    from cabinet.record_golden import main as record_golden_main

    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    monkeypatch.setenv("CABINET_RECORD", "1")
    monkeypatch.setenv("CABINET_REPLAY_DIR", str(tmp_path / "replay"))
    golden = Path(str(tmp_path / "golden"))  # conftest's CABINET_GOLDEN_DIR
    monkeypatch.setenv("CABINET_GOLDEN_DIR", str(golden))

    assert record_golden_main() == 0
    files = sorted(golden.glob("*.json"))
    # Three recordings per approved question (both analysts plus the Chief of
    # Staff), for every question in the registry.
    from cabinet.questions import QUESTIONS

    assert len(files) == 3 * len(QUESTIONS)
    roles = {json.loads(p.read_text(encoding="utf-8"))["role"] for p in files}
    assert roles == {ENROLLMENT_ANALYST, STUDENT_SUCCESS_ANALYST, CHIEF_OF_STAFF}
    contents = {p.name: p.read_bytes() for p in files}

    # A second run keeps every existing golden file.
    assert record_golden_main() == 0
    out = capsys.readouterr().out
    assert "already exists and is kept" in out
    for path in golden.glob("*.json"):
        assert path.read_bytes() == contents[path.name]


# --- the chief's one field-set derivation (assignment = grant = response) -------


def test_chief_aggregate_fields_is_normalized_and_sorted(
    chief_findings: dict[str, Any],
) -> None:
    """The one helper: list markers (``holds[].amount``) and the ROADMAP
    spelling (``hold.x``) are normalized away before sorting."""
    fields = chief_aggregate_fields({"findings": chief_findings})
    assert fields == sorted(fields)
    assert "holds.amount" in fields
    assert not any("[]" in field for field in fields)
    assert not any(field.startswith("hold.") for field in fields)


def test_chief_field_set_is_identical_in_assignment_grant_and_response(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """task.assigned's fields, the data.granted event's granted_fields, and
    the /ask response's tasks[].granted_fields are one derivation."""
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    body = client.post("/ask", json={"question": APPROVED_QUESTION}).json()
    events = client.get("/events").json()["events"]
    assigned = next(
        e
        for e in events
        if e["type"] == "task.assigned" and e["payload"]["role"] == CHIEF_OF_STAFF
    )
    granted = next(
        e
        for e in events
        if e["type"] == "data.granted" and e["actor"] == CHIEF_OF_STAFF
    )
    chief_task = next(t for t in body["tasks"] if t["role"] == CHIEF_OF_STAFF)
    assert (
        assigned["payload"]["fields"]
        == granted["payload"]["granted_fields"]
        == chief_task["granted_fields"]
    )


# --- the analyst briefing routes never interleave with an in-flight /ask -------


def test_briefing_routes_answer_409_while_a_question_is_being_answered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Holding the institution's ask_lock (an in-flight POST /ask) makes
    every analyst briefing route answer 409 instead of interleaving its own
    task assignment and run; once the run finishes they answer normally
    again."""
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    app = create_app()
    client = make_authenticated_client(app)
    institution_id = client.get("/auth/me").json()["user"]["institution_id"]
    lock = app.state.ask_lock_for(institution_id)
    assert lock.acquire(blocking=False)
    try:
        for route in ("/briefing/enrollment", "/briefing/student-success"):
            response = client.get(route)
            assert response.status_code == 409, route
            assert response.json()["reason"] == "a question is being answered"
        for route in (
            "/briefing/enrollment/refresh",
            "/briefing/student-success/refresh",
        ):
            assert client.post(route).status_code == 409, route
    finally:
        lock.release()
    response = client.get("/briefing/enrollment")
    assert response.status_code == 200
    assert response.json()["available"] is True
