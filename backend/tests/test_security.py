"""Tests for the security controls.

One test per control: login and its cookie, wrong-password and account-
enumeration resistance, the 5-per-15-min lockout (per email and per IP),
session expiry, forged cookies (a leaked DB alone cannot mint sessions),
CSRF on every state-changing method, the per-route role table, security
headers, the body
cap and content-type checks, the rate limits, the audit hash chain
(including tamper detection and the torn-tail repair), and production
fail-closed startup.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
from collections.abc import Iterator, MutableMapping
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cabinet.api import DEFAULT_FIXTURE_PATH, DEMO_DECISION_ID, create_app
from cabinet.audit import GENESIS_HASH, AuditLog, event_hash, verify_chain
from cabinet.audit import main as audit_main
from cabinet.auth import (
    COOKIE_NAME,
    USER_ROLES,
    AuthStore,
    check_password,
    hash_password,
    sign_session_id,
)
from cabinet.security import (
    DEFAULT_RATE_ASK_PER_MIN,
    DEFAULT_RATE_GENERAL_PER_MIN,
    DEFAULT_RATE_SESSION_PER_MIN,
    GENERIC_LOGIN_ERROR,
    MAX_BODY_BYTES,
    RATE_LIMIT_MESSAGE,
    ROUTE_ROLE_PREFIXES,
    ROUTE_ROLES,
)
from conftest import TEST_PASSWORD, TEST_SECRET_KEY, make_authenticated_client


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return make_authenticated_client(app)


@pytest.fixture
def prod_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("CABINET_ENV", "production")
    monkeypatch.setenv("CABINET_BIND", "127.0.0.1")
    return create_app()


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(os.environ["CABINET_DB"])
    conn.row_factory = sqlite3.Row
    return conn


def _login_production(
    app: FastAPI, *, role: str = "admin", email: str | None = None
) -> TestClient:
    """Like make_authenticated_client, but for a production-mode app: the
    Secure cookie is moved into the jar by hand because httpx will not send
    a Secure cookie over the TestClient's plain-http origin."""
    email = email or f"{role}@test.example"
    store = app.state.auth
    with contextlib.suppress(ValueError):
        store.create_user(email, TEST_PASSWORD, role)
    client = TestClient(app)
    response = client.post(
        "/auth/login", json={"email": email, "password": TEST_PASSWORD}
    )
    assert response.status_code == 200, response.text
    cookie = response.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]
    client.cookies.set(COOKIE_NAME, cookie)
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client


# --- passwords ---------------------------------------------------------------


def test_password_is_hashed_with_scrypt_and_per_user_salt() -> None:
    first = hash_password("hunter2")
    second = hash_password("hunter2")
    assert first.startswith("scrypt$16384$8$1$")
    assert first != second  # per-user salt: same password, different hashes
    assert check_password("hunter2", first)
    assert not check_password("hunter3", first)
    assert not check_password("hunter2", "not-a-hash")


# --- login -------------------------------------------------------------------


def test_login_sets_signed_httponly_cookie_and_returns_csrf(
    app: FastAPI,
) -> None:
    app.state.auth.create_user("login@test.example", TEST_PASSWORD, "admin")
    raw = TestClient(app)
    response = raw.post(
        "/auth/login",
        json={"email": "login@test.example", "password": TEST_PASSWORD},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["role"] == "admin"
    assert body["csrf_token"]
    set_cookie = response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "SameSite=strict" in set_cookie
    assert "Path=/" in set_cookie
    assert "Secure" not in set_cookie  # only in production
    cookie_value = set_cookie.split(";", 1)[0].split("=", 1)[1]
    assert "." in cookie_value  # <session id>.<HMAC signature>


def test_wrong_password_and_unknown_email_get_the_same_generic_401(
    app: FastAPI,
) -> None:
    app.state.auth.create_user("real@test.example", TEST_PASSWORD, "admin")
    raw = TestClient(app)
    wrong = raw.post(
        "/auth/login", json={"email": "real@test.example", "password": "nope"}
    )
    unknown = raw.post(
        "/auth/login", json={"email": "ghost@test.example", "password": "nope"}
    )
    assert wrong.status_code == 401
    assert unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": GENERIC_LOGIN_ERROR}


def test_login_lockout_per_email_after_5_attempts(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_sleep(seconds: float) -> None:
        pass

    monkeypatch.setattr("cabinet.security._sleep", no_sleep)
    app.state.auth.create_user("locked@test.example", TEST_PASSWORD, "admin")
    raw = TestClient(app)
    for _ in range(5):
        response = raw.post(
            "/auth/login", json={"email": "locked@test.example", "password": "bad"}
        )
        assert response.status_code == 401
    blocked = raw.post(
        "/auth/login",
        json={"email": "locked@test.example", "password": TEST_PASSWORD},
    )
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0


def test_login_lockout_per_ip_after_5_attempts(app: FastAPI) -> None:
    raw = TestClient(app)
    for index in range(5):
        response = raw.post(
            "/auth/login",
            json={"email": f"ghost{index}@test.example", "password": "bad"},
        )
        assert response.status_code == 401
    # A never-tried email from the same IP is blocked too.
    blocked = raw.post(
        "/auth/login", json={"email": "fresh@test.example", "password": "bad"}
    )
    assert blocked.status_code == 429


# --- sessions ------------------------------------------------------------------


def test_protected_routes_require_a_session(app: FastAPI) -> None:
    raw = TestClient(app)
    for method, path in ROUTE_ROLES:
        response = raw.request(method, path)
        assert response.status_code == 401, f"{method} {path}"


def test_public_routes_need_no_session(app: FastAPI) -> None:
    raw = TestClient(app)
    assert raw.get("/health").status_code == 200
    assert raw.get("/ready").status_code == 200
    assert raw.get("/ready").json() == {"ready": True}


def test_expired_session_is_401(app: FastAPI) -> None:
    store = app.state.auth
    user_id = store.create_user("old@test.example", TEST_PASSWORD, "admin")
    session = store.create_session(user_id)
    with _db() as conn:
        conn.execute(
            "UPDATE sessions SET expires_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", session["id"]),
        )
    raw = TestClient(app)
    raw.cookies.set(COOKIE_NAME, sign_session_id(TEST_SECRET_KEY, session["id"]))
    assert raw.get("/findings").status_code == 401


def test_session_lifetime_comes_from_cabinet_session_ttl_hours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CABINET_SESSION_TTL_HOURS bounds the session for real: the login
    cookie's max-age and the session row's expiry both follow it, and a
    session aged past the configured TTL — by moving the store's clock, the
    stored expires_at untouched — is 401, while the same aging under the
    12 h default leaves the session live."""
    from datetime import datetime, timedelta

    monkeypatch.setenv("CABINET_SESSION_TTL_HOURS", "1")
    ttl_app = create_app()
    store: AuthStore = ttl_app.state.auth
    store.create_user("ttl@test.example", TEST_PASSWORD, "admin")
    client = TestClient(ttl_app)
    login = client.post(
        "/auth/login",
        json={"email": "ttl@test.example", "password": TEST_PASSWORD},
    )
    assert login.status_code == 200
    # The cookie's max-age follows the configured TTL.
    assert "Max-Age=3600" in login.headers["set-cookie"]
    # So does the session row: one hour, not twelve.
    row = store._conn.execute(
        "SELECT created_at, expires_at FROM sessions"
        " ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    delta = datetime.fromisoformat(row["expires_at"]) - datetime.fromisoformat(
        row["created_at"]
    )
    assert delta.total_seconds() == 3600

    # 61 minutes pass — the clock moves, the stored expires_at does not.
    real_now = AuthStore._now
    monkeypatch.setattr(
        AuthStore,
        "_now",
        staticmethod(lambda: real_now() + timedelta(minutes=61)),
    )
    assert client.get("/findings").status_code == 401

    # Under the default TTL the same 61 minutes of aging is nothing.
    monkeypatch.delenv("CABINET_SESSION_TTL_HOURS")
    default_app = create_app()
    default_store: AuthStore = default_app.state.auth
    default_store.create_user("ttl-default@test.example", TEST_PASSWORD, "admin")
    default_client = TestClient(default_app)
    default_login = default_client.post(
        "/auth/login",
        json={"email": "ttl-default@test.example", "password": TEST_PASSWORD},
    )
    assert default_login.status_code == 200
    assert "Max-Age=43200" in default_login.headers["set-cookie"]
    assert default_client.get("/findings").status_code == 200


def test_empty_cabinet_db_env_is_a_startup_refusal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CABINET_DB set but empty is a configuration error: refuse with one
    line, never silently fall back to var/cabinet.db."""
    monkeypatch.setenv("CABINET_DB", "")
    with pytest.raises(SystemExit) as excinfo:
        create_app()
    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "CABINET_DB" in err
    assert "empty" in err
    assert "Traceback" not in err


def test_disabled_user_is_401(client: TestClient) -> None:
    assert client.get("/findings").status_code == 200
    with _db() as conn:
        conn.execute("UPDATE users SET disabled = 1")
    assert client.get("/findings").status_code == 401


def test_leaked_db_cannot_forge_a_session(app: FastAPI) -> None:
    """The raw session id from a database dump is not a valid cookie: the
    cookie must also carry the HMAC keyed by CABINET_SECRET_KEY."""
    store = app.state.auth
    user_id = store.create_user("victim@test.example", TEST_PASSWORD, "admin")
    session = store.create_session(user_id)
    raw = TestClient(app)
    raw.cookies.set(COOKIE_NAME, session["id"])  # bare id, no signature
    assert raw.get("/findings").status_code == 401
    raw.cookies.set(COOKIE_NAME, f"{session['id']}.{'0' * 64}")  # bad signature
    assert raw.get("/findings").status_code == 401
    raw.cookies.set(COOKIE_NAME, "garbage")
    assert raw.get("/findings").status_code == 401


def test_logout_kills_the_session(client: TestClient) -> None:
    cookie = client.cookies.get(COOKIE_NAME)
    assert cookie is not None
    assert client.post("/auth/logout").status_code == 200
    raw = TestClient(client.app)
    raw.cookies.set(COOKIE_NAME, cookie)  # replay the old, valid signature
    assert raw.get("/findings").status_code == 401


def test_401_is_logged_as_data_refused_with_anonymous_actor(
    app: FastAPI, client: TestClient
) -> None:
    """Pre-auth refusals land on the platform audit scope (0) — they belong
    to no institution, and /events never shows another scope's chain."""
    raw = TestClient(app)
    assert raw.get("/findings").status_code == 401
    store: AuthStore = app.state.auth
    refused = [
        e
        for e in store.audit_events(0, "data.refused")
        if e["payload"].get("path") == "/findings"
    ]
    assert refused
    assert refused[-1]["actor"] == "anonymous"
    # The institution-scoped /events route does not carry platform events.
    assert all(
        e["payload"].get("path") != "/findings"
        for e in client.get("/events", params={"type": "data.refused"}).json()["events"]
    )


# --- CSRF ------------------------------------------------------------------------


def test_post_without_csrf_token_is_403(client: TestClient) -> None:
    del client.headers["X-CSRF-Token"]
    response = client.post("/ask", json={"question": "anything"})
    assert response.status_code == 403


def test_post_with_wrong_csrf_token_is_403(client: TestClient) -> None:
    client.headers["X-CSRF-Token"] = "not-the-session-token"
    response = client.post("/ask", json={"question": "anything"})
    assert response.status_code == 403


def test_403_is_logged_as_data_refused_with_the_user_id(
    client: TestClient,
) -> None:
    user_id = client.get("/auth/me").json()["user"]["id"]
    del client.headers["X-CSRF-Token"]
    client.post("/ask", json={"question": "anything"})
    # Re-arm to read the audit log.
    client.headers["X-CSRF-Token"] = client.get("/auth/me").json()["csrf_token"]
    refused = client.get("/events", params={"type": "data.refused"}).json()["events"]
    csrf_refusals = [
        e for e in refused if "CSRF" in e["payload"].get("reason", "")
    ]
    assert csrf_refusals
    assert csrf_refusals[-1]["actor"] == str(user_id)


def test_get_requests_need_no_csrf_token(client: TestClient) -> None:
    del client.headers["X-CSRF-Token"]
    assert client.get("/findings").status_code == 200


def test_delete_without_csrf_token_is_403(client: TestClient) -> None:
    # DELETE /admin/datasets/{id} changes state, so it needs the token too.
    del client.headers["X-CSRF-Token"]
    assert client.delete("/admin/datasets/1").status_code == 403


def test_put_and_patch_without_csrf_token_are_403(client: TestClient) -> None:
    del client.headers["X-CSRF-Token"]
    assert client.put("/findings").status_code == 403
    assert client.patch("/findings").status_code == 403


def test_delete_with_csrf_token_reaches_the_route(client: TestClient) -> None:
    # Dataset 1 is the seeded active dataset: with a valid token the CSRF
    # check passes and the route itself answers (409: active, not deletable).
    assert client.delete("/admin/datasets/1").status_code == 409


# --- roles -------------------------------------------------------------------


def test_every_protected_route_is_in_the_role_table() -> None:
    """The role table is an allow-list: it must cover the whole API."""
    expected = {
        ("GET", "/questions"),
        ("GET", "/findings"),
        ("GET", "/events"),
        ("GET", "/briefing"),
        ("GET", "/briefing/enrollment"),
        ("GET", "/briefing/student-success"),
        ("GET", "/decisions"),
        ("GET", "/auth/me"),
        ("POST", "/auth/logout"),
        ("POST", "/ask"),
        ("POST", "/briefing/enrollment/refresh"),
        ("POST", "/briefing/student-success/refresh"),
        ("POST", "/governance/request"),
        ("POST", "/decisions/approve"),
        # The office address book (admin only); the dispatch routes carry a
        # decision id in the path and live in ROUTE_ROLE_PREFIXES.
        ("GET", "/admin/offices"),
        ("PUT", "/admin/offices"),
        # The counseling aggregate authorization (admin only).
        ("GET", "/admin/institution/counseling-authorization"),
        ("PUT", "/admin/institution/counseling-authorization"),
        # User administration (admin only; path-parameter routes live
        # in ROUTE_ROLE_PREFIXES).
        ("GET", "/admin/users"),
        ("POST", "/admin/users"),
        # The Financial Aid review queue (the PATCH carries a row id and
        # lives in ROUTE_ROLE_PREFIXES).
        ("GET", "/aid-queue"),
        # Explore (every role but aid; tests/test_explore.py).
        ("POST", "/explore"),
        ("POST", "/explore/stream"),
        ("GET", "/explore/catalog"),
        # The staff action worklist (its PATCH and POSTs carry an action id
        # and live in ROUTE_ROLE_PREFIXES; tests/test_staff_actions.py).
        ("GET", "/staff-actions"),
        # Institution settings: the outside connections (admin only).
        ("GET", "/admin/connections"),
        # The demonstration student directory (tests/test_roster.py).
        ("GET", "/students/search"),
        # Briefing follow-ups, answered in code (tests/test_followup.py).
        ("POST", "/briefing/follow-up"),
    }
    assert set(ROUTE_ROLES) == expected


def test_staff_reads_briefing_and_findings_but_never_approves(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    staff = make_authenticated_client(app, role="staff")
    assert staff.get("/findings").status_code == 200
    assert staff.get("/questions").status_code == 200
    assert staff.get("/decisions").status_code == 200
    assert staff.get("/briefing").status_code == 404  # allowed; none produced yet
    assert staff.post("/ask", json={"question": "anything"}).status_code == 403
    assert (
        staff.post(
            "/decisions/approve", json={"decision_id": DEMO_DECISION_ID}
        ).status_code
        == 403
    )
    assert staff.get("/events").status_code == 403


def test_reviewer_reads_everything_including_the_audit_log_nothing_else(
    app: FastAPI,
) -> None:
    reviewer = make_authenticated_client(app, role="reviewer")
    assert reviewer.get("/events").status_code == 200
    assert reviewer.get("/findings").status_code == 200
    assert reviewer.get("/decisions").status_code == 200
    assert reviewer.post("/ask", json={"question": "anything"}).status_code == 403
    assert (
        reviewer.post(
            "/governance/request",
            json={"role": "enrollment_analyst", "fields": ["profile.program"]},
        ).status_code
        == 403
    )
    assert (
        reviewer.post(
            "/decisions/approve", json={"decision_id": DEMO_DECISION_ID}
        ).status_code
        == 403
    )


def test_executive_asks_approves_and_reads_the_audit_log(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The president runs Beat 6: executives read the audit log and trigger
    the denied-request demo, on top of ask and approve."""
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    executive = make_authenticated_client(app, role="executive")
    assert executive.get("/findings").status_code == 200
    assert executive.post(
        "/ask", json={"question": "What should I know about spring registration?"}
    ).status_code == 200
    assert (
        executive.post(
            "/decisions/approve", json={"decision_id": DEMO_DECISION_ID}
        ).status_code
        == 200
    )
    assert executive.get("/events").status_code == 200
    assert (
        executive.post(
            "/governance/request",
            json={"role": "enrollment_analyst", "fields": ["holds.amount"]},
        ).status_code
        == 200
    )


def test_role_denial_is_logged_with_the_user_id(app: FastAPI) -> None:
    staff = make_authenticated_client(app, role="staff")
    staff_id = staff.get("/auth/me").json()["user"]["id"]
    staff.post("/ask", json={"question": "anything"})
    admin = make_authenticated_client(app, role="admin", email="root@test.example")
    refused = admin.get("/events", params={"type": "data.refused"}).json()["events"]
    role_refusals = [e for e in refused if "role" in e["payload"].get("reason", "")]
    assert role_refusals
    assert role_refusals[-1]["actor"] == str(staff_id)


def test_all_roles_roundtrip(app: FastAPI) -> None:
    for index, role in enumerate(USER_ROLES):
        client = make_authenticated_client(
            app, role=role, email=f"user{index}@test.example"
        )
        me = client.get("/auth/me")
        assert me.status_code == 200
        assert me.json()["user"]["role"] == role


# Concrete paths for the routes that carry a path parameter (the prefix
# rules), so the matrix below reaches every route the API serves.
_PREFIX_SAMPLES: tuple[tuple[str, str], ...] = (
    ("GET", "/admin/datasets"),
    ("POST", "/admin/datasets/999/activate"),
    ("DELETE", "/admin/datasets/999"),
    ("POST", "/admin/users/999/disable"),
    ("POST", "/admin/users/999/enable"),
    ("PATCH", "/admin/users/999"),
    ("GET", f"/decisions/{DEMO_DECISION_ID}/dispatch"),
    ("POST", f"/decisions/{DEMO_DECISION_ID}/dispatch"),
    ("POST", f"/decisions/{DEMO_DECISION_ID}/dispatch/send"),
    ("POST", f"/decisions/{DEMO_DECISION_ID}/aid-queue"),
    ("PATCH", "/aid-queue/999"),
    ("PATCH", "/staff-actions/999"),
    ("POST", "/staff-actions/999/notes"),
    ("POST", "/staff-actions/999/send"),
)

# A route that is open to a role in the table but narrows itself inside
# the handler: an executive's Send is a deliberate, logged 403.
_HANDLER_NARROWED: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("executive", "POST", f"/decisions/{DEMO_DECISION_ID}/dispatch/send"),
        ("executive", "POST", "/staff-actions/999/send"),
    }
)


def _table_roles(method: str, path: str) -> tuple[str, ...]:
    """The roles the middleware's tables allow for (method, path): the
    exact table first, then the first matching prefix rule."""
    if (method, path) in ROUTE_ROLES:
        return ROUTE_ROLES[(method, path)]
    for prefix_method, prefix, roles in ROUTE_ROLE_PREFIXES:
        if method == prefix_method and path.startswith(prefix):
            return roles
    return ()


def test_every_route_for_every_role_matrix(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every role against every route: a role the table does not list is a
    403 before the handler runs; a listed role reaches the handler (any
    answer but 401/403). Bodies are empty, so allowed POSTs stop at
    validation and nothing real happens. Logout runs last per role, since
    it ends the session."""
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    routes = sorted(
        {*ROUTE_ROLES, *_PREFIX_SAMPLES},
        key=lambda route: route == ("POST", "/auth/logout"),
    )
    for index, role in enumerate(USER_ROLES):
        client = make_authenticated_client(
            app, role=role, email=f"matrix{index}@test.example"
        )
        for method, path in routes:
            if method in ("GET", "DELETE"):
                status = client.request(method, path).status_code
            else:
                status = client.request(method, path, json={}).status_code
            allowed = role in _table_roles(method, path)
            if not allowed or (role, method, path) in _HANDLER_NARROWED:
                assert status == 403, (role, method, path, status)
            else:
                assert status not in (401, 403), (role, method, path, status)


def test_aid_role_reads_the_briefing_and_the_queue_and_nothing_else(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Financial Aid role: reads like staff, works the queue, and cannot
    ask, sign off, prepare or send a dispatch, prepare the queue, or read
    the audit log."""
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    aid = make_authenticated_client(app, role="aid")
    assert aid.get("/findings").status_code == 200
    assert aid.get("/questions").status_code == 200
    assert aid.get("/decisions").status_code == 200
    assert aid.get("/briefing").status_code == 404  # allowed; none produced yet
    assert aid.get("/aid-queue").status_code == 200
    assert aid.get("/events").status_code == 403
    assert aid.post("/ask", json={"question": "anything"}).status_code == 403
    sign_off = aid.post("/decisions/approve", json={"decision_id": DEMO_DECISION_ID})
    assert sign_off.status_code == 403
    assert aid.post(f"/decisions/{DEMO_DECISION_ID}/aid-queue").status_code == 403
    assert aid.post(f"/decisions/{DEMO_DECISION_ID}/dispatch").status_code == 403
    assert aid.get("/admin/users").status_code == 403


# --- headers, body cap, content type --------------------------------------------


def test_security_headers_on_every_response(client: TestClient) -> None:
    response = client.get("/findings")
    assert response.headers["Content-Security-Policy"] == (
        "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
        "style-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
        "form-action 'self'"
    )
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Permissions-Policy"] == (
        "camera=(), microphone=(), geolocation=()"
    )
    assert response.headers["Cache-Control"] == "no-store"
    assert "Strict-Transport-Security" not in response.headers  # dev only


def test_security_headers_on_error_responses_too(app: FastAPI) -> None:
    raw = TestClient(app)
    response = raw.get("/findings")  # 401
    assert response.status_code == 401
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"


def test_production_adds_hsts_and_secure_cookie(prod_app: FastAPI) -> None:
    prod_app.state.auth.create_user("prod@test.example", TEST_PASSWORD, "admin")
    raw = TestClient(prod_app)
    login = raw.post(
        "/auth/login",
        json={"email": "prod@test.example", "password": TEST_PASSWORD},
    )
    assert login.status_code == 200
    assert "Secure" in login.headers["set-cookie"]
    prod = _login_production(prod_app, email="prod2@test.example")
    response = prod.get("/findings")
    assert response.status_code == 200
    assert response.headers["Strict-Transport-Security"] == (
        "max-age=31536000; includeSubDomains"
    )


def test_body_cap_is_256kb(client: TestClient) -> None:
    big = "x" * (MAX_BODY_BYTES + 1024)
    response = client.post("/ask", json={"question": big})
    assert response.status_code == 413


def test_non_json_body_is_415(client: TestClient) -> None:
    response = client.post(
        "/ask", content=b"question?", headers={"Content-Type": "text/plain"}
    )
    assert response.status_code == 415


# --- rate limits ------------------------------------------------------------------


def test_general_rate_limit_429_with_retry_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The per-IP bucket also covers the public login route, so the login
    # itself spends one of the three tokens.
    monkeypatch.setenv("CABINET_RATE_GENERAL_PER_MIN", "3")
    client = make_authenticated_client(
        create_app()
    )
    assert client.get("/questions").status_code == 200
    assert client.get("/questions").status_code == 200
    limited = client.get("/questions")
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) > 0
    assert limited.json() == {"detail": RATE_LIMIT_MESSAGE}


def test_rate_limit_defaults_fit_a_shared_campus_address() -> None:
    """Everyone behind one campus address shares the per-address bucket,
    so it is ten times the old 60; one person is paced by the session."""
    assert DEFAULT_RATE_GENERAL_PER_MIN == 600
    assert DEFAULT_RATE_SESSION_PER_MIN == 120
    assert DEFAULT_RATE_ASK_PER_MIN == 5
    assert RATE_LIMIT_MESSAGE == "CampusLens is busy. Wait a minute and try again."


def test_session_rate_limit_is_its_own_bucket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A session is paced by its own bucket while the address bucket still
    has room: two calls pass, the third is a plain-worded 429."""
    monkeypatch.setenv("CABINET_RATE_SESSION_PER_MIN", "2")
    client = make_authenticated_client(create_app())
    assert client.get("/questions").status_code == 200
    assert client.get("/questions").status_code == 200
    limited = client.get("/questions")
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) > 0
    assert limited.json() == {"detail": RATE_LIMIT_MESSAGE}


def test_session_rate_limit_does_not_spend_another_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two people behind one address: one person using up their session
    bucket leaves the other person's untouched."""
    monkeypatch.setenv("CABINET_RATE_SESSION_PER_MIN", "1")
    app = create_app()
    first = make_authenticated_client(app)
    assert first.get("/questions").status_code == 200
    assert first.get("/questions").status_code == 429
    second = make_authenticated_client(app, role="executive")
    assert second.get("/questions").status_code == 200


def test_ask_has_a_tighter_rate_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    monkeypatch.setenv("CABINET_RATE_ASK_PER_MIN", "1")
    client = make_authenticated_client(
        create_app()
    )
    question = {"question": "What should I know about spring registration?"}
    assert client.post("/ask", json=question).status_code == 200
    limited = client.post("/ask", json=question)
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) > 0
    assert limited.json() == {"detail": RATE_LIMIT_MESSAGE}


# --- production fail-closed ------------------------------------------------------


def test_production_refuses_to_start_without_secret_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CABINET_ENV", "production")
    monkeypatch.setenv("CABINET_BIND", "127.0.0.1")
    monkeypatch.delenv("CABINET_SECRET_KEY")
    with pytest.raises(SystemExit) as excinfo:
        create_app()
    assert excinfo.value.code == 1
    assert "CABINET_SECRET_KEY" in capsys.readouterr().err


def test_production_refuses_a_short_secret_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CABINET_ENV", "production")
    monkeypatch.setenv("CABINET_BIND", "127.0.0.1")
    monkeypatch.setenv("CABINET_SECRET_KEY", "too-short")
    with pytest.raises(SystemExit):
        create_app()


def test_production_refuses_to_start_without_explicit_bind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CABINET_ENV", "production")
    monkeypatch.delenv("CABINET_BIND", raising=False)
    with pytest.raises(SystemExit) as excinfo:
        create_app()
    assert excinfo.value.code == 1
    assert "CABINET_BIND" in capsys.readouterr().err


def test_production_starts_with_secret_and_explicit_bind(prod_app: FastAPI) -> None:
    raw = TestClient(prod_app)
    assert raw.get("/health").status_code == 200


def test_production_rejects_cross_site_origin(prod_app: FastAPI) -> None:
    prod = _login_production(prod_app)
    cross_site = prod.post(
        "/ask",
        json={"question": "anything"},
        headers={"Origin": "https://evil.example"},
    )
    assert cross_site.status_code == 403
    same_site = prod.post(
        "/ask",
        json={"question": "anything"},
        headers={"Origin": "http://testserver"},
    )
    assert same_site.status_code == 200  # 200 on the refusal body, not a 403


# --- the audit hash chain ---------------------------------------------------------


def test_events_carry_a_verifiable_hash_chain(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    log = AuditLog(path)
    first = log.append("task.created", actor="test", payload={"n": 1})
    second = log.append("task.created", actor="test", payload={"n": 2})
    assert first["prev_hash"] == GENESIS_HASH
    assert second["prev_hash"] == event_hash(first)
    assert second["hash"] == event_hash(second)
    assert verify_chain(path) == []
    assert audit_main(["verify", str(path)]) == 0


def test_tampered_event_breaks_verify(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    log = AuditLog(path)
    log.append("task.created", actor="test", payload={"n": 1})
    log.append("task.created", actor="test", payload={"n": 2})
    lines = path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[0])
    tampered["payload"]["n"] = 999
    lines[0] = json.dumps(tampered)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    breaks = verify_chain(path)
    assert breaks
    assert audit_main(["verify", str(path)]) == 1


def test_torn_tail_repair_preserves_the_chain(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    log = AuditLog(path)
    log.append("task.created", actor="test", payload={"n": 1})
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"id": 2, "tor')  # a killed process's partial write
    AuditLog(path)  # the repair runs on open
    assert verify_chain(path) == []
    # And the chain keeps extending soundly after the repair.
    repaired = AuditLog(path)
    repaired.append("task.created", actor="test", payload={"n": 2})
    assert verify_chain(path) == []


def test_events_endpoint_includes_the_hashes(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    executive = make_authenticated_client(app, role="executive")
    executive.post(
        "/ask", json={"question": "What should I know about spring registration?"}
    )
    reviewer = make_authenticated_client(app, role="reviewer")
    events = reviewer.get("/events").json()["events"]
    assert events
    for event in events:
        assert event["hash"]
        assert event["prev_hash"]


# --- login throttling: hard block per IP and per IP+email only -----------------


def _raw_asgi_post(
    app: FastAPI, path: str, payload: dict[str, Any], client_ip: str
) -> tuple[int, dict[str, Any]]:
    """POST JSON through the full app stack from a chosen client IP
    (TestClient always comes from one address, and the lockout keys on
    the IP)."""
    import asyncio

    body = json.dumps(payload).encode("utf-8")
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ],
        "client": (client_ip, 50000),
        "server": ("testserver", 80),
    }
    incoming: list[MutableMapping[str, Any]] = [
        {"type": "http.request", "body": body, "more_body": False}
    ]
    sent: list[MutableMapping[str, Any]] = []

    async def receive() -> MutableMapping[str, Any]:
        return incoming.pop(0) if incoming else {"type": "http.disconnect"}

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    raw = b"".join(
        m.get("body", b"") for m in sent if m["type"] == "http.response.body"
    )
    return status, json.loads(raw.decode("utf-8")) if raw else {}


def test_wrong_attempts_from_one_ip_delay_but_never_block_another_ip(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hard lockout keys on IP and IP+email — never the bare email, so
    five wrong attempts from IP X cannot lock the account out for IP Y.
    IP Y's correct login succeeds, paying only the progressive email delay
    (1, 2, 4, 8 s, capped at 30 s; the sleep is monkeypatched here)."""
    delays: list[int] = []

    async def record_delay(seconds: float) -> None:
        delays.append(int(seconds))

    monkeypatch.setattr("cabinet.security._sleep", record_delay)
    app.state.auth.create_user("president@test.example", TEST_PASSWORD, "executive")
    payload = {"email": "president@test.example", "password": "wrong"}
    for _ in range(5):
        status, _ = _raw_asgi_post(app, "/auth/login", payload, "10.0.0.1")
        assert status == 401
    # The escalating delay path was taken: 1, 2, 4, 8 s before attempts
    # 2 through 5 (the first attempt had no prior failures).
    assert delays == [1, 2, 4, 8]

    # IP X is hard-blocked now, even with the right password.
    status, _ = _raw_asgi_post(
        app,
        "/auth/login",
        {"email": "president@test.example", "password": TEST_PASSWORD},
        "10.0.0.1",
    )
    assert status == 429

    # IP Y is not blocked — only delayed (16 s for the sixth attempt,
    # well under the 30 s cap).
    delays.clear()
    status, body = _raw_asgi_post(
        app,
        "/auth/login",
        {"email": "president@test.example", "password": TEST_PASSWORD},
        "10.0.0.2",
    )
    assert status == 200
    assert body["user"]["email"] == "president@test.example"
    assert delays and max(delays) <= 30


def test_login_delay_is_capped_at_30_seconds(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Many failures against one email still only delay: 1, 2, 4, 8, 16,
    then 30 s flat — and never a 429 keyed on the email alone."""
    delays: list[int] = []

    async def record_delay(seconds: float) -> None:
        delays.append(int(seconds))

    monkeypatch.setattr("cabinet.security._sleep", record_delay)
    for attempt in range(8):
        status, _ = _raw_asgi_post(
            app,
            "/auth/login",
            {"email": f"ghost{attempt}@test.example", "password": "bad"},
            f"10.9.0.{attempt}",  # distinct IPs: only the bare email repeats
        )
        assert status == 401
    delays.clear()
    for attempt in range(8, 10):
        status, _ = _raw_asgi_post(
            app,
            "/auth/login",
            {"email": "ghost-shared@test.example", "password": "bad"},
            f"10.9.0.{attempt}",
        )
        assert status == 401
    # Now hammer one email from fresh IPs: never 429, delay capped at 30.
    delays.clear()
    for attempt in range(10):
        status, _ = _raw_asgi_post(
            app,
            "/auth/login",
            {"email": "hammered@test.example", "password": "bad"},
            f"10.8.{attempt}.1",
        )
        assert status == 401
    assert delays and max(delays) <= 30
    assert min(delays) >= 1


def test_lockout_and_bucket_dicts_are_bounded() -> None:
    """Stale keys are evicted: the failure counter and the token buckets
    cannot grow without bound."""
    from cabinet.security import LoginLockout, TokenBucket

    now = [1000.0]
    clock = lambda: now[0]  # noqa: E731
    lockout = LoginLockout(max_attempts=5, window_seconds=60, clock=clock)
    for index in range(100):
        lockout.record_failure(f"email:user{index}@example.edu")
    assert lockout.key_count() == 100
    now[0] += 600  # everything ages out of the window
    lockout.record_failure("email:new@example.edu")
    assert lockout.key_count() == 1

    bucket_now = [1000.0]
    bucket = TokenBucket(60, clock=lambda: bucket_now[0], idle_evict_seconds=300)
    for index in range(100):
        bucket.allow(f"ip:10.0.0.{index}")
    assert bucket.key_count() == 100
    bucket_now[0] += 400  # past the idle horizon and the sweep interval
    bucket.allow("ip:10.1.1.1")
    assert bucket.key_count() == 1


# --- body caps on bytes actually read, public routes included --------------------


def test_chunked_login_body_over_the_cap_is_413(app: FastAPI) -> None:
    """A chunked body carries no Content-Length to pre-check; the cap is
    enforced on the bytes actually read. The public login route gets the
    same 256 KB cap as every non-upload route."""
    raw = TestClient(app)

    def chunks() -> Iterator[bytes]:
        yield b'{"email": "big@test.example", "password": "'
        yield b"x" * MAX_BODY_BYTES
        yield b'"}'

    response = raw.post(
        "/auth/login", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413


def test_chunked_login_body_under_the_cap_reaches_the_route(app: FastAPI) -> None:
    """The streaming cap hands the consumed bytes downstream: a chunked
    login under the cap still works."""
    app.state.auth.create_user("chunky@test.example", TEST_PASSWORD, "admin")
    raw = TestClient(app)

    def chunks() -> Iterator[bytes]:
        yield b'{"email": "chunky@test.example",'
        yield b' "password": "' + TEST_PASSWORD.encode("utf-8") + b'"}'

    response = raw.post(
        "/auth/login", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 200
    assert response.json()["user"]["email"] == "chunky@test.example"


def test_chunked_upload_over_20mb_is_413(client: TestClient) -> None:
    """The 20 MB upload cap is enforced on the bytes actually read too."""

    def chunks() -> Iterator[bytes]:
        for _ in range(21):
            yield b"x" * (1024 * 1024)

    response = client.post(
        "/admin/datasets",
        content=chunks(),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_ready_is_rate_limited_per_ip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The public readiness route sits behind the per-IP bucket like every
    other route."""
    monkeypatch.setenv("CABINET_RATE_GENERAL_PER_MIN", "2")
    raw = TestClient(create_app())
    assert raw.get("/ready").status_code == 200
    assert raw.get("/ready").status_code == 200
    limited = raw.get("/ready")
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) > 0


# --- the body is read only after auth on guarded routes ------------------------


def _raw_asgi_post_counting(
    app: FastAPI,
    path: str,
    *,
    chunks: list[bytes],
    headers: list[tuple[bytes, bytes]] | None = None,
    client_ip: str = "10.7.0.1",
) -> tuple[int, dict[str, Any], int]:
    """POST through the full app stack, counting how many body chunks the
    app pulled from ``receive`` before it answered — the pre-auth memory
    amplification check: a refused caller's body must never be buffered."""
    import asyncio

    declared = sum(len(chunk) for chunk in chunks)
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"content-length", str(declared).encode("ascii")),
            *(headers or []),
        ],
        "client": (client_ip, 50000),
        "server": ("testserver", 80),
    }
    pending = list(chunks)
    pulled = 0
    sent: list[MutableMapping[str, Any]] = []

    async def receive() -> MutableMapping[str, Any]:
        nonlocal pulled
        if not pending:
            return {"type": "http.disconnect"}
        pulled += 1
        chunk = pending.pop(0)
        return {"type": "http.request", "body": chunk, "more_body": bool(pending)}

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    raw = b"".join(
        m.get("body", b"") for m in sent if m["type"] == "http.response.body"
    )
    return status, json.loads(raw.decode("utf-8")) if raw else {}, pulled


def _session_headers(app: FastAPI, role: str) -> list[tuple[bytes, bytes]]:
    """A signed session cookie and its CSRF token, as raw ASGI headers."""
    store: AuthStore = app.state.auth
    with contextlib.suppress(ValueError):
        store.create_user(f"{role}@raw.test.example", TEST_PASSWORD, role)
    user = store.user_by_email(f"{role}@raw.test.example")
    assert user is not None
    session = store.create_session(user["id"])
    cookie = sign_session_id(TEST_SECRET_KEY, session["id"])
    return [
        (b"cookie", f"{COOKIE_NAME}={cookie}".encode("ascii")),
        (b"x-csrf-token", str(session["csrf_token"]).encode("ascii")),
    ]


def test_unauthenticated_upload_is_401_without_reading_the_body(
    app: FastAPI,
) -> None:
    """Pre-auth memory amplification: an anonymous POST /admin/datasets
    declaring a ~9 MB body is refused 401 without a single body chunk being
    read — the 20 MB override applies only past the admin role check."""
    body = b'{"meta": ' + b" " * 9_000_000 + b"{}"  # under the 20 MB cap
    chunks = [body[i : i + 65536] for i in range(0, len(body), 65536)]
    status, _, pulled = _raw_asgi_post_counting(app, "/admin/datasets", chunks=chunks)
    assert status == 401
    assert pulled == 0


def test_wrong_role_upload_is_403_without_reading_the_body(app: FastAPI) -> None:
    """An authenticated non-admin is 403 — equally before any body read."""
    body = b'{"meta": ' + b" " * 9_000_000 + b"{}"
    chunks = [body[i : i + 65536] for i in range(0, len(body), 65536)]
    status, _, pulled = _raw_asgi_post_counting(
        app,
        "/admin/datasets",
        chunks=chunks,
        headers=_session_headers(app, "executive"),
    )
    assert status == 403
    assert pulled == 0


def test_authenticated_admin_upload_still_reads_and_validates_the_body(
    app: FastAPI,
) -> None:
    """The authorized path is unchanged: the admin's chunked body is read
    (past the role check) and validated, landing as a 201."""
    body = DEFAULT_FIXTURE_PATH.read_bytes()
    chunks = [body[:1000], body[1000:]]
    status, parsed, pulled = _raw_asgi_post_counting(
        app,
        "/admin/datasets",
        chunks=chunks,
        headers=_session_headers(app, "admin"),
    )
    assert status == 201
    assert pulled == 2
    assert parsed["validation"]["row_counts"] == {
        "students": 185,
        "prior_year_students": 135,
    }


# --- login is async end to end -------------------------------------------------


def test_post_login_is_a_coroutine_and_verifies_in_the_threadpool(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blocking sleep in the old sync login pinned a threadpool worker
    (enough concurrent sleepers stall every sync route), and scrypt on the
    event loop would stall it directly: the handler must be a coroutine,
    the delay must await the patchable async _sleep, and verify_credentials
    must run under run_in_threadpool. Three failures for one email pay
    delays 0, 1, 2 on successive attempts."""
    import asyncio

    from fastapi.routing import APIRoute
    from starlette.concurrency import run_in_threadpool

    from cabinet.security import LoginLockout

    route = next(
        r
        for r in app.routes
        if isinstance(r, APIRoute) and r.path == "/auth/login"
    )
    assert asyncio.iscoroutinefunction(route.endpoint)

    slept: list[float] = []

    async def record_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("cabinet.security._sleep", record_sleep)

    delays: list[int] = []
    real_delay = LoginLockout.email_delay_seconds

    def spy_delay(self: LoginLockout, key: str) -> int:
        delay = real_delay(self, key)
        delays.append(delay)
        return delay

    monkeypatch.setattr(LoginLockout, "email_delay_seconds", spy_delay)

    threadpool_calls: list[Any] = []

    async def spy_run(func: Any, *args: Any) -> Any:
        threadpool_calls.append(func)
        return await run_in_threadpool(func, *args)

    monkeypatch.setattr("cabinet.api.run_in_threadpool", spy_run)

    app.state.auth.create_user("slow@test.example", TEST_PASSWORD, "admin")
    raw = TestClient(app)
    for _ in range(3):
        response = raw.post(
            "/auth/login", json={"email": "slow@test.example", "password": "bad"}
        )
        assert response.status_code == 401
    assert delays == [0, 1, 2]
    assert slept == [1, 2]  # no sleep at all on the first attempt
    assert threadpool_calls
    assert all(
        getattr(func, "__name__", "") == "verify_credentials"
        for func in threadpool_calls
    )


def test_health_is_served_while_a_login_pays_the_delay(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A login sleeping off its progressive delay holds no threadpool
    worker and does not touch the event loop: /health answers while the
    sleep is still pending. The fake sleep is event-driven, so the test
    never waits real seconds — and on the old sync handler it could never
    even start (a sync caller does not await the coroutine hook)."""
    import asyncio
    import threading
    import time

    import httpx2 as httpx  # the pinned httpx fork (requirements.txt)

    sleeping = threading.Event()
    release = threading.Event()

    async def fake_sleep(seconds: float) -> None:
        sleeping.set()
        await asyncio.to_thread(release.wait, 5)

    monkeypatch.setattr("cabinet.security._sleep", fake_sleep)
    app.state.auth.create_user("dozing@test.example", TEST_PASSWORD, "admin")

    async def scenario() -> tuple[int, float, int]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            payload = {"email": "dozing@test.example", "password": "bad"}
            first = await client.post("/auth/login", json=payload)
            assert first.status_code == 401  # no delay yet; one failure recorded
            login = asyncio.create_task(client.post("/auth/login", json=payload))
            assert await asyncio.to_thread(sleeping.wait, 5)
            start = time.monotonic()
            health = await client.get("/health")
            elapsed = time.monotonic() - start
            release.set()
            response = await login
            return health.status_code, elapsed, response.status_code

    health_status, elapsed, login_status = asyncio.run(scenario())
    assert health_status == 200
    assert elapsed < 1.0
    assert login_status == 401
