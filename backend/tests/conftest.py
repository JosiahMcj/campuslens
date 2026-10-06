"""Shared test setup.

Every test runs against an empty golden-run directory so the committed
recordings in ``data/golden/`` never leak into replay tests; a test that
wants a golden recording writes its own into ``CABINET_GOLDEN_DIR``.

Every test also gets a fresh users/sessions database (``CABINET_DB`` under
the test's tmp dir) and rate limits raised out of the way (the rate-limit
tests set their own). Every route except /health, /ready, and
/auth/login requires a session, so API tests build their clients with
:func:`make_authenticated_client`, which creates a user in the app's own
store and logs it in for real — the tests exercise the same login path the
demo uses.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cabinet.auth import AuthStore

TEST_PASSWORD = "test-password-not-a-secret"
TEST_SECRET_KEY = "test-secret-key-with-well-over-32-bytes!!"

REPO_VAR = Path(__file__).resolve().parents[2] / "var"


def _repo_var_contents() -> set[str]:
    if not REPO_VAR.exists():
        return set()
    # SQLite journals, server logs and pid files are runtime byproducts a
    # local server (or its crash) can leave in var/ between runs; they are
    # not the seeded-database leak this guard exists to catch.
    return {
        str(p.relative_to(REPO_VAR))
        for p in REPO_VAR.rglob("*")
        if not (p.name.endswith("-journal") or p.suffix in (".log", ".pid"))
    }


# Snapshotted at conftest IMPORT — before pytest collects (and imports) the
# test modules, which is when a module-level cabinet.app:app would seed the
# repo's var/ with ambient settings. A baseline taken in the fixture would
# already contain that leak.
_REPO_VAR_BEFORE = _repo_var_contents()
_REPO_VAR_EXISTED = REPO_VAR.exists()


@pytest.fixture(autouse=True, scope="session")
def _tests_never_touch_the_repo_var() -> Iterator[None]:
    """Fail the run if the repo's var/ appeared or grew during the session.

    The suite runs entirely against tmp dirs (the fixtures below); var/ is
    the developer's real data. A test that builds the app outside the
    conftest env — the module-level ``cabinet.app:app`` at import time is
    the classic case — seeds ``var/cabinet.db`` with ambient settings, and
    that regression must fail loudly, not silently pollute the repo.
    """
    yield
    after = _repo_var_contents()
    new_entries = sorted(after - _REPO_VAR_BEFORE)
    if REPO_VAR.exists() and not _REPO_VAR_EXISTED and not after:
        new_entries = ["(empty directory)"]
    if new_entries:
        pytest.fail(
            "the test session wrote into the repo's var/ "
            f"({REPO_VAR}): {', '.join(new_entries)} — tests must build "
            "their app under the conftest env, never from the module-level "
            "cabinet.app:app"
        )


@pytest.fixture(autouse=True)
def _isolated_golden_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    golden = tmp_path / "golden"
    golden.mkdir()
    monkeypatch.setenv("CABINET_GOLDEN_DIR", str(golden))
    return golden


@pytest.fixture(autouse=True)
def _isolated_security_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CABINET_DB", str(tmp_path / "cabinet.db"))
    # A fixed secret so app instances within one test share session signing
    # (restart tests) and no ephemeral-key warning line is printed (the
    # fixture-startup test asserts exactly one stderr line).
    monkeypatch.setenv("CABINET_SECRET_KEY", TEST_SECRET_KEY)
    # Rate limits are per app instance; raise them so functional tests never
    # trip them. The rate-limit tests set their own values before create_app.
    monkeypatch.setenv("CABINET_RATE_GENERAL_PER_MIN", "100000")
    monkeypatch.setenv("CABINET_RATE_SESSION_PER_MIN", "100000")
    monkeypatch.setenv("CABINET_RATE_ASK_PER_MIN", "100000")
    # A stand-in built UI: /ready requires an index.html and the static
    # mount serves whatever this directory holds, so tests never depend on a
    # real `make build`.
    ui_dist = tmp_path / "ui-dist"
    ui_dist.mkdir()
    (ui_dist / "index.html").write_text("<html>test build</html>\n")
    monkeypatch.setenv("CABINET_UI_DIST", str(ui_dist))
    # Never let a developer's shell leak production mode into the test run.
    for name in ("CABINET_ENV", "CABINET_BIND"):
        monkeypatch.delenv(name, raising=False)


def make_authenticated_client(
    app: FastAPI,
    *,
    role: str = "admin",
    email: str | None = None,
    password: str = TEST_PASSWORD,
) -> TestClient:
    """A TestClient logged in as a freshly created user of ``role``.

    Creates the user in the app's own AuthStore, logs in through
    ``POST /auth/login`` (the real path), and arms the client with the
    session cookie (httpx keeps it) and the session's CSRF token as a
    default header.
    """
    store: AuthStore = app.state.auth
    with contextlib.suppress(ValueError):
        # A restarted app over the same CABINET_DB already has the user.
        store.create_user(email or f"{role}@test.example", password, role)
    client = TestClient(app)
    response = client.post(
        "/auth/login",
        json={"email": email or f"{role}@test.example", "password": password},
    )
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client
