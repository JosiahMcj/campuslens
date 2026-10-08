"""A Data page chart sent in an inbox alert (``source.kind == "chart"``).

Only the reference is stored; the series is computed again for the reader's
role every time the message is shown, after the same role checks the series
route makes, so a role that may not read the chart (or narrow or split it
that way) gets "not available", never a value.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cabinet import dashboards as d
from cabinet.api import create_app
from cabinet.store import CabinetStore
from conftest import make_authenticated_client

GENERATE = Path(__file__).resolve().parents[2] / "data" / "school" / "generate.py"


@pytest.fixture(scope="module")
def school_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("school-inbox-chart") / "school.db"
    proc = subprocess.run(
        [sys.executable, str(GENERATE), "--scale", "0.01", "--out", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return out


@pytest.fixture(autouse=True)
def _school_env(school_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CABINET_SCHOOL_DB", str(school_db))
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    d.clear_cache()


@pytest.fixture
def app() -> FastAPI:
    return create_app()


def _login(app: FastAPI, role: str) -> TestClient:
    return make_authenticated_client(app, role=role)


def _id(client: TestClient) -> int:
    return int(client.get("/auth/me").json()["user"]["id"])


def _events(app: FastAPI, event_type: str) -> list[dict[str, Any]]:
    store: CabinetStore = app.state.auth
    institution = store.ensure_bootstrap_institution()
    return store.audit_events(institution, event_type)


def _college_point() -> tuple[str, str]:
    """A term and a college on the by-college chart."""
    result = d.cached_series(d.CHARTS_BY_ID["headcount_by_college"], {}, None)
    return str(result["x"][-1]["key"]), str(result["series"][0]["key"])


def _send(client: TestClient, to: int, ref: str) -> Any:
    return client.post(
        "/inbox",
        json={
            "recipient_id": to,
            "note": "Enrollment dipped here; can you look?",
            "source": {"kind": "chart", "ref": ref},
        },
    )


def test_a_chart_is_stored_as_a_reference_and_reread_for_the_reader(
    app: FastAPI,
) -> None:
    president = _login(app, "executive")
    registrar = _login(app, "registrar")
    term, college = _college_point()
    # Keys in any order: the stored ref is the canonical one.
    sent = _send(
        president,
        _id(registrar),
        f"series={college}&at={term}&chart=headcount_by_college",
    )
    assert sent.status_code == 201, sent.text
    ref = f"chart=headcount_by_college&at={term}&series={college}"
    assert sent.json()["source_ref"] == ref

    store: CabinetStore = app.state.auth
    with store._lock:
        row = store._conn.execute(
            "SELECT source_kind, source_ref, snapshot FROM inbox_messages"
        ).fetchone()
    assert (row["source_kind"], row["source_ref"], row["snapshot"]) == (
        "chart",
        ref,
        None,
    )

    received = registrar.get("/inbox").json()["received"][0]
    assert received["attachment_available"] is True
    chart = received["snapshot"]
    assert chart["chart"] == "headcount_by_college"
    assert chart["at"] == term and chart["focus_series"] == college
    assert len(chart["series"]) > 1
    # The same figures the series route serves the reader, nothing more.
    served = registrar.get("/data/series?chart=headcount_by_college").json()
    assert chart["series"] == served["series"]
    assert "S-" not in repr(chart)

    sent_events = _events(app, "inbox.sent")
    assert sent_events[-1]["payload"]["source_kind"] == "chart"
    assert sent_events[-1]["payload"]["source_ref"] == ref


def test_a_role_that_cannot_read_the_chart_is_refused_and_not_offered(
    app: FastAPI,
) -> None:
    president = _login(app, "executive")
    registrar = _login(app, "registrar")
    finance = _login(app, "studentaccounts")
    aid = _login(app, "aid")
    # Pell status is for the finance roles only; the registrar has no
    # finances dashboard at all.
    for ref in ("chart=retention_by_pell", "chart=headcount&pell=pell"):
        refused = _send(president, _id(registrar), ref)
        assert refused.status_code == 403, ref
    refusals = _events(app, "data.refused")
    assert any("recipient" in e["payload"]["reason"] for e in refusals)

    people = president.get(
        "/inbox/recipients",
        params={"kind": "chart", "ref": "chart=retention_by_pell"},
    ).json()
    roles = {p["role"] for p in people}
    assert "registrar" not in roles and "staff" not in roles
    assert {"studentaccounts", "aid"} <= roles and "finance" not in roles
    assert _send(president, _id(finance), "chart=retention_by_pell").status_code == 201

    # A sender may only attach a chart their own role may read.
    assert _send(aid, _id(president), "chart=headcount").status_code == 403


def test_the_attachment_follows_the_readers_current_role(app: FastAPI) -> None:
    president = _login(app, "executive")
    staff = _login(app, "staff")
    assert _send(president, _id(staff), "chart=avg_gpa").status_code == 201
    assert staff.get("/inbox").json()["received"][0]["attachment_available"] is True

    store: CabinetStore = app.state.auth
    institution = store.ensure_bootstrap_institution()
    store.set_user_role(institution, _id(staff), "aid")
    # Same session, new role: the chart is no longer theirs to read.
    received = staff.get("/inbox").json()["received"][0]
    assert received["attachment_available"] is False
    assert received["snapshot"] is None


@pytest.mark.parametrize(
    "ref",
    [
        "chart=nope",
        "chart=headcount&college=NOPE",
        "chart=headcount&at=199910",
        "chart=headcount&series=nobody",
        "chart=headcount&college=x&compare=gender",
        "chart=headcount&bogus=1",
        "chart=headcount&chart=avg_gpa",
    ],
)
def test_a_reference_that_does_not_resolve_is_refused(app: FastAPI, ref: str) -> None:
    president = _login(app, "executive")
    staff = _login(app, "staff")
    assert _send(president, _id(staff), ref).status_code == 422, ref


def test_migration_11_keeps_every_inbox_row(tmp_path: Path) -> None:
    import sqlite3

    from cabinet.migrations import MIGRATIONS, migrate, recorded_versions

    conn = sqlite3.connect(tmp_path / "cabinet.db")
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY,"
            " name TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        for version, name, migration in MIGRATIONS[:10]:
            migration(conn)
            conn.execute(
                "INSERT INTO schema_migrations VALUES (?, ?, 'then')", (version, name)
            )
        institution = conn.execute(
            "INSERT INTO institutions (name, slug, created_at)"
            " VALUES ('U', 'u', 'then')"
        ).lastrowid
        conn.commit()
        for email in ("a@x.test", "b@x.test"):
            conn.execute(
                "INSERT INTO users (institution_id, email, password_hash, role,"
                " created_at) VALUES (?, ?, 'x', 'staff', 'then')",
                (institution, email),
            )
        conn.execute(
            "INSERT INTO inbox_messages (id, institution_id, sender_id,"
            " recipient_id, note, source_kind, source_ref, created_at, read_at)"
            " VALUES (7, ?, 1, 2, 'hello', 'finding', 'M5', 'then', 'later')",
            (institution,),
        )
        conn.commit()
        assert migrate(conn) == [11, 12]
        assert recorded_versions(conn)[-1] == 12
        row = conn.execute("SELECT * FROM inbox_messages").fetchone()
        assert (row["id"], row["note"], row["source_ref"], row["read_at"]) == (
            7,
            "hello",
            "M5",
            "later",
        )
        conn.execute(
            "INSERT INTO inbox_messages (institution_id, sender_id, recipient_id,"
            " note, source_kind, source_ref, created_at)"
            " VALUES (?, 1, 2, 'chart', 'chart', 'chart=headcount', 'now')",
            (institution,),
        )
        assert conn.execute("SELECT MAX(id) FROM inbox_messages").fetchone()[0] == 8
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO inbox_messages (institution_id, sender_id,"
                " recipient_id, note, source_kind, created_at)"
                " VALUES (?, 1, 2, 'x', 'bogus', 'now')",
                (institution,),
            )
    finally:
        conn.close()
