"""Institutions as tenants, bring-your-own data, durable storage.

Covers: per-institution findings/datasets/audit/decisions/briefings
isolation (cross-tenant reads are 404, never 403), upload validation
(strict schema, PII reject list, pseudonymous ids, counseling flag, row
counts, 20 MB cap), activation recomputing findings for that institution
only, soft delete + purge, the seeded fictional dataset and its
``meta.fictional`` label, backup/restore round-trip, the migration runner
and its unknown-version refusal, and audit export/verify on the database.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cabinet.api import DEFAULT_FIXTURE_PATH, DEMO_DECISION_ID, create_app
from cabinet.auth import AuthStore
from cabinet.backup import create_backup, restore_backup
from cabinet.migrations import SchemaVersionError
from cabinet.store import CabinetStore
from conftest import TEST_PASSWORD, make_authenticated_client

TwoInstitutions = tuple[TestClient, TestClient, int, int]

APPROVED_QUESTION = "What should I know about spring registration?"


def _fixture_document() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(
        DEFAULT_FIXTURE_PATH.read_text(encoding="utf-8")
    )
    return document


@pytest.fixture
def app() -> FastAPI:
    return create_app()


def _login(app: FastAPI, email: str) -> TestClient:
    """Log in through the real path and arm the CSRF header."""
    client = TestClient(app)
    response = client.post(
        "/auth/login", json={"email": email, "password": TEST_PASSWORD}
    )
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client


@pytest.fixture
def two_institutions(app: FastAPI) -> TwoInstitutions:
    """Admin clients for two institutions: the bootstrap one and a second."""
    store: AuthStore = app.state.auth
    bootstrap = store.ensure_bootstrap_institution()
    second = store.create_institution("Two Rivers College", "two-rivers")
    store.create_user("admin-a@test.example", TEST_PASSWORD, "admin", bootstrap)
    store.create_user("admin-b@test.example", TEST_PASSWORD, "admin", second)
    return (
        _login(app, "admin-a@test.example"),
        _login(app, "admin-b@test.example"),
        bootstrap,
        second,
    )


# -- tenancy: isolation -----------------------------------------------------


def test_new_institution_is_seeded_with_the_fictional_demo_dataset(
    two_institutions: TwoInstitutions,
) -> None:
    _, second_client, _, _ = two_institutions
    datasets = second_client.get("/admin/datasets").json()["datasets"]
    assert len(datasets) == 1
    assert datasets[0]["name"] == "Demonstration (fictional)"
    assert datasets[0]["is_active"] is True
    assert datasets[0]["row_counts"] == {"students": 185, "prior_year_students": 135}
    findings = second_client.get("/findings").json()
    assert findings["meta"]["fictional"] is True
    assert findings["M2"]["value"] == 42


def test_audit_ids_and_events_are_per_institution(
    two_institutions: TwoInstitutions,
) -> None:
    first, second, _, _ = two_institutions
    first.post("/ask", json={"question": "not an approved question"})
    second.post("/ask", json={"question": "not an approved question"})
    first_events = first.get("/events").json()["events"]
    second_events = second.get("/events").json()["events"]
    # Both chains start at id 1, and neither contains the other's events.
    assert first_events[0]["id"] == 1
    assert second_events[0]["id"] == 1
    assert {e["id"] for e in first_events} == {1, 2}
    assert {e["id"] for e in second_events} == {1, 2}
    first.post("/ask", json={"question": "not an approved question"})
    assert len(first.get("/events").json()["events"]) == 4
    assert len(second.get("/events").json()["events"]) == 2


def test_decisions_do_not_leak_across_institutions(
    two_institutions: TwoInstitutions,
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    first, second, _, _ = two_institutions
    asked = first.post("/ask", json={"question": APPROVED_QUESTION})
    decision_id = asked.json()["briefing"]["sections"]["6"]["decisions"][0]["id"]
    approved = first.post("/decisions/approve", json={"decision_id": decision_id})
    assert approved.json()["created"] is True
    assert (
        first.get("/decisions").json()["decisions"][0]["approved"] is True
    )
    assert (
        second.get("/decisions").json()["decisions"][0]["approved"] is False
    )
    # The second institution's briefing is its own, not the first's.
    assert second.get("/briefing").status_code == 404
    briefing = first.get("/briefing").json()
    assert briefing["meta"]["fictional"] is True


def test_recordings_do_not_leak_across_institutions(
    two_institutions: TwoInstitutions,
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    first, second, first_id, second_id = two_institutions
    first.post("/ask", json={"question": APPROVED_QUESTION})
    store: AuthStore = app.state.auth
    with store._lock:
        first_recordings = store._conn.execute(
            "SELECT role, key FROM recordings WHERE institution_id = ?", (first_id,)
        ).fetchall()
        second_recordings = store._conn.execute(
            "SELECT role, key FROM recordings WHERE institution_id = ?", (second_id,)
        ).fetchall()
    assert len(first_recordings) == 3  # both analysts plus the chief
    assert second_recordings == []


# -- upload validation --------------------------------------------------------


def _upload(client: TestClient, document: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    response = client.post(
        "/admin/datasets",
        content=json.dumps(document).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    return response.status_code, response.json()


def test_upload_valid_dataset_reports_counts_and_counseling_flag(
    two_institutions: TwoInstitutions,
) -> None:
    _, second, _, _ = two_institutions
    status, body = _upload(second, _fixture_document())
    assert status == 201
    assert body["validation"]["row_counts"] == {
        "students": 185,
        "prior_year_students": 135,
    }
    assert body["validation"]["counseling"] == "present, will always be refused"
    assert body["dataset"]["is_active"] is False
    # The demo dataset is still active; the upload waits for activation.
    assert second.get("/findings").json()["meta"]["fictional"] is True


def test_upload_rejects_unknown_fields(two_institutions: TwoInstitutions) -> None:
    _, second, _, _ = two_institutions
    document = _fixture_document()
    document["students"][0]["attendance_risk"] = 0.9
    status, body = _upload(second, document)
    assert status == 422
    assert any("attendance_risk" in error for error in body["errors"])


def test_upload_rejects_pii_columns(two_institutions: TwoInstitutions) -> None:
    _, second, _, _ = two_institutions
    for pii_key in ("email", "phone", "ssn", "dob", "name"):
        document = _fixture_document()
        document["students"][0]["profile"][pii_key] = "x"
        status, body = _upload(second, document)
        assert status == 422, pii_key
        assert any(pii_key in error for error in body["errors"]), pii_key


def test_upload_rejects_non_pseudonymous_student_ids(
    two_institutions: TwoInstitutions,
) -> None:
    _, second, _, _ = two_institutions
    document = _fixture_document()
    document["students"][0]["profile"]["student_id"] = "John Smith"
    status, body = _upload(second, document)
    assert status == 422
    assert any("student_id" in error for error in body["errors"])


def test_upload_rejects_unparseable_and_wrong_shape(
    two_institutions: TwoInstitutions,
) -> None:
    _, second, _, _ = two_institutions
    response = second.post(
        "/admin/datasets",
        content=b"{ not json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    status, body = _upload(second, {"terms": {}, "students": []})
    assert status == 422
    assert body["errors"]


def test_upload_body_cap_is_20mb(two_institutions: TwoInstitutions) -> None:
    _, second, _, _ = two_institutions
    big = b'{"meta": ' + b" " * (21 * 1024 * 1024) + b"{}"
    response = second.post(
        "/admin/datasets",
        content=big,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_upload_requires_admin_role(app: FastAPI) -> None:
    executive = make_authenticated_client(app, role="executive")
    status, _ = _upload(executive, _fixture_document())
    assert status == 403
    assert executive.get("/admin/datasets").status_code == 403


# -- activation, deletion, retention -------------------------------------------


def _document_with_one_hold_removed() -> dict[str, Any]:
    document = _fixture_document()
    for student in document["students"]:
        if student["profile"]["student_id"] == "STU-0120":
            student["holds"] = []
    return document


def test_activation_recomputes_findings_for_that_institution_only(
    two_institutions: TwoInstitutions,
) -> None:
    first, second, _, _ = two_institutions
    assert first.get("/findings").json()["M3"]["value"] == 18
    status, body = _upload(second, _document_with_one_hold_removed())
    assert status == 201
    dataset_id = body["dataset"]["id"]
    assert second.post(f"/admin/datasets/{dataset_id}/activate").status_code == 200
    # M3 drops by one for the second institution; the first is untouched.
    assert second.get("/findings").json()["M3"]["value"] == 17
    assert first.get("/findings").json()["M3"]["value"] == 18
    # The previous dataset stays until deleted.
    assert len(second.get("/admin/datasets").json()["datasets"]) == 2


def test_cross_tenant_dataset_ids_are_404_not_403(
    two_institutions: TwoInstitutions,
) -> None:
    first, second, _, _ = two_institutions
    status, body = _upload(second, _fixture_document())
    dataset_id = body["dataset"]["id"]
    assert first.post(f"/admin/datasets/{dataset_id}/activate").status_code == 404
    assert first.delete(f"/admin/datasets/{dataset_id}").status_code == 404
    # And institution one's datasets never appear in institution two's list.
    second_ids = {d["id"] for d in second.get("/admin/datasets").json()["datasets"]}
    first_ids = {d["id"] for d in first.get("/admin/datasets").json()["datasets"]}
    assert not first_ids & second_ids


def test_soft_delete_rules_and_purge(
    two_institutions: TwoInstitutions, app: FastAPI
) -> None:
    _, second, _, second_id = two_institutions
    active = second.get("/admin/datasets").json()["datasets"][0]
    # The active dataset cannot be deleted.
    assert second.delete(f"/admin/datasets/{active['id']}").status_code == 409
    status, body = _upload(second, _fixture_document())
    dataset_id = body["dataset"]["id"]
    deleted = second.delete(f"/admin/datasets/{dataset_id}")
    assert deleted.status_code == 200
    assert deleted.json()["purge_after_days"] == 30
    # Soft-deleted: hidden from the list, file still on disk.
    assert dataset_id not in {
        d["id"] for d in second.get("/admin/datasets").json()["datasets"]
    }
    store: AuthStore = app.state.auth
    row = store.dataset_row(second_id, dataset_id, include_deleted=True)
    assert row is not None
    path = store.dataset_path(row)
    assert path.exists()
    # Within the retention window: purge keeps it.
    assert store.purge_deleted_datasets(older_than_days=30) == []
    # Past it: row and file are gone.
    store._conn.execute(
        "UPDATE datasets SET deleted_at = ? WHERE id = ?",
        ("2000-01-01T00:00:00+00:00", dataset_id),
    )
    store._conn.commit()
    purged = store.purge_deleted_datasets(older_than_days=30)
    assert [row["id"] for row in purged] == [dataset_id]
    assert not path.exists()


def test_dataset_files_are_0600(
    two_institutions: TwoInstitutions, app: FastAPI
) -> None:
    _, second, _, second_id = two_institutions
    store: AuthStore = app.state.auth
    for row in store.datasets_for(second_id):
        mode = stat.S_IMODE(store.dataset_path(row).stat().st_mode)
        assert mode == 0o600


def test_tampered_dataset_file_fails_loudly(
    two_institutions: TwoInstitutions, app: FastAPI
) -> None:
    _, second, _, second_id = two_institutions
    store: AuthStore = app.state.auth
    row = store.active_dataset(second_id)
    assert row is not None
    path = store.dataset_path(row)
    path.write_bytes(path.read_bytes() + b" ")
    response = second.get("/findings")
    assert response.status_code == 503
    assert "sha256" in response.json()["reason"]


# -- migrations ------------------------------------------------------------------


def test_app_refuses_an_unknown_schema_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "cabinet.db"
    monkeypatch.setenv("CABINET_DB", str(db))
    store = CabinetStore(db)  # fresh: migrates to the known version
    store._conn.execute(
        "INSERT INTO schema_migrations (version, name, applied_at)"
        " VALUES (999, 'from the future', '2026-01-01')"
    )
    store._conn.commit()
    store.close()
    with pytest.raises(SchemaVersionError):
        CabinetStore(db)
    with pytest.raises(SystemExit) as excinfo:
        create_app()
    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "schema version" in err
    assert "Traceback" not in err


def test_migrate_cli_reports_version(tmp_path: Path) -> None:
    from cabinet.migrations import main as migrate_main

    db = tmp_path / "cabinet.db"
    assert migrate_main(["--db", str(db)]) == 0
    assert migrate_main(["--db", str(db)]) == 0  # idempotent


def test_failed_migration_rolls_back_and_reruns_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash after a migration's first statement must leave nothing
    behind: no schema_migrations row, no partial tables — so the next start
    retries instead of dying on 'already exists' (executescript used to
    commit implicitly, leaving exactly that mess)."""
    import sqlite3

    import cabinet.migrations as migrations

    conn = sqlite3.connect(tmp_path / "cabinet.db")

    def broken(c: sqlite3.Connection) -> None:
        c.execute("CREATE TABLE crash_test (id INTEGER)")
        raise sqlite3.OperationalError("simulated crash mid-migration")

    monkeypatch.setattr(migrations, "MIGRATIONS", [(1, "broken", broken)])
    with pytest.raises(sqlite3.OperationalError):
        migrations.migrate(conn)
    assert migrations.recorded_versions(conn) == []
    assert "crash_test" not in migrations._table_names(conn)

    def good(c: sqlite3.Connection) -> None:
        c.execute("CREATE TABLE crash_test (id INTEGER)")

    monkeypatch.setattr(migrations, "MIGRATIONS", [(1, "good", good)])
    assert migrations.migrate(conn) == [1]
    assert migrations.migrate(conn) == []  # idempotent re-run
    conn.close()


def test_migrate_cli_turns_a_database_error_into_one_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from cabinet.migrations import main as migrate_main

    db = tmp_path / "cabinet.db"
    db.write_bytes(b"this is not a sqlite database")
    assert migrate_main(["--db", str(db)]) == 1
    err = capsys.readouterr().err
    assert err.startswith("migrate:")
    assert "Traceback" not in err


def test_app_refuses_a_corrupt_database_with_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "cabinet.db"
    db.write_bytes(b"this is not a sqlite database")
    monkeypatch.setenv("CABINET_DB", str(db))
    with pytest.raises(SystemExit) as excinfo:
        create_app()
    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "cannot start" in err
    assert "Traceback" not in err


def test_version_1_database_gains_dataset_pinning_columns(tmp_path: Path) -> None:
    """A database migrated only to version 1 upgrades in place:
    briefings/decisions gain dataset_id + dataset_sha256, existing rows are
    backfilled to the institution's active dataset, and the audit index is
    created."""
    import sqlite3

    from cabinet.migrations import _migration_1

    db = tmp_path / "cabinet.db"
    conn = sqlite3.connect(db)
    with conn:
        _migration_1(conn)
        conn.execute(
            "CREATE TABLE schema_migrations ("
            " version INTEGER PRIMARY KEY, name TEXT NOT NULL,"
            " applied_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO schema_migrations (version, name, applied_at)"
            " VALUES (1, 'h2 tenancy baseline', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO institutions (name, slug, created_at)"
            " VALUES ('Old U', 'old-u', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO datasets (institution_id, name, uploaded_by, uploaded_at,"
            " sha256, row_counts, is_active, deleted_at)"
            " VALUES (1, 'Old data', 'system', '2026-01-01', 'abc', '{}', 1, NULL)"
        )
        conn.execute(
            "INSERT INTO briefings (institution_id, question_id, produced_at,"
            " sections) VALUES (1, 'q1', '2026-01-01', '{}')"
        )
        conn.execute(
            "INSERT INTO decisions (institution_id, decision_id, approved_by, at,"
            " task) VALUES (1, 'd1', 'exec@x', '2026-01-01', '{}')"
        )
    conn.close()

    store = CabinetStore(db)  # applies migration 2 on open
    briefing = store._conn.execute("SELECT * FROM briefings").fetchone()
    decision = store._conn.execute("SELECT * FROM decisions").fetchone()
    assert briefing["dataset_id"] == 1
    assert briefing["dataset_sha256"] == "abc"
    assert decision["dataset_id"] == 1
    indexes = {
        row[0]
        for row in store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        )
    }
    assert "idx_audit_events_institution_type_id" in indexes
    store.close()


# -- backup and restore -------------------------------------------------------------


def test_backup_restore_round_trip(
    two_institutions: TwoInstitutions,
    app: FastAPI,
    tmp_path: Path,
) -> None:
    first, second, _, _ = two_institutions
    second.post("/ask", json={"question": "not an approved question"})
    status, body = _upload(second, _fixture_document())
    assert status == 201
    store: AuthStore = app.state.auth
    db_path = store.path
    data_dir = store.data_dir
    store.close()

    backup = create_backup(db_path, data_dir, tmp_path / "backups" / "b1")
    assert (backup / "cabinet.db").exists()
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["data_files"]

    # Restore moves the live files aside (never deletes) and verifies.
    restore_backup(backup, db_path, data_dir)
    assert list(db_path.parent.glob("cabinet.db.pre-restore-*"))
    assert list(tmp_path.glob("data.pre-restore-*"))

    reopened = CabinetStore(db_path)
    assert len(reopened.list_institutions()) == 2
    events = reopened.audit_events(
        reopened.institution_by_slug("two-rivers")["id"]  # type: ignore[index]
    )
    assert events
    second_datasets = reopened.datasets_for(
        reopened.institution_by_slug("two-rivers")["id"]  # type: ignore[index]
    )
    assert len(second_datasets) == 2
    reopened.close()

    from cabinet.audit import verify_db

    assert verify_db(db_path) == []


def test_restore_refuses_a_tampered_backup(tmp_path: Path) -> None:
    db = tmp_path / "cabinet.db"
    store = CabinetStore(db, seed_fixture=DEFAULT_FIXTURE_PATH)
    store.ensure_bootstrap_institution()
    store.close()
    backup = create_backup(db, tmp_path / "data", tmp_path / "b1")
    (backup / "cabinet.db").write_bytes(b"tampered")
    with pytest.raises(SystemExit):
        restore_backup(backup, db, tmp_path / "data")


# -- audit export/verify on the database ---------------------------------------------


def test_audit_export_and_verify_on_db_and_jsonl(
    two_institutions: TwoInstitutions,
    app: FastAPI,
    tmp_path: Path,
) -> None:
    first, _, _, _ = two_institutions
    first.post("/ask", json={"question": "not an approved question"})
    store: AuthStore = app.state.auth

    from cabinet.audit import export_db, verify_chain, verify_db
    from cabinet.audit import main as audit_main

    assert verify_db(store.path) == []
    out = tmp_path / "export.jsonl"
    assert export_db(store.path, 1, out) == 2
    assert verify_chain(out) == []
    # The CLI on the database, on an export, and the tamper case.
    assert audit_main(["verify", str(store.path)]) == 0
    assert audit_main(["verify", str(out)]) == 0
    lines = out.read_text(encoding="utf-8").splitlines()
    tampered = tmp_path / "tampered.jsonl"
    event = json.loads(lines[0])
    event["payload"]["question"] = "edited"
    tampered.write_text(
        json.dumps(event) + "\n" + "\n".join(lines[1:]) + "\n", encoding="utf-8"
    )
    assert audit_main(["verify", str(tampered)]) == 1


# -- store write ordering --------------------------------------------------------


def test_failed_seed_leaves_no_institution_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """create_institution commits the tenant row only together with its
    seeded dataset: a file-write failure must leave no orphaned tenant
    (the old code committed the row first, and ensure_bootstrap_institution
    then returned it forever with no active dataset)."""
    store = CabinetStore(
        tmp_path / "cabinet.db",
        data_dir=tmp_path / "data",
        seed_fixture=DEFAULT_FIXTURE_PATH,
    )

    def boom(self: Any, path: Path, raw: bytes) -> None:
        raise OSError("simulated disk failure")

    monkeypatch.setattr(CabinetStore, "_write_dataset_file", boom)
    with pytest.raises(OSError):
        store.create_institution("Two Rivers College", "two-rivers")
    assert store.institution_by_slug("two-rivers") is None
    assert store.list_institutions() == []
    assert list((tmp_path / "data").rglob("*.json")) == []
    store.close()


def test_failed_dataset_write_leaves_no_row_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """add_dataset commits the row only once its file is written and
    fsync'd: a write failure leaves no dataset row whose file is missing
    (the old code committed first, so activation later raised
    FileNotFoundError → 500)."""
    store = CabinetStore(
        tmp_path / "cabinet.db",
        data_dir=tmp_path / "data",
        seed_fixture=DEFAULT_FIXTURE_PATH,
    )
    institution_id = store.create_institution("Two Rivers College", "two-rivers")
    before = {d["name"] for d in store.datasets_for(institution_id)}

    def boom(self: Any, path: Path, raw: bytes) -> None:
        raise OSError("simulated disk failure")

    monkeypatch.setattr(CabinetStore, "_write_dataset_file", boom)
    with pytest.raises(OSError):
        store.add_dataset(
            institution_id,
            name="Broken upload",
            raw=DEFAULT_FIXTURE_PATH.read_bytes(),
            uploaded_by="test",
        )
    assert {d["name"] for d in store.datasets_for(institution_id)} == before
    assert list((tmp_path / "data").rglob("*.json"))  # only the seed file
    assert not (tmp_path / "data" / "two-rivers" / "2.json").exists()
    store.close()


def test_activate_with_a_missing_file_is_a_plain_503(app: FastAPI) -> None:
    """A dataset row whose file vanished fails activation with a StoreError
    → 503 with a plain reason, and the previous dataset stays active."""
    client = make_authenticated_client(app)
    store: AuthStore = app.state.auth
    status, body = _upload(client, _fixture_document())
    assert status == 201
    dataset_id = body["dataset"]["id"]
    row = store.dataset_row(1, dataset_id)
    assert row is not None
    store.dataset_path(row).unlink()  # the file is lost out from under the row

    response = client.post(f"/admin/datasets/{dataset_id}/activate")
    assert response.status_code == 503
    assert "missing" in response.json()["reason"]
    # The activation rolled back: the demo dataset is still active.
    active = store.active_dataset(1)
    assert active is not None and active["id"] != dataset_id
    assert client.get("/findings").status_code == 200


def test_ready_is_not_ready_without_an_active_dataset(app: FastAPI) -> None:
    """A bootstrap institution with no active dataset (e.g. a crash during
    seeding) must report not-ready, not pass readiness silently."""
    store: AuthStore = app.state.auth
    with store._lock:
        store._conn.execute("UPDATE datasets SET is_active = 0")
        store._conn.commit()
    response = TestClient(app).get("/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["checks"]["active_dataset"] is False
    assert any("active dataset" in reason for reason in body["reasons"])


# -- dataset-pinned briefings and approvals ---------------------------------------


def test_activating_a_new_dataset_retires_the_old_briefing_and_approvals(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Briefings and approvals are pinned to the dataset they were computed
    from: after activation GET /briefing is 404 until the next ask, and the
    previous approval is no longer shown as approved."""
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    client = make_authenticated_client(app)

    asked = client.post("/ask", json={"question": APPROVED_QUESTION})
    assert asked.status_code == 200
    assert client.get("/briefing").status_code == 200
    approved = client.post(
        "/decisions/approve", json={"decision_id": DEMO_DECISION_ID}
    )
    assert approved.status_code == 200
    before = client.get("/decisions").json()["decisions"]
    assert any(d["approved"] for d in before)

    document = _fixture_document()
    document["meta"]["title"] = "Modified export"
    status, body = _upload(client, document)
    assert status == 201
    new_dataset_id = body["dataset"]["id"]
    assert (
        client.post(f"/admin/datasets/{new_dataset_id}/activate").status_code == 200
    )

    retired = client.get("/briefing")
    assert retired.status_code == 404
    after = client.get("/decisions").json()["decisions"]
    assert after and not any(d["approved"] for d in after)

    reasked = client.post("/ask", json={"question": APPROVED_QUESTION})
    assert reasked.status_code == 200
    briefing = client.get("/briefing")
    assert briefing.status_code == 200
    assert briefing.json()["meta"]["dataset"]["id"] == new_dataset_id

    # Re-approving against the new dataset is a NEW approval (the decisions
    # primary key includes dataset_id since migration 3), not a collision
    # with the old dataset's row: created=True and fresh events.
    events_before = {
        e["id"]
        for e in client.get("/events", params={"type": "decision.approved"}).json()[
            "events"
        ]
    }
    reapproved = client.post(
        "/decisions/approve", json={"decision_id": DEMO_DECISION_ID}
    )
    assert reapproved.status_code == 200
    assert reapproved.json()["created"] is True
    assert reapproved.json()["event_ids"]
    events_after = client.get("/events", params={"type": "decision.approved"}).json()[
        "events"
    ]
    new_approvals = [e for e in events_after if e["id"] not in events_before]
    assert len(new_approvals) == 1
    assert new_approvals[0]["payload"]["decision_id"] == DEMO_DECISION_ID
    # Approving once more against the same dataset stays idempotent.
    again = client.post("/decisions/approve", json={"decision_id": DEMO_DECISION_ID})
    assert again.json()["created"] is False
    assert again.json()["event_ids"] == []
    # And the old dataset's approval row still exists, untouched.
    store: AuthStore = app.state.auth
    old_dataset_id = next(
        d["id"] for d in store.datasets_for(1) if d["id"] != new_dataset_id
    )
    assert DEMO_DECISION_ID in store.approved_decision_ids(1, dataset_id=old_dataset_id)
    assert DEMO_DECISION_ID in store.approved_decision_ids(1, dataset_id=new_dataset_id)


def test_reactivating_a_dataset_restores_its_own_briefing(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Briefings are keyed per dataset (migration 4): asking on dataset A,
    switching to B, and re-activating A serves A's own briefing again — a
    re-ask on B must not have destroyed A's row (pre-migration-4 the primary
    key was (institution, question), so B's INSERT OR REPLACE overwrote A's
    briefing and re-activating A was a 404)."""
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    client = make_authenticated_client(app)
    store: AuthStore = app.state.auth

    # Dataset A: ask, briefing produced.
    dataset_a = next(d["id"] for d in store.datasets_for(1) if d["is_active"])
    assert client.post("/ask", json={"question": APPROVED_QUESTION}).status_code == 200
    briefing_a = client.get("/briefing")
    assert briefing_a.status_code == 200
    assert briefing_a.json()["meta"]["dataset"]["id"] == dataset_a
    assert briefing_a.json()["sections"]["4"]["findings"]

    # Upload and activate dataset B: no briefing for it yet.
    document = _fixture_document()
    document["meta"]["title"] = "Second export"
    status, body = _upload(client, document)
    assert status == 201
    dataset_b = body["dataset"]["id"]
    assert client.post(f"/admin/datasets/{dataset_b}/activate").status_code == 200
    assert client.get("/briefing").status_code == 404

    # Ask on B; its briefing is pinned to B.
    assert client.post("/ask", json={"question": APPROVED_QUESTION}).status_code == 200
    briefing_b = client.get("/briefing")
    assert briefing_b.status_code == 200
    assert briefing_b.json()["meta"]["dataset"]["id"] == dataset_b

    # Re-activate A: A's own briefing is back, with its sections.
    assert client.post(f"/admin/datasets/{dataset_a}/activate").status_code == 200
    restored = client.get("/briefing")
    assert restored.status_code == 200
    assert restored.json()["meta"]["dataset"]["id"] == dataset_a
    assert restored.json()["sections"] == briefing_a.json()["sections"]

    # And re-activating B serves B's again — both rows coexist.
    assert client.post(f"/admin/datasets/{dataset_b}/activate").status_code == 200
    again_b = client.get("/briefing")
    assert again_b.status_code == 200
    assert again_b.json()["meta"]["dataset"]["id"] == dataset_b
    assert again_b.json()["sections"] == briefing_b.json()["sections"]


def test_version_1_2_database_migrates_to_5_keeping_every_approval_and_briefing(
    tmp_path: Path,
) -> None:
    """A database at versions [1, 2] with existing approvals and briefings
    upgrades to the current schema in place: every decision and briefing row
    survives the two rebuilds, and the new primary keys admit the same
    decision id approved — and the same question answered — against a second
    dataset while staying idempotent per (institution, decision/question,
    dataset). Migration 5 adds the dispatch tables alongside."""
    import sqlite3

    from cabinet.migrations import _migration_1, _migration_2

    db = tmp_path / "cabinet.db"
    conn = sqlite3.connect(db)
    with conn:
        _migration_1(conn)
        _migration_2(conn)
        conn.execute(
            "CREATE TABLE schema_migrations ("
            " version INTEGER PRIMARY KEY, name TEXT NOT NULL,"
            " applied_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO schema_migrations (version, name, applied_at)"
            " VALUES (1, 'h2 tenancy baseline', '2026-01-01'),"
            " (2, 'r3 dataset pinning and audit index', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO institutions (name, slug, created_at)"
            " VALUES ('Old U', 'old-u', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO datasets (institution_id, name, uploaded_by, uploaded_at,"
            " sha256, row_counts, is_active, deleted_at)"
            " VALUES (1, 'Old data', 'system', '2026-01-01', 'abc', '{}', 1, NULL),"
            " (1, 'New data', 'system', '2026-01-02', 'def', '{}', 0, NULL)"
        )
        conn.execute(
            "INSERT INTO decisions (institution_id, decision_id, approved_by,"
            " at, task, dataset_id, dataset_sha256) VALUES"
            " (1, 'd1', 'exec@x', '2026-01-01', '{\"id\": \"TASK-d1\"}', 1, 'abc'),"
            " (1, 'd2', 'exec@x', '2026-01-01', '{\"id\": \"TASK-d2\"}', 1, 'abc')"
        )
        conn.execute(
            "INSERT INTO briefings (institution_id, question_id, produced_at,"
            " sections, dataset_id, dataset_sha256) VALUES"
            " (1, 'q1', '2026-01-01', '{\"question_id\": \"q1\"}', 1, 'abc')"
        )
    conn.close()

    store = CabinetStore(db)  # applies migrations 3 through 8 on open
    from cabinet.migrations import recorded_versions

    assert recorded_versions(store._conn) == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    assert store.approved_decision_ids(1, dataset_id=1) == {"d1", "d2"}
    # The briefing row survived the rebuild, still pinned to dataset 1.
    assert store.latest_briefing(1, dataset_id=1) == {"question_id": "q1"}
    assert store.latest_briefing(1, dataset_id=2) is None
    # The new primary keys include dataset_id.
    decisions_columns = {
        str(row[1])
        for row in store._conn.execute("PRAGMA table_info(decisions)")
    }
    assert {"institution_id", "decision_id", "dataset_id"} <= decisions_columns
    briefings_columns = {
        str(row[1])
        for row in store._conn.execute("PRAGMA table_info(briefings)")
    }
    assert {"institution_id", "question_id", "dataset_id"} <= briefings_columns
    # The same decision approved against a second dataset is a new row;
    # against the same dataset it is ignored.
    task = {"id": "TASK-d1", "status": "simulated, nothing sent"}
    assert (
        store.record_decision(
            1, "d1", "exec@x", task, dataset_id=2, dataset_sha256="def"
        )
        is True
    )
    assert (
        store.record_decision(
            1, "d1", "exec@x", task, dataset_id=2, dataset_sha256="def"
        )
        is False
    )
    assert (
        store.record_decision(
            1, "d1", "exec@x", task, dataset_id=1, dataset_sha256="abc"
        )
        is False
    )
    assert store.approved_decision_ids(1, dataset_id=1) == {"d1", "d2"}
    assert store.approved_decision_ids(1, dataset_id=2) == {"d1"}
    # The per-dataset task lookup returns the row the caller just hit.
    assert store.decision_task(1, "d1", dataset_id=1) == {"id": "TASK-d1"}
    # The same question answered against a second dataset is a NEW briefing
    # row — dataset 1's briefing is not replaced.
    store.save_briefing(
        1, "q1", {"question_id": "q1", "meta": {"dataset": {"id": 2}}},
        dataset_id=2, dataset_sha256="def",
    )
    assert store.latest_briefing(1, dataset_id=1) == {"question_id": "q1"}
    briefing_2 = store.latest_briefing(1, dataset_id=2)
    assert briefing_2 is not None
    assert briefing_2["meta"]["dataset"]["id"] == 2
    # And a re-ask on dataset 2 replaces only dataset 2's row.
    store.save_briefing(
        1, "q1", {"question_id": "q1", "again": True},
        dataset_id=2, dataset_sha256="def",
    )
    assert store.latest_briefing(1, dataset_id=2) == {
        "question_id": "q1",
        "again": True,
    }
    assert store.latest_briefing(1, dataset_id=1) == {"question_id": "q1"}
    store.close()
    # Re-opening is a no-op (migrate twice stays idempotent).
    store = CabinetStore(db)
    assert store.approved_decision_ids(1) == {"d1", "d2"}
    assert store.latest_briefing(1, dataset_id=1) == {"question_id": "q1"}
    store.close()


# -- per-institution ask lock -----------------------------------------------------


def test_one_institutions_run_never_blocks_anothers(
    two_institutions: TwoInstitutions,
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ask lock is per institution: tenant A's in-flight question never
    refuses tenant B, and a 409 never mentions another institution."""
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    first, second, first_id, _ = two_institutions
    lock = app.state.ask_lock_for(first_id)
    assert lock.acquire(blocking=False)
    try:
        response = second.post("/ask", json={"question": APPROVED_QUESTION})
        assert response.status_code == 200
        conflict = first.get("/briefing/enrollment")
        assert conflict.status_code == 409
        reason = conflict.json()["reason"]
        assert "two-rivers" not in reason and "institution" not in reason.lower()
    finally:
        lock.release()


def _ask_lock(app: FastAPI, institution_id: int) -> Any:
    """The institution's ask lock, however this build stores it: through
    the accessor when one is exposed, else on the runtime itself — the
    design this regression test guards against kept the lock on the
    runtime, and against that design the test must fail on its identity
    assertions below, not on an AttributeError for a missing accessor."""
    accessor = getattr(app.state, "ask_lock_for", None)
    if accessor is not None:
        return accessor(institution_id)
    return app.state.runtime_for(institution_id).ask_lock


def test_the_ask_lock_survives_runtime_invalidation(
    app: FastAPI,
) -> None:
    """Activating (or deleting) a dataset rebuilds the institution's
    runtime, but the ask lock must NOT be rebuilt with it: an activation
    landing while an ask holds the lock used to hand the next ask a fresh
    lock, so two runs interleaved on one audit chain and each run's
    event-ids watermark swallowed the other's events. The lock is keyed by
    institution beside the runtimes and is never popped."""
    client = make_authenticated_client(app)
    lock = _ask_lock(app, 1)
    assert lock.acquire(blocking=False)
    try:
        document = _fixture_document()
        document["meta"]["title"] = "Switched export"
        status, body = _upload(client, document)
        assert status == 201
        # Activation invalidates the runtime while the lock is held.
        assert (
            client.post(
                f"/admin/datasets/{body['dataset']['id']}/activate"
            ).status_code
            == 200
        )
        after = _ask_lock(app, 1)
        assert after is lock  # the same object, still held
        assert after.locked()
        # A new runtime was built, but it did not bring a new lock.
        assert not hasattr(app.state.runtime_for(1), "ask_lock")
    finally:
        lock.release()


# -- ask event ids without full-table scans ----------------------------------------


def test_ask_event_ids_are_exactly_the_runs_own_events(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a large pre-existing audit log, an ask's event_ids equal exactly
    the ids appended during it — computed from a MAX(id) watermark, not by
    reading the whole table twice."""
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    store: AuthStore = app.state.auth
    audit = store.audit_for(1)
    for index in range(2000):
        audit.append("question.asked", actor="test", payload={"seed": index})

    client = make_authenticated_client(app)
    response = client.post("/ask", json={"question": APPROVED_QUESTION})
    assert response.status_code == 200
    event_ids = response.json()["event_ids"]
    assert event_ids == list(range(2001, 2001 + len(event_ids)))
    # The route's ids equal an independently computed set: the events this
    # test can see appended after the 2000 it seeded, read back in full —
    # not the same store method the route itself calls.
    expected = [e["id"] for e in store.audit_events(1) if e["id"] > 2000]
    assert event_ids == expected
    types = [
        e["type"] for e in store.audit_events(1) if e["id"] in set(event_ids)
    ]
    assert types[0] == "question.asked"
    assert types[-1] == "briefing.produced"


# -- uploads run off the event loop ------------------------------------------------


def test_a_slow_upload_does_not_block_health(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The upload's heavy synchronous work (validation, hashing, storage)
    runs in the threadpool, so /health keeps answering while an upload is
    mid-flight. On the old all-async handler the sleep below blocks the
    loop and this test's health checks take as long as the upload."""
    import asyncio
    import time

    import httpx2 as httpx  # the pinned httpx fork (requirements.txt)

    store_cls = type(app.state.auth)
    real_add_dataset = store_cls.add_dataset

    def slow_add_dataset(self: Any, *args: Any, **kwargs: Any) -> Any:
        time.sleep(1.5)
        return real_add_dataset(self, *args, **kwargs)

    monkeypatch.setattr(store_cls, "add_dataset", slow_add_dataset)

    async def scenario() -> tuple[int, float]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            app.state.auth.create_user("uploader@test.example", TEST_PASSWORD, "admin")
            login = await client.post(
                "/auth/login",
                json={"email": "uploader@test.example", "password": TEST_PASSWORD},
            )
            assert login.status_code == 200
            csrf = login.json()["csrf_token"]
            body = json.dumps(_fixture_document()).encode("utf-8")
            upload = asyncio.create_task(
                client.post(
                    "/admin/datasets",
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-CSRF-Token": csrf,
                    },
                )
            )
            await asyncio.sleep(0.2)  # let the upload reach the slow part
            start = time.monotonic()
            for _ in range(3):
                health = await client.get("/health")
                assert health.status_code == 200
            elapsed = time.monotonic() - start
            response = await upload
            return response.status_code, elapsed

    status, elapsed = asyncio.run(scenario())
    assert status == 201
    assert elapsed < 1.0


def test_a_slow_upload_write_stalls_neither_the_loop_nor_other_sessions(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dataset file's write + fsync is staged OUTSIDE the store lock,
    and the middleware's session lookups and refusals run in the
    threadpool: while an upload sits in its (here slowed to 1 s) write, an
    authenticated GET /auth/me answers immediately and the event loop
    itself keeps scheduling. Pre-fix the write ran under store._lock and
    the middleware's synchronous get_session waited on it on the loop
    thread, so every tenant stalled for the whole write (measured: 1.22 s
    for /auth/me against a 1.5 s write)."""
    import asyncio
    import time

    import httpx2 as httpx  # the pinned httpx fork (requirements.txt)

    store_cls = type(app.state.auth)
    real_write = store_cls._write_dataset_file

    def slow_write(self: Any, path: Path, raw: bytes) -> None:
        time.sleep(1.0)
        real_write(self, path, raw)

    monkeypatch.setattr(store_cls, "_write_dataset_file", slow_write)

    async def scenario() -> tuple[int, int, float, float]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            app.state.auth.create_user("uploader2@test.example", TEST_PASSWORD, "admin")
            login = await client.post(
                "/auth/login",
                json={"email": "uploader2@test.example", "password": TEST_PASSWORD},
            )
            assert login.status_code == 200
            csrf = login.json()["csrf_token"]
            body = json.dumps(_fixture_document()).encode("utf-8")
            upload = asyncio.create_task(
                client.post(
                    "/admin/datasets",
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-CSRF-Token": csrf,
                    },
                )
            )
            await asyncio.sleep(0.2)  # let the upload reach the slow write
            me_start = time.monotonic()
            me = asyncio.create_task(client.get("/auth/me"))
            # The loop must keep scheduling while that request is in
            # flight: pre-fix the middleware's synchronous store call
            # pinned the loop on the upload's lock for the rest of the
            # write, and this 50 ms sleep took the same ~1 s.
            loop_start = time.monotonic()
            await asyncio.sleep(0.05)
            loop_elapsed = time.monotonic() - loop_start
            me_response = await me
            me_elapsed = time.monotonic() - me_start
            response = await upload
            return (
                response.status_code,
                me_response.status_code,
                me_elapsed,
                loop_elapsed,
            )

    status, me_status, me_elapsed, loop_elapsed = asyncio.run(scenario())
    assert status == 201
    assert me_status == 200
    assert me_elapsed < 0.5  # well under the 1 s write
    assert loop_elapsed < 0.3


# -- backup source validation ------------------------------------------------------


def test_backup_refuses_a_nonexistent_database(tmp_path: Path) -> None:
    """sqlite3.connect would silently CREATE an empty database at a
    misconfigured CABINET_DB and then 'successfully' back it up; the backup
    must instead refuse with a plain reason and create nothing."""
    missing = tmp_path / "var" / "cabinet.db"
    with pytest.raises(SystemExit) as excinfo:
        create_backup(missing, tmp_path / "data")
    assert "does not exist" in str(excinfo.value)
    assert not (tmp_path / "var" / "backups").exists()


def test_backup_refuses_a_non_sqlite_file(tmp_path: Path) -> None:
    db = tmp_path / "cabinet.db"
    db.write_bytes(b"definitely not a sqlite database")
    with pytest.raises(SystemExit) as excinfo:
        create_backup(db, tmp_path / "data", tmp_path / "b1")
    assert "not a readable SQLite database" in str(excinfo.value)
    assert not (tmp_path / "b1").exists()


def test_backup_refuses_an_empty_database_unless_allowed(tmp_path: Path) -> None:
    db = tmp_path / "cabinet.db"
    store = CabinetStore(db)  # schema only, zero rows in every cabinet table
    store.close()
    with pytest.raises(SystemExit) as excinfo:
        create_backup(db, tmp_path / "data", tmp_path / "b1")
    assert "empty" in str(excinfo.value)
    assert not (tmp_path / "b1").exists()
    out = create_backup(db, tmp_path / "data", tmp_path / "b2", allow_empty=True)
    assert (out / "cabinet.db").exists()


def test_backup_cli_on_a_bogus_db_path_exits_nonzero_with_a_plain_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cabinet.backup import main as backup_main

    missing = tmp_path / "var" / "cabinet.db"
    monkeypatch.setenv("CABINET_DB", str(missing))
    with pytest.raises(SystemExit) as excinfo:
        backup_main(["create"])
    assert excinfo.value.code  # non-zero
    assert "does not exist" in str(excinfo.value.code)
    assert not (tmp_path / "var" / "backups").exists()
