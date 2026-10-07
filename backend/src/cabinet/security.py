"""HTTP security layer: session auth, roles, CSRF, rate limits, and
security headers, enforced in one middleware so no route can forget them.

Every route except ``GET /health``, ``GET /ready``, and ``POST /auth/login``
requires a logged-in user; each route also names the user roles that may
call it. Not logged in is 401, the wrong role is 403; both are logged as
``data.refused`` audit events (no new event types).

The same process also serves the built UI (``cabinet.webui``). The
UI shell is public — it is the same bytes for everyone and carries no data
— so a GET/HEAD whose path is not a known API route passes through to the
static mount (or a 404). Every route in the tables below, and every
state-changing method on any path, is guarded exactly as before: unknown
API paths are unreachable without a session, and a typo'd GET answers the
HTML shell, never data.

Controls, in the order a request meets them (the first three apply to the
public routes too — ``GET /health``, ``GET /ready``, ``POST /auth/login`` —
so a no-session caller is still capped and rate-limited):

1. **Rate limit (per client IP)** — in-process token bucket (default 600
   requests/min, generous because a whole campus can share one address;
   each signed-in session has its own tighter bucket, default 120/min, in
   step 6). Over the limit is 429 with ``Retry-After`` and the plain
   message ``RATE_LIMIT_MESSAGE``. The buckets
   are per process: behind more than one worker or replica, limits must
   move to the proxy (see docs/SECURITY.md). Keys idle longer than the
   eviction horizon are dropped, so the bucket dict cannot grow without
   bound.
2. **Content type** — POSTs to JSON routes must be ``application/json``
   (415 otherwise). A header-only check; no body is read.
3. **Body cap (public routes)** — POST bodies over 256 KB are 413. The cap
   is enforced on the bytes actually read (the body is consumed with a
   running total in this middleware and the capped bytes are handed
   downstream), so a chunked body without a Content-Length cannot bypass
   it.
4. **Session** — the signed ``cabinet_session`` cookie (see
   ``cabinet.auth``); missing, forged, expired, or disabled is 401.
5. **CSRF** — every state-changing request (POST, PUT, PATCH, DELETE)
   must carry ``X-CSRF-Token`` equal to the session's token (403
   otherwise; ``GET /auth/me`` hands the token to the UI). In production
   the Origin/Referer host is checked against the request host as a
   second layer. CSRF needs no body, so it runs before the body is read.
6. **Rate limit (per session)** — a separate bucket per signed-in
   session (default 120 requests/min, ``CABINET_RATE_SESSION_PER_MIN``),
   plus the tighter consequential-action bucket (default 5/min) on
   ``POST /ask`` (it spends model calls) and on the dispatch Send route
   (it can make a message leave the machine).
7. **Role** — per-route role table below; 403 when the user's role is
   not listed.
8. **Body cap (guarded routes)** — only once the caller is authenticated
   AND authorized is the body read (an unauthenticated or wrong-role
   caller's body is never buffered, so a pre-auth POST cannot make the
   server allocate the route's cap). The data-upload route carries a
   whole dataset document, so ``/admin/datasets`` has its own 20 MB cap
   (``BODY_CAP_OVERRIDES`` below) — reachable only past the admin role
   check; every other route keeps the default 256 KB.

Headers on every response: Content-Security-Policy, X-Content-Type-Options,
Referrer-Policy, Permissions-Policy, HSTS in production, and
``Cache-Control: no-store`` on API JSON.

The CSP needs no ``'unsafe-inline'`` for styles: this API serves only JSON
(the UI is served separately by Vite, and ``ui/src`` uses no inline
styles — verified by grep), so ``style-src 'self'`` suffices.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import os
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from cabinet.auth import (
    COOKIE_NAME,
    CSRF_HEADER,
    ROLE_ADMIN,
    ROLE_AID,
    ROLE_EXECUTIVE,
    ROLE_REVIEWER,
    ROLE_STAFF,
    AuthStore,
    verify_session_cookie,
)

MAX_BODY_BYTES = 256 * 1024
UPLOAD_BODY_BYTES = 20 * 1024 * 1024

# Routes whose bodies legitimately exceed the default cap: the dataset
# upload carries a whole dataset document (up to 20 MB). The first prefix
# that matches the path wins; anything else gets MAX_BODY_BYTES. The
# override is consulted only AFTER the session and role checks, so the 20 MB
# allowance exists exclusively for an authenticated admin — a pre-auth
# caller is refused (401/403) without its body being read at all.
BODY_CAP_OVERRIDES: tuple[tuple[str, int], ...] = (
    ("/admin/datasets", UPLOAD_BODY_BYTES),
)

ALL_ROLES = (ROLE_ADMIN, ROLE_EXECUTIVE, ROLE_STAFF, ROLE_REVIEWER, ROLE_AID)
READ_ROLES = ALL_ROLES  # every logged-in role may read
ACT_ROLES = (ROLE_ADMIN, ROLE_EXECUTIVE)  # ask / approve / refresh
# The student ids behind a finding (GET /findings row lists, M5's per-office
# holds, M8's per-student indicators): the executive and admin, whose work
# acts on the records. Every other role reads the figures and counts only.
ROW_ROLES = (ROLE_ADMIN, ROLE_EXECUTIVE)
# The audit log itself: admin and reviewer, and the executive (the
# president runs the Beat 6 audit walkthrough; staff still may not).
AUDIT_ROLES = (ROLE_ADMIN, ROLE_EXECUTIVE, ROLE_REVIEWER)
# The Financial Aid review queue holds per-student rows, so it is narrower
# than READ_ROLES: the aid office works it, the admin manages it, and the
# executive and reviewer may watch it. Staff may create it from the decision
# panel but never read the rows.
AID_QUEUE_READ_ROLES = (ROLE_AID, ROLE_ADMIN, ROLE_EXECUTIVE, ROLE_REVIEWER)
AID_QUEUE_EDIT_ROLES = (ROLE_AID, ROLE_ADMIN)

# (method, path) -> roles allowed. Anything not listed here (and not
# public) is denied to every role: the table is the allow-list.
ROUTE_ROLES: dict[tuple[str, str], tuple[str, ...]] = {
    ("GET", "/questions"): READ_ROLES,
    ("GET", "/findings"): READ_ROLES,
    ("GET", "/events"): AUDIT_ROLES,
    ("GET", "/briefing"): READ_ROLES,
    ("GET", "/briefing/enrollment"): READ_ROLES,
    ("GET", "/briefing/student-success"): READ_ROLES,
    ("GET", "/decisions"): READ_ROLES,
    ("GET", "/auth/me"): ALL_ROLES,
    ("POST", "/auth/logout"): ALL_ROLES,
    ("POST", "/ask"): ACT_ROLES,
    ("POST", "/briefing/enrollment/refresh"): ACT_ROLES,
    ("POST", "/briefing/student-success/refresh"): ACT_ROLES,
    ("POST", "/governance/request"): ACT_ROLES,
    ("POST", "/decisions/approve"): ACT_ROLES,
    ("GET", "/aid-queue"): AID_QUEUE_READ_ROLES,
    # The dispatch address book: the institution's admin manages the
    # office mailboxes messages may be sent to (never a student address).
    ("GET", "/admin/offices"): (ROLE_ADMIN,),
    ("PUT", "/admin/offices"): (ROLE_ADMIN,),
    # The counseling aggregate authorization: the admin records or revokes
    # what the counseling director authorized in writing.
    ("GET", "/admin/institution/counseling-authorization"): (ROLE_ADMIN,),
    ("PUT", "/admin/institution/counseling-authorization"): (ROLE_ADMIN,),
    # Institution admins manage their own institution's users; the
    # institution always comes from the session, never from the client.
    ("GET", "/admin/users"): (ROLE_ADMIN,),
    ("POST", "/admin/users"): (ROLE_ADMIN,),
    # Explore (cabinet.explore): aggregate questions over the school data;
    # every role but aid.
    ("POST", "/explore"): (ROLE_ADMIN, ROLE_EXECUTIVE, ROLE_STAFF, ROLE_REVIEWER),
    ("POST", "/explore/stream"): (
        ROLE_ADMIN,
        ROLE_EXECUTIVE,
        ROLE_STAFF,
        ROLE_REVIEWER,
    ),
    ("GET", "/explore/catalog"): AUDIT_ROLES + (ROLE_STAFF,),
    # The staff action worklist (cabinet.staffactions_api): every role reads
    # it (the aid role sees Financial Aid's actions only, filtered by the
    # route).
    ("GET", "/staff-actions"): READ_ROLES,
    # The outside connections (Ellucian import, outgoing mail): whether each
    # is configured, never a credential. Admin only.
    ("GET", "/admin/connections"): (ROLE_ADMIN,),
    # The demonstration student directory (cabinet.roster): a name search
    # that returns named student records, so only the roles that may open
    # the records behind a figure. Every search is logged.
    ("GET", "/students/search"): ROW_ROLES,
    # Support programs (cabinet.outreach): the aggregate page is open to
    # every role.
    ("GET", "/interventions"): READ_ROLES,
}

# Prefix rules, checked when the exact table misses (routes with path
# parameters). Institution admins manage their own institution's
# datasets; the institution always comes from the session, never from the
# client. Same for their institution's users.
ROUTE_ROLE_PREFIXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("GET", "/admin/datasets", (ROLE_ADMIN,)),
    ("POST", "/admin/datasets", (ROLE_ADMIN,)),
    ("DELETE", "/admin/datasets", (ROLE_ADMIN,)),
    ("POST", "/admin/users", (ROLE_ADMIN,)),
    ("PATCH", "/admin/users", (ROLE_ADMIN,)),
    # The dispatch routes carry the decision id in the path. Reading the
    # draft is open to every role (the reviewer watches governance); both
    # composing and sending are POSTs, and the send route itself narrows
    # this table to staff and admin — an executive's Send is a loud 403
    # with a data.refused event, not a silent drop.
    ("GET", "/decisions/", READ_ROLES),
    ("POST", "/decisions/", (ROLE_ADMIN, ROLE_EXECUTIVE, ROLE_STAFF)),
    # One Financial Aid review row by id: the office records its own status
    # and note. The aid role and the admin only; everyone else is a 403.
    ("PATCH", "/aid-queue/", AID_QUEUE_EDIT_ROLES),
    # One staff action by id: staff and the admin set its status, owner and
    # due date; notes are open to the executive too, and the send route
    # narrows itself to staff and admin (an executive's Send is a logged
    # 403, like the decision dispatch).
    ("PATCH", "/staff-actions/", (ROLE_ADMIN, ROLE_STAFF)),
    ("POST", "/staff-actions/", (ROLE_ADMIN, ROLE_EXECUTIVE, ROLE_STAFF)),
    # Support-program outreach lists name students: the executive and admin
    # (ROW_ROLES) and the aid role, which the route narrows to the program
    # its office runs (the theology funding bridge). Approving is the
    # executive's or admin's; the decision route narrows itself.
    ("POST", "/interventions/", ROW_ROLES + (ROLE_AID,)),
    ("GET", "/outreach/", ROW_ROLES + (ROLE_AID,)),
    ("POST", "/outreach/", ROW_ROLES + (ROLE_AID,)),
    ("PATCH", "/outreach/", ROW_ROLES + (ROLE_AID,)),
)

# No session needed: liveness, readiness, and login itself.
PUBLIC_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/health"),
        ("GET", "/ready"),
        ("POST", "/auth/login"),
    }
)


def is_api_route(method: str, path: str) -> bool:
    """True when (method, path) is a known API route or a state-changing
    request — i.e. anything the guard below must handle. A GET/HEAD outside
    the tables is a static-UI request and passes through; every known
    route and every POST/PUT/PATCH/DELETE anywhere is guarded."""
    if method not in ("GET", "HEAD"):
        return True
    if (method, path) in ROUTE_ROLES or (method, path) in PUBLIC_ROUTES:
        return True
    return any(
        method == prefix_method and path.startswith(prefix)
        for prefix_method, prefix, _ in ROUTE_ROLE_PREFIXES
    )


ENV_RATE_GENERAL = "CABINET_RATE_GENERAL_PER_MIN"
ENV_RATE_SESSION = "CABINET_RATE_SESSION_PER_MIN"
ENV_RATE_ASK = "CABINET_RATE_ASK_PER_MIN"
# Per client address. Everyone behind one campus address shares it, so it
# is generous; the per-session bucket below is what paces one person.
DEFAULT_RATE_GENERAL_PER_MIN = 600
DEFAULT_RATE_SESSION_PER_MIN = 120
DEFAULT_RATE_ASK_PER_MIN = 5

# The body of every rate-limit 429: plain words a person can act on. The
# Retry-After header carries the seconds.
RATE_LIMIT_MESSAGE = "CampusLens is busy. Wait a minute and try again."

GENERIC_LOGIN_ERROR = "invalid email or password"

# Login throttling: a HARD lockout (429) after 5 failed attempts per
# 15 minutes per IP and per IP+email pair — never per bare email, or anyone
# could lock a specific account out by failing logins against it. The bare
# email instead earns a progressive delay that never hard-blocks.
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_DELAY_CAP_SECONDS = 30
# Stale keys are swept at most this often (seconds of the monotonic clock),
# and a key idle longer than this is evicted, so the dicts stay bounded.
_SWEEP_INTERVAL_SECONDS = 60
_BUCKET_IDLE_EVICT_SECONDS = 15 * 60


async def _sleep(seconds: float) -> None:
    """The delay hook — module-level so tests can monkeypatch it away.
    Async: a progressive login delay must not hold a threadpool worker."""
    await asyncio.sleep(seconds)


CSP = (
    "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
    "style-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)
PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=()"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


class TokenBucket:
    """A simple in-process token bucket: ``rate_per_min`` tokens refill
    continuously; one request costs one token. Keys idle longer than the
    eviction horizon are swept (periodically, not per call), so the dict
    cannot grow without bound."""

    def __init__(
        self,
        rate_per_min: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        idle_evict_seconds: float = _BUCKET_IDLE_EVICT_SECONDS,
    ) -> None:
        self.rate_per_sec = rate_per_min / 60.0
        self.capacity = float(rate_per_min)
        self.idle_evict_seconds = idle_evict_seconds
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, at)
        self._clock = clock
        self._lock = threading.Lock()
        self._last_sweep: float | None = None

    def _sweep_locked(self, now: float) -> None:
        if (
            self._last_sweep is not None
            and now - self._last_sweep < _SWEEP_INTERVAL_SECONDS
        ):
            return
        self._last_sweep = now
        cutoff = now - self.idle_evict_seconds
        self._buckets = {
            key: state for key, state in self._buckets.items() if state[1] >= cutoff
        }

    def allow(self, key: str) -> tuple[bool, int]:
        """Consume one token for ``key``. Returns (allowed, retry_after_s)."""
        now = self._clock()
        with self._lock:
            self._sweep_locked(now)
            tokens, at = self._buckets.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - at) * self.rate_per_sec)
            if tokens >= 1.0:
                self._buckets[key] = (tokens - 1.0, now)
                return True, 0
            self._buckets[key] = (tokens, now)
            retry_after = max(1, int((1.0 - tokens) / self.rate_per_sec) + 1)
            return False, retry_after

    def key_count(self) -> int:
        """How many keys the bucket currently tracks (for tests)."""
        with self._lock:
            return len(self._buckets)


class LoginLockout:
    """Failed-login throttle.

    Hard lockout (429) applies per IP and per IP+email pair — never to the
    bare email, otherwise anyone could lock a specific account out for 15
    minutes by failing logins against it. The bare email instead earns a
    progressive delay before the next attempt (1, 2, 4, 8 s, capped at 30 s)
    that slows a distributed guessing campaign without ever denying the
    account's owner. A successful login clears the email's counter. Keys
    whose failures have all aged out of the window are evicted.
    """

    def __init__(
        self,
        max_attempts: int = LOGIN_MAX_ATTEMPTS,
        window_seconds: int = LOGIN_WINDOW_SECONDS,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._clock = clock
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self._last_sweep: float | None = None

    def _recent(self, key: str, now: float) -> list[float]:
        attempts = [
            at for at in self._failures.get(key, []) if now - at < self.window_seconds
        ]
        if attempts:
            self._failures[key] = attempts
        else:
            self._failures.pop(key, None)
        return attempts

    def _sweep_locked(self, now: float) -> None:
        if (
            self._last_sweep is not None
            and now - self._last_sweep < _SWEEP_INTERVAL_SECONDS
        ):
            return
        self._last_sweep = now
        for key in list(self._failures):
            self._recent(key, now)

    def blocked(self, *keys: str) -> int:
        """Seconds until the oldest relevant failure ages out, or 0 when
        none of the keys is over the limit. Only ever called with the hard
        keys (per IP, per IP+email) — the bare email never hard-blocks."""
        now = self._clock()
        with self._lock:
            self._sweep_locked(now)
            wait = 0
            for key in keys:
                attempts = self._recent(key, now)
                if len(attempts) >= self.max_attempts:
                    wait = max(wait, int(self.window_seconds - (now - attempts[0])) + 1)
            return wait

    def email_delay_seconds(self, email_key: str) -> int:
        """The progressive delay the next attempt for this email pays:
        1, 2, 4, 8 s by recent failure count, capped at
        ``LOGIN_DELAY_CAP_SECONDS``. Never a hard block."""
        now = self._clock()
        with self._lock:
            count = len(self._recent(email_key, now))
        if count == 0:
            return 0
        return min(LOGIN_DELAY_CAP_SECONDS, 1 << (count - 1))

    async def apply_email_delay(self, email_key: str) -> int:
        """Sleep the progressive delay for this email; returns the delay
        applied (0 when there was none). Async — awaited from the login
        route so the delay never pins a threadpool worker."""
        delay = self.email_delay_seconds(email_key)
        if delay:
            await _sleep(delay)
        return delay

    def record_failure(self, *keys: str) -> None:
        now = self._clock()
        with self._lock:
            self._sweep_locked(now)
            for key in keys:
                attempts = self._recent(key, now)
                attempts.append(now)
                self._failures[key] = attempts

    def record_success(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._failures.pop(key, None)

    def key_count(self) -> int:
        """How many keys the lockout currently tracks (for tests)."""
        with self._lock:
            return len(self._failures)


class CabinetSecurityMiddleware(BaseHTTPMiddleware):
    """The one security chokepoint; see the module docstring."""

    def __init__(
        self,
        app: Any,
        *,
        auth: AuthStore,
        secret_key: str,
        production: bool,
        rate_general_per_min: int | None = None,
        rate_session_per_min: int | None = None,
        rate_ask_per_min: int | None = None,
    ) -> None:
        super().__init__(app)
        self.auth = auth
        self.secret_key = secret_key
        self.production = production
        self.general_bucket = TokenBucket(
            rate_general_per_min
            if rate_general_per_min is not None
            else _env_int(ENV_RATE_GENERAL, DEFAULT_RATE_GENERAL_PER_MIN)
        )
        self.session_bucket = TokenBucket(
            rate_session_per_min
            if rate_session_per_min is not None
            else _env_int(ENV_RATE_SESSION, DEFAULT_RATE_SESSION_PER_MIN)
        )
        self.ask_bucket = TokenBucket(
            rate_ask_per_min
            if rate_ask_per_min is not None
            else _env_int(ENV_RATE_ASK, DEFAULT_RATE_ASK_PER_MIN)
        )

    # -- helpers ------------------------------------------------------------

    async def _refuse(
        self,
        status_code: int,
        detail: str,
        request: Request,
        *,
        actor: str,
        reason: str,
        institution_id: int = 0,
        headers: dict[str, str] | None = None,
    ) -> JSONResponse:
        """A 401/403/429 that is also a ``data.refused`` audit event.

        Events land on the caller's institution chain once a user is known;
        pre-auth refusals go to the platform scope (0). The append takes
        the store lock, so it runs in the threadpool — synchronously on
        the loop it would stall every request behind a slow store write.
        """
        await run_in_threadpool(
            self.auth.audit_append,
            institution_id,
            "data.refused",
            actor=actor,
            payload={
                "reason": reason,
                "method": request.method,
                "path": request.url.path,
            },
        )
        return JSONResponse(
            status_code=status_code, content={"detail": detail}, headers=headers
        )

    @staticmethod
    def _client_ip(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def _origin_host_mismatch(self, request: Request) -> bool:
        """Production second layer: an Origin or Referer whose host part is
        not the request's own host means a cross-site request."""
        host = request.headers.get("host", "")
        for header in ("origin", "referer"):
            value = request.headers.get(header)
            if not value:
                continue
            netloc = value.split("//", 1)[-1].split("/", 1)[0]
            if netloc and netloc.lower() != host.lower():
                return True
        return False

    # -- the pipeline ---------------------------------------------------------

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await self._dispatch(request, call_next)
        self._add_security_headers(response)
        return response

    @staticmethod
    async def _read_capped_body(request: Request, cap: int) -> bytes | None:
        """Consume the request body with a running total; None once it
        exceeds the cap. The cap is enforced on the bytes actually read, so
        a chunked body (no Content-Length to pre-check) cannot bypass it."""
        received = 0
        chunks: list[bytes] = []
        async for chunk in request.stream():
            received += len(chunk)
            if received > cap:
                return None
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _body_cap_response(cap: int) -> JSONResponse:
        return JSONResponse(
            status_code=413,
            content={"detail": f"request body exceeds the {cap}-byte cap"},
        )

    async def _dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        method = request.method
        path = request.url.path
        public = (method, path) in PUBLIC_ROUTES

        # The built UI shell is public (the same bytes for everyone, no
        # data). A GET/HEAD outside the API route tables passes through to
        # the static mount; every table route, every public API route, and
        # every state-changing request continues through the guard below.
        if not public and not is_api_route(method, path):
            return await call_next(request)

        # 1. Per-IP general rate limit — every guarded route, public ones
        # included (bounds audit-log spam too, since it runs before any
        # data.refused is written).
        client_ip = self._client_ip(request)
        allowed, retry_after = self.general_bucket.allow(f"ip:{client_ip}")
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": RATE_LIMIT_MESSAGE},
                headers={"Retry-After": str(retry_after)},
            )

        # 2. Every POST route here takes JSON; a request that carries a body
        # must declare it JSON (bodyless POSTs — refresh, logout — are fine).
        has_body = request.headers.get("transfer-encoding") is not None
        content_length = request.headers.get("content-length")
        declared_length: int | None = None
        if content_length is not None:
            with contextlib.suppress(ValueError):
                declared_length = int(content_length)
            if declared_length:
                has_body = True
        if method == "POST":
            content_type = request.headers.get("content-type", "").split(";")[0].strip()
            if has_body and content_type.lower() != "application/json":
                return JSONResponse(
                    status_code=415,
                    content={"detail": "expected Content-Type: application/json"},
                )

        # 3. Body cap (default 256 KB; /admin/datasets carries a dataset
        # document and has its own 20 MB cap). A declared Content-Length over
        # the cap is refused up front; otherwise the body is consumed here
        # with a running total and the capped bytes are handed downstream, so
        # a chunked body cannot bypass the cap either. On PUBLIC routes this
        # happens now; on guarded routes it happens after the session, CSRF,
        # and role checks (step 8 below), so a pre-auth caller's body is
        # never buffered and the 20 MB upload allowance exists only for an
        # authenticated admin.
        cap = MAX_BODY_BYTES
        for prefix, override in BODY_CAP_OVERRIDES:
            if path.startswith(prefix):
                cap = override
                break

        async def read_capped() -> JSONResponse | None:
            """The cap enforcement; None when the body passed and was
            cached on the request for the route downstream."""
            if declared_length is not None and declared_length > cap:
                return self._body_cap_response(cap)
            if has_body:
                body = await self._read_capped_body(request, cap)
                if body is None:
                    return self._body_cap_response(cap)
                # Cache the consumed body on the request: Starlette's
                # BaseHTTPMiddleware hands the cached bytes downstream.
                request._body = body
            return None

        if public:
            capped = await read_capped()
            if capped is not None:
                return capped
            return await call_next(request)

        # 4. Session. The store calls take the store lock, so they run in
        # the threadpool — on the loop they would stall every request
        # behind a slow store write (an upload's commit, say).
        cookie = request.cookies.get(COOKIE_NAME, "")
        session_id = verify_session_cookie(self.secret_key, cookie) if cookie else None
        session = (
            await run_in_threadpool(self.auth.get_session, session_id)
            if session_id
            else None
        )
        user = (
            await run_in_threadpool(self.auth.user_by_id, session["user_id"])
            if session
            else None
        )
        if session is None or user is None or user["disabled"]:
            return await self._refuse(
                401,
                "authentication required",
                request,
                actor="anonymous",
                reason="no valid session (missing, forged, expired, or disabled)",
            )
        request.scope["cabinet_user"] = user
        request.scope["cabinet_session_row"] = session
        actor = str(user["id"])

        # 5. CSRF on every state-changing request (POST/PUT/PATCH/DELETE).
        if method in ("POST", "PUT", "PATCH", "DELETE"):
            token = request.headers.get(CSRF_HEADER, "")
            if not token or not hmac.compare_digest(token, session["csrf_token"]):
                return await self._refuse(
                    403,
                    "missing or invalid CSRF token",
                    request,
                    actor=actor,
                    institution_id=int(user["institution_id"]),
                    reason="CSRF token missing or does not match the session",
                )
            if self.production and self._origin_host_mismatch(request):
                return await self._refuse(
                    403,
                    "cross-site request refused",
                    request,
                    actor=actor,
                    institution_id=int(user["institution_id"]),
                    reason="Origin/Referer host does not match the request host",
                )

        # 6. Per-session rate limits.
        allowed, retry_after = self.session_bucket.allow(f"session:{session['id']}")
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": RATE_LIMIT_MESSAGE},
                headers={"Retry-After": str(retry_after)},
            )
        # The tighter ask bucket covers POST /ask (it spends model calls)
        # and both Sends (each can make a message leave the machine);
        # both are consequential enough to pace per session and per IP.
        if (method, path) in (
            ("POST", "/ask"),
            ("POST", "/explore"),
            ("POST", "/explore/stream"),
        ) or (
            # Both Sends (a decision's dispatch, a staff action) end in /send.
            method == "POST" and path.endswith("/send")
        ):
            for key in (f"ask:session:{session['id']}", f"ask:ip:{client_ip}"):
                allowed, retry_after = self.ask_bucket.allow(key)
                if not allowed:
                    return JSONResponse(
                        status_code=429,
                        content={"detail": RATE_LIMIT_MESSAGE},
                        headers={"Retry-After": str(retry_after)},
                    )

        # 7. Role. Exact table first, then the prefix rules (routes with
        # path parameters, e.g. /admin/datasets/{id}).
        allowed_roles = ROUTE_ROLES.get((method, path))
        if allowed_roles is None:
            for prefix_method, prefix, prefix_roles in ROUTE_ROLE_PREFIXES:
                if method == prefix_method and path.startswith(prefix):
                    allowed_roles = prefix_roles
                    break
        if allowed_roles is None or user["role"] not in allowed_roles:
            return await self._refuse(
                403,
                "this role may not call this route",
                request,
                actor=actor,
                institution_id=int(user["institution_id"]),
                reason=f"role {user['role']!r} is not allowed on {method} {path}",
            )

        # 8. Body cap for guarded routes — only now, with an authenticated,
        # authorized caller, is the body read.
        capped = await read_capped()
        if capped is not None:
            return capped

        return await call_next(request)

    def _add_security_headers(self, response: Response) -> None:
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
        if self.production:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        content_type = response.headers.get("content-type", "")
        if content_type.startswith("application/json"):
            response.headers.setdefault("Cache-Control", "no-store")
