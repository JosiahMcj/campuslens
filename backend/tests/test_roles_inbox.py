"""Department accounts, IT, the department overviews, the inbox, and the
demonstration personas (docs/ROLES.md).

Every new route is checked for an allowed and a refused role; the full
role-by-route matrix in test_security.py covers the rest of the table.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cabinet.api import create_app
from cabinet.departments import DEPARTMENTS, WITHHELD, overview, protect
from cabinet.store import CabinetStore
from cabinet.users import DEMO_PERSONAS, reset_password
from cabinet.users import main as users_main
from conftest import make_authenticated_client

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "data" / "fixture.json"
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
    # IT never sees the new account's password: an administrator issues it.
    assert created.json()["one_time_password"] is None
    assert created.json()["password_issued_by_admin"] is True
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


@pytest.mark.parametrize("role", [*DEPARTMENTS])
def test_department_account_reads_aggregates_and_its_own_overview(
    app: FastAPI, role: str
) -> None:
    client = _login(app, role)
    findings = client.get("/findings").json()
    assert findings["M5"]["rows_withheld"] is True
    assert client.get("/students/search", params={"q": "a"}).status_code == 403
    assert client.get("/events").status_code == 403
    assert client.get("/admin/users").status_code == 403
    assert client.post("/ask", json={"question": "anything"}).status_code == 403
    if role != "aid":
        assert client.get("/aid-queue").status_code == 403
        assert client.get("/explore/catalog").status_code == 200
    own = client.get("/departments/overview")
    assert own.status_code == 200
    assert own.json()["department"] == role
    other = "registrar" if role != "registrar" else "finance"
    if role == "aid":
        # Financial Aid has no Explore (its work is the queue) and no briefing
        # rows beyond the briefing figures; the department checks below hold.
        assert client.get("/explore/catalog").status_code == 403
    refused = client.get("/departments/overview", params={"department": other})
    assert refused.status_code == 403


def test_president_reads_every_overview_and_others_do_not(app: FastAPI) -> None:
    president = _login(app, "executive")
    for department in DEPARTMENTS:
        body = president.get("/departments/overview", params={"department": department})
        assert body.status_code == 200, department
        assert len(body.json()["tiles"]) == 4, department
    unknown = president.get("/departments/overview", params={"department": "football"})
    assert unknown.status_code == 422
    # No department reads another's overview (budget stays with Finance).
    for role in DEPARTMENTS:
        client = _login(app, role)
        for department in DEPARTMENTS:
            expected = 200 if department == role else 403
            got = client.get("/departments/overview", params={"department": department})
            assert got.status_code == expected, (role, department)
    for role in ("staff", "reviewer", "it"):
        assert _login(app, role).get("/departments/overview").status_code == 403, role


def test_overview_withholds_small_groups_and_carries_no_ids(school_db: Path) -> None:
    for department in DEPARTMENTS:
        data = overview(department, school_db)
        text = repr(data)
        assert "S-" not in text and "student_id" not in text, department
    accounts = overview("studentaccounts", school_db)
    offices = accounts["tables"][0]["rows"]
    # The small school has offices with fewer than 10 students on hold.
    assert any(row["students"] == "Fewer than 10" for row in offices)


def test_overview_without_school_data_is_503(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CABINET_SCHOOL_DB", str(tmp_path / "absent.db"))
    response = _login(app, "studentaccounts").get("/departments/overview")
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
    # Student Accounts may share its own overview figure, not the Registrar's
    # and not Finance's budget.
    finance = _login(app, "studentaccounts")
    president_id = _id(president)
    own = finance.post(
        "/inbox",
        json={
            "recipient_id": president_id,
            "note": "Balances are up.",
            "source": {"kind": "overview", "ref": "studentaccounts:open_balance"},
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
    budget = finance.post(
        "/inbox",
        json={
            "recipient_id": president_id,
            "note": "x",
            "source": {"kind": "overview", "ref": "finance:budget_spending"},
        },
    )
    assert budget.status_code == 403
    # Finance shares its budget figure, and may not share a student account one.
    cfo = _login(app, "finance", "cfo@test.example")
    assert (
        cfo.post(
            "/inbox",
            json={
                "recipient_id": president_id,
                "note": "Spending is on plan.",
                "source": {"kind": "overview", "ref": "finance:budget_spending"},
            },
        ).status_code
        == 201
    )
    assert (
        cfo.post(
            "/inbox",
            json={
                "recipient_id": president_id,
                "note": "x",
                "source": {"kind": "overview", "ref": "studentaccounts:open_balance"},
            },
        ).status_code
        == 403
    )


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
    # An answer that names no instructor travels to every role.
    plain = {
        "kind": "explore",
        "question": "Which offices hold the most active holds?",
        "answer": ["Student Accounts holds the most: 46 holds on 46 students."],
    }
    to_finance_plain = president.post(
        "/inbox", json={"recipient_id": finance_id, "note": "See this", "source": plain}
    ).json()["snapshot"]
    assert to_finance_plain["answer"] == plain["answer"]
    assert to_finance_plain["answer_withheld"] is False
    too_long = {**source, "answer": ["a"] * 7}
    assert (
        president.post(
            "/inbox", json={"recipient_id": admin_id, "note": "x", "source": too_long}
        ).status_code
        == 422
    )


def test_inbox_send_has_its_own_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Alerts have their own bucket: a spent question allowance does not stop
    an alert, and the alert limit still applies."""
    monkeypatch.setenv("CABINET_RATE_INBOX_PER_MIN", "2")
    monkeypatch.setenv("CABINET_RATE_ASK_PER_MIN", "1")
    app = create_app()
    president = _login(app, "executive")
    finance_id = _id(_login(app, "finance"))
    president.post("/explore", json={"question": "Average GPA by major?"})
    assert (
        president.post(
            "/explore", json={"question": "Average GPA by major?"}
        ).status_code
        == 429
    )
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


# --- review fixes (security review of roles-inbox) ------------------------------


def test_it_cannot_create_or_manage_aid_or_reviewer_accounts(app: FastAPI) -> None:
    it = _login(app, "it")
    for role in ("aid", "reviewer"):
        refused = it.post(
            "/admin/users", json={"email": f"x-{role}@test.example", "role": role}
        )
        assert refused.status_code == 403, role
    aid_id = _id(_login(app, "aid"))
    assert it.post(f"/admin/users/{aid_id}/disable").status_code == 403
    staff = it.post("/admin/users", json={"email": "s@test.example", "role": "staff"})
    assert staff.status_code == 201
    assert (
        it.patch(
            f"/admin/users/{staff.json()['id']}", json={"role": "reviewer"}
        ).status_code
        == 403
    )
    # The admin still gets the one-time password, as before.
    admin = _login(app, "admin", "root@test.example")
    made = admin.post("/admin/users", json={"email": "r@test.example", "role": "aid"})
    assert made.status_code == 201 and made.json()["one_time_password"]


def test_reset_password_issues_a_new_password_and_ends_sessions(app: FastAPI) -> None:
    it = _login(app, "it")
    created = it.post(
        "/admin/users", json={"email": "bursar@test.example", "role": "finance"}
    ).json()
    store: CabinetStore = app.state.auth
    password = reset_password(store, "bursar@test.example")
    assert password is not None
    login = TestClient(app).post(
        "/auth/login", json={"email": "bursar@test.example", "password": password}
    )
    assert login.status_code == 200 and login.json()["user"]["id"] == created["id"]
    assert reset_password(store, "nobody@test.example") is None


ACCOUNTS_FIGURE = {"kind": "overview", "ref": "studentaccounts:open_balance"}


@pytest.mark.parametrize(
    ("sender_role", "recipient_role", "source"),
    [
        ("staff", "it", {"kind": "finding", "ref": "M1"}),
        ("executive", "it", {"kind": "finding", "ref": "M5"}),
        ("studentaccounts", "it", ACCOUNTS_FIGURE),
        ("executive", "registrar", ACCOUNTS_FIGURE),
        ("executive", "finance", ACCOUNTS_FIGURE),
        ("executive", "aid", {"kind": "explore", "question": "GPA by major?"}),
        ("executive", "it", {"kind": "explore", "question": "GPA by major?"}),
    ],
)
def test_recipient_must_be_allowed_to_read_the_attachment(
    app: FastAPI, sender_role: str, recipient_role: str, source: dict[str, str]
) -> None:
    sender = _login(app, sender_role, f"sender-{sender_role}@test.example")
    recipient_id = _id(_login(app, recipient_role, f"to-{recipient_role}@test.example"))
    response = sender.post(
        "/inbox", json={"recipient_id": recipient_id, "note": "look", "source": source}
    )
    assert response.status_code == 403
    assert "recipient" in response.json()["detail"]
    # The picker never offers that person for that attachment.
    offered = sender.get(
        "/inbox/recipients",
        params={"kind": source["kind"], "ref": source.get("ref", "")},
    ).json()
    assert recipient_id not in [person["id"] for person in offered]
    # A plain note still reaches them.
    assert (
        sender.post(
            "/inbox", json={"recipient_id": recipient_id, "note": "hello"}
        ).status_code
        == 201
    )


def test_counseling_figure_is_never_sent(app: FastAPI) -> None:
    president = _login(app, "executive")
    finance_id = _id(_login(app, "finance"))
    response = president.post(
        "/inbox",
        json={
            "recipient_id": finance_id,
            "note": "x",
            "source": {"kind": "finding", "ref": "M9"},
        },
    )
    assert response.status_code == 422
    assert "counseling" in response.json()["detail"]


def test_figures_are_re_read_and_disappear_when_withdrawn(app: FastAPI) -> None:
    president = _login(app, "executive")
    finance = _login(app, "finance")
    finance_id = _id(finance)
    sent = president.post(
        "/inbox",
        json={
            "recipient_id": finance_id,
            "note": "x",
            "source": {"kind": "finding", "ref": "M5"},
        },
    ).json()
    # Nothing about the figure is frozen in the database: only its ref.
    conn = sqlite3.connect(os.environ["CABINET_DB"])
    try:
        (stored,) = conn.execute(
            "SELECT snapshot FROM inbox_messages WHERE id = ?", (sent["id"],)
        ).fetchone()
    finally:
        conn.close()
    assert stored is None
    shown = finance.get("/inbox").json()["received"][0]
    assert shown["attachment_available"] is True and shown["snapshot"]["id"] == "M5"
    # The figure goes away (a new dataset, a purge, a revoked authorization):
    # the alert keeps its note and says the attachment is no longer there.
    store: CabinetStore = app.state.auth
    app.state.runtime_for(store.ensure_bootstrap_institution()).findings.pop("M5")
    gone = finance.get("/inbox").json()["received"][0]
    assert gone["attachment_available"] is False and gone["snapshot"] is None
    assert gone["note"] == "x"


def test_attachment_is_hidden_from_a_reader_who_lost_the_right(app: FastAPI) -> None:
    president = _login(app, "executive")
    finance = _login(app, "studentaccounts")
    finance_id = _id(finance)
    president.post(
        "/inbox",
        json={
            "recipient_id": finance_id,
            "note": "x",
            "source": {"kind": "overview", "ref": "studentaccounts:open_balance"},
        },
    )
    store: CabinetStore = app.state.auth
    store.set_user_role(store.ensure_bootstrap_institution(), finance_id, "staff")
    shown = finance.get("/inbox").json()["received"][0]
    assert shown["snapshot"] is None and shown["attachment_available"] is False


def test_explore_quotes_are_redacted_labelled_and_instructor_checked(
    app: FastAPI, school_db: Path
) -> None:
    conn = sqlite3.connect(school_db)
    try:
        first, last = conn.execute(
            "SELECT first_name, last_name FROM instructors LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    president = _login(app, "executive")
    finance_id = _id(_login(app, "finance"))

    def quote(*sentences: str) -> dict[str, Any]:
        response = president.post(
            "/inbox",
            json={
                "recipient_id": finance_id,
                "note": "x",
                "source": {
                    "kind": "explore",
                    "question": "Who taught it?",
                    "answer": list(sentences),
                },
            },
        )
        assert response.status_code == 201, response.text
        snapshot: dict[str, Any] = response.json()["snapshot"]
        return snapshot

    plain = quote("Student S-12345 owes the most; 46 holds in all.")
    assert plain["quoted_by_sender"] is True
    assert "S-12345" not in plain["answer"][0]
    for naming in (
        f"Dr. {last} taught it most often.",
        f"{first} {last} taught it.",
        "Instructor I-0001 taught it.",
        "Someone (fictional) taught it.",
    ):
        withheld = quote(naming)
        assert withheld["answer"] == [] and withheld["answer_withheld"] is True, naming


def test_activating_real_data_disables_the_demo_accounts(app: FastAPI) -> None:
    store: CabinetStore = app.state.auth
    institution_id = store.ensure_bootstrap_institution()
    admin = _login(app, "admin", "admin@demo.test")
    _login(app, "finance", "finance@demo.test")
    _login(app, "staff", "colleague@test.example")
    document = json.loads(FIXTURE_PATH.read_text())
    document["meta"]["fictional"] = False
    dataset_id = store.add_dataset(
        institution_id,
        name="Spring term (real)",
        raw=json.dumps(document).encode(),
        uploaded_by="admin@demo.test",
        activate=False,
    )["id"]
    assert admin.post(f"/admin/datasets/{dataset_id}/activate").status_code == 200
    users = {row["email"]: row for row in store.users_for(institution_id)}
    assert users["finance@demo.test"]["disabled"]
    assert not users["admin@demo.test"]["disabled"]  # never lock the admin out
    assert not users["colleague@test.example"]["disabled"]


def test_demo_accounts_need_out_and_refuse_a_symlink(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit):
        users_main(["demo-accounts"])
    target = tmp_path / "elsewhere.txt"
    target.write_text("")
    link = tmp_path / "accounts.txt"
    link.symlink_to(target)
    assert users_main(["demo-accounts", "--out", str(link)]) == 1
    assert target.read_text() == ""
    # Nothing was created, so no password was generated with nowhere to go.
    assert users_main(["demo-accounts", "--out", str(tmp_path / "ok.txt")]) == 0
    assert len((tmp_path / "ok.txt").read_text().splitlines()) == len(DEMO_PERSONAS)


# --- complementary suppression in the overviews ----------------------------------


def test_protect_withholds_a_second_cell_beside_a_lone_withheld_one() -> None:
    # Bands under a shown total: 164 - (60 + 60 + 39) would give the lone 5.
    assert protect([[60], [60], [39], [5]]) == [[False], [False], [True], [True]]
    # Two withheld already: nothing more to hide.
    assert protect([[60], [3], [4]]) == [[False], [True], [True]]
    # A 2 x 2 table (housing): one small cell takes its row and its column,
    # and then the rest, since each line would otherwise give one back.
    assert protect([[91, 4], [70, 26]]) == [[True, True], [True, True]]
    # Nothing small: nothing withheld.
    assert protect([[40, 50], [60, 70]]) == [[False, False], [False, False]]


def _numeric_cells(table: dict[str, Any]) -> list[list[str]]:
    keys = [column["key"] for column in table["columns"]][1:]
    return [[row[key] for key in keys] for row in table["rows"]]


@pytest.mark.parametrize("department", [*DEPARTMENTS])
def test_no_overview_table_leaves_a_lone_withheld_cell(
    department: str, school_db: Path
) -> None:
    """No row or column of any overview table has exactly one withheld cell
    beside shown ones, so no cell can be had by subtraction from a total."""
    data = overview(department, school_db)
    saw_withheld = False
    for table in data["tables"]:
        cells = [
            [value for value in row if value != "—"] for row in _numeric_cells(table)
        ]
        columns = [list(column) for column in zip(*cells, strict=False)]
        for line in cells + columns:
            hidden = sum(value == WITHHELD for value in line)
            saw_withheld = saw_withheld or hidden > 0
            if len(line) > 1:
                assert hidden != 1, (department, table["key"], line)
    # The small school has small groups in every department with student
    # counts (Finance shows only the university's own budget, in dollars).
    # Institutional Research's trends are large even here.
    assert saw_withheld or department in ("finance", "ir")


# --- the demonstration accounts and the department directory -------------------


def test_demo_personas_cover_every_department_once() -> None:
    roles = [role for _, role, _ in DEMO_PERSONAS]
    assert len(roles) == len(set(roles))
    for department in DEPARTMENTS:
        assert department in roles, department
    names = {email: name for email, _, name in DEMO_PERSONAS}
    assert names["finance@demo.test"] == "Finance"
    assert names["studentaccounts@demo.test"] == "Student Accounts"
    assert "bursar@demo.test" not in names


def test_it_manages_every_department_account_but_not_admin(app: FastAPI) -> None:
    it = _login(app, "it")
    for role in DEPARTMENTS:
        if role == "aid":
            continue  # the aid office stays with an administrator
        created = it.post(
            "/admin/users", json={"email": f"{role}@test.example", "role": role}
        )
        assert created.status_code == 201, role
    assert (
        it.post(
            "/admin/users", json={"email": "aid-x@test.example", "role": "aid"}
        ).status_code
        == 403
    )


def test_decisions_route_to_the_right_department() -> None:
    from cabinet.inbox import department_roles

    assert department_roles("Bursar") == ("studentaccounts",)
    assert department_roles("Student Accounts") == ("studentaccounts",)
    assert department_roles("Finance") == ("finance",)
    assert department_roles("Admissions") == ("admissions",)
    assert department_roles("Financial Aid") == ("aid",)


# --- overview privacy review fixes ---------------------------------------------


def test_rate_table_withholds_a_small_remainder() -> None:
    from cabinet.departments_more import _rates

    table = _rates(
        "k", "t", "g", "n", "r", [("Good standing", 12, 11), ("Big", 100, 60)]
    )
    small, big = table["rows"]
    # 12 students at 11 in good standing would reveal 1 student outside it.
    assert small["rate"] == WITHHELD
    assert "%" in big["rate"] or big["rate"] == WITHHELD
    for row in table["rows"]:
        assert "92%" not in row["rate"]


def test_athletics_tiles_match_their_tables(school_db: Path) -> None:
    data = overview("athletics", school_db)
    tiles = {t["key"]: t for t in data["tiles"]}
    tables = {t["key"]: t for t in data["tables"]}
    gpa_other = tables["gpa_comparison"]["rows"][1]["value"]
    assert tiles["athlete_gpa"]["note"].endswith(f"Other students: {gpa_other}.")
    ret_other = tables["retention_comparison"]["rows"][1]["rate"]
    assert tiles["athlete_retention"]["note"].endswith(f"Other students: {ret_other}.")


def test_international_holds_exclude_financial_and_aid_has_no_campus_total(
    school_db: Path,
) -> None:
    import sqlite3

    con = sqlite3.connect(school_db)
    (expected,) = con.execute(
        "SELECT COUNT(DISTINCT h.student_id) FROM person_holds h JOIN students s"
        " ON s.student_id = h.student_id WHERE s.residency = 'international'"
        " AND h.end_date IS NULL AND h.category != 'financial'"
    ).fetchone()
    con.close()
    tiles = {t["key"]: t for t in overview("international", school_db)["tiles"]}
    shown = tiles["international_holds"]["display"]
    assert shown == WITHHELD or shown.replace(",", "") == str(expected)
    aid = {t["key"]: t for t in overview("aid", school_db)["tiles"]}
    assert "in all" not in aid["pell_with_hold"]["note"]
