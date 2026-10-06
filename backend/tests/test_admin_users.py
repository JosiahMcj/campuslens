"""User management for institution admins.

Covers the /admin/users routes end to end: per-role access (admin 200/201,
executive/staff/reviewer 403, anonymous 401), CSRF, institution scoping
(cross-tenant ids are 404, never 403), the self-disable and last-admin
protections, idempotent disable/enable and role changes, the one-time
password appearing only in the creation response (never in the audit log,
never in the store, never in the user list), and a disabled user being
unable to log in or keep using an existing session.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cabinet.api import create_app
from cabinet.audit import verify_db
from cabinet.auth import AuthStore
from conftest import TEST_PASSWORD, make_authenticated_client


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture
def admin(app: FastAPI) -> TestClient:
    return make_authenticated_client(app, role="admin")


def _login(app: FastAPI, email: str, password: str = TEST_PASSWORD) -> TestClient:
    client = TestClient(app)
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client


def _add_user(
    client: TestClient, email: str, role: str = "staff"
) -> dict[str, Any]:
    response = client.post("/admin/users", json={"email": email, "role": role})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


# -- access control ----------------------------------------------------------


def test_list_users_admin_sees_their_institutions_users(admin: TestClient) -> None:
    response = admin.get("/admin/users")
    assert response.status_code == 200
    users = response.json()
    assert [u["email"] for u in users] == ["admin@test.example"]
    assert users[0]["role"] == "admin"
    assert users[0]["disabled"] is False
    assert "created_at" in users[0]
    # The list never carries a hash or a password.
    assert "password_hash" not in users[0]
    assert "one_time_password" not in users[0]


@pytest.mark.parametrize("role", ["executive", "staff", "reviewer", "aid"])
def test_list_users_other_roles_are_403(app: FastAPI, role: str) -> None:
    client = make_authenticated_client(app, role=role)
    assert client.get("/admin/users").status_code == 403


@pytest.mark.parametrize("role", ["executive", "staff", "reviewer", "aid"])
def test_create_user_other_roles_are_403(app: FastAPI, role: str) -> None:
    client = make_authenticated_client(app, role=role)
    response = client.post(
        "/admin/users", json={"email": "new@test.example", "role": "staff"}
    )
    assert response.status_code == 403


def test_user_routes_anonymous_are_401(app: FastAPI) -> None:
    anonymous = TestClient(app)
    assert anonymous.get("/admin/users").status_code == 401
    assert (
        anonymous.post(
            "/admin/users", json={"email": "a@b.example", "role": "staff"}
        ).status_code
        == 401
    )
    assert anonymous.post("/admin/users/1/disable").status_code == 401
    assert anonymous.post("/admin/users/1/enable").status_code == 401
    assert (
        anonymous.patch("/admin/users/1", json={"role": "staff"}).status_code == 401
    )


@pytest.mark.parametrize("role", ["executive", "staff", "reviewer", "aid"])
def test_disable_enable_patch_other_roles_are_403(app: FastAPI, role: str) -> None:
    make_authenticated_client(app, role="admin")
    actor = make_authenticated_client(app, role=role)
    assert actor.post("/admin/users/1/disable").status_code == 403
    assert actor.post("/admin/users/1/enable").status_code == 403
    assert actor.patch("/admin/users/1", json={"role": "staff"}).status_code == 403


def test_create_user_without_csrf_token_is_403(app: FastAPI) -> None:
    client = TestClient(app)
    login = client.post(
        "/auth/login", json={"email": "admin@test.example", "password": TEST_PASSWORD}
    )
    assert login.status_code == 401  # not created yet
    make_authenticated_client(app, role="admin")
    bare = TestClient(app)
    login = bare.post(
        "/auth/login", json={"email": "admin@test.example", "password": TEST_PASSWORD}
    )
    assert login.status_code == 200
    # No X-CSRF-Token header: the middleware refuses before the route runs.
    response = bare.post(
        "/admin/users", json={"email": "new@test.example", "role": "staff"}
    )
    assert response.status_code == 403
    assert "CSRF" in response.json()["detail"]


# -- creation and the one-time password --------------------------------------


def test_create_user_returns_the_password_exactly_once(admin: TestClient) -> None:
    body = _add_user(admin, "staff1@test.example", "staff")
    assert body["email"] == "staff1@test.example"
    assert body["role"] == "staff"
    assert isinstance(body["id"], int)
    password = body["one_time_password"]
    assert isinstance(password, str) and len(password) >= 12
    # The list does not repeat it.
    users = admin.get("/admin/users").json()
    assert "one_time_password" not in users[-1]
    assert password not in str(users)


def test_created_user_can_log_in_with_the_one_time_password(
    app: FastAPI, admin: TestClient
) -> None:
    body = _add_user(admin, "staff1@test.example", "staff")
    client = TestClient(app)
    login = client.post(
        "/auth/login",
        json={"email": "staff1@test.example", "password": body["one_time_password"]},
    )
    assert login.status_code == 200
    assert login.json()["user"]["role"] == "staff"


def test_password_is_not_stored_and_not_in_the_audit_log(
    app: FastAPI, admin: TestClient
) -> None:
    body = _add_user(admin, "staff1@test.example", "staff")
    password = str(body["one_time_password"])
    user_id = int(body["id"])

    store: AuthStore = app.state.auth
    row = store.user_by_id(user_id)
    assert row is not None
    assert password not in row["password_hash"]

    # The creation is recorded as one admin.changed event whose payload
    # names the action, the target, the role, and the actor — no password.
    events = admin.get("/events").json()["events"]
    created = [e for e in events if e["type"] == "admin.changed"]
    assert len(created) == 1
    assert created[0]["payload"] == {
        "action": "created",
        "target_user_id": user_id,
        "role": "staff",
        "by": "admin@test.example",
    }
    assert password not in str(events)
    # The hash chain still verifies with the new event type in it.
    breaks = verify_db(os.environ["CABINET_DB"])
    assert breaks == []


def test_events_endpoint_accepts_the_admin_changed_filter(admin: TestClient) -> None:
    _add_user(admin, "staff1@test.example", "staff")
    response = admin.get("/events", params={"type": "admin.changed"})
    assert response.status_code == 200
    assert len(response.json()["events"]) == 1


def test_create_user_validation(admin: TestClient) -> None:
    assert (
        admin.post("/admin/users", json={"email": "", "role": "staff"}).status_code
        == 422
    )
    assert (
        admin.post(
            "/admin/users", json={"email": "x@test.example", "role": "superuser"}
        ).status_code
        == 422
    )
    _add_user(admin, "dup@test.example", "staff")
    duplicate = admin.post(
        "/admin/users", json={"email": "dup@test.example", "role": "staff"}
    )
    assert duplicate.status_code == 409


# -- disable / enable ---------------------------------------------------------


def test_disable_and_enable_are_idempotent_and_audited(admin: TestClient) -> None:
    user_id = int(_add_user(admin, "staff1@test.example", "staff")["id"])

    first = admin.post(f"/admin/users/{user_id}/disable")
    assert first.status_code == 200
    assert first.json() == {
        "user": {
            "id": user_id,
            "email": "staff1@test.example",
            "role": "staff",
            "disabled": True,
            "created_at": first.json()["user"]["created_at"],
        },
        "changed": True,
    }
    again = admin.post(f"/admin/users/{user_id}/disable")
    assert again.status_code == 200
    assert again.json()["changed"] is False
    assert again.json()["user"]["disabled"] is True

    enable = admin.post(f"/admin/users/{user_id}/enable")
    assert enable.json()["changed"] is True
    assert enable.json()["user"]["disabled"] is False
    assert admin.post(f"/admin/users/{user_id}/enable").json()["changed"] is False

    events = admin.get("/events", params={"type": "admin.changed"}).json()["events"]
    actions = [e["payload"]["action"] for e in events]
    # One event per real change; the idempotent repeats wrote none.
    assert actions == ["created", "disabled", "enabled"]


def test_admin_cannot_disable_themselves(admin: TestClient, app: FastAPI) -> None:
    store: AuthStore = app.state.auth
    me = store.user_by_email("admin@test.example")
    assert me is not None
    response = admin.post(f"/admin/users/{me['id']}/disable")
    assert response.status_code == 409
    assert "own account" in response.json()["detail"]


def test_last_enabled_admin_cannot_be_disabled_or_demoted(
    app: FastAPI, admin: TestClient
) -> None:
    # A second admin can be disabled while two are enabled...
    second_id = int(_add_user(admin, "admin2@test.example", "admin")["id"])
    assert admin.post(f"/admin/users/{second_id}/disable").json()["changed"] is True
    # ...leaving the acting admin as the institution's last enabled one.
    # Disabling themselves is refused, and so is demoting themselves: the
    # institution can never be left without an enabled admin through the API.
    store: AuthStore = app.state.auth
    me = store.user_by_email("admin@test.example")
    assert me is not None
    assert store.enabled_admin_count(int(me["institution_id"])) == 1
    assert admin.post(f"/admin/users/{me['id']}/disable").status_code == 409
    demote = admin.patch(f"/admin/users/{me['id']}", json={"role": "staff"})
    assert demote.status_code == 409
    assert "last enabled administrator" in demote.json()["detail"]
    # Re-enabling the second admin lifts the protection again.
    assert admin.post(f"/admin/users/{second_id}/enable").json()["changed"] is True
    assert admin.patch(f"/admin/users/{second_id}", json={"role": "staff"}).json()[
        "changed"
    ] is True


# -- role changes -------------------------------------------------------------


def test_role_change_is_audited_and_idempotent(admin: TestClient) -> None:
    user_id = int(_add_user(admin, "staff1@test.example", "staff")["id"])
    changed = admin.patch(f"/admin/users/{user_id}", json={"role": "executive"})
    assert changed.status_code == 200
    assert changed.json()["changed"] is True
    assert changed.json()["user"]["role"] == "executive"
    same = admin.patch(f"/admin/users/{user_id}", json={"role": "executive"})
    assert same.json()["changed"] is False
    assert (
        admin.patch(f"/admin/users/{user_id}", json={"role": "boss"}).status_code
        == 422
    )
    events = admin.get("/events", params={"type": "admin.changed"}).json()["events"]
    assert [e["payload"]["action"] for e in events] == ["created", "role_changed"]
    assert events[-1]["payload"]["role"] == "executive"


# -- tenancy -------------------------------------------------------------------


def test_cross_tenant_user_ids_are_404(app: FastAPI) -> None:
    store: AuthStore = app.state.auth
    bootstrap = store.ensure_bootstrap_institution()
    second = store.create_institution("Two Rivers College", "two-rivers")
    store.create_user("admin-a@test.example", TEST_PASSWORD, "admin", bootstrap)
    store.create_user("admin-b@test.example", TEST_PASSWORD, "admin", second)
    admin_a = _login(app, "admin-a@test.example")
    admin_b = _login(app, "admin-b@test.example")

    b_user_id = int(_add_user(admin_b, "staff-b@test.example", "staff")["id"])

    # Admin A cannot see, disable, enable, or repoint institution B's user;
    # every one is a 404 so existence never leaks across tenants.
    assert admin_a.post(f"/admin/users/{b_user_id}/disable").status_code == 404
    assert admin_a.post(f"/admin/users/{b_user_id}/enable").status_code == 404
    assert (
        admin_a.patch(f"/admin/users/{b_user_id}", json={"role": "admin"}).status_code
        == 404
    )
    listed = admin_a.get("/admin/users").json()
    assert {u["email"] for u in listed} == {"admin-a@test.example"}
    # The unknown-id case inside the admin's own institution is also 404.
    assert admin_a.post("/admin/users/9999/disable").status_code == 404


def test_created_user_lands_in_the_callers_institution(
    app: FastAPI, admin: TestClient
) -> None:
    user_id = int(_add_user(admin, "staff1@test.example", "staff")["id"])
    store: AuthStore = app.state.auth
    me = store.user_by_email("admin@test.example")
    created = store.user_by_id(user_id)
    assert me is not None and created is not None
    assert created["institution_id"] == me["institution_id"]


# -- a disabled account is really off -----------------------------------------


def test_disabled_user_cannot_log_in(app: FastAPI, admin: TestClient) -> None:
    body = _add_user(admin, "staff1@test.example", "staff")
    password = str(body["one_time_password"])
    user_id = int(body["id"])
    admin.post(f"/admin/users/{user_id}/disable")
    login = TestClient(app).post(
        "/auth/login", json={"email": "staff1@test.example", "password": password}
    )
    assert login.status_code == 401
    assert login.json()["detail"] == "invalid email or password"


def test_disabled_users_existing_session_is_rejected_on_the_next_request(
    app: FastAPI, admin: TestClient
) -> None:
    body = _add_user(admin, "staff1@test.example", "staff")
    staff = _login(app, "staff1@test.example", str(body["one_time_password"]))
    assert staff.get("/findings").status_code == 200
    admin.post(f"/admin/users/{int(body['id'])}/disable")
    # The very next request on the still-valid cookie is a 401.
    assert staff.get("/findings").status_code == 401
    assert staff.get("/admin/users").status_code == 401
