"""Tests for the governed execution step: dispatches.

Covers migration 5 (idempotent, upgrades a real version-1..4 database), the
office address book routes, message composition (deterministic, no student
identifiers), the Send gating (staff and admin only, CSRF and the
consequential-action rate bucket from the middleware), the outbox default,
the smtp provider against a mocked smtplib, and every loud refusal.
"""

from __future__ import annotations

import json
import sqlite3
import ssl
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cabinet.api import create_app
from cabinet.audit import EVENT_TYPES
from cabinet.migrations import migrate, recorded_versions
from cabinet.outbound import (
    MisconfiguredSmtpProvider,
    OutboundError,
    OutboxProvider,
    outbound_from_env,
)
from cabinet.questions import (
    DEMO_DECISION_ID,
    UNRESOLVED_HOLDS_DECISION_ID,
    compose_dispatch,
)
from conftest import make_authenticated_client

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO_ROOT / "data" / "fixture.json"

FINANCIAL_AID = {"office": "Financial Aid", "email": "financial-aid@example.edu"}


# --- migration 5 -------------------------------------------------------------


def test_migration_5_is_idempotent_and_upgrades_a_real_1_to_4_database(
    tmp_path: Path,
) -> None:
    """A database at versions [1..4] (the pre-dispatch schema, built by the
    real migration functions) gains the dispatch tables at 5, and a second
    run applies nothing."""
    from cabinet import migrations

    db = tmp_path / "cabinet.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "CREATE TABLE schema_migrations ("
            " version INTEGER PRIMARY KEY, name TEXT NOT NULL,"
            " applied_at TEXT NOT NULL)"
        )
        for version, name, migration in migrations.MIGRATIONS[:4]:
            conn.execute("BEGIN")
            migration(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at)"
                " VALUES (?, ?, '2026-01-01')",
                (version, name),
            )
            conn.commit()
        assert recorded_versions(conn) == [1, 2, 3, 4]
        assert migrate(conn) == [5]
        assert recorded_versions(conn) == [1, 2, 3, 4, 5]
        # Idempotent: nothing pending, nothing applied.
        assert migrate(conn) == []
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {"dispatches", "office_contacts"} <= tables
        statuses = {
            str(row[0]) for row in conn.execute("SELECT status FROM dispatches")
        }
        assert statuses == set()
    finally:
        conn.close()


def test_migration_5_rejects_a_bad_status(tmp_path: Path) -> None:
    """The status CHECK holds: only draft, sent, failed."""
    from cabinet.store import CabinetStore

    store = CabinetStore(tmp_path / "cabinet.db", seed_fixture=FIXTURE_PATH)
    try:
        store.ensure_bootstrap_institution()
        store.create_dispatch(
            1,
            task_id="TASK-x",
            dataset_id=1,
            to_office="Financial Aid",
            channel="email",
            subject="s",
            body="b",
            created_by="staff@example.edu",
        )
        with pytest.raises(sqlite3.IntegrityError):
            store._conn.execute("UPDATE dispatches SET status = 'queued' WHERE id = 1")
        store._conn.rollback()
    finally:
        store.close()


# --- helpers -----------------------------------------------------------------


def _approve(client: TestClient, decision_id: str = DEMO_DECISION_ID) -> None:
    response = client.post("/decisions/approve", json={"decision_id": decision_id})
    assert response.status_code == 200, response.text


def _compose(client: TestClient, decision_id: str = DEMO_DECISION_ID) -> dict[str, Any]:
    response = client.post(f"/decisions/{decision_id}/dispatch")
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def _set_offices(client: TestClient, offices: list[dict[str, str]]) -> None:
    response = client.put("/admin/offices", json={"offices": offices})
    assert response.status_code == 200, response.text


def _events(client: TestClient, event_type: str) -> list[dict[str, Any]]:
    return client.get("/events", params={"type": event_type}).json()["events"]  # type: ignore[no-any-return]


# --- composition ---------------------------------------------------------------


def test_compose_refused_loudly_before_approval() -> None:
    client = make_authenticated_client(create_app(), role="executive")
    response = client.post(f"/decisions/{DEMO_DECISION_ID}/dispatch")
    assert response.status_code == 409
    assert "has not been approved" in response.json()["detail"]
    refused = _events(client, "data.refused")
    assert len(refused) == 1
    assert "has not been approved" in refused[0]["payload"]["reason"]


def test_compose_builds_the_draft_from_the_findings() -> None:
    client = make_authenticated_client(create_app(), role="executive")
    _approve(client)
    body = _compose(client)
    assert body["created"] is True
    dispatch = body["dispatch"]
    assert dispatch["status"] == "draft"
    assert dispatch["to_office"] == "Financial Aid"
    assert dispatch["channel"] == "email"
    assert dispatch["created_by"] == "executive@test.example"
    assert dispatch["sent_by"] is None
    assert dispatch["sent_at"] is None
    assert dispatch["provider"] is None
    # The office, the decision, the numbers with their finding ids, and the
    # approving user are all named.
    assert "Financial Aid" in dispatch["subject"]
    assert DEMO_DECISION_ID in dispatch["body"]
    assert "Approved by: executive@test.example" in dispatch["body"]
    assert "18 continuing students" in dispatch["body"]
    assert "(finding M3)" in dispatch["body"]
    assert "$1,000" in dispatch["body"]
    # Never a student id or advisor id (STU-…, ADV-… in the fixture).
    text = dispatch["subject"] + dispatch["body"]
    assert "STU-" not in text
    assert "ADV-" not in text

    dispatched = _events(client, "task.dispatched")
    assert len(dispatched) == 1
    assert dispatched[0]["actor"] == "executive@test.example"
    assert dispatched[0]["payload"]["dispatch_id"] == dispatch["id"]
    assert dispatched[0]["payload"]["decision_id"] == DEMO_DECISION_ID


def test_compose_is_idempotent() -> None:
    client = make_authenticated_client(create_app(), role="executive")
    _approve(client)
    first = _compose(client)
    second = _compose(client)
    assert second["created"] is False
    assert second["dispatch"]["id"] == first["dispatch"]["id"]
    assert len(_events(client, "task.dispatched")) == 1


def test_compose_dispatch_unknown_decision_is_none() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    from cabinet.fixture import parse_fixture
    from cabinet.metrics import findings as compute_findings

    findings_obj = compute_findings(parse_fixture(fixture), fixture_path=FIXTURE_PATH)
    assert compose_dispatch(findings_obj, "D-nope", "exec@example.edu") is None


def test_dispatch_routes_404_unknown_decision() -> None:
    client = make_authenticated_client(create_app())
    assert client.get("/decisions/D-nope/dispatch").status_code == 404
    assert client.post("/decisions/D-nope/dispatch").status_code == 404
    assert client.post("/decisions/D-nope/dispatch/send").status_code == 404


def test_get_dispatch_reports_approval_and_contact_state() -> None:
    app = create_app()
    admin = make_authenticated_client(app, role="admin")
    reviewer = make_authenticated_client(app, role="reviewer")
    before = reviewer.get(f"/decisions/{DEMO_DECISION_ID}/dispatch").json()
    assert before["approved"] is False
    assert before["office"] == "Financial Aid"
    assert before["office_contact"] is None
    assert before["dispatch"] is None

    _approve(admin)
    _compose(admin)
    _set_offices(admin, [FINANCIAL_AID])
    after = reviewer.get(f"/decisions/{DEMO_DECISION_ID}/dispatch").json()
    assert after["approved"] is True
    assert after["office_contact"] == "financial-aid@example.edu"
    assert after["dispatch"]["status"] == "draft"


def test_reviewer_may_not_compose() -> None:
    app = create_app()
    admin = make_authenticated_client(app, role="admin")
    _approve(admin)
    reviewer = make_authenticated_client(app, role="reviewer")
    response = reviewer.post(f"/decisions/{DEMO_DECISION_ID}/dispatch")
    assert response.status_code == 403


# --- the office address book ----------------------------------------------------


def test_admin_manages_office_contacts() -> None:
    app = create_app()
    admin = make_authenticated_client(app, role="admin")
    assert admin.get("/admin/offices").json() == {"offices": []}

    _set_offices(
        admin, [FINANCIAL_AID, {"office": "Bursar", "email": "bursar@example.edu"}]
    )
    offices = admin.get("/admin/offices").json()["offices"]
    assert offices == [
        {"office": "Bursar", "email": "bursar@example.edu"},
        FINANCIAL_AID,
    ]
    changed = _events(admin, "admin.changed")
    assert changed[-1]["payload"]["action"] == "office_contacts"
    assert changed[-1]["payload"]["offices"] == ["Bursar", "Financial Aid"]

    # PUT replaces the whole book.
    _set_offices(admin, [FINANCIAL_AID])
    offices = admin.get("/admin/offices").json()["offices"]
    assert offices == [FINANCIAL_AID]


def test_office_contacts_validate_and_refuse_non_admins() -> None:
    app = create_app()
    admin = make_authenticated_client(app, role="admin")
    bad = admin.put(
        "/admin/offices",
        json={
            "offices": [
                {"office": "Financial Aid", "email": "not-an-address"},
                {"office": "", "email": "bursar@example.edu"},
            ]
        },
    )
    assert bad.status_code == 422
    assert len(bad.json()["errors"]) == 2
    assert admin.get("/admin/offices").json() == {"offices": []}

    staff = make_authenticated_client(app, role="staff")
    assert staff.get("/admin/offices").status_code == 403
    assert (
        staff.put("/admin/offices", json={"offices": [FINANCIAL_AID]}).status_code
        == 403
    )


# --- send ------------------------------------------------------------------------


def _ready_to_send(
    app: FastAPI,
) -> tuple[TestClient, TestClient, TestClient]:
    """Admin approved and composed, the address book has Financial Aid."""
    admin = make_authenticated_client(app, role="admin")
    _approve(admin)
    _compose(admin)
    _set_offices(admin, [FINANCIAL_AID])
    executive = make_authenticated_client(app, role="executive")
    staff = make_authenticated_client(app, role="staff")
    return admin, executive, staff


def test_send_requires_a_draft() -> None:
    app = create_app()
    admin = make_authenticated_client(app, role="admin")
    _approve(admin)
    _set_offices(admin, [FINANCIAL_AID])
    response = admin.post(f"/decisions/{DEMO_DECISION_ID}/dispatch/send")
    assert response.status_code == 409
    assert "prepare the message first" in response.json()["detail"]


def test_executive_may_not_send() -> None:
    app = create_app()
    _, executive, _ = _ready_to_send(app)
    response = executive.post(f"/decisions/{DEMO_DECISION_ID}/dispatch/send")
    assert response.status_code == 403
    assert "staff member" in response.json()["detail"]
    refused = _events(executive, "data.refused")
    assert any("staff member" in str(event["payload"]["reason"]) for event in refused)


def test_send_refuses_when_the_office_has_no_mailbox() -> None:
    app = create_app()
    admin = make_authenticated_client(app, role="admin")
    _approve(admin)
    _compose(admin)
    response = admin.post(f"/decisions/{DEMO_DECISION_ID}/dispatch/send")
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "Financial Aid" in detail
    assert "PUT /admin/offices" in detail
    refused = _events(admin, "data.refused")
    assert any("no mailbox" in str(event["payload"]["reason"]) for event in refused)
    # Nothing was sent: the row is still a draft.
    dispatch = admin.get(f"/decisions/{DEMO_DECISION_ID}/dispatch").json()["dispatch"]
    assert dispatch["status"] == "draft"
    assert dispatch["sent_at"] is None


def test_staff_sends_through_the_outbox(tmp_path: Path) -> None:
    app = create_app()
    admin, _, staff = _ready_to_send(app)
    response = staff.post(f"/decisions/{DEMO_DECISION_ID}/dispatch/send")
    assert response.status_code == 200, response.text
    dispatch = response.json()["dispatch"]
    assert dispatch["status"] == "sent"
    assert dispatch["sent_by"] == "staff@test.example"
    assert dispatch["sent_at"] is not None
    assert dispatch["provider"] == "outbox"
    # The outbox file: var/outbox is the database's sibling (tmp here), and
    # the provider_ref is the path relative to it.
    db_path = Path(app.state.auth.path)
    eml = db_path.parent / "outbox" / dispatch["provider_ref"]
    assert dispatch["provider_ref"] == f"bootstrap/{dispatch['id']}.eml"
    assert eml.is_file()
    content = eml.read_text(encoding="utf-8")
    assert "To: financial-aid@example.edu" in content
    assert "Subject: Approved follow-up for Financial Aid" in content
    assert "18 continuing students" in content
    assert "STU-" not in content
    # The repo's var/ was not touched (the conftest guard also checks).
    assert not str(eml).startswith(str(REPO_ROOT / "var"))

    # Staff may not read the audit log; the assertions read it as admin.
    sent = _events(admin, "task.sent")
    assert len(sent) == 1
    assert sent[0]["actor"] == "staff@test.example"
    assert sent[0]["payload"]["provider"] == "outbox"
    assert sent[0]["payload"]["provider_ref"] == dispatch["provider_ref"]

    # A second send is refused with the earlier record.
    again = staff.post(f"/decisions/{DEMO_DECISION_ID}/dispatch/send")
    assert again.status_code == 409
    assert again.json()["dispatch"]["id"] == dispatch["id"]
    assert again.json()["dispatch"]["sent_by"] == "staff@test.example"
    assert len(_events(admin, "task.sent")) == 1


def test_q2_dispatch_goes_to_the_bursar_and_refuses_without_a_contact() -> None:
    app = create_app()
    admin = make_authenticated_client(app, role="admin")
    _approve(admin, UNRESOLVED_HOLDS_DECISION_ID)
    composed = _compose(admin, UNRESOLVED_HOLDS_DECISION_ID)
    dispatch = composed["dispatch"]
    assert dispatch["to_office"] == "Bursar"
    assert "(finding M5)" in dispatch["body"]
    assert "STU-" not in dispatch["body"]

    refused = admin.post(f"/decisions/{UNRESOLVED_HOLDS_DECISION_ID}/dispatch/send")
    assert refused.status_code == 409
    assert "Bursar" in refused.json()["detail"]

    _set_offices(admin, [{"office": "Bursar", "email": "bursar@example.edu"}])
    sent = admin.post(f"/decisions/{UNRESOLVED_HOLDS_DECISION_ID}/dispatch/send")
    assert sent.status_code == 200
    assert sent.json()["dispatch"]["provider"] == "outbox"


def test_dispatch_state_survives_a_restart() -> None:
    app = create_app()
    admin = make_authenticated_client(app, role="admin")
    _approve(admin)
    _compose(admin)

    restarted = make_authenticated_client(create_app(), role="admin")
    state = restarted.get(f"/decisions/{DEMO_DECISION_ID}/dispatch").json()
    assert state["dispatch"]["status"] == "draft"


def test_send_is_rate_limited_under_the_ask_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CABINET_RATE_ASK_PER_MIN", "1")
    app = create_app()
    admin, _, staff = _ready_to_send(app)
    first = staff.post(f"/decisions/{DEMO_DECISION_ID}/dispatch/send")
    assert first.status_code == 200
    second = staff.post(f"/decisions/{DEMO_DECISION_ID}/dispatch/send")
    assert second.status_code == 429
    # The 429 came from the bucket, not the already-sent rule: no second
    # task.sent event, and no new refusal.
    assert len(_events(admin, "task.sent")) == 1


# --- the smtp provider ------------------------------------------------------------


class _FakeSmtp:
    """A stand-in for smtplib.SMTP/SMTP_SSL recording the whole flow."""

    instances: list[_FakeSmtp] = []

    def __init__(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        context: ssl.SSLContext | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.context = context
        self.calls: list[tuple[str, object]] = []
        _FakeSmtp.instances.append(self)

    def starttls(self, context: ssl.SSLContext | None = None) -> None:
        self.calls.append(("starttls", context))

    def login(self, user: str, password: str) -> None:
        self.calls.append(("login", (user, password)))

    def send_message(self, message: object) -> None:
        self.calls.append(("send_message", message))

    def __enter__(self) -> _FakeSmtp:
        return self

    def __exit__(self, *args: object) -> None:
        self.calls.append(("quit", None))


def _smtp_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, port: str = "2525"
) -> Path:
    password_file = tmp_path / "smtp-password"
    password_file.write_text("smtp-secret\n", encoding="utf-8")
    password_file.chmod(0o600)
    monkeypatch.setenv("CABINET_OUTBOUND", "smtp")
    monkeypatch.setenv("CABINET_SMTP_HOST", "smtp.example.edu")
    monkeypatch.setenv("CABINET_SMTP_PORT", port)
    monkeypatch.setenv("CABINET_SMTP_FROM", "cabinet@example.edu")
    monkeypatch.setenv("CABINET_SMTP_PASSWORD_FILE", str(password_file))
    return password_file


def test_smtp_send_uses_starttls_and_the_password_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _smtp_env(tmp_path, monkeypatch)
    _FakeSmtp.instances = []
    monkeypatch.setattr("cabinet.outbound.smtplib.SMTP", _FakeSmtp)
    app = create_app()
    _, _, staff = _ready_to_send(app)
    response = staff.post(f"/decisions/{DEMO_DECISION_ID}/dispatch/send")
    assert response.status_code == 200, response.text
    dispatch = response.json()["dispatch"]
    assert dispatch["provider"] == "smtp"
    assert "dispatch-" in dispatch["provider_ref"]

    smtp = _FakeSmtp.instances[0]
    assert smtp.host == "smtp.example.edu"
    assert smtp.port == 2525
    kinds = [kind for kind, _ in smtp.calls]
    assert kinds == ["starttls", "login", "send_message", "quit"]
    # The TLS context verifies the server; the stdlib default would not.
    context = dict(smtp.calls)["starttls"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert dict(smtp.calls)["login"] == ("cabinet@example.edu", "smtp-secret")
    message = dict(smtp.calls)["send_message"]
    assert message["To"] == "financial-aid@example.edu"  # type: ignore[index]


def test_smtp_port_465_uses_smtps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _smtp_env(tmp_path, monkeypatch, port="465")
    _FakeSmtp.instances = []
    monkeypatch.setattr("cabinet.outbound.smtplib.SMTP_SSL", _FakeSmtp)
    provider = outbound_from_env()
    ref = provider.send(
        to="financial-aid@example.edu",
        subject="s",
        body="b",
        dispatch_id=7,
        institution_slug="bootstrap",
    )
    assert "dispatch-7" in ref
    smtp = _FakeSmtp.instances[0]
    assert smtp.port == 465
    # Implicit TLS: no STARTTLS step.
    assert "starttls" not in [kind for kind, _ in smtp.calls]


def test_smtp_missing_settings_refuse_in_production(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CABINET_OUTBOUND", "smtp")
    monkeypatch.setenv("CABINET_SMTP_HOST", "smtp.example.edu")
    # port, from, and the password file are missing.
    with pytest.raises(RuntimeError) as excinfo:
        outbound_from_env(production=True)
    message = str(excinfo.value)
    assert "CABINET_SMTP_PORT" in message
    assert "CABINET_SMTP_FROM" in message
    assert "CABINET_SMTP_PASSWORD_FILE" in message
    assert "CABINET_SMTP_HOST" not in message

    # In development the app starts, but the send fails loudly.
    provider = outbound_from_env(production=False)
    assert isinstance(provider, MisconfiguredSmtpProvider)
    with pytest.raises(OutboundError) as send_error:
        provider.send(
            to="x@example.edu",
            subject="s",
            body="b",
            dispatch_id=1,
            institution_slug="bootstrap",
        )
    assert "CABINET_SMTP_PORT" in str(send_error.value)

    # And production startup itself refuses, one line, SystemExit.
    monkeypatch.setenv("CABINET_ENV", "production")
    monkeypatch.setenv("CABINET_BIND", "127.0.0.1:8939")
    with pytest.raises(SystemExit):
        create_app()


def test_smtp_failure_marks_the_dispatch_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _smtp_env(tmp_path, monkeypatch)

    class _DownSmtp(_FakeSmtp):
        def starttls(self, context: ssl.SSLContext | None = None) -> None:
            raise OSError("connection refused")

    monkeypatch.setattr("cabinet.outbound.smtplib.SMTP", _DownSmtp)
    app = create_app()
    _, _, staff = _ready_to_send(app)
    response = staff.post(f"/decisions/{DEMO_DECISION_ID}/dispatch/send")
    assert response.status_code == 503
    assert "connection refused" in response.json()["detail"]
    dispatch = staff.get(f"/decisions/{DEMO_DECISION_ID}/dispatch").json()["dispatch"]
    assert dispatch["status"] == "failed"
    assert "connection refused" in dispatch["error"]
    assert dispatch["sent_at"] is None


def test_unknown_outbound_provider_refuses_to_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CABINET_OUTBOUND", "pigeon")
    with pytest.raises(SystemExit):
        create_app()


def test_fake_provider_records_the_message() -> None:
    from cabinet.outbound import FakeOutboundProvider

    fake = FakeOutboundProvider()
    ref = fake.send(
        to="office@example.edu",
        subject="s",
        body="b",
        dispatch_id=3,
        institution_slug="bootstrap",
    )
    assert ref == "fake-1"
    assert fake.sent[0]["to"] == "office@example.edu"


def test_outbox_write_failure_is_an_outbound_error(tmp_path: Path) -> None:
    provider = OutboxProvider(tmp_path / "outbox")
    # A file where the directory must be makes mkdir fail.
    blocker = tmp_path / "outbox"
    blocker.write_text("not a directory", encoding="utf-8")
    with pytest.raises(OutboundError):
        provider.send(
            to="x@example.edu",
            subject="s",
            body="b",
            dispatch_id=1,
            institution_slug="bootstrap",
        )


def test_dispatch_events_are_in_the_audit_vocabulary() -> None:
    assert "task.dispatched" in EVENT_TYPES
    assert "task.sent" in EVENT_TYPES


def test_two_concurrent_sends_deliver_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two staff members clicking Send at the same moment: the provider is
    called exactly once, one request wins, the other is the 409."""
    app = create_app()
    admin, _, staff = _ready_to_send(app)
    other = make_authenticated_client(app, role="staff", email="staff2@test.example")
    calls: list[str] = []

    class SlowProvider:
        name = "fake"

        def send(self, **kwargs: str | int) -> str:
            calls.append(str(kwargs["to"]))
            time.sleep(0.3)
            return "slow-1"

    provider = SlowProvider()
    monkeypatch.setattr("cabinet.api.outbound_from_env", lambda **_: provider)
    statuses: list[int] = []

    def go(client: TestClient) -> None:
        statuses.append(
            client.post(f"/decisions/{DEMO_DECISION_ID}/dispatch/send").status_code
        )

    threads = [threading.Thread(target=go, args=(c,)) for c in (staff, other)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(statuses) == [200, 409]
    assert calls == ["financial-aid@example.edu"]
    assert len(_events(admin, "task.sent")) == 1


def test_a_failed_send_is_on_the_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app()
    admin, _, staff = _ready_to_send(app)

    class BrokenProvider:
        name = "fake"

        def send(self, **kwargs: str | int) -> str:
            raise OutboundError("the SMTP send failed: 503 try later")

    monkeypatch.setattr("cabinet.api.outbound_from_env", lambda **_: BrokenProvider())
    response = staff.post(f"/decisions/{DEMO_DECISION_ID}/dispatch/send")
    assert response.status_code == 503
    failed = _events(admin, "task.send_failed")
    assert len(failed) == 1
    assert failed[0]["actor"] == "staff@test.example"
    assert "503" in failed[0]["payload"]["error"]
    assert "body" not in failed[0]["payload"]
    assert _events(admin, "task.sent") == []
    row = staff.get(f"/decisions/{DEMO_DECISION_ID}/dispatch").json()["dispatch"]
    assert row["status"] == "failed"


def test_a_loose_smtp_password_file_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    password_file = _smtp_env(tmp_path, monkeypatch)
    password_file.chmod(0o644)
    _FakeSmtp.instances = []
    monkeypatch.setattr("cabinet.outbound.smtplib.SMTP", _FakeSmtp)
    app = create_app()
    _, _, staff = _ready_to_send(app)
    response = staff.post(f"/decisions/{DEMO_DECISION_ID}/dispatch/send")
    assert response.status_code == 503
    assert "readable by group or others" in response.json()["detail"]
    assert _FakeSmtp.instances == []
