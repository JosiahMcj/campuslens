"""Users, sessions, and password hashing.

Standard library only: ``hashlib.scrypt`` for password hashing (per-user
salt), ``secrets`` for session ids and generated passwords, ``hmac`` for
signing session cookies, ``sqlite3`` for storage.

Storage is a SQLite database (default ``var/cabinet.db``, override with
``CABINET_DB``). The tables are owned by ``cabinet.store``
(``CabinetStore``) and versioned by ``cabinet.migrations``:

- ``users(id, email UNIQUE, password_hash, role, institution_id NOT NULL,
  created_at, disabled)`` — ``password_hash`` is
  ``scrypt$N$r$p$<salt hex>$<hash hex>``; every user belongs to an
  institution (the bootstrap admin gets the bootstrap institution).
- ``sessions(id, user_id, created_at, expires_at, last_seen,
  csrf_token)`` — the id is a random 256-bit hex string; sessions expire
  ``CABINET_SESSION_TTL_HOURS`` (default 12) after creation.

``AuthStore`` below is the same object as ``CabinetStore`` under its original
name, so existing callers and tests keep working.

The ``cabinet_session`` cookie never carries the bare session id: it
carries ``<session id>.<HMAC-SHA256(session id, CABINET_SECRET_KEY)>``, so
a leaked database alone cannot forge a session — the attacker would also
need the secret key, which lives only in the environment. Verification
uses ``hmac.compare_digest`` throughout.

Password verification is constant-time in the user-existence sense: an
unknown email is checked against a dummy hash so the timing does not
reveal whether the account exists. The API layer adds the generic error
message and the login attempt lockout on top.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cabinet.store import CabinetStore

ENV_DB = "CABINET_DB"
DEFAULT_DB_PATH = Path(__file__).resolve().parents[3] / "var" / "cabinet.db"

ENV_SECRET_KEY = "CABINET_SECRET_KEY"
ENV_ENV = "CABINET_ENV"
ENV_BIND = "CABINET_BIND"
PRODUCTION = "production"
MIN_SECRET_KEY_BYTES = 32

ROLE_ADMIN = "admin"
ROLE_EXECUTIVE = "executive"
ROLE_STAFF = "staff"
ROLE_REVIEWER = "reviewer"
USER_ROLES: tuple[str, ...] = (ROLE_ADMIN, ROLE_EXECUTIVE, ROLE_STAFF, ROLE_REVIEWER)

ENV_SESSION_TTL_HOURS = "CABINET_SESSION_TTL_HOURS"
DEFAULT_SESSION_TTL_HOURS = 12
COOKIE_NAME = "cabinet_session"
CSRF_HEADER = "x-csrf-token"


def session_ttl() -> timedelta:
    """The session lifetime: CABINET_SESSION_TTL_HOURS, default 12.

    This is the one place the variable is read, so the session rows and the
    cookie's max-age cannot disagree. A set-but-unusable value is a
    RuntimeError (the app refuses to start) rather than a silent fallback
    to the default.
    """
    raw = os.environ.get(ENV_SESSION_TTL_HOURS, "").strip()
    if not raw:
        return timedelta(hours=DEFAULT_SESSION_TTL_HOURS)
    try:
        hours = float(raw)
    except ValueError:
        hours = float("nan")
    if not math.isfinite(hours) or hours <= 0:
        raise RuntimeError(
            f"{ENV_SESSION_TTL_HOURS} must be a positive number of hours; "
            f"got {raw!r}"
        )
    return timedelta(hours=hours)

# scrypt parameters: N=2**14, r=8, p=1 is the RFC 7914 "interactive login"
# profile (~16 MiB of memory per hash, tens of milliseconds per attempt).
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_MAXMEM = 64 * 1024 * 1024
_SCRYPT_DKLEN = 32
_SALT_BYTES = 16

# Checked when the email is unknown, so the timing of a failed login does
# not reveal whether the account exists.
_DUMMY_PASSWORD_HASH = ""  # initialized below


def hash_password(password: str) -> str:
    """``scrypt$N$r$p$<salt hex>$<hash hex>`` for one password."""
    salt = secrets.token_bytes(_SALT_BYTES)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        maxmem=_SCRYPT_MAXMEM,
        dklen=_SCRYPT_DKLEN,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${derived.hex()}"


_DUMMY_PASSWORD_HASH = hash_password("cabinet-dummy-password-for-timing")


def check_password(password: str, stored: str) -> bool:
    """Constant-time check of a password against a stored scrypt hash."""
    try:
        scheme, n, r, p, salt_hex, hash_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=_SCRYPT_MAXMEM,
            dklen=len(bytes.fromhex(hash_hex)),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(derived.hex(), hash_hex)


def generate_password() -> str:
    """A generated password for bootstrap/user creation; printed once."""
    return secrets.token_urlsafe(12)


def sign_session_id(secret_key: str, session_id: str) -> str:
    """The cookie value for a session id: ``<id>.<HMAC-SHA256 hex>``.

    The HMAC is keyed by ``CABINET_SECRET_KEY``; the database holds only
    the bare id, so a leaked database cannot be turned into valid cookies.
    """
    signature = hmac.new(
        secret_key.encode("utf-8"), session_id.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return f"{session_id}.{signature}"


def verify_session_cookie(secret_key: str, cookie_value: str) -> str | None:
    """The session id from a cookie value, or None when the signature or
    the shape is wrong."""
    session_id, dot, signature = cookie_value.rpartition(".")
    if not dot or not session_id or not signature:
        return None
    expected = hmac.new(
        secret_key.encode("utf-8"), session_id.encode("ascii", "ignore"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    return session_id


def db_path_from_env() -> Path:
    """The configured database: CABINET_DB, else var/cabinet.db.

    A SET-BUT-EMPTY CABINET_DB is a configuration error, not an unset
    variable: silently falling back to the default would seed and serve a
    database the operator never asked for, so it raises RuntimeError (the
    startup path turns it into a one-line refusal, like a missing file).
    """
    override = os.environ.get(ENV_DB)
    if override is not None and not override.strip():
        raise RuntimeError(
            f"{ENV_DB} is set but empty; set it to a database path or "
            "unset it to use the default (var/cabinet.db); refusing to "
            "start"
        )
    return Path(override) if override else DEFAULT_DB_PATH


def resolve_secret_key(production: bool) -> tuple[str, bool]:
    """The session-signing secret, and whether it is ephemeral.

    Production fails closed: no ``CABINET_SECRET_KEY`` of at least 32 bytes
    raises ``RuntimeError`` (the caller turns it into a one-line startup
    failure). Outside production a missing key becomes an ephemeral random
    one — sessions simply do not survive a restart.
    """
    secret = os.environ.get(ENV_SECRET_KEY, "").strip()
    if secret and len(secret.encode("utf-8")) >= MIN_SECRET_KEY_BYTES:
        return secret, False
    if production:
        raise RuntimeError(
            f"{ENV_SECRET_KEY} must be set to at least {MIN_SECRET_KEY_BYTES} "
            f"bytes when {ENV_ENV}={PRODUCTION}; refusing to start"
        )
    return secrets.token_hex(32), True


def check_production_bind() -> None:
    """Fail closed on the bind address in production.

    The app binds to 127.0.0.1 by default (the Makefile does it); binding
    to anything else is allowed only when ``CABINET_BIND`` was set
    explicitly. In production an unset ``CABINET_BIND`` means the bind
    address cannot be proven, so startup is refused.
    """
    if not os.environ.get(ENV_BIND, "").strip():
        raise RuntimeError(
            f"{ENV_BIND} must be set explicitly when {ENV_ENV}={PRODUCTION} "
            "(the default bind is 127.0.0.1; set CABINET_BIND to the bind "
            "address to prove it was chosen); refusing to start"
        )


class AuthStore(CabinetStore):
    """The users/sessions handle — the whole cabinet store.

    Kept under its original name so the middleware, the CLI, and the tests
    keep their imports; all storage now lives in :class:`CabinetStore` and
    the schema is versioned by ``cabinet.migrations``.
    """

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    def verify_credentials(self, email: str, password: str) -> dict[str, Any] | None:
        """The user row when the email exists, is not disabled, and the
        password matches; None otherwise. An unknown email still pays the
        scrypt cost (against a dummy hash) so timing does not enumerate
        accounts."""
        user = self.user_by_email(email)
        stored = user["password_hash"] if user is not None else _DUMMY_PASSWORD_HASH
        if not check_password(password, stored):
            return None
        if user is None or user["disabled"]:
            return None
        return user

    def create_session(self, user_id: int) -> dict[str, Any]:
        """A new session row (TTL from ``session_ttl()``): random 256-bit
        id and CSRF token."""
        now = self._now()
        session = {
            "id": secrets.token_hex(32),
            "user_id": user_id,
            "created_at": now.isoformat(),
            "expires_at": (now + session_ttl()).isoformat(),
            "last_seen": now.isoformat(),
            "csrf_token": secrets.token_hex(32),
        }
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions (id, user_id, created_at, expires_at,"
                " last_seen, csrf_token) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    session["id"],
                    user_id,
                    session["created_at"],
                    session["expires_at"],
                    session["last_seen"],
                    session["csrf_token"],
                ),
            )
            self._conn.commit()
        return session

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """The live session row (expired sessions are treated as absent);
        ``last_seen`` is refreshed on each lookup."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return None
            session = dict(row)
            if session["expires_at"] <= self._now().isoformat():
                return None
            self._conn.execute(
                "UPDATE sessions SET last_seen = ? WHERE id = ?",
                (self._now().isoformat(), session_id),
            )
            self._conn.commit()
        return session

    def delete_session(self, session_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self._conn.commit()
