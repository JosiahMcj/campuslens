"""The wording a person reads after approving and sending, and the demo
institution's display name.

- The follow-up task never says "simulated, nothing sent": it waits for
  the message until a named person sends it, and once the office's message
  has left a repeated approval answers "Sent". A task stored by an earlier
  build is rewritten on the way out.
- A dispatch message never contains "nothing is sent" (the decision text
  the president reads keeps it, because approving really sends nothing).
- Migration 8 renames "Bootstrap Institution" to "Demonstration
  University", leaves the slug and any admin-chosen name alone, and a new
  database starts with the new name.
"""

from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cabinet.api import (
    LEGACY_TASK_STATUS,
    TASK_STATUS_SENT,
    TASK_STATUS_WAITING,
    create_app,
)
from cabinet.migrations import (
    BOOTSTRAP_NAME,
    BOOTSTRAP_SLUG,
    LEGACY_BOOTSTRAP_NAME,
    MIGRATIONS,
    SCHEMA_VERSION,
    migrate,
    recorded_versions,
)
from cabinet.questions import (
    DEMO_DECISION_ID,
    UNRESOLVED_HOLDS_DECISION_ID,
    compose_dispatch,
)
from cabinet.store import CabinetStore
from conftest import make_authenticated_client

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO_ROOT / "data" / "fixture.json"
FINANCIAL_AID = {"office": "Financial Aid", "email": "financial-aid@example.edu"}


def _approve(client: TestClient, decision_id: str = DEMO_DECISION_ID) -> dict[str, Any]:
    response = client.post("/decisions/approve", json={"decision_id": decision_id})
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


# --- the follow-up task's status -------------------------------------------------


def test_a_new_task_waits_for_the_message() -> None:
    client = make_authenticated_client(create_app(), role="executive")
    first = _approve(client)
    assert first["created"] is True
    assert first["task"]["status"] == TASK_STATUS_WAITING == (
        "Waiting for the message to be sent"
    )
    again = _approve(client)
    assert again["created"] is False
    assert again["task"]["status"] == TASK_STATUS_WAITING


def test_a_task_stored_with_the_legacy_status_is_rewritten(tmp_path: Path) -> None:
    app = create_app()
    client = make_authenticated_client(app, role="executive")
    _approve(client)
    store: CabinetStore = app.state.auth
    # An approval recorded by an earlier build carries the old wording.
    with store._lock:
        row = store._conn.execute(
            "SELECT task FROM decisions WHERE decision_id = ?", (DEMO_DECISION_ID,)
        ).fetchone()
        task = json.loads(row["task"])
        task["status"] = LEGACY_TASK_STATUS
        store._conn.execute(
            "UPDATE decisions SET task = ? WHERE decision_id = ?",
            (json.dumps(task), DEMO_DECISION_ID),
        )
        store._conn.commit()
    again = _approve(client)
    assert again["created"] is False
    assert again["task"]["status"] == TASK_STATUS_WAITING
    assert "nothing sent" not in json.dumps(again)


def _send(app: FastAPI) -> TestClient:
    admin = make_authenticated_client(app, role="admin")
    _approve(admin)
    assert admin.post(f"/decisions/{DEMO_DECISION_ID}/dispatch").status_code == 200
    assert (
        admin.put("/admin/offices", json={"offices": [FINANCIAL_AID]}).status_code
        == 200
    )
    staff = make_authenticated_client(app, role="staff")
    sent = staff.post(f"/decisions/{DEMO_DECISION_ID}/dispatch/send")
    assert sent.status_code == 200, sent.text
    assert sent.json()["dispatch"]["status"] == "sent"
    return admin


def test_once_sent_a_repeated_approval_says_sent() -> None:
    app = create_app()
    admin = _send(app)
    again = _approve(admin)
    assert again["created"] is False
    assert again["task"]["status"] == TASK_STATUS_SENT == "Sent"
    assert "nothing sent" not in json.dumps(again)


# --- the dispatch message ----------------------------------------------------------


def _findings() -> dict[str, Any]:
    from cabinet.fixture import parse_fixture
    from cabinet.metrics import findings as compute_findings

    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return compute_findings(parse_fixture(fixture), fixture_path=FIXTURE_PATH)


def test_no_dispatch_message_says_nothing_is_sent() -> None:
    """All four decision texts (two questions, with and without a count)
    end with "and nothing is sent." in the briefing; none of them may carry
    that clause into the message that is being sent."""
    with_counts = _findings()
    without_counts = copy.deepcopy(with_counts)
    without_counts["M3"]["value"] = None
    without_counts["M5"]["value"] = None
    for findings_obj in (with_counts, without_counts):
        for decision_id in (DEMO_DECISION_ID, UNRESOLVED_HOLDS_DECISION_ID):
            message = compose_dispatch(findings_obj, decision_id, "exec@example.edu")
            assert message is not None
            text = (message["subject"] + "\n" + message["body"]).lower()
            assert "nothing is sent" not in text
            assert "nothing sent" not in text
            assert "Approving authorizes the review only." in message["body"]


def test_the_decision_text_itself_is_unchanged() -> None:
    """Approving sends nothing, so the decision the president reads still
    says so; only the dispatch quotes it without that clause."""
    client = make_authenticated_client(create_app(), role="executive")
    decisions = client.get("/decisions").json()["decisions"]
    assert decisions[0]["text"].endswith("and nothing is sent.")


def test_the_q1_dispatch_quotes_the_decision_with_a_full_stop() -> None:
    message = compose_dispatch(_findings(), DEMO_DECISION_ID, "exec@example.edu")
    assert message is not None
    assert (
        "Approving authorizes the review only. No financial-aid eligibility "
        "decision is made.\n"
    ) in message["body"]


# --- the demonstration institution's name -------------------------------------------


def _database_at_version_7(db: Path, name: str) -> sqlite3.Connection:
    """A real version-7 database: migrations 1 to 7 applied, with the first
    institution carrying ``name`` (migration 1 now seeds the new name, so a
    pre-8 database is reproduced by writing the old one back)."""
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE schema_migrations ("
        " version INTEGER PRIMARY KEY, name TEXT NOT NULL,"
        " applied_at TEXT NOT NULL)"
    )
    for version, migration_name, migration in MIGRATIONS[:7]:
        conn.execute("BEGIN")
        migration(conn)
        conn.execute(
            "INSERT INTO schema_migrations (version, name, applied_at)"
            " VALUES (?, ?, '2026-01-01')",
            (version, migration_name),
        )
        conn.commit()
    present = conn.execute(
        "SELECT COUNT(*) FROM institutions WHERE slug = ?", (BOOTSTRAP_SLUG,)
    ).fetchone()[0]
    if present == 0:
        conn.execute(
            "INSERT INTO institutions (name, slug, created_at)"
            " VALUES (?, ?, '2026-01-01')",
            (name, BOOTSTRAP_SLUG),
        )
    else:
        conn.execute(
            "UPDATE institutions SET name = ? WHERE slug = ?", (name, BOOTSTRAP_SLUG)
        )
    conn.commit()
    return conn


def _name(conn: sqlite3.Connection, slug: str = BOOTSTRAP_SLUG) -> str:
    row = conn.execute(
        "SELECT name FROM institutions WHERE slug = ?", (slug,)
    ).fetchone()
    return str(row[0])


def test_the_names() -> None:
    assert SCHEMA_VERSION == 11
    assert BOOTSTRAP_NAME == "Demonstration University"
    assert LEGACY_BOOTSTRAP_NAME == "Bootstrap Institution"
    assert BOOTSTRAP_SLUG == "bootstrap"


def test_migration_8_renames_the_old_name_and_keeps_the_slug(tmp_path: Path) -> None:
    conn = _database_at_version_7(tmp_path / "cabinet.db", LEGACY_BOOTSTRAP_NAME)
    try:
        assert recorded_versions(conn) == [1, 2, 3, 4, 5, 6, 7]
        assert migrate(conn) == [8, 9, 10, 11]
        assert recorded_versions(conn) == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
        assert _name(conn) == "Demonstration University"
        assert migrate(conn) == []
        assert _name(conn) == "Demonstration University"
    finally:
        conn.close()


def test_migration_8_leaves_a_name_an_admin_chose(tmp_path: Path) -> None:
    conn = _database_at_version_7(tmp_path / "cabinet.db", "Golden Eagle University")
    try:
        conn.execute(
            "INSERT INTO institutions (name, slug, created_at)"
            " VALUES ('Bootstrap Institution', 'other', '2026-01-01')"
        )
        conn.commit()
        assert migrate(conn) == [8, 9, 10, 11]
        assert _name(conn) == "Golden Eagle University"
        # Another institution that happens to carry the old name keeps it:
        # only the bootstrap slug is the demonstration institution.
        assert _name(conn, "other") == "Bootstrap Institution"
    finally:
        conn.close()


def test_a_new_database_starts_with_the_new_name(tmp_path: Path) -> None:
    store = CabinetStore(tmp_path / "cabinet.db", seed_fixture=FIXTURE_PATH)
    institution = store.institution_by_id(store.ensure_bootstrap_institution())
    assert institution is not None
    assert institution["name"] == "Demonstration University"
    assert institution["slug"] == "bootstrap"
    assert recorded_versions(store._conn)[-1] == 11


def test_the_signed_in_user_sees_the_new_name() -> None:
    client = make_authenticated_client(create_app(), role="executive")
    me = client.get("/auth/me").json()
    user = me.get("user", me)
    assert user["institution"]["name"] == "Demonstration University"
    assert user["institution"]["slug"] == "bootstrap"
