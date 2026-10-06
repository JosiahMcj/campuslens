"""Tests for the Financial Aid review queue (facts for the aid office).

Covers migration 6 (idempotent, upgrades a real version 1..5 database), the
facts builder (exactly the M3 population, only the listed facts, never a
counseling field), the three routes and their role gates, idempotency, the
loud 409 before sign-off, the note cap and the status list, the audit
events and what they leave out, tenancy, retention with the dataset, and
the model boundary: no AI employee ever receives a queue row, a fact, or
a note.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cabinet.aidqueue import (
    AID_NOTE_MAX_CHARS,
    FACT_KEYS,
    HOLD_FACT_KEYS,
    queue_rows,
)
from cabinet.api import create_app
from cabinet.auth import AuthStore
from cabinet.fixture import load_fixture
from cabinet.metrics import M3_AMOUNT_LIMIT
from cabinet.metrics import findings as compute_findings
from cabinet.migrations import migrate, recorded_versions
from cabinet.provider import Explanation, FakeProvider
from cabinet.questions import DEMO_DECISION_ID, UNRESOLVED_HOLDS_DECISION_ID
from cabinet.store import CabinetStore
from conftest import TEST_PASSWORD, make_authenticated_client

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO_ROOT / "data" / "fixture.json"
SRC = REPO_ROOT / "backend" / "src" / "cabinet"

Q1 = "What should I know about spring registration?"
Q2 = "Where are unresolved holds affecting continued enrollment?"
QUEUE_URL = f"/decisions/{DEMO_DECISION_ID}/aid-queue"


def _m3_ids() -> list[str]:
    fixture = load_fixture(FIXTURE_PATH)
    m3: list[str] = compute_findings(fixture, fixture_path=FIXTURE_PATH)["M3"][
        "row_ids"
    ]
    return m3


def _events(client: TestClient, event_type: str) -> list[dict[str, Any]]:
    return client.get("/events", params={"type": event_type}).json()["events"]  # type: ignore[no-any-return]


def _sign_off(client: TestClient, decision_id: str = DEMO_DECISION_ID) -> None:
    response = client.post("/decisions/approve", json={"decision_id": decision_id})
    assert response.status_code == 200, response.text


def _opened(client: TestClient, review_id: int) -> str | None:
    """The row's updated_at as this client reads it now (None when the row
    was never saved, or when the client cannot see it)."""
    response = client.get("/aid-queue")
    if response.status_code != 200:
        return None
    for row in response.json()["rows"]:
        if row["id"] == review_id:
            return row["updated_at"]  # type: ignore[no-any-return]
    return None


def _save(client: TestClient, review_id: int, **fields: Any) -> Any:
    """PATCH one row the way the panel does: the changed fields plus the
    updated_at the row had when it was read."""
    return client.patch(
        f"/aid-queue/{review_id}",
        json={**fields, "expected_updated_at": _opened(client, review_id)},
    )


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture
def queued(app: FastAPI) -> FastAPI:
    """An app whose bootstrap institution has the queue prepared."""
    executive = make_authenticated_client(app, role="executive")
    _sign_off(executive)
    response = executive.post(QUEUE_URL)
    assert response.status_code == 200, response.text
    return app


# --- migration 6 ---------------------------------------------------------------


def test_migration_6_is_idempotent_and_upgrades_a_real_1_to_5_database(
    tmp_path: Path,
) -> None:
    from cabinet import migrations

    db = tmp_path / "cabinet.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "CREATE TABLE schema_migrations ("
            " version INTEGER PRIMARY KEY, name TEXT NOT NULL,"
            " applied_at TEXT NOT NULL)"
        )
        for version, name, migration in migrations.MIGRATIONS[:5]:
            conn.execute("BEGIN")
            migration(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at)"
                " VALUES (?, ?, '2026-01-01')",
                (version, name),
            )
            conn.commit()
        # A dispatch row written under version 5 survives the upgrade.
        conn.execute(
            "INSERT INTO dispatches (institution_id, task_id, dataset_id,"
            " to_office, channel, subject, body, status, created_by,"
            " created_at) VALUES (1, 'TASK-x', 1, 'Financial Aid', 'email',"
            " 's', 'b', 'draft', 'staff@x', '2026-01-01')"
        )
        conn.commit()
        assert recorded_versions(conn) == [1, 2, 3, 4, 5]
        assert migrate(conn) == [6]
        assert recorded_versions(conn) == [1, 2, 3, 4, 5, 6]
        assert migrate(conn) == []
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(aid_reviews)")
        }
        assert columns == {
            "id",
            "institution_id",
            "dataset_id",
            "decision_id",
            "student_id",
            "facts_json",
            "status",
            "note",
            "updated_by",
            "updated_at",
            "created_at",
        }
        assert conn.execute("SELECT COUNT(*) FROM dispatches").fetchone()[0] == 1
    finally:
        conn.close()


def test_migration_6_constraints_hold(tmp_path: Path) -> None:
    """Only the three statuses, and one row per student per decision per
    dataset."""
    store = CabinetStore(tmp_path / "cabinet.db", seed_fixture=FIXTURE_PATH)
    try:
        store.ensure_bootstrap_institution()
        assert store.create_aid_queue(
            1, decision_id=DEMO_DECISION_ID, dataset_id=1, rows=[("STU-1", {})]
        )
        with pytest.raises(sqlite3.IntegrityError):
            store._conn.execute("UPDATE aid_reviews SET status = 'done'")
        store._conn.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            store._conn.execute(
                "INSERT INTO aid_reviews (institution_id, dataset_id,"
                " decision_id, student_id, facts_json, created_at)"
                " VALUES (1, 1, ?, 'STU-1', '{}', 'now')",
                (DEMO_DECISION_ID,),
            )
        store._conn.rollback()
    finally:
        store.close()


# --- the facts ---------------------------------------------------------------------


def test_queue_rows_are_exactly_the_m3_population_with_only_the_listed_facts() -> None:
    rows = queue_rows(load_fixture(FIXTURE_PATH))
    assert [student_id for student_id, _ in rows] == _m3_ids()
    assert len(rows) == 18
    for _, facts in rows:
        assert set(facts) == FACT_KEYS
        assert facts["registration_status"] != "registered"
        assert facts["holds"], "every M3 student has a qualifying hold"
        for hold in facts["holds"]:
            assert set(hold) == HOLD_FACT_KEYS
            assert hold["amount"] < float(M3_AMOUNT_LIMIT)
        # Plain JSON: the store writes it as is.
        assert json.loads(json.dumps(facts)) == facts
    dumped = json.dumps(rows)
    assert "counseling" not in dumped
    assert "chaplain" not in dumped


# --- the routes --------------------------------------------------------------------


def test_queue_refused_loudly_before_sign_off(app: FastAPI) -> None:
    executive = make_authenticated_client(app, role="executive")
    response = executive.post(QUEUE_URL)
    assert response.status_code == 409
    assert "has not been authorized" in response.json()["detail"]
    refused = _events(executive, "data.refused")
    assert len(refused) == 1
    assert refused[0]["payload"]["path"] == QUEUE_URL
    assert _events(executive, "aid.queued") == []


def test_only_the_emergency_aid_review_opens_a_queue(app: FastAPI) -> None:
    executive = make_authenticated_client(app, role="executive")
    # Sign-off accepts any approved question's decision id, so no ask (and
    # no model call) is needed to reach Q2's decision.
    _sign_off(executive, UNRESOLVED_HOLDS_DECISION_ID)
    assert (
        executive.post(
            f"/decisions/{UNRESOLVED_HOLDS_DECISION_ID}/aid-queue"
        ).status_code
        == 404
    )
    assert executive.post("/decisions/D-unknown/aid-queue").status_code == 404


@pytest.mark.parametrize("role", ["executive", "staff", "admin"])
def test_queue_is_created_once_with_18_rows(
    app: FastAPI, role: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    executive = make_authenticated_client(
        app, role="executive", email="exec@test.example"
    )
    _sign_off(executive)
    creator = (
        executive
        if role == "executive"
        else make_authenticated_client(app, role=role)
    )
    first = creator.post(QUEUE_URL)
    assert first.status_code == 200, first.text
    assert first.json()["created"] is True
    assert first.json()["count"] == 18
    second = creator.post(QUEUE_URL)
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert second.json()["count"] == 18
    queued = _events(executive, "aid.queued")
    assert len(queued) == 1
    expected_actor = (
        "exec@test.example" if role == "executive" else f"{role}@test.example"
    )
    assert queued[0]["actor"] == expected_actor
    assert queued[0]["payload"] == {
        "decision_id": DEMO_DECISION_ID,
        "dataset_id": 1,
        "count": 18,
    }
    assert "STU-" not in json.dumps(queued[0])


def test_aid_user_reads_18_rows_with_facts_and_no_counseling(queued: FastAPI) -> None:
    aid = make_authenticated_client(queued, role="aid")
    response = aid.get("/aid-queue")
    assert response.status_code == 200
    body = response.json()
    assert body["statuses"] == ["open", "in_review", "closed"]
    assert body["note_max_chars"] == AID_NOTE_MAX_CHARS
    rows = body["rows"]
    assert [row["student_id"] for row in rows] == _m3_ids()
    for row in rows:
        assert row["status"] == "open"
        assert row["note"] == ""
        assert row["updated_by"] is None
        assert set(row["facts"]) == FACT_KEYS
    assert "counseling" not in response.text
    assert "chaplain" not in response.text


@pytest.mark.parametrize(
    ("role", "status"),
    [
        ("aid", 200),
        ("admin", 200),
        ("executive", 200),
        ("reviewer", 200),
        ("staff", 403),
    ],
)
def test_who_reads_the_queue(queued: FastAPI, role: str, status: int) -> None:
    client = make_authenticated_client(queued, role=role)
    assert client.get("/aid-queue").status_code == status


@pytest.mark.parametrize(
    ("role", "status"),
    [
        ("aid", 200),
        ("admin", 200),
        ("executive", 403),
        ("reviewer", 403),
        ("staff", 403),
    ],
)
def test_who_updates_a_row(queued: FastAPI, role: str, status: int) -> None:
    client = make_authenticated_client(queued, role=role)
    response = client.patch(
        "/aid-queue/1", json={"status": "in_review", "expected_updated_at": None}
    )
    assert response.status_code == status


def test_aid_user_updates_a_row_and_the_event_names_them(queued: FastAPI) -> None:
    aid = make_authenticated_client(queued, role="aid", email="aid@test.example")
    note = "  Called the student.\nWaiting on the FAFSA correction.  "
    response = _save(aid, 1, status="in_review", note=note)
    assert response.status_code == 200, response.text
    row = response.json()["row"]
    assert row["status"] == "in_review"
    assert row["note"] == note  # stored exactly as typed
    assert row["updated_by"] == "aid@test.example"
    assert row["updated_at"] is not None
    # The executive reads the same row back, read only.
    executive = make_authenticated_client(queued, role="executive")
    rows = executive.get("/aid-queue").json()["rows"]
    assert rows[0]["note"] == note
    assert _save(executive, 1, note="x").status_code == 403
    updated = _events(executive, "aid.updated")
    assert len(updated) == 1
    event = updated[0]
    assert event["actor"] == "aid@test.example"
    assert event["payload"] == {
        "aid_review_id": 1,
        "decision_id": DEMO_DECISION_ID,
        "status_from": "open",
        "status_to": "in_review",
        "note_changed": True,
    }
    assert "FAFSA" not in json.dumps(event)


def test_status_alone_leaves_the_note_and_note_alone_leaves_the_status(
    queued: FastAPI,
) -> None:
    aid = make_authenticated_client(queued, role="aid")
    _save(aid, 2, note="first note")
    row = _save(aid, 2, status="closed").json()["row"]
    assert (row["status"], row["note"]) == ("closed", "first note")
    row = _save(aid, 2, note="").json()["row"]
    assert (row["status"], row["note"]) == ("closed", "")


def test_patch_validation(queued: FastAPI) -> None:
    aid = make_authenticated_client(queued, role="aid")
    at_cap = "n" * AID_NOTE_MAX_CHARS
    assert _save(aid, 1, note=at_cap).status_code == 200
    over = _save(aid, 1, note=at_cap + "n")
    assert over.status_code == 422
    assert "limit is 1,000" in over.json()["detail"]
    bad = _save(aid, 1, status="done")
    assert bad.status_code == 422
    assert "open, in_review, closed" in bad.json()["detail"]
    assert _save(aid, 1).status_code == 422
    assert _save(aid, 9999, status="open").status_code == 404
    # A rejected update changes nothing.
    assert aid.get("/aid-queue").json()["rows"][0]["note"] == at_cap


def test_patch_without_csrf_token_is_403(queued: FastAPI) -> None:
    aid = make_authenticated_client(queued, role="aid")
    del aid.headers["X-CSRF-Token"]
    assert _save(aid, 1, status="closed").status_code == 403


def test_dispatch_state_carries_the_queue_count(app: FastAPI) -> None:
    executive = make_authenticated_client(app, role="executive")
    before = executive.get(f"/decisions/{DEMO_DECISION_ID}/dispatch").json()
    assert before["aid_queue"] == {"supported": True, "count": None, "created_at": None}
    _sign_off(executive)
    executive.post(QUEUE_URL)
    after = executive.get(f"/decisions/{DEMO_DECISION_ID}/dispatch").json()
    assert after["aid_queue"]["count"] == 18
    other = executive.get(
        f"/decisions/{UNRESOLVED_HOLDS_DECISION_ID}/dispatch"
    ).json()
    assert other["aid_queue"]["supported"] is False


def test_admin_can_create_an_aid_user(app: FastAPI) -> None:
    admin = make_authenticated_client(app, role="admin")
    response = admin.post(
        "/admin/users", json={"email": "aid-office@test.example", "role": "aid"}
    )
    assert response.status_code == 201, response.text
    assert response.json()["role"] == "aid"
    listed = {user["email"]: user["role"] for user in admin.get("/admin/users").json()}
    assert listed["aid-office@test.example"] == "aid"


def test_users_cli_accepts_the_aid_role(capsys: pytest.CaptureFixture[str]) -> None:
    from cabinet.users import main as users_main

    assert users_main(["add", "--email", "cli-aid@test.example", "--role", "aid"]) == 0
    assert "created aid user cli-aid@test.example" in capsys.readouterr().out


# --- tenancy and retention ---------------------------------------------------------


def test_queue_does_not_leak_across_institutions(queued: FastAPI) -> None:
    store: AuthStore = queued.state.auth
    second = store.create_institution("Two Rivers College", "two-rivers")
    store.create_user("aid-b@test.example", TEST_PASSWORD, "aid", second)
    client = TestClient(queued)
    login = client.post(
        "/auth/login", json={"email": "aid-b@test.example", "password": TEST_PASSWORD}
    )
    assert login.status_code == 200
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    assert client.get("/aid-queue").json()["rows"] == []
    # Another institution's row id is a 404, the same as no row at all.
    assert _save(client, 1, status="closed").status_code == 404


def test_queue_is_pinned_to_its_dataset_and_purged_with_it(tmp_path: Path) -> None:
    store = CabinetStore(tmp_path / "cabinet.db", seed_fixture=FIXTURE_PATH)
    try:
        store.ensure_bootstrap_institution()
        # A second, soft-deleted dataset well past the retention window.
        store._conn.execute(
            "INSERT INTO datasets (id, institution_id, name, uploaded_by,"
            " uploaded_at, sha256, row_counts, is_active, deleted_at)"
            " VALUES (2, 1, 'old', 'admin', '2020-01-01', 'x', '{}', 0,"
            " '2020-01-02T00:00:00+00:00')"
        )
        store._conn.commit()
        for dataset_id in (1, 2):
            store.create_aid_queue(
                1,
                decision_id=DEMO_DECISION_ID,
                dataset_id=dataset_id,
                rows=[("STU-0001", {}), ("STU-0002", {})],
            )
        assert len(store.aid_reviews_for(1, dataset_id=2)) == 2
        purged = store.purge_deleted_datasets()
        assert [row["id"] for row in purged] == [2]
        assert store.aid_reviews_for(1, dataset_id=2) == []
        assert len(store.aid_reviews_for(1, dataset_id=1)) == 2
    finally:
        store.close()


# --- the model boundary -------------------------------------------------------------


class _CapturingProvider(FakeProvider):
    """The deterministic fake, keeping every payload a model would receive."""

    def __init__(self) -> None:
        super().__init__()
        self.received: list[str] = []

    def explain(self, findings: dict[str, Any], role: str) -> Explanation:
        self.received.append(json.dumps(findings, sort_keys=True, default=str))
        return super().explain(findings, role)


def test_model_never_receives_the_queue_or_the_notes(
    queued: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a queue in place and a note on a row, both questions run
    the whole cabinet again: no payload any AI employee receives carries a
    student id, a queue field, a fact, or the note."""
    provider = _CapturingProvider()
    monkeypatch.setattr("cabinet.api.provider_from_env", lambda: provider)
    aid = make_authenticated_client(queued, role="aid")
    sentinel = "SENTINEL-NOTE-7f3a"
    assert (
        _save(aid, 1, status="in_review", note=sentinel).status_code
        == 200
    )
    executive = make_authenticated_client(queued, role="executive")
    for question in (Q1, Q2):
        response = executive.post("/ask", json={"question": question})
        assert response.status_code == 200, response.text
        assert response.json()["accepted"] is True
    # Two analysts and the Chief of Staff per question.
    assert len(provider.received) == 6
    queue_rows_now = aid.get("/aid-queue").json()["rows"]
    # Schema field names such as hold_date or responsible_office legitimately
    # appear in the findings' formula text (M5's definition), so the check
    # targets what only the queue carries: its keys, its status values, the
    # note, and every queued student id.
    forbidden = [
        sentinel,
        "STU-",
        "aid_review",
        "facts",
        "note",
        "in_review",
        "advising_appointment_status",
        "advising_last_appointment_date",
        *{row["student_id"] for row in queue_rows_now},
    ]
    for payload in provider.received:
        for needle in forbidden:
            assert needle not in payload, (needle, payload[:200])


def test_model_side_modules_never_reach_the_queue() -> None:
    """Structural half of the boundary: the code that builds model payloads
    and talks to the model does not import the queue module or touch its
    table."""
    for module in ("analysts.py", "provider.py", "permissions.py", "questions.py"):
        source = (SRC / module).read_text(encoding="utf-8")
        assert "aidqueue" not in source, module
        assert "aid_reviews" not in source, module


# --- concurrent edits, the audit payload, the active dataset, path ids -----------

STALE_MESSAGE = "This row changed since you opened it. Reload to see the latest."


def _read_events(app: FastAPI, event_type: str) -> list[dict[str, Any]]:
    """The events of one type, read as an executive (the aid role does not
    read the audit log)."""
    return _events(make_authenticated_client(app, role="executive"), event_type)


def test_two_stale_saves_on_one_row_never_lose_a_note(queued: FastAPI) -> None:
    """Two aid users open the same row, then both save: one sets a note, the
    other a status, each carrying the updated_at it opened. The second save
    is refused with 409 instead of overwriting the first, the note survives,
    and the audit chain records only the transition that happened."""
    first = make_authenticated_client(queued, role="aid", email="aid-a@test.example")
    second = make_authenticated_client(queued, role="aid", email="aid-b@test.example")
    opened = first.get("/aid-queue").json()["rows"][0]
    assert opened["updated_at"] is None
    one = first.patch(
        f"/aid-queue/{opened['id']}",
        json={"note": "Called the student.", "expected_updated_at": None},
    )
    two = second.patch(
        f"/aid-queue/{opened['id']}",
        json={"status": "closed", "expected_updated_at": opened["updated_at"]},
    )
    assert one.status_code == 200, one.text
    assert two.status_code == 409, two.text
    assert two.json()["detail"] == STALE_MESSAGE
    row = first.get("/aid-queue").json()["rows"][0]
    assert (row["status"], row["note"]) == ("open", "Called the student.")
    events = _read_events(queued, "aid.updated")
    assert len(events) == 1
    assert events[0]["actor"] == "aid-a@test.example"
    assert events[0]["payload"]["status_from"] == "open"
    assert events[0]["payload"]["status_to"] == "open"
    assert events[0]["payload"]["note_changed"] is True
    # Reloaded, the second user saves against the current row and succeeds.
    retry = second.patch(
        f"/aid-queue/{opened['id']}",
        json={"status": "closed", "expected_updated_at": row["updated_at"]},
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["row"]["note"] == "Called the student."
    events = _read_events(queued, "aid.updated")
    assert len(events) == 2
    latest = max(events, key=lambda event: int(event["id"]))
    assert (latest["payload"]["status_from"], latest["payload"]["status_to"]) == (
        "open",
        "closed",
    )
    assert latest["payload"]["note_changed"] is False


def test_concurrent_saves_on_one_row_one_wins_one_409(queued: FastAPI) -> None:
    """The same race on real threads: both saves carry the row's opening
    updated_at, so exactly one lands and the other is a 409."""
    from concurrent.futures import ThreadPoolExecutor

    clients = [
        make_authenticated_client(queued, role="aid", email=f"aid-{n}@test.example")
        for n in range(2)
    ]
    opened = clients[0].get("/aid-queue").json()["rows"][0]
    bodies: list[dict[str, Any]] = [
        {"note": "note from A", "expected_updated_at": opened["updated_at"]},
        {"status": "closed", "expected_updated_at": opened["updated_at"]},
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda pair: pair[0].patch(f"/aid-queue/{opened['id']}", json=pair[1]),
                zip(clients, bodies, strict=True),
            )
        )
    codes = sorted(response.status_code for response in responses)
    assert codes == [200, 409], [response.text for response in responses]
    winner = responses[0] if responses[0].status_code == 200 else responses[1]
    row = clients[0].get("/aid-queue").json()["rows"][0]
    assert row == winner.json()["row"]
    events = _read_events(queued, "aid.updated")
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["status_from"] == "open"
    assert payload["status_to"] == row["status"]
    assert payload["note_changed"] is (row["note"] != "")


def test_an_unchanged_field_is_not_an_audited_change(queued: FastAPI) -> None:
    aid = make_authenticated_client(queued, role="aid")
    assert _save(aid, 1, note="same").status_code == 200
    assert _save(aid, 1, status="open", note="same").status_code == 200
    events = _read_events(queued, "aid.updated")
    latest = max(events, key=lambda event: int(event["id"]))
    assert latest["payload"]["note_changed"] is False
    assert latest["payload"]["status_from"] == latest["payload"]["status_to"] == "open"


def test_no_aid_event_payload_carries_a_student_id(queued: FastAPI) -> None:
    """The audit log is append-only and outlives the dataset purge, so the
    aid events name the row, never the student."""
    aid = make_authenticated_client(queued, role="aid")
    rows = aid.get("/aid-queue").json()["rows"]
    for row in rows[:3]:
        response = _save(aid, row["id"], status="in_review", note="n")
        assert response.status_code == 200, response.text
    executive = make_authenticated_client(queued, role="executive")
    events = [
        event
        for event in executive.get("/events").json()["events"]
        if str(event["type"]).startswith("aid.")
    ]
    assert {event["type"] for event in events} == {"aid.queued", "aid.updated"}
    student_ids = {row["student_id"] for row in rows}
    for event in events:
        dumped = json.dumps(event["payload"])
        assert "student_id" not in event["payload"]
        assert "STU-" not in dumped
        for student_id in student_ids:
            assert student_id not in dumped
    updated = [event for event in events if event["type"] == "aid.updated"]
    assert set(updated[0]["payload"]) == {
        "aid_review_id",
        "decision_id",
        "status_from",
        "status_to",
        "note_changed",
    }


def test_rows_of_an_inactive_or_deleted_dataset_are_not_editable(
    queued: FastAPI,
) -> None:
    """A row id from a dataset that is no longer active (or was deleted) is
    a 404, the same as no row at all, and nothing changes."""
    store: CabinetStore = queued.state.auth
    store._conn.execute(
        "INSERT INTO datasets (id, institution_id, name, uploaded_by,"
        " uploaded_at, sha256, row_counts, is_active, deleted_at)"
        " VALUES (2, 1, 'old', 'admin', '2026-01-01', 'x', '{}', 0, NULL),"
        " (3, 1, 'gone', 'admin', '2026-01-01', 'x', '{}', 0,"
        " '2026-01-02T00:00:00+00:00')"
    )
    store._conn.commit()
    for dataset_id in (2, 3):
        assert store.create_aid_queue(
            1,
            decision_id=DEMO_DECISION_ID,
            dataset_id=dataset_id,
            rows=[("STU-0001", {})],
        )
    aid = make_authenticated_client(queued, role="aid")
    for dataset_id in (2, 3):
        (row,) = store.aid_reviews_for(1, dataset_id=dataset_id)
        response = aid.patch(
            f"/aid-queue/{row['id']}",
            json={"status": "closed", "expected_updated_at": None},
        )
        assert response.status_code == 404, response.text
        (after,) = store.aid_reviews_for(1, dataset_id=dataset_id)
        assert (after["status"], after["updated_by"]) == ("open", None)
    assert _read_events(queued, "aid.updated") == []


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("PATCH", "/aid-queue/{id}", {"status": "closed", "expected_updated_at": None}),
        ("POST", "/admin/datasets/{id}/activate", None),
        ("DELETE", "/admin/datasets/{id}", None),
        ("POST", "/admin/users/{id}/disable", None),
        ("POST", "/admin/users/{id}/enable", None),
        ("PATCH", "/admin/users/{id}", {"role": "staff"}),
    ],
)
@pytest.mark.parametrize(
    "row_id", ["99999999999999999999999", str(2**63), "-99999999999999999999999"]
)
def test_integer_path_ids_beyond_sqlite_range_are_404(
    queued: FastAPI, method: str, path: str, body: dict[str, Any] | None, row_id: str
) -> None:
    admin = make_authenticated_client(queued, role="admin")
    response = admin.request(method, path.format(id=row_id), json=body)
    assert response.status_code == 404, response.text


def test_a_save_without_expected_updated_at_is_422_and_changes_nothing(
    queued: FastAPI,
) -> None:
    """The row's updated_at as it was read is required on every save, so no
    client can overwrite a row it has not read."""
    aid = make_authenticated_client(queued, role="aid")
    response = aid.patch("/aid-queue/1", json={"status": "closed", "note": "x"})
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert "Reload the row" in detail
    assert "expected_updated_at" in detail
    row = aid.get("/aid-queue").json()["rows"][0]
    assert (row["status"], row["note"], row["updated_at"]) == ("open", "", None)
    assert _read_events(queued, "aid.updated") == []
