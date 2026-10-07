"""Department accounts, IT, the department overviews, the inbox, and the
demonstration personas (docs/ROLES.md).

Every new route is checked for an allowed and a refused role; the full
role-by-route matrix in test_security.py covers the rest of the table.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cabinet.api import create_app
from cabinet.departments import overview
from cabinet.store import CabinetStore
from cabinet.users import DEMO_PERSONAS
from cabinet.users import main as users_main
from conftest import make_authenticated_client

GENERATE = Path(__file__).resolve().parents[2] / "data" / "school" / "generate.py"


@pytest.fixture(scope="module")
def school_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("school-roles") / "school.db"
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


@pytest.fixture
def app() -> FastAPI:
    return create_app()


def _login(app: FastAPI, role: str, email: str | None = None) -> TestClient:
    return make_authenticated_client(app, role=role, email=email)


def _id(client: TestClient) -> int:
    return int(client.get("/auth/me").json()["user"]["id"])


def _events(app: FastAPI, event_type: str) -> list[dict[str, Any]]:
    store: CabinetStore = app.state.auth
    institution = store.ensure_bootstrap_institution()
    return store.audit_events(institution, event_type)


# --- IT ----------------------------------------------------------------------


def test_it_sees_accounts_sessions_connections_and_audit_but_no_students(
    app: FastAPI,
) -> None:
    it = _login(app, "it")
    for path in ("/admin/users", "/admin/sessions", "/admin/connections", "/events"):
        assert it.get(path).status_code == 200, path
    for path in (
        "/findings",
        "/briefing",
        "/decisions",
        "/questions",
        "/staff-actions",
        "/aid-queue",
        "/explore/catalog",
        "/departments/overview",
        "/students/search",
        "/admin/datasets",
        "/admin/offices",
    ):
        assert it.get(path).status_code == 403, path
    assert (
        it.post("/explore", json={"question": "Average GPA by major?"}).status_code
        == 403
    )
    assert it.post("/ask", json={"question": "anything"}).status_code == 403


def test_it_manages_department_accounts_only(app: FastAPI) -> None:
    it = _login(app, "it")
    admin = _login(app, "admin", "root@test.example")
    created = it.post(
        "/admin/users", json={"email": "bursar@test.example", "role": "finance"}
    )
    assert created.status_code == 201
    staff_id = created.json()["id"]
    assert it.post(f"/admin/users/{staff_id}/disable").status_code == 200
    assert it.post(f"/admin/users/{staff_id}/enable").status_code == 200
    assert (
        it.patch(f"/admin/users/{staff_id}", json={"role": "registrar"}).status_code
        == 200
    )
    # Never an admin, executive or IT account, and never promote to one.
    for role in ("admin", "executive", "it"):
        refused = it.post(
            "/admin/users", json={"email": f"x-{role}@test.example", "role": role}
        )
        assert refused.status_code == 403, role
    assert (
        it.patch(f"/admin/users/{staff_id}", json={"role": "admin"}).status_code == 403
    )
    admin_id = _id(admin)
    assert it.post(f"/admin/users/{admin_id}/disable").status_code == 403
    assert (
        it.patch(f"/admin/users/{admin_id}", json={"role": "staff"}).status_code == 403
    )
    refusals = _events(app, "data.refused")
    assert any("IT manages department" in e["payload"]["reason"] for e in refusals)


def test_sessions_lists_counts_never_ids(app: FastAPI) -> None:
    it = _login(app, "it")
    _login(app, "finance")
    rows = it.get("/admin/sessions").json()
    finance = next(row for row in rows if row["role"] == "finance")
    assert finance["live_sessions"] == 1 and finance["last_seen"]
    assert set(finance) == {
        "id",
        "email",
        "role",
        "disabled",
        "live_sessions",
        "last_seen",
    }
    assert _login(app, "executive").get("/admin/sessions").status_code == 200
    assert _login(app, "staff").get("/admin/sessions").status_code == 403
    assert (
        _login(app, "finance", "f2@test.example").get("/admin/sessions").status_code
        == 403
    )


# --- department accounts -------------------------------------------------------


@pytest.mark.parametrize("role", ["finance", "registrar", "studentlife"])
def test_department_account_reads_aggregates_and_its_own_overview(
    app: FastAPI, role: str
) -> None:
    client = _login(app, role)
    findings = client.get("/findings").json()
    assert findings["M5"]["rows_withheld"] is True
    assert client.get("/students/search", params={"q": "a"}).status_code == 403
    assert client.get("/events").status_code == 403
    assert client.get("/aid-queue").status_code == 403
    assert client.get("/admin/users").status_code == 403
    assert client.post("/ask", json={"question": "anything"}).status_code == 403
    assert client.get("/explore/catalog").status_code == 200
    own = client.get("/departments/overview")
    assert own.status_code == 200
    assert own.json()["department"] == role
    other = "registrar" if role != "registrar" else "finance"
    refused = client.get("/departments/overview", params={"department": other})
    assert refused.status_code == 403


def test_president_reads_every_overview_and_others_do_not(app: FastAPI) -> None:
    president = _login(app, "executive")
    for department in ("finance", "registrar", "studentlife"):
        body = president.get("/departments/overview", params={"department": department})
        assert body.status_code == 200
        assert len(body.json()["tiles"]) == 4
    unknown = president.get("/departments/overview", params={"department": "athletics"})
    assert unknown.status_code == 422
    for role in ("staff", "reviewer", "aid", "it"):
        assert _login(app, role).get("/departments/overview").status_code == 403, role


def test_overview_withholds_small_groups_and_carries_no_ids(school_db: Path) -> None:
    for department in ("finance", "registrar", "studentlife"):
        data = overview(department, school_db)
        text = repr(data)
        assert "S-" not in text and "student_id" not in text
    finance = overview("finance", school_db)
    offices = finance["tables"][0]["rows"]
    # The small school has offices with fewer than 10 students on hold.
    assert any(row["students"] == "Fewer than 10" for row in offices)


def test_overview_without_school_data_is_503(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CABINET_SCHOOL_DB", str(tmp_path / "absent.db"))
    response = _login(app, "finance").get("/departments/overview")
    assert response.status_code == 503
    assert response.json()["available"] is False


# --- inbox ---------------------------------------------------------------------


def test_president_alerts_finance_about_a_figure_end_to_end(app: FastAPI) -> None:
    president = _login(app, "executive")
    finance = _login(app, "finance")
    finance_id = _id(finance)
    recipients = president.get("/inbox/recipients").json()
    assert finance_id in [r["id"] for r in recipients]
    assert _id(president) not in [r["id"] for r in recipients]

    sent = president.post(
        "/inbox",
        json={
            "recipient_id": finance_id,
            "note": "Please look at the holds before Thursday's cabinet meeting.",
            "review_by": "2026-10-15",
            "source": {"kind": "finding", "ref": "M5"},
        },
    )
    assert sent.status_code == 201, sent.text
    message = sent.json()
    snapshot = message["snapshot"]
    assert set(snapshot) == {
        "id",
        "title",
        "display",
        "definition",
        "reason",
        "dataset",
    }
    assert "row_ids" not in repr(snapshot) and "hold_row_ids" not in repr(snapshot)

    inbox = finance.get("/inbox").json()
    assert inbox["unread"] == 1
    received = inbox["received"][0]
    assert received["note"].startswith("Please look")
    assert received["from"]["role"] == "executive"
    assert received["review_by"] == "2026-10-15"

    # Nobody but the recipient can mark it: a 404, not a 403.
    assert president.post(f"/inbox/{message['id']}/read").status_code == 404
    assert (
        _login(app, "staff").post(f"/inbox/{message['id']}/reviewed").status_code == 404
    )

    first = finance.post(f"/inbox/{message['id']}/read").json()
    assert first["changed"] is True
    assert finance.post(f"/inbox/{message['id']}/read").json()["changed"] is False
    reviewed = finance.post(f"/inbox/{message['id']}/reviewed").json()
    assert reviewed["changed"] is True and reviewed["message"]["reviewed_at"]
    assert finance.get("/inbox").json()["unread"] == 0

    status = president.get("/inbox").json()["sent"][0]
    assert status["read_at"] and status["reviewed_at"]

    sent_events = _events(app, "inbox.sent")
    assert len(sent_events) == 1
    payload = sent_events[0]["payload"]
    assert payload["source_kind"] == "finding" and payload["source_ref"] == "M5"
    assert "note" not in payload and "Thursday" not in repr(payload)
    assert len(_events(app, "inbox.read")) == 1
    assert len(_events(app, "inbox.reviewed")) == 1


def test_inbox_validation(app: FastAPI) -> None:
    president = _login(app, "executive")
    finance_id = _id(_login(app, "finance"))

    def send(**body: Any) -> int:
        return president.post(
            "/inbox", json={"recipient_id": finance_id, "note": "x", **body}
        ).status_code

    assert send(note="   ") == 422
    assert send(note="x" * 1001) == 422
    assert send(review_by="next week") == 422
    assert send(review_by="2026-02-30") == 422
    assert send(source={"kind": "finding", "ref": "M99"}) == 422
    assert send(source={"kind": "telepathy"}) == 422
    assert send(recipient_id=999_999) == 404
    assert send(recipient_id=_id(president)) == 422
    assert send(source={"kind": "note"}) == 201


def test_cross_institution_recipient_is_404(app: FastAPI) -> None:
    store: CabinetStore = app.state.auth
    other = store.create_institution("Two Rivers College", "two-rivers")
    outsider = store.create_user(
        "dean@two-rivers.example",
        "pw-not-a-secret-123",
        "finance",
        institution_id=other,
    )
    president = _login(app, "executive")
    assert outsider not in [r["id"] for r in president.get("/inbox/recipients").json()]
    response = president.post(
        "/inbox", json={"recipient_id": outsider, "note": "hello"}
    )
    assert response.status_code == 404


def test_attachments_follow_the_sender_role(app: FastAPI) -> None:
    it = _login(app, "it")
    finance = _login(app, "finance")
    president = _login(app, "executive")
    finance_id = _id(finance)
    # IT may send a plain note, never a figure or an Explore answer.
    assert (
        it.post(
            "/inbox", json={"recipient_id": finance_id, "note": "Password reset done."}
        ).status_code
        == 201
    )
    assert (
        it.post(
            "/inbox",
            json={
                "recipient_id": finance_id,
                "note": "x",
                "source": {"kind": "finding", "ref": "M1"},
            },
        ).status_code
        == 403
    )
    assert (
        it.post(
            "/inbox",
            json={
                "recipient_id": finance_id,
                "note": "x",
                "source": {"kind": "explore", "question": "GPA?"},
            },
        ).status_code
        == 403
    )
    # Finance may share its own overview figure, not the Registrar's.
    president_id = _id(president)
    own = finance.post(
        "/inbox",
        json={
            "recipient_id": president_id,
            "note": "Balances are up.",
            "source": {"kind": "overview", "ref": "finance:open_balance"},
        },
    )
    assert own.status_code == 201
    assert own.json()["snapshot"]["label"] == "Balance on open account holds"
    other = finance.post(
        "/inbox",
        json={
            "recipient_id": president_id,
            "note": "x",
            "source": {"kind": "overview", "ref": "registrar:enrolled"},
        },
    )
    assert other.status_code == 403


def test_explore_answer_travels_only_to_roles_allowed_to_read_it(app: FastAPI) -> None:
    president = _login(app, "executive")
    finance_id = _id(_login(app, "finance"))
    admin_id = _id(_login(app, "admin", "root@test.example"))
    source = {
        "kind": "explore",
        "question": "Which instructor taught MATH 2210 to student S-12345?",
        "answer": ["Dr. Example (fictional) taught it most often."],
    }
    to_finance = president.post(
        "/inbox",
        json={"recipient_id": finance_id, "note": "See this", "source": source},
    ).json()["snapshot"]
    assert to_finance["answer"] == [] and to_finance["answer_withheld"] is True
    assert "S-12345" not in to_finance["question"]
    to_admin = president.post(
        "/inbox", json={"recipient_id": admin_id, "note": "See this", "source": source}
    ).json()["snapshot"]
    assert to_admin["answer"] == source["answer"]
    too_long = {**source, "answer": ["a"] * 7}
    assert (
        president.post(
            "/inbox", json={"recipient_id": admin_id, "note": "x", "source": too_long}
        ).status_code
        == 422
    )


def test_inbox_send_shares_the_consequential_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CABINET_RATE_ASK_PER_MIN", "2")
    app = create_app()
    president = _login(app, "executive")
    finance_id = _id(_login(app, "finance"))
    codes = [
        president.post(
            "/inbox", json={"recipient_id": finance_id, "note": "n"}
        ).status_code
        for _ in range(3)
    ]
    assert codes == [201, 201, 429]


def test_inbox_needs_csrf(app: FastAPI) -> None:
    president = _login(app, "executive")
    finance_id = _id(_login(app, "finance"))
    del president.headers["X-CSRF-Token"]
    assert (
        president.post(
            "/inbox", json={"recipient_id": finance_id, "note": "n"}
        ).status_code
        == 403
    )


# --- demonstration personas ----------------------------------------------------


def test_demo_accounts_seed_once_and_write_a_private_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "accounts.txt"
    assert users_main(["demo-accounts", "--out", str(out)]) == 0
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
    lines = out.read_text().splitlines()
    assert [line.split("\t")[0] for line in lines] == [p[0] for p in DEMO_PERSONAS]
    printed = capsys.readouterr().out
    for line in lines:
        assert line.split("\t")[2] not in printed  # passwords never printed with --out
    # A second run changes nothing and keeps the file as it was.
    assert users_main(["demo-accounts", "--out", str(out)]) == 0
    assert out.read_text().splitlines() == lines
    # Every persona can sign in with the written password and has its role.
    app = create_app()
    client = TestClient(app)
    roles = {email: role for email, role, _ in DEMO_PERSONAS}
    for line in lines:
        email, _, password = line.split("\t")
        response = client.post(
            "/auth/login", json={"email": email, "password": password}
        )
        assert response.status_code == 200, email
        assert response.json()["user"]["role"] == roles[email]
