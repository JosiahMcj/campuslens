"""Tests for the staff action worklist (cabinet.staffactions and its routes).

Covers migration 9, the list built from the findings (counts, plain words,
idempotent creation), the roles (staff and admin edit, the executive views
and comments, the reviewer reads, the aid role sees Financial Aid only),
optimistic concurrency on every save, the audit events (no note text, no
student id), the history, Send to office through the fake provider and the
outbox default, and the purge with its dataset.
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

from cabinet.api import create_app
from cabinet.audit import EVENT_TYPES
from cabinet.migrations import migrate, recorded_versions
from cabinet.outbound import FakeOutboundProvider
from cabinet.staffactions import action_specs, compose_message
from conftest import make_authenticated_client

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO_ROOT / "data" / "fixture.json"

STUDENT_ID = re.compile(r"\bSTU-\d+")


def _items(client: TestClient) -> list[dict[str, Any]]:
    response = client.get("/staff-actions")
    assert response.status_code == 200, response.text
    return response.json()["items"]  # type: ignore[no-any-return]


def _by_office(client: TestClient, office: str) -> dict[str, Any]:
    return next(item for item in _items(client) if item["office"] == office)


def _events(client: TestClient, event_type: str) -> list[dict[str, Any]]:
    return client.get("/events", params={"type": event_type}).json()["events"]  # type: ignore[no-any-return]


# --- migration 9 -----------------------------------------------------------------


def test_migration_9_upgrades_a_real_1_to_8_database(tmp_path: Path) -> None:
    from cabinet import migrations

    conn = sqlite3.connect(tmp_path / "cabinet.db")
    try:
        conn.execute(
            "CREATE TABLE schema_migrations ("
            " version INTEGER PRIMARY KEY, name TEXT NOT NULL,"
            " applied_at TEXT NOT NULL)"
        )
        for version, name, migration in migrations.MIGRATIONS[:8]:
            conn.execute("BEGIN")
            migration(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at)"
                " VALUES (?, ?, '2026-01-01')",
                (version, name),
            )
            conn.commit()
        assert migrate(conn) == [9]
        assert recorded_versions(conn)[-1] == 9
        assert migrate(conn) == []
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"staff_actions", "staff_action_notes", "staff_action_history"} <= tables
        # No table of the worklist has a student column.
        for table in ("staff_actions", "staff_action_notes", "staff_action_history"):
            info = conn.execute(f"PRAGMA table_info({table})")
            columns = {str(row[1]) for row in info}
            assert not any("student" in column for column in columns)
    finally:
        conn.close()


def test_status_check_constraint_holds(tmp_path: Path) -> None:
    from cabinet.store import CabinetStore

    store = CabinetStore(tmp_path / "cabinet.db", seed_fixture=FIXTURE_PATH)
    try:
        store.ensure_bootstrap_institution()
        store.ensure_staff_actions(
            1, dataset_id=1, specs=[("k", "Bursar", "M5", 3)]
        )
        with pytest.raises(sqlite3.IntegrityError):
            store._conn.execute("UPDATE staff_actions SET status = 'blocked'")
        store._conn.rollback()
    finally:
        store.close()


# --- the list --------------------------------------------------------------------


def test_the_list_is_built_from_the_findings_in_plain_words() -> None:
    client = make_authenticated_client(create_app(), role="staff")
    body = client.get("/staff-actions").json()
    assert body["statuses"] == ["todo", "in_progress", "done"]
    assert body["can_edit"] is True and body["can_send"] is True
    items = body["items"]
    offices = [item["office"] for item in items]
    assert offices == [
        "Student Success",
        "Financial Aid",
        "Bursar",
        "Library",
        "Registrar",
        "Student Life",
    ]
    counts = {item["office"]: item["count"] for item in items}
    assert counts == {
        "Student Success": 12,
        "Financial Aid": 18,
        "Bursar": 24,
        "Library": 1,
        "Registrar": 2,
        "Student Life": 1,
    }
    for item in items:
        assert item["status"] == "todo"
        assert item["owner"] is None
        assert item["due_date"] is None
        assert item["updated_at"] is None
        assert item["message"] is None
        text = item["title"] + item["what"] + item["noun"]
        assert re.search(r"\bM\d\b", text) is None
        assert STUDENT_ID.search(json.dumps(item)) is None
    assert _by_office(client, "Library")["noun"] == "1 unresolved hold"


def test_the_list_is_created_once() -> None:
    app = create_app()
    staff = make_authenticated_client(app, role="staff")
    first = _items(staff)
    second = _items(staff)
    assert [item["id"] for item in first] == [item["id"] for item in second]
    store = app.state.auth
    count = store._conn.execute("SELECT count(*) FROM staff_actions").fetchone()[0]
    assert count == len(first)


def test_a_missing_figure_keeps_its_action_without_a_count() -> None:
    specs = action_specs({"M3": {"value": None}, "M4": {"value": 4}, "M5": {}})
    assert [(spec.office, spec.count) for spec in specs] == [
        ("Student Success", 4),
        ("Financial Aid", None),
    ]


# --- roles -----------------------------------------------------------------------


def test_aid_sees_only_financial_aid() -> None:
    aid = make_authenticated_client(create_app(), role="aid")
    body = aid.get("/staff-actions").json()
    assert [item["office"] for item in body["items"]] == ["Financial Aid"]
    assert body["can_edit"] is False
    assert body["can_note"] is False
    item = body["items"][0]
    assert aid.patch(
        f"/staff-actions/{item['id']}",
        json={"status": "done", "expected_updated_at": None},
    ).status_code == 403
    assert aid.post(
        f"/staff-actions/{item['id']}/notes", json={"text": "x"}
    ).status_code == 403


def test_reviewer_reads_only() -> None:
    reviewer = make_authenticated_client(create_app(), role="reviewer")
    body = reviewer.get("/staff-actions").json()
    assert len(body["items"]) == 6
    assert body["can_edit"] is False and body["can_note"] is False
    action_id = body["items"][0]["id"]
    assert reviewer.patch(
        f"/staff-actions/{action_id}",
        json={"status": "done", "expected_updated_at": None},
    ).status_code == 403
    assert reviewer.post(
        f"/staff-actions/{action_id}/notes", json={"text": "x"}
    ).status_code == 403
    assert reviewer.post(f"/staff-actions/{action_id}/send").status_code == 403


def test_executive_comments_but_does_not_edit_or_send() -> None:
    app = create_app()
    executive = make_authenticated_client(app, role="executive")
    body = executive.get("/staff-actions").json()
    assert body["can_edit"] is False
    assert body["can_note"] is True
    assert body["can_send"] is False
    action_id = body["items"][0]["id"]
    assert executive.patch(
        f"/staff-actions/{action_id}",
        json={"status": "done", "expected_updated_at": None},
    ).status_code == 403
    noted = executive.post(
        f"/staff-actions/{action_id}/notes", json={"text": "Please start this week."}
    )
    assert noted.status_code == 200, noted.text
    assert noted.json()["item"]["notes"][0]["author"] == "executive@test.example"
    refused = executive.post(f"/staff-actions/{action_id}/send")
    assert refused.status_code == 403
    assert "staff member or an administrator" in refused.json()["detail"]
    reasons = [e["payload"]["reason"] for e in _events(executive, "data.refused")]
    assert any("send an action" in reason for reason in reasons)


# --- editing and concurrency -------------------------------------------------


def test_staff_sets_status_owner_and_due_date_with_history_and_audit() -> None:
    app = create_app()
    admin = make_authenticated_client(app, role="admin")
    staff = make_authenticated_client(app, role="staff")
    item = _by_office(staff, "Bursar")
    response = staff.patch(
        f"/staff-actions/{item['id']}",
        json={
            "status": "in_progress",
            "owner": "STAFF@test.example",
            "due_date": "2026-10-20",
            "expected_updated_at": None,
        },
    )
    assert response.status_code == 200, response.text
    updated = response.json()["item"]
    assert updated["status"] == "in_progress"
    assert updated["owner"] == "staff@test.example"
    assert updated["due_date"] == "2026-10-20"
    assert updated["updated_by"] == "staff@test.example"
    changes = {entry["change"]: entry for entry in updated["history"]}
    assert changes["status"]["from_value"] == "todo"
    assert changes["status"]["to_value"] == "in_progress"
    assert changes["owner"]["to_value"] == "staff@test.example"
    assert changes["due_date"]["to_value"] == "2026-10-20"

    events = _events(admin, "action.updated")
    assert len(events) == 1
    payload = events[0]["payload"]
    assert events[0]["actor"] == "staff@test.example"
    assert payload["action_id"] == item["id"]
    assert payload["office"] == "Bursar"
    assert payload["fields"] == ["due_date", "owner", "status"]
    assert payload["status_from"] == "todo"
    assert payload["status_to"] == "in_progress"

    # Back to the office, no due date.
    cleared = staff.patch(
        f"/staff-actions/{item['id']}",
        json={
            "owner": None,
            "due_date": "",
            "expected_updated_at": updated["updated_at"],
        },
    ).json()["item"]
    assert cleared["owner"] is None
    assert cleared["due_date"] is None


def test_a_stale_save_is_refused_and_writes_nothing() -> None:
    app = create_app()
    first = make_authenticated_client(app, role="staff")
    second = make_authenticated_client(app, role="admin")
    item = _by_office(first, "Registrar")
    ok = first.patch(
        f"/staff-actions/{item['id']}",
        json={"status": "done", "expected_updated_at": None},
    )
    assert ok.status_code == 200
    stale = second.patch(
        f"/staff-actions/{item['id']}",
        json={"status": "todo", "expected_updated_at": None},
    )
    assert stale.status_code == 409
    assert stale.json()["item"]["status"] == "done"
    assert _by_office(second, "Registrar")["status"] == "done"
    assert len(_events(second, "action.updated")) == 1


def test_saves_are_validated() -> None:
    staff = make_authenticated_client(create_app(), role="staff")
    action_id = _items(staff)[0]["id"]
    missing_version = staff.patch(
        f"/staff-actions/{action_id}", json={"status": "done"}
    )
    assert missing_version.status_code == 422
    bad = staff.patch(
        f"/staff-actions/{action_id}",
        json={
            "status": "finished",
            "owner": "stranger@example.edu",
            "due_date": "2026-02-30",
            "expected_updated_at": None,
        },
    )
    assert bad.status_code == 422
    assert len(bad.json()["errors"]) == 3
    nothing = staff.patch(
        f"/staff-actions/{action_id}", json={"expected_updated_at": None}
    )
    assert nothing.status_code == 422
    assert staff.patch(
        "/staff-actions/999999", json={"status": "done", "expected_updated_at": None}
    ).status_code == 404
    assert staff.patch(
        f"/staff-actions/{2**70}", json={"status": "done", "expected_updated_at": None}
    ).status_code in (404, 422)


def test_notes_are_kept_but_never_reach_the_audit_log() -> None:
    app = create_app()
    staff = make_authenticated_client(app, role="staff")
    admin = make_authenticated_client(app, role="admin")
    item = _by_office(staff, "Financial Aid")
    secret = "Called the family of STU-0007 about the plan"
    response = staff.post(f"/staff-actions/{item['id']}/notes", json={"text": secret})
    assert response.status_code == 200, response.text
    notes = response.json()["item"]["notes"]
    assert notes[-1]["text"] == secret
    history = response.json()["item"]["history"]
    assert history[-1]["change"] == "note"
    assert history[-1]["to_value"] is None

    events = admin.get("/events").json()["events"]
    noted = [event for event in events if event["type"] == "action.noted"]
    assert len(noted) == 1
    assert noted[0]["payload"] == {"action_id": item["id"], "office": "Financial Aid"}
    for event in events:
        if event["type"].startswith("action."):
            dumped = json.dumps(event)
            assert "Called the family" not in dumped
            assert STUDENT_ID.search(dumped) is None

    empty = staff.post(f"/staff-actions/{item['id']}/notes", json={"text": "   "})
    assert empty.status_code == 422
    long = staff.post(f"/staff-actions/{item['id']}/notes", json={"text": "x" * 1001})
    assert long.status_code == 422


# --- send to office --------------------------------------------------------------


def _set_office(client: TestClient, office: str, email: str) -> None:
    response = client.put(
        "/admin/offices", json={"offices": [{"office": office, "email": email}]}
    )
    assert response.status_code == 200, response.text


@pytest.fixture
def fake_outbound(monkeypatch: pytest.MonkeyPatch) -> FakeOutboundProvider:
    fake = FakeOutboundProvider()
    monkeypatch.setattr(
        "cabinet.staffactions_api.outbound_from_env", lambda **_kwargs: fake
    )
    return fake


def test_send_without_a_mailbox_is_refused_loudly(
    fake_outbound: FakeOutboundProvider,
) -> None:
    staff = make_authenticated_client(create_app(), role="staff")
    item = _by_office(staff, "Bursar")
    response = staff.post(f"/staff-actions/{item['id']}/send")
    assert response.status_code == 409
    assert "no mailbox is set for the Bursar office" in response.json()["detail"]
    assert fake_outbound.sent == []


def test_send_delivers_counts_and_a_link_never_a_student(
    fake_outbound: FakeOutboundProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CABINET_PUBLIC_URL", "https://campuslens.example.edu/")
    app = create_app()
    admin = make_authenticated_client(app, role="admin")
    staff = make_authenticated_client(app, role="staff")
    _set_office(admin, "Bursar", "bursar@example.edu")
    item = _by_office(staff, "Bursar")
    staff.post(f"/staff-actions/{item['id']}/notes", json={"text": "STU-0007 first"})
    response = staff.post(f"/staff-actions/{item['id']}/send")
    assert response.status_code == 200, response.text
    message = response.json()["item"]["message"]
    assert message["status"] == "sent"
    assert message["sent_by"] == "staff@test.example"

    assert len(fake_outbound.sent) == 1
    sent = fake_outbound.sent[0]
    assert sent["to"] == "bursar@example.edu"
    assert "Bursar" in sent["subject"]
    assert "24 unresolved holds" in sent["body"]
    assert "staff@test.example sent this staff action" in sent["body"]
    assert "https://campuslens.example.edu/view/staff-actions" in sent["body"]
    assert STUDENT_ID.search(sent["subject"] + sent["body"]) is None
    assert re.search(r"\bM\d\b", sent["body"]) is None

    events = _events(admin, "action.sent")
    assert len(events) == 1
    assert events[0]["payload"]["office"] == "Bursar"
    assert events[0]["payload"]["provider"] == "fake"
    assert response.json()["item"]["history"][-1]["change"] == "sent"

    # Never sent twice.
    again = staff.post(f"/staff-actions/{item['id']}/send")
    assert again.status_code == 409
    assert len(fake_outbound.sent) == 1


def test_a_failed_send_is_recorded_and_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    from cabinet.outbound import OutboundError

    class _Down:
        name = "fake"

        def send(self, **_kwargs: Any) -> str:
            raise OutboundError("the mail server did not answer")

    monkeypatch.setattr(
        "cabinet.staffactions_api.outbound_from_env", lambda **_kwargs: _Down()
    )
    app = create_app()
    admin = make_authenticated_client(app, role="admin")
    _set_office(admin, "Library", "library@example.edu")
    item = _by_office(admin, "Library")
    failed = admin.post(f"/staff-actions/{item['id']}/send")
    assert failed.status_code == 503
    assert failed.json()["item"]["message"]["status"] == "failed"
    assert "did not answer" in failed.json()["item"]["message"]["error"]
    assert len(_events(admin, "action.send_failed")) == 1

    fake = FakeOutboundProvider()
    monkeypatch.setattr(
        "cabinet.staffactions_api.outbound_from_env", lambda **_kwargs: fake
    )
    retried = admin.post(f"/staff-actions/{item['id']}/send")
    assert retried.status_code == 200, retried.text
    assert retried.json()["item"]["message"]["status"] == "sent"
    assert retried.json()["item"]["message"]["error"] is None
    assert len(fake.sent) == 1


def test_the_outbox_default_writes_an_eml_file(tmp_path: Path) -> None:
    app = create_app()
    admin = make_authenticated_client(app, role="admin")
    _set_office(admin, "Registrar", "registrar@example.edu")
    item = _by_office(admin, "Registrar")
    response = admin.post(f"/staff-actions/{item['id']}/send")
    assert response.status_code == 200, response.text
    outbox = app.state.auth.path.parent / "outbox"
    files = list(outbox.rglob("*.eml"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "To: registrar@example.edu" in text
    assert "2 unresolved holds" in text
    assert STUDENT_ID.search(text) is None


def test_compose_message_without_a_link_names_the_page() -> None:
    message = compose_message(
        {
            "office": "Library",
            "finding_id": "M5",
            "count": 1,
            "status": "in_progress",
            "owner": None,
            "due_date": "2026-11-02",
        },
        sender="staff@example.edu",
        institution_name="Demonstration University",
        sign_in_url=None,
    )
    assert "Staff actions page" in message["body"]
    assert "Due: November 2, 2026" in message["body"]
    assert "Owner: the Library office" in message["body"]
    assert "Status: In progress" in message["body"]


# --- vocabulary and purge -------------------------------------------------------


def test_the_new_event_types_are_in_the_vocabulary() -> None:
    for event_type in (
        "action.updated",
        "action.noted",
        "action.sent",
        "action.send_failed",
    ):
        assert event_type in EVENT_TYPES


def test_the_worklist_is_purged_with_its_dataset() -> None:
    app: FastAPI = create_app()
    staff = make_authenticated_client(app, role="staff")
    item = _items(staff)[0]
    staff.post(f"/staff-actions/{item['id']}/notes", json={"text": "hello"})
    store = app.state.auth
    dataset_id = store._conn.execute(
        "SELECT dataset_id FROM staff_actions WHERE id = ?", (item["id"],)
    ).fetchone()[0]
    store._conn.execute(
        "UPDATE datasets SET deleted_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
        (dataset_id,),
    )
    store._conn.commit()
    store.purge_deleted_datasets()
    for table in ("staff_actions", "staff_action_notes", "staff_action_history"):
        left = store._conn.execute(
            f"SELECT count(*) FROM {table} WHERE dataset_id = ?", (dataset_id,)
        ).fetchone()[0]
        assert left == 0, table


# --- institution settings: the outside connections ---------------------------


def test_connections_say_what_is_configured_never_a_credential(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    key = tmp_path / "ethos.key"
    key.write_text("very-secret-ethos-key\n")
    monkeypatch.setenv("CABINET_ETHOS_BASE_URL", "https://ethos.example.edu")
    monkeypatch.setenv("CABINET_ETHOS_API_KEY_FILE", str(key))
    monkeypatch.delenv("CABINET_PSEUDONYM_KEY_FILE", raising=False)
    app = create_app()
    admin = make_authenticated_client(app, role="admin")
    body = admin.get("/admin/connections").json()
    ellucian = body["ellucian"]
    assert ellucian["configured"] is False
    assert [s["set"] for s in ellucian["settings"]] == [True, True, False]
    assert ellucian["last_import"] is None
    assert body["outbound"] == {"provider": "outbox"}
    dumped = json.dumps(body)
    assert "very-secret" not in dumped
    assert str(key) not in dumped
    assert "ethos.example.edu" not in dumped

    store = app.state.auth
    store._conn.execute(
        "UPDATE datasets SET uploaded_by = 'ethos-import' WHERE institution_id = 1"
    )
    store._conn.commit()
    last = admin.get("/admin/connections").json()["ellucian"]["last_import"]
    assert last is not None and last["in_use"] is True

    staff = make_authenticated_client(app, role="staff")
    assert staff.get("/admin/connections").status_code == 403
