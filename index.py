"""Vercel entry point for the hosted demo (the ``vercel-demo`` branch only).

Vercel runs this file as one Python serverless function that serves both the
API and the built UI (``ui/dist``), exactly as ``make serve`` does. The
deployment filesystem is read-only except ``/tmp``, and ``/tmp`` is wiped
whenever Vercel starts a fresh instance, so on every cold start this module:

- points the database, audit log and dataset files at ``/tmp/cabinet``;
- runs in replay mode, so the briefing is the committed golden run and no
  model key is needed;
- creates the demo admin and president accounts, with passwords taken from
  the ``DEMO_ADMIN_PASSWORD`` and ``DEMO_PRESIDENT_PASSWORD`` environment
  variables (set in the Vercel project, never in the repo).

Because storage lives in ``/tmp``, anything done in the demo (approvals,
uploads, extra users) resets when the instance is recycled. That is
intended: this is a look-and-feel demo on fictional data, not a production
deployment. ``RUNBOOK.md`` "Operating in production" covers the real one.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend" / "src"))

STATE_DIR = Path("/tmp/cabinet")

os.environ.setdefault("CABINET_DB", str(STATE_DIR / "cabinet.db"))
os.environ.setdefault("CABINET_AUDIT_PATH", str(STATE_DIR / "audit" / "events.jsonl"))
os.environ.setdefault("CABINET_PROVIDER", "replay")
os.environ.setdefault("CABINET_UI_DIST", str(ROOT / "ui" / "dist"))
# The production startup check wants proof the bind address was chosen; on
# Vercel the platform owns the listener, so record that explicitly.
os.environ.setdefault("CABINET_BIND", "vercel")

from cabinet.api import create_app, fixture_path_from_env  # noqa: E402
from cabinet.auth import AuthStore, db_path_from_env  # noqa: E402

DEMO_ADMIN_EMAIL = os.environ.get("DEMO_ADMIN_EMAIL", "admin@demo.test")
DEMO_PRESIDENT_EMAIL = os.environ.get("DEMO_PRESIDENT_EMAIL", "president@demo.test")


def _seed_demo_users() -> None:
    """Create the demo accounts once per fresh ``/tmp`` (idempotent)."""
    admin_password = os.environ.get("DEMO_ADMIN_PASSWORD", "")
    president_password = os.environ.get("DEMO_PRESIDENT_PASSWORD", "")
    if not admin_password or not president_password:
        raise RuntimeError(
            "DEMO_ADMIN_PASSWORD and DEMO_PRESIDENT_PASSWORD must be set in "
            "the Vercel project; refusing to start without demo accounts"
        )
    store = AuthStore(db_path_from_env(), seed_fixture=fixture_path_from_env())
    if store.has_admin():
        return
    institution_id = store.ensure_bootstrap_institution()
    store.create_user(
        DEMO_ADMIN_EMAIL, admin_password, "admin", institution_id=institution_id
    )
    store.create_user(
        DEMO_PRESIDENT_EMAIL,
        president_password,
        "executive",
        institution_id=institution_id,
    )


def _portable_sessions() -> None:
    """Let a session created on one Vercel instance work on the others.

    Vercel may route consecutive requests to different instances, each with
    its own ``/tmp`` database, so a session row written by one instance is
    missing on the next. For this demo only, the session id carries the user
    id and expiry plus an HMAC over them (keyed by ``CABINET_SECRET_KEY``),
    and the CSRF token is derived from the id. An instance that does not have
    the row verifies the HMAC, checks the expiry and that the user exists and
    is active, then writes the row locally. The demo accounts are seeded in
    the same order everywhere, so user ids agree across instances. Accounts
    created in the demo exist only on the instance that created them.
    """
    import hashlib
    import hmac
    import secrets
    from datetime import datetime

    from cabinet.auth import session_ttl

    key = os.environ.get("CABINET_SECRET_KEY", "").encode("utf-8")

    def _mac(message: str) -> str:
        return hmac.new(key, message.encode("utf-8"), hashlib.sha256).hexdigest()

    def _insert(store: AuthStore, session: dict[str, object]) -> None:
        with store._lock:
            store._conn.execute(
                "INSERT OR IGNORE INTO sessions (id, user_id, created_at,"
                " expires_at, last_seen, csrf_token) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    session["id"],
                    session["user_id"],
                    session["created_at"],
                    session["expires_at"],
                    session["last_seen"],
                    session["csrf_token"],
                ),
            )
            store._conn.commit()

    def _build(user_id: int, created: str, expires: str, nonce: str) -> dict[str, object]:
        body = f"{user_id}.{int(datetime.fromisoformat(expires).timestamp())}.{nonce}"
        session_id = f"{body}.{_mac('session|' + body)}"
        return {
            "id": session_id,
            "user_id": user_id,
            "created_at": created,
            "expires_at": expires,
            "last_seen": created,
            "csrf_token": _mac("csrf|" + session_id),
        }

    def create_session(self: AuthStore, user_id: int) -> dict[str, object]:
        now = self._now()
        session = _build(
            user_id,
            now.isoformat(),
            (now + session_ttl()).isoformat(),
            secrets.token_hex(16),
        )
        _insert(self, session)
        return session

    original_get = AuthStore.get_session

    def get_session(self: AuthStore, session_id: str) -> dict[str, object] | None:
        found = original_get(self, session_id)
        if found is not None:
            return found
        parts = session_id.split(".")
        if len(parts) != 4:
            return None
        user_text, expires_text, nonce, mac = parts
        body = f"{user_text}.{expires_text}.{nonce}"
        if not hmac.compare_digest(mac, _mac("session|" + body)):
            return None
        try:
            user_id = int(user_text)
            expires_ts = int(expires_text)
        except ValueError:
            return None
        now = self._now()
        if expires_ts <= now.timestamp():
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM users WHERE id = ? AND disabled = 0", (user_id,)
            ).fetchone()
        if row is None:
            return None
        expires = datetime.fromtimestamp(expires_ts, tz=now.tzinfo).isoformat()
        session = {
            "id": session_id,
            "user_id": user_id,
            "created_at": now.isoformat(),
            "expires_at": expires,
            "last_seen": now.isoformat(),
            "csrf_token": _mac("csrf|" + session_id),
        }
        _insert(self, session)
        return original_get(self, session_id)

    AuthStore.create_session = create_session  # type: ignore[method-assign]
    AuthStore.get_session = get_session  # type: ignore[method-assign]


_seed_demo_users()
_portable_sessions()
app = create_app()
