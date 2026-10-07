"""Tests for M9, the counseling figure in aggregate only.

Covers migration 7 (idempotent, upgrades a real version 1..6 database), the
admin route (admin only, CSRF, validation, the admin.changed event and what
it leaves out), M9 absent while the authorization is off, present and
suppressed on the committed fixture (2 students, below the minimum group
size), present with its number on a fixture edited to 12, the model
boundary (the Chief of Staff receives the count or a marker with no number,
the analysts never receive M9, no counseling field or student id in any
analyst prompt), the M9 data.granted event, revoking, the unchanged
refusals (the beat-6 question and /governance/request), and the golden
replay keyed for the payload that carries M9.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cabinet.analysts import (
    CHIEF_OF_STAFF,
    ENROLLMENT_ANALYST,
    STUDENT_SUCCESS_ANALYST,
    build_prompt,
    chief_received,
    run_chief_of_staff,
)
from cabinet.api import OUT_OF_SCOPE_REFUSAL, create_app
from cabinet.audit import AuditLog
from cabinet.counseling import (
    MINIMUM_CELL_SIZE,
    SUPPRESSED_DISPLAY,
    SUPPRESSED_MODEL_DISPLAY,
    m9_count,
    m9_finding,
)
from cabinet.fixture import load_fixture
from cabinet.metrics import findings as compute_findings
from cabinet.migrations import (
    COUNSELING_AUTHORIZATION_COLUMNS,
    MIGRATIONS,
    migrate,
    recorded_versions,
)
from cabinet.permissions import findings_for_role
from cabinet.provider import FakeProvider
from cabinet.questions import DEFAULT_QUESTION, UNRESOLVED_HOLDS, received_for
from conftest import make_authenticated_client

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO_ROOT / "data" / "fixture.json"
GOLDEN_DIR = REPO_ROOT / "data" / "golden"

URL = "/admin/institution/counseling-authorization"
Q1 = "What should I know about spring registration?"
Q2 = "Where are unresolved holds affecting continued enrollment?"
BEAT_6 = "Which students are in counseling?"

AUTHORIZED_BY = "Dr. Example, Director of Counseling"
REFERENCE = "memo 2026-09-26"

# The committed fixture: two continuing, not-registered students (STU-0126,
# STU-0147) carry counseling contact; STU-0177 does too but is not
# continuing, so it is outside M2. data/VERIFY.md records the same recount.
FIXTURE_M9_COUNT = 2

COUNSELING_NOTE_TEXTS = (
    "homesick",
    "grief",
    "prayer",
    "chaplain after chapel",
    "dinner group",
)


def _events(client: TestClient, event_type: str) -> list[dict[str, Any]]:
    return client.get("/events", params={"type": event_type}).json()["events"]  # type: ignore[no-any-return]


def _authorize(admin: TestClient) -> dict[str, Any]:
    response = admin.put(
        URL,
        json={
            "authorized": True,
            "authorized_by": AUTHORIZED_BY,
            "document_reference": REFERENCE,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def _revoke(admin: TestClient) -> dict[str, Any]:
    response = admin.put(URL, json={"authorized": False})
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def _without_ids(text: str) -> str:
    """Text with finding ids (M2, M9) removed, so a digit check sees only
    real numbers."""
    return re.sub(r"\bM\d+\b", "", text)


def _edited_fixture(tmp_path: Path, contacts: int) -> Path:
    """The committed fixture with counseling contact on exactly ``contacts``
    M2 students (and on no other M2 student). Written under tmp_path only."""
    raw: dict[str, Any] = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    m2 = [
        s
        for s in raw["students"]
        if s["profile"]["continuing"]
        and s["enrollment"]["registration_status"] != "registered"
    ]
    assert len(m2) >= contacts
    for index, student in enumerate(m2):
        if index < contacts:
            student["counseling"] = {
                "counseling_notes": "Checked in with the care team.",
                "chaplain_contact": False,
            }
        else:
            student["counseling"] = {
                "counseling_notes": None,
                "chaplain_contact": False,
            }
    path = tmp_path / f"fixture-{contacts}.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    return create_app()


@pytest.fixture
def admin(app: FastAPI) -> TestClient:
    return make_authenticated_client(app, role="admin")


@pytest.fixture
def executive(app: FastAPI) -> TestClient:
    return make_authenticated_client(app, role="executive")


# --- migration 7 ---------------------------------------------------------------


def _database_at_version_6(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE schema_migrations ("
        " version INTEGER PRIMARY KEY, name TEXT NOT NULL,"
        " applied_at TEXT NOT NULL)"
    )
    for version, name, migration in MIGRATIONS[:6]:
        conn.execute("BEGIN")
        migration(conn)
        conn.execute(
            "INSERT INTO schema_migrations (version, name, applied_at)"
            " VALUES (?, ?, '2026-01-01')",
            (version, name),
        )
        conn.commit()
    conn.execute(
        "INSERT INTO institutions (name, slug, created_at)"
        " VALUES ('Two Rivers College', 'two-rivers', '2026-01-01')"
    )
    conn.commit()
    return conn


def test_migration_7_is_idempotent_and_upgrades_a_real_1_to_6_database(
    tmp_path: Path,
) -> None:
    conn = _database_at_version_6(tmp_path / "cabinet.db")
    try:
        assert recorded_versions(conn) == [1, 2, 3, 4, 5, 6]
        assert migrate(conn) == [7, 8, 9, 10, 11, 12]
        assert recorded_versions(conn) == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
        assert migrate(conn) == []
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(institutions)")
        }
        assert {name for name, _ in COUNSELING_AUTHORIZATION_COLUMNS} <= columns
        # The institution written under version 6 survives, unauthorized.
        row = conn.execute(
            "SELECT name, counseling_aggregate_authorized,"
            " counseling_aggregate_authorized_by FROM institutions"
        ).fetchone()
        assert row == ("Two Rivers College", 0, None)
    finally:
        conn.close()


def test_migration_7_tolerates_a_column_that_already_exists(tmp_path: Path) -> None:
    """A database that already carries one of the columns (a hand repair)
    still upgrades, with no duplicate-column failure."""
    conn = _database_at_version_6(tmp_path / "cabinet.db")
    try:
        conn.execute(
            "ALTER TABLE institutions ADD COLUMN"
            " counseling_aggregate_authorized INTEGER NOT NULL DEFAULT 0"
        )
        conn.commit()
        assert migrate(conn) == [7, 8, 9, 10, 11, 12]
        assert migrate(conn) == []
    finally:
        conn.close()


# --- the admin route -----------------------------------------------------------


def test_route_starts_unauthorized_and_records_and_revokes(
    admin: TestClient,
) -> None:
    initial = admin.get(URL).json()
    assert initial == {
        "authorized": False,
        "authorized_by": None,
        "document_reference": None,
        "recorded_by": None,
        "recorded_at": None,
    }
    recorded = _authorize(admin)
    assert recorded["authorized"] is True
    assert recorded["authorized_by"] == AUTHORIZED_BY
    assert recorded["document_reference"] == REFERENCE
    assert recorded["recorded_by"] == "admin@test.example"
    assert isinstance(recorded["recorded_at"], str)
    assert admin.get(URL).json() == recorded

    revoked = _revoke(admin)
    assert revoked["authorized"] is False
    # The earlier texts stay for the record; who revoked it is recorded.
    assert revoked["authorized_by"] == AUTHORIZED_BY
    assert revoked["recorded_by"] == "admin@test.example"


def test_route_is_admin_only(app: FastAPI) -> None:
    for role in ("executive", "staff", "reviewer", "aid"):
        client = make_authenticated_client(
            app, role=role, email=f"{role}-m9@test.example"
        )
        assert client.get(URL).status_code == 403, role
        put = client.put(
            URL,
            json={
                "authorized": True,
                "authorized_by": AUTHORIZED_BY,
                "document_reference": REFERENCE,
            },
        )
        assert put.status_code == 403, role
    admin = make_authenticated_client(app, role="admin")
    assert admin.get(URL).json()["authorized"] is False


def test_route_requires_the_csrf_token(admin: TestClient) -> None:
    token = admin.headers.pop("X-CSRF-Token")
    response = admin.put(
        URL,
        json={
            "authorized": True,
            "authorized_by": AUTHORIZED_BY,
            "document_reference": REFERENCE,
        },
    )
    assert response.status_code == 403
    admin.headers["X-CSRF-Token"] = token
    assert admin.get(URL).json()["authorized"] is False


def test_recording_needs_both_texts(admin: TestClient) -> None:
    response = admin.put(
        URL, json={"authorized": True, "authorized_by": "  ", "document_reference": ""}
    )
    assert response.status_code == 422
    body = response.json()
    assert body["detail"] == "the authorization was not recorded"
    assert len(body["errors"]) == 2
    too_long = admin.put(
        URL,
        json={
            "authorized": True,
            "authorized_by": "x" * 201,
            "document_reference": REFERENCE,
        },
    )
    assert too_long.status_code == 422
    assert admin.get(URL).json()["authorized"] is False
    assert [
        e
        for e in _events(admin, "admin.changed")
        if e["payload"]["action"] == "counseling_authorization"
    ] == []


def test_admin_changed_event_carries_the_authorization_and_no_counseling_data(
    admin: TestClient,
) -> None:
    _authorize(admin)
    _revoke(admin)
    events = [
        e
        for e in _events(admin, "admin.changed")
        if e["payload"]["action"] == "counseling_authorization"
    ]
    assert [e["payload"]["authorized"] for e in events] == [True, False]
    assert events[0]["payload"] == {
        "action": "counseling_authorization",
        "by": "admin@test.example",
        "authorized": True,
        "authorized_by": AUTHORIZED_BY,
        "document_reference": REFERENCE,
    }
    dumped = json.dumps(events)
    assert "STU-" not in dumped
    assert "counseling_notes" not in dumped
    assert "chaplain_contact" not in dumped
    for text in COUNSELING_NOTE_TEXTS:
        assert text not in dumped


# --- authorization off: M9 absent, refusals unchanged --------------------------


def test_off_m9_is_absent_from_findings_and_the_chief_payload(
    executive: TestClient,
) -> None:
    findings = executive.get("/findings").json()
    assert "M9" not in findings
    assert "counseling" not in json.dumps(findings)
    asked = executive.post("/ask", json={"question": Q1})
    assert asked.status_code == 200
    body = asked.json()
    chief_task = next(t for t in body["tasks"] if t["role"] == CHIEF_OF_STAFF)
    assert "M9" not in chief_task["findings"]
    assert "counseling" not in json.dumps(body)
    granted = _events(executive, "data.granted")
    assert all(e["payload"].get("finding_id") != "M9" for e in granted)


def test_governance_refuses_counseling_fields_in_both_states(
    admin: TestClient, executive: TestClient
) -> None:
    def refusal() -> dict[str, Any]:
        response = executive.post(
            "/governance/request",
            json={
                "role": CHIEF_OF_STAFF,
                "fields": ["counseling.counseling_notes"],
            },
        )
        assert response.status_code == 200
        return response.json()  # type: ignore[no-any-return]

    off = refusal()
    _authorize(admin)
    on = refusal()
    for body in (off, on):
        assert body["granted"] is False
        assert body["refused_fields"] == ["counseling.counseling_notes"]
        assert body["reason"] == (
            "Role 'chief_of_staff' is not permitted to access "
            "counseling.counseling_notes; the request was refused before "
            "any model call."
        )
        assert body["event"]["type"] == "data.refused"
    assert off["reason"] == on["reason"]
    # Every role, every counseling field, still refused while authorized.
    for role in (CHIEF_OF_STAFF, ENROLLMENT_ANALYST, STUDENT_SUCCESS_ANALYST):
        for field in ("counseling.counseling_notes", "counseling.chaplain_contact"):
            response = executive.post(
                "/governance/request", json={"role": role, "fields": [field]}
            )
            assert response.json()["granted"] is False, (role, field)


def test_beat_6_refusal_is_word_for_word_in_both_states(
    admin: TestClient, executive: TestClient
) -> None:
    off = executive.post("/ask", json={"question": BEAT_6}).json()
    _authorize(admin)
    on = executive.post("/ask", json={"question": BEAT_6}).json()
    for body in (off, on):
        assert body["accepted"] is False
        assert body["refusal"] == OUT_OF_SCOPE_REFUSAL
    refused = _events(executive, "data.refused")
    assert [e["payload"]["reason"] for e in refused] == [OUT_OF_SCOPE_REFUSAL] * 2


# --- authorization on: the committed fixture is suppressed ----------------------


def test_fixture_recount_matches_verify() -> None:
    raw = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    fixture = load_fixture(FIXTURE_PATH)
    m2 = compute_findings(fixture, fixture_path=FIXTURE_PATH)["M2"]
    assert m9_count(raw, m2["row_ids"]) == FIXTURE_M9_COUNT
    assert FIXTURE_M9_COUNT < MINIMUM_CELL_SIZE


def test_on_fixture_m9_is_present_and_suppressed(
    admin: TestClient, executive: TestClient
) -> None:
    _authorize(admin)
    m9 = executive.get("/findings").json()["M9"]
    assert m9["title"] == "Students in M2 with any counseling contact this term"
    assert m9["value"] is None
    assert m9["display"] == SUPPRESSED_DISPLAY == "fewer than 10"
    assert m9["suppressed"] is True
    assert m9["minimum_cell_size"] == MINIMUM_CELL_SIZE
    assert m9["aggregate_only"] is True
    assert m9["row_ids"] == []
    assert m9["source_fields"] == [
        "profile.continuing",
        "enrollment.registration_status",
        "counseling.counseling_notes",
        "counseling.chaplain_contact",
    ]
    assert m9["authorization"]["authorized_by"] == AUTHORIZED_BY
    assert m9["authorization"]["document_reference"] == REFERENCE
    assert m9["authorization"]["recorded_by"] == "admin@test.example"
    assert isinstance(m9["authorization"]["recorded_at"], str)
    # Never the withheld count, never a student, never a note.
    dumped = json.dumps(m9)
    for key in ("display", "reason"):
        assert str(FIXTURE_M9_COUNT) not in str(m9[key]), key
    assert "STU-" not in dumped
    for text in COUNSELING_NOTE_TEXTS:
        assert text not in dumped
    # The stat rail's findings are untouched.
    findings = executive.get("/findings").json()
    assert findings["M2"]["value"] == 42


def test_on_suppressed_model_payload_and_prompt_carry_no_number_for_m9() -> None:
    raw = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    findings = compute_findings(load_fixture(FIXTURE_PATH), fixture_path=FIXTURE_PATH)
    findings["M9"] = m9_finding(
        raw,
        findings["M2"],
        {
            "authorized": True,
            "authorized_by": AUTHORIZED_BY,
            "document_reference": REFERENCE,
            "recorded_by": "admin@test.example",
            "recorded_at": "2026-09-26T15:00:00+00:00",
        },
    )
    received = chief_received(findings, {"a": "x"})
    m9 = received["findings"]["M9"]
    assert m9 == {
        "id": "M9",
        "title": "Students in M2 with any counseling contact this term",
        "value": None,
        "display": SUPPRESSED_MODEL_DISPLAY,
        "suppressed": True,
    }
    assert not re.search(r"\d", _without_ids(json.dumps(m9)))
    _, user = build_prompt(received, CHIEF_OF_STAFF)
    start = user.index('"M9": {')
    block = user[start : user.index("}", start)]
    assert not re.search(r"\d", _without_ids(block))
    assert SUPPRESSED_MODEL_DISPLAY in block
    assert AUTHORIZED_BY not in user
    assert "counseling.counseling_notes" not in user


# --- authorization on: an edited fixture at or above the minimum ----------------


@pytest.fixture
def twelve_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    monkeypatch.setenv("CABINET_FIXTURE", str(_edited_fixture(tmp_path, 12)))
    return create_app()


def test_on_edited_fixture_m9_shows_its_number(twelve_app: FastAPI) -> None:
    admin = make_authenticated_client(twelve_app, role="admin")
    executive = make_authenticated_client(twelve_app, role="executive")
    _authorize(admin)
    m9 = executive.get("/findings").json()["M9"]
    assert m9["value"] == 12
    assert m9["display"] == "12"
    assert m9["suppressed"] is False
    assert m9["reason"] is None
    assert m9["row_ids"] == []


def test_on_edited_fixture_the_chief_receives_the_count_and_the_grant_is_logged(
    twelve_app: FastAPI,
) -> None:
    admin = make_authenticated_client(twelve_app, role="admin")
    executive = make_authenticated_client(twelve_app, role="executive")
    _authorize(admin)
    body = executive.post("/ask", json={"question": Q1}).json()
    assert body["accepted"] is True
    assert body["briefing"]["sections"]["1"]["kind"] == "available"

    granted = _events(executive, "data.granted")
    m9_grants = [e for e in granted if e["payload"].get("finding_id") == "M9"]
    assert len(m9_grants) == 1
    grant = m9_grants[0]
    assert grant["actor"] == CHIEF_OF_STAFF
    assert grant["payload"]["aggregate_only"] is True
    assert grant["payload"]["authorization"]["authorized_by"] == AUTHORIZED_BY
    assert grant["payload"]["authorization"]["document_reference"] == REFERENCE
    assert "counseling.chaplain_contact" in grant["payload"]["fields_read"]
    assert grant["payload"]["task_id"] == f"task-{CHIEF_OF_STAFF}-" + str(
        body["briefing"]["question_event_id"]
    )
    # The aggregate grant comes first (the task card reads the first one)
    # and never names a counseling field, so no refusal fires.
    chief_grants = [
        e for e in granted if e["payload"]["task_id"] == grant["payload"]["task_id"]
    ]
    assert chief_grants[0]["payload"]["level"] == "aggregate"
    assert all(
        not f.startswith("counseling.")
        for f in chief_grants[0]["payload"]["granted_fields"]
    )
    assert _events(executive, "data.refused") == []
    assert "STU-" not in json.dumps(m9_grants)

    chief_task = [
        e
        for e in _events(executive, "task.assigned")
        if e["payload"]["role"] == CHIEF_OF_STAFF
    ][-1]
    assert "M9" in chief_task["payload"]["findings"]
    # The response's task card agrees with the task.assigned event.
    card = next(t for t in body["tasks"] if t["role"] == CHIEF_OF_STAFF)
    assert card["findings"] == chief_task["payload"]["findings"]


def test_chief_run_with_the_count_logs_no_refusal(tmp_path: Path) -> None:
    raw = json.loads(_edited_fixture(tmp_path, 12).read_text(encoding="utf-8"))
    from cabinet.fixture import parse_fixture

    findings = compute_findings(parse_fixture(raw), fixture_path="edited")
    findings["M9"] = m9_finding(
        raw,
        findings["M2"],
        {
            "authorized": True,
            "authorized_by": AUTHORIZED_BY,
            "document_reference": REFERENCE,
            "recorded_by": "admin@test.example",
            "recorded_at": "2026-09-26T15:00:00+00:00",
        },
    )
    log = AuditLog(tmp_path / "events.jsonl")
    texts = {ENROLLMENT_ANALYST: "x [M1].", STUDENT_SUCCESS_ANALYST: "y [M3]."}
    received = chief_received(findings, texts)
    assert received["findings"]["M9"]["value"] == 12
    _, user = build_prompt(received, CHIEF_OF_STAFF)
    assert '"value": 12' in user
    result = run_chief_of_staff(findings, texts, FakeProvider(), log, "task-chief-m9")
    assert result.available is True, result.reason
    assert log.events("data.refused") == []
    grants = log.events("data.granted")
    assert [g["payload"].get("finding_id") for g in grants] == [None, "M9"]


# --- analysts never receive M9 -------------------------------------------------


def test_analysts_never_receive_m9_or_a_counseling_field(tmp_path: Path) -> None:
    raw = json.loads(_edited_fixture(tmp_path, 12).read_text(encoding="utf-8"))
    from cabinet.fixture import parse_fixture

    findings = compute_findings(parse_fixture(raw), fixture_path="edited")
    findings["M9"] = m9_finding(
        raw,
        findings["M2"],
        {"authorized": True, "authorized_by": AUTHORIZED_BY},
    )
    for role in (ENROLLMENT_ANALYST, STUDENT_SUCCESS_ANALYST):
        with pytest.raises(ValueError):
            findings_for_role(role, findings, ["M9"])
        for question in (DEFAULT_QUESTION, UNRESOLVED_HOLDS):
            received = received_for(question, role, findings)
            assert "M9" not in received
            system, user = build_prompt(received, role)
            prompt = system + user
            assert "counseling." not in prompt
            assert "counseling_notes" not in prompt
            assert "chaplain_contact" not in prompt
            assert "STU-" not in prompt
            assert "PRI-" not in prompt


def test_q2_chief_dispatch_does_not_carry_m9(
    admin: TestClient, executive: TestClient
) -> None:
    _authorize(admin)
    body = executive.post("/ask", json={"question": Q2}).json()
    assert body["accepted"] is True
    granted = _events(executive, "data.granted")
    assert all(e["payload"].get("finding_id") != "M9" for e in granted)


# --- revoking ------------------------------------------------------------------


def test_revoking_removes_m9_on_the_next_ask(
    admin: TestClient, executive: TestClient
) -> None:
    _authorize(admin)
    executive.post("/ask", json={"question": Q1})
    assert "M9" in executive.get("/findings").json()
    first = [
        e
        for e in _events(executive, "data.granted")
        if e["payload"].get("finding_id") == "M9"
    ]
    assert len(first) == 1

    _revoke(admin)
    assert "M9" not in executive.get("/findings").json()
    body = executive.post("/ask", json={"question": Q1}).json()
    assert body["accepted"] is True
    after = [
        e
        for e in _events(executive, "data.granted")
        if e["payload"].get("finding_id") == "M9"
    ]
    assert after == first
    chief_task = [
        e
        for e in _events(executive, "task.assigned")
        if e["payload"]["role"] == CHIEF_OF_STAFF
    ][-1]
    assert "M9" not in chief_task["payload"]["findings"]


# --- replay ------------------------------------------------------------------------


def test_golden_replay_answers_q1_in_both_states(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recorded Chief of Staff text replays with and without M9: the
    committed golden run carries a re-keyed copy for the payload with the
    suppressed M9 marker, and the original key is untouched."""
    monkeypatch.setenv("CABINET_PROVIDER", "replay")
    monkeypatch.setenv("CABINET_GOLDEN_DIR", str(GOLDEN_DIR))
    monkeypatch.setenv("CABINET_REPLAY_DIR", str(tmp_path / "no-replay"))
    app = create_app()
    admin = make_authenticated_client(app, role="admin")
    executive = make_authenticated_client(app, role="executive")

    off = executive.post("/ask", json={"question": Q1}).json()
    assert off["briefing"]["sections"]["1"]["kind"] == "available"
    _authorize(admin)
    on = executive.post("/ask", json={"question": Q1}).json()
    section = on["briefing"]["sections"]["1"]
    assert section["kind"] == "available", section
    assert section["text"] == off["briefing"]["sections"]["1"]["text"]
    assert section["provenance"]["rekeyed_from"] is not None


# --- revoking withdraws the count at once ---------------------------------------


def test_a_stored_briefing_citing_m9_is_withheld_after_a_revoke(
    app: FastAPI, admin: TestClient, executive: TestClient
) -> None:
    """A Chief of Staff section that cited M9 is served as unavailable while
    the authorization is off, and comes back if it is recorded again. The
    other sections and the stored row are untouched."""
    _authorize(admin)
    body = executive.post("/ask", json={"question": Q1}).json()
    briefing = body["briefing"]
    briefing["sections"]["1"]["claims"].append(
        {"text": "Fewer than 10 have had counseling contact.", "finding_ids": ["M9"]}
    )
    store = app.state.auth
    dataset = store.active_dataset(1)
    store.save_briefing(
        1,
        briefing["question_id"],
        briefing,
        dataset_id=int(dataset["id"]),
        dataset_sha256=str(dataset["sha256"]),
    )
    assert executive.get("/briefing").json()["sections"]["1"]["kind"] == "available"

    _revoke(admin)
    served = executive.get("/briefing").json()
    assert served["sections"]["1"]["kind"] == "unavailable"
    assert "revoked" in served["sections"]["1"]["reason"]
    assert "counseling contact" not in json.dumps(served)
    assert served["sections"]["2"]["kind"] == "available"
    assert served["sections"]["7"]["kind"] == "available"

    _authorize(admin)
    assert executive.get("/briefing").json()["sections"]["1"]["kind"] == "available"


def test_a_change_waits_for_the_question_being_answered(
    app: FastAPI, admin: TestClient
) -> None:
    """The PUT takes the institution's ask lock, so a run in progress keeps
    the state it started with and the next run sees the change."""
    import threading

    lock = app.state.ask_lock_for(1)
    results: list[int] = []

    def put() -> None:
        response = admin.put(
            URL,
            json={
                "authorized": True,
                "authorized_by": AUTHORIZED_BY,
                "document_reference": REFERENCE,
            },
        )
        results.append(response.status_code)

    with lock:
        worker = threading.Thread(target=put)
        worker.start()
        worker.join(timeout=0.5)
        assert worker.is_alive()
        assert results == []
    worker.join(timeout=10)
    assert results == [200]


# --- the briefing carries its own M9 ----------------------------------------------


def test_only_a_q1_briefing_produced_while_authorized_carries_m9(
    admin: TestClient, executive: TestClient
) -> None:
    before = executive.post("/ask", json={"question": Q1}).json()["briefing"]
    assert before["aggregates"] == {}
    assert "M9" not in before["sections"]["4"]["findings"]

    _authorize(admin)
    q1 = executive.post("/ask", json={"question": Q1}).json()["briefing"]
    m9 = q1["aggregates"]["M9"]
    assert m9["display"] == "fewer than 10"
    assert m9["row_ids"] == []
    assert m9["authorization"]["authorized_by"] == AUTHORIZED_BY
    assert q1["sections"]["4"]["findings"]["M9"] == {
        "title": "Students in M2 with any counseling contact this term",
        "display": "fewer than 10",
        "source_fields": m9["source_fields"],
        "aggregate_only": True,
    }
    assert executive.get("/briefing").json()["aggregates"]["M9"] == m9

    q2 = executive.post("/ask", json={"question": Q2}).json()["briefing"]
    assert q2["aggregates"] == {}
    assert "M9" not in q2["sections"]["4"]["findings"]


def test_a_revoke_drops_the_stored_briefing_m9_at_once(
    admin: TestClient, executive: TestClient
) -> None:
    _authorize(admin)
    executive.post("/ask", json={"question": Q1})
    _revoke(admin)
    served = executive.get("/briefing").json()
    assert served["aggregates"] == {}
    assert "M9" not in served["sections"]["4"]["findings"]
    assert "counseling contact" not in json.dumps(served)
