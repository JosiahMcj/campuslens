"""The governance API.

Endpoints:

- ``GET  /health`` — liveness.
- ``GET  /questions`` — the approved executive questions (the registry in
  ``cabinet.questions``): ``[{id, text}]``.
- ``GET  /findings`` — the full findings object (executive view, row IDs
  included for the evidence drawer).
- ``GET  /events`` — the audit log in order, ``?type=`` filters.
- ``POST /ask`` — accepts only an approved question; anything else is
  refused with a sentence naming the approved questions and a
  ``data.refused`` event. An accepted question runs the whole cabinet:
  the Chief of Staff dispatches the two analyst tasks (``task.assigned`` +
  ``data.granted`` each), both analysts run through the shared runner/cache
  on the question's dispatch, the Chief of Staff is assigned and granted the
  question's aggregates plus the analysts' validated texts, writes sections
  1 and 7, and the API logs ``briefing.produced`` once and returns the full
  seven-section briefing. If an analyst is unavailable the briefing still
  returns with that section marked unavailable and the Chief of Staff run
  skipped.
- ``GET  /briefing`` — the caller's institution's most recent produced
  briefing FOR ITS ACTIVE DATASET (from the store; a restart loses
  nothing), with the question it answers, or 404 with a plain reason when
  there is none yet — including right after a new dataset is activated,
  which retires the previous dataset's briefing and its approvals from view
  until the next ask (briefings are keyed per dataset: re-activating an
  earlier dataset serves its own briefing again).
- ``POST /governance/request`` — demo endpoint for the §5 field-request gate.
  An unknown role or an empty field list is a client error (422, no audit
  event); a request outside the role is refused through the gate with a
  ``data.refused`` event.
- ``GET  /decisions`` — the leadership decision for the latest question
  asked (the registry default before anything is asked), with approval
  state; the text is built from the findings at request time.
- ``POST /decisions/approve`` — creates one simulated follow-up task,
  idempotent per decision id (any approved question's decision id is
  accepted), restart-safe via the ``decisions`` table.
- ``GET  /decisions/{id}/dispatch`` — the dispatch state for one decision
  (approved?, office mailbox configured?, the draft or sent record); every
  logged-in role may read it.
- ``POST /decisions/{id}/dispatch`` — composes the draft message to the
  responsible office from the approved task's findings (code, never the
  model; staff, executive, admin). Idempotent per task and dataset; 409
  when the decision is not approved. Logs ``task.dispatched``.
- ``POST /decisions/{id}/dispatch/send`` — sends the draft through the
  configured outbound provider (staff and admin only; an executive's send
  is a loud 403). The provider is the on-machine outbox by default
  (``var/outbox/<institution>/<dispatch id>.eml``); ``CABINET_OUTBOUND=smtp``
  delivers for real and refuses to start in production when its settings
  are incomplete. A sent dispatch is never resent (409 with the earlier
  record). Logs ``task.sent`` with provider and reference.
- ``GET  /admin/offices`` / ``PUT /admin/offices`` (admin role) — the
  institution's office address book, the only source of dispatch
  recipients. Offices, never student addresses.
- ``GET  /admin/datasets`` / ``POST /admin/datasets`` /
  ``POST /admin/datasets/{id}/activate`` / ``DELETE /admin/datasets/{id}``
  (admin role) — the caller's institution's datasets: upload (strict
  SCHEMA.md validation before anything is stored), activate (recomputes the
  findings), soft-delete (purged after the retention window).
- ``GET  /admin/users`` / ``POST /admin/users`` /
  ``POST /admin/users/{id}/disable`` / ``POST /admin/users/{id}/enable`` /
  ``PATCH /admin/users/{id}`` (admin role) — the caller's institution's
  users. Creation answers 201 with a generated one-time password (the same
  code path as `make user`; the response is the only place it ever
  appears). Disable/enable are idempotent; an admin cannot disable their
  own account, and the institution's last enabled admin can be neither
  disabled nor demoted. Every change is one ``admin.changed`` audit event
  whose payload never carries the password.
- ``GET  /briefing/enrollment`` — the Enrollment Analyst's validated
  explanation of M1, M2, M7. 503 with
  ``{available: false, reason}`` when the provider cannot answer.
- ``GET  /briefing/student-success`` — the Student Success Analyst's
  validated explanation of M3, M4, M5, same shape and
  503 behaviour. For both routes the validated result is cached in-process
  keyed by (role, provider, model label, findings hash); a cache hit writes
  no audit events. ``?refresh=1`` or ``POST /briefing/<role>/refresh``
  forces a new run. All four analyst routes run under the same PER-
  INSTITUTION lock as ``POST /ask``: while that institution's question is
  being answered they answer 409 with ``{available: false, reason: "a
  question is being answered"}`` instead of interleaving with the run; one
  institution's run never blocks another's.

Security: every route except ``GET /health``, ``GET /ready``, and
``POST /auth/login`` requires a logged-in user with the right role — the
table lives in ``cabinet.security`` — with server-side signed sessions
(``cabinet.auth``), a CSRF token on every POST, per-IP and per-session rate
limits, security headers on every response, and a 256 KB request-body cap
(20 MB on ``/admin/datasets``). With ``CABINET_ENV=production`` startup
fails closed: no ``CABINET_SECRET_KEY`` (32+ bytes) or no explicit
``CABINET_BIND`` is a one-line startup refusal. The OpenAPI docs routes are
off: every route requires a session, and the schema is not public.

Tenancy: every route resolves the institution from the session's
user — no route takes an institution id from the client. Data, findings,
briefings, decisions, recordings, and audit events are all scoped by
institution in ``var/cabinet.db`` (``cabinet.store``); dataset documents
are files under ``var/data/<institution slug>/<dataset id>.json``. Admin
routes under ``/admin/datasets`` manage the caller's own institution's
datasets (upload with strict SCHEMA.md validation, activate, soft-delete);
a dataset id from another institution is a 404, never a 403, so existence
does not leak. Every new institution is seeded with the fictional
demonstration dataset (``meta.fictional: true`` in every data-bearing
response). Startup refuses on a database whose schema version this build
does not know (``cabinet.migrations``), and on a malformed seed fixture
with one clear line on stderr, not a traceback.

Production serving: the same process also serves the built UI from
``CABINET_UI_DIST`` (default ``ui/dist``, written by ``make build``) with
SPA fallback and deploy cache headers, and strips the ``/api`` prefix the
UI client sends — both in ``cabinet.webui``. ``GET /ready`` checks the
dependencies (database, migrations, secret, built UI, golden replay) and
answers 503 with reasons. In production every request logs one JSON line
on stdout with a request id (``cabinet.accesslog``).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from cabinet.accesslog import RequestLogMiddleware
from cabinet.analysts import (
    CHIEF_OF_STAFF,
    ENROLLMENT_ANALYST,
    STUDENT_SUCCESS_ANALYST,
    chief_aggregate_fields,
    chief_received,
    run_analyst,
    run_chief_of_staff,
)
from cabinet.audit import EVENT_TYPES
from cabinet.auth import (
    COOKIE_NAME,
    ENV_ENV,
    PRODUCTION,
    ROLE_ADMIN,
    ROLE_STAFF,
    USER_ROLES,
    AuthStore,
    check_production_bind,
    db_path_from_env,
    generate_password,
    resolve_secret_key,
    session_ttl,
    sign_session_id,
)
from cabinet.datasets import UploadError, validate_upload
from cabinet.fixture import parse_fixture
from cabinet.metrics import findings as compute_findings
from cabinet.migrations import (
    BOOTSTRAP_SLUG,
    MIGRATIONS,
    SchemaVersionError,
    recorded_versions,
)
from cabinet.outbound import OutboundError, outbound_from_env
from cabinet.permissions import (
    ROLE_FINDINGS,
    ROLE_TASK_FIELDS,
    ROLES,
    FieldRequestRefused,
    request_fields,
)
from cabinet.provider import (
    Explanation,
    canonical_findings_json,
    golden_dir_from_env,
    load_local_env,
    provider_from_env,
    recording_payload,
)
from cabinet.questions import (
    DEFAULT_QUESTION,
    QUESTIONS,
    Question,
    find_decision,
    match_question,
    received_for,
)
from cabinet.questions import DEMO_DECISION_ID as DEMO_DECISION_ID
from cabinet.questions import OUT_OF_SCOPE_REFUSAL as OUT_OF_SCOPE_REFUSAL
from cabinet.security import (
    GENERIC_LOGIN_ERROR,
    CabinetSecurityMiddleware,
    LoginLockout,
)
from cabinet.store import CabinetStore, StoreError
from cabinet.webui import ApiPrefixMiddleware, SpaStaticFiles, ui_dist_from_env

ENV_FIXTURE = "CABINET_FIXTURE"
DEFAULT_FIXTURE_PATH = Path(__file__).resolve().parents[3] / "data" / "fixture.json"

# The original approved question (Q1); the full registry lives in
# cabinet.questions and GET /questions lists it.
APPROVED_QUESTION = DEFAULT_QUESTION.text

ANALYST_ROLES = ("enrollment_analyst", "student_success_analyst")

# Backward-compatible aliases for Q1's section builders (the registry owns
# them now; per-question sections are built by the asked question).
build_decisions = DEFAULT_QUESTION.build_decisions
build_actions = DEFAULT_QUESTION.build_actions

SIMULATED_STATUS = "simulated, nothing sent"


class AskRequest(BaseModel):
    question: str


class ApproveRequest(BaseModel):
    decision_id: str


class GovernanceRequest(BaseModel):
    role: str
    fields: list[str]
    task_id: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


class AdminUserCreateRequest(BaseModel):
    email: str
    role: str


class AdminUserRoleRequest(BaseModel):
    role: str


class OfficeContactEntry(BaseModel):
    office: str
    email: str


class OfficesPutRequest(BaseModel):
    offices: list[OfficeContactEntry]


# Office mailboxes: a deliberately simple shape check (local@domain.tld).
# The address book holds office mailboxes only, never student addresses.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _user_body(store: CabinetStore, user: dict[str, Any]) -> dict[str, Any]:
    """The user object the API returns, with its institution."""
    institution = store.institution_by_id(int(user["institution_id"]))
    return {
        "id": user["id"],
        "email": user["email"],
        "role": user["role"],
        "institution_id": user["institution_id"],
        "institution": (
            {"slug": institution["slug"], "name": institution["name"]}
            if institution is not None
            else None
        ),
    }


def is_approved_question(question: str) -> bool:
    """True when ``question`` matches an approved question (normalized,
    case-insensitive, trailing '?' optional)."""
    return match_question(question) is not None


def fixture_path_from_env() -> Path:
    """The configured fixture path: CABINET_FIXTURE, else data/fixture.json."""
    override = os.environ.get(ENV_FIXTURE)
    return Path(override) if override else DEFAULT_FIXTURE_PATH


class InstitutionRuntime:
    """One institution's in-process state: its active dataset's fixture and
    findings, its briefing caches, and the last question asked.

    Built lazily from the store and rebuilt after a dataset is activated
    (``runtimes.pop`` below). Caches are keyed by institution + dataset sha
    + provider + model label + the sha256 of the received findings (which
    covers the dataset content and, for non-default questions, the
    question itself).
    """

    def __init__(self, store: CabinetStore, institution_id: int) -> None:
        dataset = store.active_dataset(institution_id)
        if dataset is None:
            raise StoreError(f"institution {institution_id} has no active dataset")
        self.dataset = dataset
        self.raw = store.read_dataset_bytes(dataset)
        self.document = json.loads(self.raw.decode("utf-8"))
        self.fixture = parse_fixture(self.document)
        self.findings = compute_findings(
            self.fixture, fixture_path=store.dataset_path(dataset)
        )
        meta = self.document.get("meta") if isinstance(self.document, dict) else None
        self.fictional = isinstance(meta, dict) and meta.get("fictional") is True
        self.findings["meta"]["fictional"] = self.fictional
        self.findings["meta"]["dataset"] = {
            "id": dataset["id"],
            "name": dataset["name"],
            "sha256": dataset["sha256"],
        }
        self.briefing_cache: dict[tuple[Any, ...], dict[str, Any]] = {}
        self.chief_cache: dict[tuple[Any, ...], dict[str, Any]] = {}
        # The last question asked, for GET /decisions; rebuilt from the
        # persisted briefings so a restart keeps it.
        self.last_question: Question = DEFAULT_QUESTION
        latest = store.latest_briefing(institution_id, dataset_id=int(dataset["id"]))
        if latest is not None:
            for question in QUESTIONS:
                if question.id == latest.get("question_id"):
                    self.last_question = question
                    break


def create_app(
    *,
    db_path: str | Path | None = None,
) -> FastAPI:
    """Build the API. ``db_path`` overrides CABINET_DB (tests).

    The seed fixture for new institutions comes from ``CABINET_FIXTURE``
    (default ``data/fixture.json``). A malformed seed fixture or a database
    whose schema version this build does not know fails startup with one
    clear line on stderr and ``SystemExit(1)`` — never a traceback. So does
    a production environment that cannot prove its secrets and bind address
    (fail closed).
    """
    load_local_env()
    production = os.environ.get(ENV_ENV, "").strip().lower() == PRODUCTION
    try:
        secret_key, ephemeral_secret = resolve_secret_key(production)
        if production:
            check_production_bind()
    except RuntimeError as exc:
        print(f"cabinet: cannot start: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    if ephemeral_secret:
        print(
            "cabinet: CABINET_SECRET_KEY is not set; using an ephemeral key — "
            "sessions will not survive a restart (set CABINET_ENV=production "
            "to make a missing key a startup failure)",
            file=sys.stderr,
        )

    app = FastAPI(
        title="Golden Eagle AI Cabinet API",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    fixture_path = fixture_path_from_env()
    try:
        effective_db_path = Path(db_path) if db_path is not None else db_path_from_env()
        session_ttl_seconds = int(session_ttl().total_seconds())
    except RuntimeError as exc:
        print(f"cabinet: cannot start: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    try:
        store = AuthStore(
            effective_db_path,
            seed_fixture=fixture_path,
        )
    except SchemaVersionError as exc:
        print(f"cabinet: cannot start: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except sqlite3.Error as exc:
        # A failed or half-applied migration, a corrupt file, an unreadable
        # path: one clear line, never a traceback.
        print(f"cabinet: cannot start: database error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    try:
        # The outbound provider's configuration is checked at startup:
        # an unknown CABINET_OUTBOUND is a refusal in every mode, and
        # CABINET_OUTBOUND=smtp with a missing setting is a refusal in
        # production (fail closed). Outside production a partial smtp
        # configuration still starts; its first send fails loudly instead.
        outbound_from_env(
            outbox_dir=store.path.parent / "outbox", production=production
        )
    except RuntimeError as exc:
        print(f"cabinet: cannot start: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    app.state.auth = store
    app.add_middleware(
        CabinetSecurityMiddleware,
        auth=store,
        secret_key=secret_key,
        production=production,
    )
    # Middleware order: Starlette runs the LAST added first, so the stack a
    # request meets is ApiPrefix (strip /api) -> RequestLog (production only;
    # the line then carries the stripped path) -> CabinetSecurity -> routes.
    if production:
        app.add_middleware(RequestLogMiddleware)
    app.add_middleware(ApiPrefixMiddleware)
    # The built UI's directory (make build -> ui/dist). The mount itself
    # happens at the end of create_app: Starlette matches routes in insertion
    # order, and a "/" mount swallows every path it precedes.
    ui_dist = ui_dist_from_env()
    login_lockout = LoginLockout()
    try:
        store.ensure_bootstrap_institution()
    except Exception as exc:
        print(
            f"cabinet: cannot start: fixture {fixture_path}: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from None

    # Per-institution runtime state (findings, caches); invalidated when an
    # admin activates another dataset. The ask locks live BESIDE the
    # runtimes, not on them: a runtime is rebuilt on activation, but the
    # lock must survive — otherwise an activation while an ask holds the
    # lock lets the next ask start a second interleaved run on the same
    # audit chain. ask_locks is never popped.
    runtimes: dict[int, InstitutionRuntime] = {}
    ask_locks: dict[int, threading.Lock] = {}
    runtimes_lock = threading.Lock()

    def runtime_for(institution_id: int) -> InstitutionRuntime:
        with runtimes_lock:
            runtime = runtimes.get(institution_id)
            if runtime is None:
                runtime = InstitutionRuntime(store, institution_id)
                runtimes[institution_id] = runtime
            return runtime

    def ask_lock_for(institution_id: int) -> threading.Lock:
        """The institution's ask lock — the same object for the process's
        lifetime, across runtime invalidations."""
        with runtimes_lock:
            lock = ask_locks.get(institution_id)
            if lock is None:
                lock = threading.Lock()
                ask_locks[institution_id] = lock
            return lock

    def invalidate_runtime(institution_id: int) -> None:
        with runtimes_lock:
            runtimes.pop(institution_id, None)

    def request_institution(request: Request) -> int:
        """The caller's institution — always from the session's user."""
        return int(request.scope["cabinet_user"]["institution_id"])

    @app.exception_handler(StoreError)
    def store_error_handler(_request: Request, exc: StoreError) -> JSONResponse:
        """A dataset file that fails its sha256 integrity check (tampered or
        truncated on disk) is a loud 503, never silently different findings."""
        return JSONResponse(
            status_code=503, content={"available": False, "reason": str(exc)}
        )

    def readiness() -> tuple[dict[str, bool], list[str]]:
        """The /ready checks: each dependency named, True when usable.

        /health is liveness (the process answers); /ready is readiness (the
        process can actually serve): the database opens and its schema
        version is one this build knows, a session secret is configured (not
        ephemeral), the built UI is present, and the golden replay run is
        readable. Anything failing is named in ``reasons``.
        """
        checks: dict[str, bool] = {}
        reasons: list[str] = []

        db_path = store.path
        try:
            probe = sqlite3.connect(str(db_path))
            try:
                probe.execute("SELECT 1")
                checks["database"] = True
                known = {version for version, _, _ in MIGRATIONS}
                recorded = recorded_versions(probe)
                checks["migrations"] = all(v in known for v in recorded)
            finally:
                probe.close()
        except sqlite3.Error as exc:
            checks["database"] = False
            checks["migrations"] = False
            reasons.append(f"database {db_path} does not open: {exc}")
        if checks.get("migrations") is False and checks.get("database"):
            reasons.append(
                f"database {db_path} records a schema version this build does not know"
            )

        checks["secret"] = not ephemeral_secret
        if ephemeral_secret:
            reasons.append(
                "CABINET_SECRET_KEY is not configured; sessions use an ephemeral key"
            )

        checks["ui_dist"] = (ui_dist / "index.html").is_file()
        if not checks["ui_dist"]:
            reasons.append(f"the built UI is missing at {ui_dist} (run `make build`)")

        golden = golden_dir_from_env()
        golden_problem: str | None = None
        if not golden.is_dir():
            golden_problem = f"{golden} is not a directory"
        else:
            for recording in sorted(golden.glob("*.json")):
                try:
                    json.loads(recording.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    golden_problem = f"{recording} is not readable JSON: {exc}"
                    break
        checks["replay_golden"] = golden_problem is None
        if golden_problem is not None:
            reasons.append(f"the golden replay run is not readable: {golden_problem}")

        # A bootstrap institution whose dataset seeding never completed (or
        # whose active dataset was lost) cannot serve answers.
        bootstrap = store.institution_by_slug(BOOTSTRAP_SLUG)
        checks["active_dataset"] = (
            bootstrap is not None
            and store.active_dataset(int(bootstrap["id"])) is not None
        )
        if not checks["active_dataset"]:
            reasons.append("the bootstrap institution has no active dataset")

        return checks, reasons

    @app.get("/health")
    def health() -> dict[str, bool]:
        """Liveness only: the process answers. Dependencies are /ready's job."""
        return {"ok": True}

    @app.get("/ready")
    def ready() -> JSONResponse:
        """Readiness: 200 when every dependency checks out, 503 with
        the failing reasons otherwise."""
        checks, reasons = readiness()
        if reasons:
            return JSONResponse(
                status_code=503,
                content={"ready": False, "checks": checks, "reasons": reasons},
            )
        return JSONResponse(content={"ready": True})

    @app.post("/auth/login")
    async def post_login(body: LoginRequest, request: Request) -> JSONResponse:
        """Log in: sets the signed session cookie and returns the user plus
        the session's CSRF token. The error is deliberately generic (no
        account enumeration). Throttling: 5 failed attempts per 15 minutes
        hard-lock the IP and the IP+email pair (429) — never the bare
        email, so nobody can lock an account out from elsewhere; the bare
        email earns a progressive delay (1, 2, 4, 8 s, capped at 30 s)
        instead.

        Async end to end: the progressive delay is awaited (a blocking
        sleep here would pin a threadpool worker — enough concurrent
        sleepers stall every sync route), and the scrypt password check
        runs in the threadpool so it never blocks the event loop. So do
        the session insert and the institution lookup: they take the store
        lock, which a slow upload's commit may hold."""
        client_ip = request.client.host if request.client else "unknown"
        email = body.email.strip().lower()
        email_key = f"email:{email}"
        ip_key = f"ip:{client_ip}"
        ip_email_key = f"ip-email:{client_ip}|{email}"
        wait = login_lockout.blocked(ip_key, ip_email_key)
        if wait:
            return JSONResponse(
                status_code=429,
                content={"detail": "too many login attempts; try again later"},
                headers={"Retry-After": str(wait)},
            )
        await login_lockout.apply_email_delay(email_key)
        user = await run_in_threadpool(
            store.verify_credentials, body.email, body.password
        )
        if user is None:
            login_lockout.record_failure(ip_key, ip_email_key, email_key)
            return JSONResponse(
                status_code=401, content={"detail": GENERIC_LOGIN_ERROR}
            )
        login_lockout.record_success(email_key, ip_email_key)
        session = await run_in_threadpool(store.create_session, user["id"])
        user_body = await run_in_threadpool(_user_body, store, user)
        response = JSONResponse(
            content={
                "user": user_body,
                "csrf_token": session["csrf_token"],
            }
        )
        response.set_cookie(
            COOKIE_NAME,
            sign_session_id(secret_key, session["id"]),
            max_age=session_ttl_seconds,
            path="/",
            httponly=True,
            samesite="strict",
            secure=production,
        )
        return response

    @app.post("/auth/logout")
    def post_logout(request: Request) -> JSONResponse:
        session = request.scope["cabinet_session_row"]
        store.delete_session(session["id"])
        response = JSONResponse(content={"ok": True})
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    @app.get("/auth/me")
    def get_me(request: Request) -> dict[str, Any]:
        """The logged-in user and the session's CSRF token (the UI reads it
        here and sends it back as X-CSRF-Token on every POST)."""
        user = request.scope["cabinet_user"]
        session = request.scope["cabinet_session_row"]
        return {
            "user": _user_body(store, user),
            "csrf_token": session["csrf_token"],
        }

    @app.get("/questions")
    def get_questions() -> list[dict[str, str]]:
        """The approved questions the Cabinet answers (the registry)."""
        return [{"id": q.id, "text": q.text} for q in QUESTIONS]

    @app.get("/findings")
    def get_findings(request: Request) -> dict[str, Any]:
        """The findings computed from the caller's institution's active
        dataset (row IDs included, for the evidence drawer)."""
        return runtime_for(request_institution(request)).findings

    @app.get("/events")
    def get_events(
        request: Request,
        event_type: str | None = Query(default=None, alias="type"),
    ) -> dict[str, Any]:
        """The caller's institution's audit chain, in order."""
        if event_type is not None and event_type not in EVENT_TYPES:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"unknown event type {event_type!r}; "
                    f"expected one of {', '.join(EVENT_TYPES)}"
                ),
            )
        institution_id = request_institution(request)
        return {"events": store.audit_events(institution_id, event_type)}

    # One question run at a time PER INSTITUTION (the locks live in
    # ask_locks, keyed by institution and never invalidated): a run's audit
    # events, cache check-and-set, and event_ids must stay contiguous and
    # exactly its own, but tenant B is never serialized behind — or refused
    # because of — tenant A's model call. The analyst briefing routes take
    # their own institution's lock non-blocking (409 while its question is
    # being answered). runtime_for and ask_lock_for are exposed on
    # app.state for tests.
    app.state.runtime_for = runtime_for
    app.state.ask_lock_for = ask_lock_for

    def analyst_task_id(role: str, runtime: InstitutionRuntime) -> str:
        """The task this briefing run's events belong to.

        Reuses the most recent question's task for that role when there is
        one (an indexed lookup, not a scan of every task.assigned);
        otherwise the briefing assigns its own task first, so
        ``finding.produced`` always shares a task_id with a
        ``task.assigned`` and its ``data.granted``. Scoped to the
        institution's own chain.
        """
        institution_id = int(runtime.dataset["institution_id"])
        audit = store.audit_for(institution_id)
        latest = store.latest_task_assigned(institution_id, role)
        if latest is not None:
            return str(latest["payload"]["task_id"])
        task_id = f"briefing-{role}"
        audit.append(
            "task.assigned",
            actor="chief_of_staff",
            payload={
                "task_id": task_id,
                "role": role,
                "fields": list(ROLE_TASK_FIELDS[role]),
                "findings": list(ROLE_FINDINGS[role]),
            },
        )
        return task_id

    def analyst_body(
        role: str,
        task_id: str,
        force_refresh: bool,
        runtime: InstitutionRuntime,
        question: Question = DEFAULT_QUESTION,
    ) -> tuple[dict[str, Any] | None, str | None]:
        """One analyst's validated explanation as a response body, or
        (None, reason) when the provider cannot answer.

        The provider is chosen per call from the environment
        (``CABINET_PROVIDER``), so ``make api REPLAY=1`` takes effect without
        a restart of the code, only of the process. A validated result is
        cached in-process keyed by (institution, dataset sha, role, provider
        name, model label, sha256 of the received findings — which includes
        the question for any question other than the default); a cache hit
        returns the same body and writes no audit events. Failures are never
        cached. A fresh validated result is also persisted to the
        institution's recordings table under the same key.
        """
        institution_id = int(runtime.dataset["institution_id"])
        audit = store.audit_for(institution_id)
        try:
            provider = provider_from_env()
        except ValueError as exc:
            return None, str(exc)
        received = received_for(question, role, runtime.findings)
        received_sha = hashlib.sha256(
            canonical_findings_json(received).encode("utf-8")
        ).hexdigest()
        cache_key = (
            institution_id,
            runtime.dataset["sha256"],
            role,
            provider.name,
            provider.model_label,
            received_sha,
        )
        if not force_refresh and cache_key in runtime.briefing_cache:
            return runtime.briefing_cache[cache_key], None
        result = run_analyst(
            role, runtime.findings, provider, audit, task_id=task_id, question=question
        )
        if not result.available:
            return None, str(result.reason)
        body = {
            "available": True,
            "text": result.text,
            "claims": [
                {"text": c.text, "finding_ids": c.finding_ids} for c in result.claims
            ],
            "provider": result.provider,
            "model_label": result.model_label,
            "recorded": result.recorded,
            "rekeyed_from": result.rekeyed_from,
        }
        runtime.briefing_cache[cache_key] = body
        store.save_recording(
            institution_id,
            role,
            received_sha,
            {
                **recording_payload(
                    received,
                    role,
                    Explanation(
                        text=str(result.text),
                        provider=str(result.provider),
                        model_label=str(result.model_label),
                        recorded=bool(result.recorded),
                    ),
                ),
                "institution_id": institution_id,
                "dataset_sha256": runtime.dataset["sha256"],
                "question_id": question.id,
            },
        )
        return body, None

    def analyst_section(
        role: str, body: dict[str, Any] | None, reason: str | None
    ) -> dict[str, Any]:
        """Section 2 or 3 of the briefing: the analyst's validated claims, or
        the section marked unavailable — never invented text."""
        if body is None:
            return {"kind": "unavailable", "reason": reason}
        return {
            "kind": "available",
            "text": body["text"],
            "claims": body["claims"],
            "provenance": {
                "source": role,
                "provider": body["provider"],
                "model_label": body["model_label"],
                "recorded": body["recorded"],
                "rekeyed_from": body.get("rekeyed_from"),
            },
        }

    def chief_section(
        text: str | None, claims: list[dict[str, Any]], result: dict[str, Any]
    ) -> dict[str, Any]:
        if text is None:
            return {"kind": "unavailable", "reason": result["reason"]}
        return {
            "kind": "available",
            "text": text,
            "claims": claims,
            "provenance": {
                "source": CHIEF_OF_STAFF,
                "provider": result["provider"],
                "model_label": result["model_label"],
                "recorded": result["recorded"],
                "rekeyed_from": result.get("rekeyed_from"),
            },
        }

    @app.post("/ask")
    def post_ask(body: AskRequest, request: Request) -> dict[str, Any]:
        institution_id = request_institution(request)
        runtime = runtime_for(institution_id)
        audit = store.audit_for(institution_id)
        with ask_lock_for(institution_id):
            return _ask_unlocked(body, runtime, audit)

    def _ask_unlocked(
        body: AskRequest, runtime: InstitutionRuntime, audit: Any
    ) -> dict[str, Any]:
        institution_id = int(runtime.dataset["institution_id"])
        findings_obj = runtime.findings
        # The run's own events are exactly those with id > this watermark —
        # no full-table reads before and after (the audit log grows without
        # bound, and this route runs on every ask).
        max_event_id_before = store.audit_max_id(institution_id)
        question = match_question(body.question)
        question_event = audit.append(
            "question.asked",
            actor="executive",
            payload={
                "question": body.question,
                "question_id": question.id if question is not None else None,
            },
        )
        if question is None:
            refused_event = audit.append(
                "data.refused",
                actor="chief_of_staff",
                payload={
                    "question": body.question,
                    "reason": OUT_OF_SCOPE_REFUSAL,
                },
            )
            return {
                "accepted": False,
                "refusal": OUT_OF_SCOPE_REFUSAL,
                "event_ids": [question_event["id"], refused_event["id"]],
            }

        question_event_id = question_event["id"]
        # Beat 2: the Chief of Staff dispatches the two analyst tasks, each
        # visibly scoped to its role's fields and the question's findings.
        analyst_task_ids: dict[str, str] = {}
        for role in ANALYST_ROLES:
            task_id = f"task-{role}-{question_event_id}"
            analyst_task_ids[role] = task_id
            audit.append(
                "task.assigned",
                actor="chief_of_staff",
                payload={
                    "task_id": task_id,
                    "role": role,
                    "fields": list(ROLE_TASK_FIELDS[role]),
                    "findings": list(question.dispatch[role]),
                },
            )

        # Run both analysts through the shared runner (cached); each actual
        # run logs its data.granted and, on success, finding.produced under
        # this question's task.
        analyst_bodies: dict[str, dict[str, Any] | None] = {}
        analyst_reasons: dict[str, str | None] = {}
        for role in ANALYST_ROLES:
            body_out, reason = analyst_body(
                role, analyst_task_ids[role], False, runtime, question
            )
            analyst_bodies[role] = body_out
            analyst_reasons[role] = reason

        # The Chief of Staff merges the analysts' validated findings into
        # sections 1 and 7. It runs only when both analysts produced; if one
        # is unavailable the briefing still returns, with the Chief of Staff
        # sections marked unavailable and nothing invented in their place.
        chief_task_id: str | None = None
        chief_body: dict[str, Any] | None = None
        chief_reason: str | None = None
        chief_fields: list[str] = []
        if all(analyst_bodies[role] is not None for role in ANALYST_ROLES):
            chief_task_id = f"task-{CHIEF_OF_STAFF}-{question_event_id}"
            analyst_texts = {
                role: str(ran["text"])
                for role in ANALYST_ROLES
                if (ran := analyst_bodies[role]) is not None
            }
            received = chief_received(findings_obj, analyst_texts, question)
            chief_fields = chief_aggregate_fields(received)
            audit.append(
                "task.assigned",
                actor=CHIEF_OF_STAFF,
                payload={
                    "task_id": chief_task_id,
                    "role": CHIEF_OF_STAFF,
                    "fields": chief_fields,
                    "findings": sorted(received["findings"]),
                    "level": "aggregate",
                    "analyst_explanations": sorted(analyst_texts),
                },
            )
            try:
                provider = provider_from_env()
            except ValueError as exc:
                chief_reason = str(exc)
            else:
                chief_received_sha = hashlib.sha256(
                    canonical_findings_json(received).encode("utf-8")
                ).hexdigest()
                chief_key = (
                    institution_id,
                    runtime.dataset["sha256"],
                    provider.name,
                    provider.model_label,
                    chief_received_sha,
                )
                if chief_key in runtime.chief_cache:
                    chief_body = runtime.chief_cache[chief_key]
                else:
                    result = run_chief_of_staff(
                        findings_obj,
                        analyst_texts,
                        provider,
                        audit,
                        chief_task_id,
                        question,
                    )
                    if result.available:
                        chief_body = {
                            "executive_summary": result.executive_summary,
                            "summary_claims": [
                                {"text": c.text, "finding_ids": c.finding_ids}
                                for c in result.summary_claims
                            ],
                            "limitations": result.limitations,
                            "limitation_claims": [
                                {"text": c.text, "finding_ids": c.finding_ids}
                                for c in result.limitation_claims
                            ],
                            "provider": result.provider,
                            "model_label": result.model_label,
                            "recorded": result.recorded,
                            "rekeyed_from": result.rekeyed_from,
                        }
                        runtime.chief_cache[chief_key] = chief_body
                        store.save_recording(
                            institution_id,
                            CHIEF_OF_STAFF,
                            chief_received_sha,
                            {
                                "provider": result.provider,
                                "model_label": result.model_label,
                                "role": CHIEF_OF_STAFF,
                                "findings_sha256": chief_received_sha,
                                "institution_id": institution_id,
                                "dataset_sha256": runtime.dataset["sha256"],
                                "question_id": question.id,
                                "recorded_at": datetime.now(UTC).isoformat(),
                                "text": result.text,
                            },
                        )
                    else:
                        chief_reason = result.reason
        else:
            missing = [role for role in ANALYST_ROLES if analyst_bodies[role] is None]
            chief_reason = (
                "the Chief of Staff did not run because "
                + " and ".join(missing).replace("_", " ")
                + " did not produce a validated section"
            )

        chief_result = {
            "reason": chief_reason,
            "provider": chief_body["provider"] if chief_body else None,
            "model_label": chief_body["model_label"] if chief_body else None,
            "recorded": chief_body["recorded"] if chief_body else False,
            "rekeyed_from": chief_body.get("rekeyed_from") if chief_body else None,
        }
        sections: dict[str, Any] = {
            "1": chief_section(
                chief_body["executive_summary"] if chief_body else None,
                chief_body["summary_claims"] if chief_body else [],
                chief_result,
            ),
            "2": analyst_section(
                ENROLLMENT_ANALYST,
                analyst_bodies[ENROLLMENT_ANALYST],
                analyst_reasons[ENROLLMENT_ANALYST],
            ),
            "3": analyst_section(
                STUDENT_SUCCESS_ANALYST,
                analyst_bodies[STUDENT_SUCCESS_ANALYST],
                analyst_reasons[STUDENT_SUCCESS_ANALYST],
            ),
            "4": {
                "findings": {
                    finding_id: {
                        "title": findings_obj[finding_id]["title"],
                        "display": findings_obj[finding_id]["display"],
                        "source_fields": findings_obj[finding_id]["source_fields"],
                    }
                    for finding_id in ("M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8")
                }
            },
            "5": {"actions": question.build_actions(findings_obj)},
            "6": {"decisions": question.build_decisions(findings_obj)},
            "7": chief_section(
                chief_body["limitations"] if chief_body else None,
                chief_body["limitation_claims"] if chief_body else [],
                chief_result,
            ),
        }
        briefing = {
            "question_id": question.id,
            "question": question.text,
            "question_event_id": question_event_id,
            "sections": sections,
            "meta": {
                "fictional": runtime.fictional,
                "dataset": {
                    "id": runtime.dataset["id"],
                    "name": runtime.dataset["name"],
                    "sha256": runtime.dataset["sha256"],
                },
            },
        }

        def source_entry(section: dict[str, Any]) -> dict[str, Any]:
            if section["kind"] != "available":
                return {"available": False, "reason": section["reason"]}
            provenance = section["provenance"]
            return {
                "available": True,
                "provider": provenance["provider"],
                "model_label": provenance["model_label"],
                "recorded": provenance["recorded"],
                "rekeyed_from": provenance.get("rekeyed_from"),
            }

        audit.append(
            "briefing.produced",
            actor=CHIEF_OF_STAFF,
            payload={
                "question_id": question.id,
                "question_event_id": question_event_id,
                "sections": [1, 2, 3, 4, 5, 6, 7],
                "sources": {
                    section: source_entry(sections[section])
                    for section in ("1", "2", "3", "7")
                },
                "analyst_task_ids": analyst_task_ids,
                "chief_task_id": chief_task_id,
            },
        )
        store.save_briefing(
            institution_id,
            question.id,
            briefing,
            dataset_id=int(runtime.dataset["id"]),
            dataset_sha256=str(runtime.dataset["sha256"]),
        )
        runtime.last_question = question

        tasks = [
            {
                "task_id": analyst_task_ids[role],
                "role": role,
                "granted_fields": list(ROLE_TASK_FIELDS[role]),
                "findings": list(question.dispatch[role]),
            }
            for role in ANALYST_ROLES
        ]
        if chief_task_id is not None:
            tasks.append(
                {
                    "task_id": chief_task_id,
                    "role": CHIEF_OF_STAFF,
                    "granted_fields": chief_fields,
                    "findings": list(question.dispatch[CHIEF_OF_STAFF]),
                    "level": "aggregate",
                }
            )
        return {
            "accepted": True,
            "question_id": question.id,
            "question": question.text,
            "tasks": tasks,
            "findings": {
                role: {
                    finding_id: findings_obj[finding_id]["display"]
                    for finding_id in question.dispatch[role]
                }
                for role in ANALYST_ROLES
            },
            "briefing": briefing,
            "meta": briefing["meta"],
            "event_ids": store.audit_event_ids_after(
                institution_id, max_event_id_before
            ),
        }

    @app.get("/briefing")
    def get_briefing(request: Request) -> JSONResponse:
        """The caller's institution's most recent produced briefing FOR ITS
        ACTIVE DATASET, from the store, so a page reload — or a restart —
        renders it without re-running anything. 404 with a plain reason when
        the active dataset has no briefing yet — including right after a new
        dataset is activated, which retires the previous dataset's briefing
        from view until the next ask. Briefings are keyed per dataset
        (migration 4), so re-activating an earlier dataset serves that
        dataset's own briefing again."""
        institution_id = request_institution(request)
        dataset = store.active_dataset(institution_id)
        briefing = (
            store.latest_briefing(institution_id, dataset_id=int(dataset["id"]))
            if dataset is not None
            else None
        )
        if briefing is None:
            return JSONResponse(
                status_code=404,
                content={
                    "available": False,
                    "reason": (
                        "No briefing has been produced for the active dataset "
                        "yet; ask the approved question first (POST /ask)."
                    ),
                },
            )
        return JSONResponse(content=briefing)

    @app.post("/governance/request")
    def post_governance_request(
        body: GovernanceRequest, request: Request
    ) -> dict[str, Any]:
        # Client errors (unknown role, empty field list) are 422s and never
        # reach the audit log; a known role asking outside its lane goes
        # through the gate and is logged as data.refused on the caller's
        # institution's chain.
        if body.role not in ROLES:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"unknown role {body.role!r}; expected one of {', '.join(ROLES)}"
                ),
            )
        if not body.fields:
            raise HTTPException(
                status_code=422, detail="fields must be a non-empty list"
            )
        audit = store.audit_for(request_institution(request))
        task_id = body.task_id or f"demo-{body.role}"
        try:
            granted = request_fields(body.role, body.fields, task_id, audit)
        except FieldRequestRefused as exc:
            return {
                "granted": False,
                "role": body.role,
                "refused_fields": exc.refused_fields,
                "reason": exc.reason,
                "event": exc.event,
            }
        return {"granted": True, "role": body.role, "granted_fields": granted}

    def briefing_response(
        role: str, force_refresh: bool, request: Request
    ) -> JSONResponse:
        """One analyst's briefing section: its validated explanation.

        A provider that cannot answer — no key, no recording, output that
        failed validation — yields HTTP 503 with ``{available: false,
        reason}``; the metrics and the audit log still work. Caching lives in
        ``analyst_body`` (shared with ``POST /ask``); a cache hit writes no
        audit events. Runs under the institution's own ``ask_lock``: while
        ITS question is being answered the route answers 409 instead of
        interleaving its own task assignment and run with the in-flight
        ``POST /ask``; another institution's run never blocks it.
        """
        runtime = runtime_for(request_institution(request))
        lock = ask_lock_for(int(runtime.dataset["institution_id"]))
        if not lock.acquire(blocking=False):
            return JSONResponse(
                status_code=409,
                content={
                    "available": False,
                    "reason": "a question is being answered",
                },
            )
        try:
            body, reason = analyst_body(
                role, analyst_task_id(role, runtime), force_refresh, runtime
            )
        finally:
            lock.release()
        if body is None:
            return JSONResponse(
                status_code=503,
                content={"available": False, "reason": reason},
            )
        return JSONResponse(content=body)

    @app.get("/briefing/enrollment")
    def get_briefing_enrollment(
        request: Request,
        refresh: bool = Query(default=False),
    ) -> JSONResponse:
        return briefing_response(ENROLLMENT_ANALYST, refresh, request)

    @app.post("/briefing/enrollment/refresh")
    def post_briefing_enrollment_refresh(request: Request) -> JSONResponse:
        return briefing_response(ENROLLMENT_ANALYST, True, request)

    @app.get("/briefing/student-success")
    def get_briefing_student_success(
        request: Request,
        refresh: bool = Query(default=False),
    ) -> JSONResponse:
        return briefing_response(STUDENT_SUCCESS_ANALYST, refresh, request)

    @app.post("/briefing/student-success/refresh")
    def post_briefing_student_success_refresh(request: Request) -> JSONResponse:
        return briefing_response(STUDENT_SUCCESS_ANALYST, True, request)

    @app.get("/decisions")
    def get_decisions(request: Request) -> dict[str, Any]:
        """The leadership decision for the latest question the caller's
        institution asked (the registry default before anything is asked),
        with approval state; the text is built from the institution's
        findings at request time. Approval state is pinned to the active
        dataset: an approval made against a previous dataset's numbers is
        not shown as approved."""
        institution_id = request_institution(request)
        runtime = runtime_for(institution_id)
        approved = store.approved_decision_ids(
            institution_id, dataset_id=int(runtime.dataset["id"])
        )
        return {
            "question_id": runtime.last_question.id,
            "decisions": [
                {**decision, "approved": decision["id"] in approved}
                for decision in runtime.last_question.build_decisions(runtime.findings)
            ],
        }

    @app.post("/decisions/approve")
    def post_decision_approve(body: ApproveRequest, request: Request) -> dict[str, Any]:
        # Any approved question's decision id is accepted, whichever question
        # was asked latest; approval is idempotent per (decision id, active
        # dataset), anchored by the decisions table's primary key
        # (restart-safe) — re-approving after a dataset switch is a new
        # approval, pinned to the new dataset's numbers.
        institution_id = request_institution(request)
        runtime = runtime_for(institution_id)
        findings_obj = runtime.findings
        decision = next(
            (
                d
                for q in QUESTIONS
                for d in q.build_decisions(findings_obj)
                if d["id"] == body.decision_id
            ),
            None,
        )
        if decision is None:
            raise HTTPException(
                status_code=404, detail=f"unknown decision_id {body.decision_id!r}"
            )
        task = {
            "id": f"TASK-{body.decision_id}",
            "decision_id": body.decision_id,
            "office": decision["follow_up"]["office"],
            "description": decision["follow_up"]["description"],
            "status": SIMULATED_STATUS,
        }
        user = request.scope["cabinet_user"]
        created = store.record_decision(
            institution_id,
            body.decision_id,
            str(user["email"]),
            task,
            dataset_id=int(runtime.dataset["id"]),
            dataset_sha256=str(runtime.dataset["sha256"]),
        )
        if not created:
            existing = store.decision_task(
                institution_id,
                body.decision_id,
                dataset_id=int(runtime.dataset["id"]),
            )
            return {
                "task": existing if existing is not None else task,
                "created": False,
                "event_ids": [],
            }
        audit = store.audit_for(institution_id)
        approved_event = audit.append(
            "decision.approved",
            actor="executive",
            payload={"decision_id": body.decision_id},
        )
        created_event = audit.append(
            "task.created",
            actor="chief_of_staff",
            payload={"decision_id": body.decision_id, "task": task},
        )
        return {
            "task": task,
            "created": True,
            "event_ids": [approved_event["id"], created_event["id"]],
        }

    # -- the governed execution step: dispatches ------------------------------
    #
    # An approved task can go to its responsible office, but only when a
    # named person clicks Send. The message is composed in code from the
    # findings (cabinet.questions.compose_dispatch — never the model, never
    # free text, never a student identifier), lands as a draft, and leaves
    # through the configured outbound provider, which is the on-machine
    # outbox by default. Every refusal here is loud: a data.refused event
    # with the reason, and a plain message to the caller.

    def dispatch_body(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "task_id": row["task_id"],
            "to_office": row["to_office"],
            "channel": row["channel"],
            "subject": row["subject"],
            "body": row["body"],
            "status": row["status"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "sent_by": row["sent_by"],
            "sent_at": row["sent_at"],
            "provider": row["provider"],
            "provider_ref": row["provider_ref"],
            "error": row["error"],
        }

    def dispatch_refused(
        request: Request, status_code: int, detail: str
    ) -> JSONResponse:
        """A dispatch refusal is never silent: it lands on the caller's
        institution chain as data.refused with the reason, like every other
        governance refusal."""
        user = request.scope["cabinet_user"]
        store.audit_append(
            int(user["institution_id"]),
            "data.refused",
            actor=str(user["email"]),
            payload={
                "reason": detail,
                "method": request.method,
                "path": request.url.path,
            },
        )
        return JSONResponse(status_code=status_code, content={"detail": detail})

    def dispatch_context(
        institution_id: int, decision_id: str
    ) -> tuple[InstitutionRuntime, Question, dict[str, Any], int, str] | None:
        """Everything a dispatch route needs, or None for an unknown
        decision id (a 404, same rule as approve). The task id is the
        approval's (TASK-<decision id>), and the dataset pin is the active
        dataset's, so a dispatch is bound to the numbers it quotes."""
        runtime = runtime_for(institution_id)
        found = find_decision(runtime.findings, decision_id)
        if found is None:
            return None
        question, decision = found
        dataset_id = int(runtime.dataset["id"])
        return runtime, question, decision, dataset_id, f"TASK-{decision_id}"

    @app.get("/decisions/{decision_id}/dispatch")
    def get_decision_dispatch(decision_id: str, request: Request) -> JSONResponse:
        """The dispatch state for one decision: whether it is approved,
        whether the office has a mailbox configured, and the draft or sent
        record when one exists. Every logged-in role may read this — the
        reviewer watches governance."""
        institution_id = request_institution(request)
        context = dispatch_context(institution_id, decision_id)
        if context is None:
            raise HTTPException(
                status_code=404, detail=f"unknown decision_id {decision_id!r}"
            )
        _, _, decision, dataset_id, task_id = context
        office = str(decision["follow_up"]["office"])
        row = store.dispatch_for_task(institution_id, task_id, dataset_id=dataset_id)
        return JSONResponse(
            content={
                "decision_id": decision_id,
                "task_id": task_id,
                "office": office,
                "office_contact": store.office_contact(institution_id, office),
                "approved": decision_id
                in store.approved_decision_ids(institution_id, dataset_id=dataset_id),
                "dispatch": dispatch_body(row) if row is not None else None,
            }
        )

    @app.post("/decisions/{decision_id}/dispatch")
    def post_decision_dispatch(decision_id: str, request: Request) -> JSONResponse:
        """Compose the draft from the approved task (staff, executive,
        admin). Idempotent per task and dataset: a second Prepare returns
        the existing draft with created=false. 409, loudly, when the
        decision is not approved — no approval, no message."""
        institution_id = request_institution(request)
        user = request.scope["cabinet_user"]
        context = dispatch_context(institution_id, decision_id)
        if context is None:
            raise HTTPException(
                status_code=404, detail=f"unknown decision_id {decision_id!r}"
            )
        runtime, question, decision, dataset_id, task_id = context
        approval = store.decision_row(
            institution_id, decision_id, dataset_id=dataset_id
        )
        if approval is None:
            return dispatch_refused(
                request,
                409,
                f"decision {decision_id!r} has not been approved for the "
                "active dataset; approve it before a message is composed",
            )
        existing = store.dispatch_for_task(
            institution_id, task_id, dataset_id=dataset_id
        )
        if existing is not None:
            return JSONResponse(
                content={"dispatch": dispatch_body(existing), "created": False}
            )
        composed = question.build_dispatch(
            runtime.findings, decision, str(approval["approved_by"])
        )
        row = store.create_dispatch(
            institution_id,
            task_id=task_id,
            dataset_id=dataset_id,
            to_office=composed["to_office"],
            channel=composed["channel"],
            subject=composed["subject"],
            body=composed["body"],
            created_by=str(user["email"]),
        )
        if row is None:
            # A concurrent Prepare won the UNIQUE constraint; its row is
            # the answer either way.
            existing = store.dispatch_for_task(
                institution_id, task_id, dataset_id=dataset_id
            )
            assert existing is not None
            return JSONResponse(
                content={"dispatch": dispatch_body(existing), "created": False}
            )
        event = store.audit_append(
            institution_id,
            "task.dispatched",
            actor=str(user["email"]),
            payload={
                "dispatch_id": row["id"],
                "task_id": task_id,
                "decision_id": decision_id,
                "to_office": row["to_office"],
                "subject": row["subject"],
            },
        )
        return JSONResponse(
            content={
                "dispatch": dispatch_body(row),
                "created": True,
                "event_id": event["id"],
            }
        )

    @app.post("/decisions/{decision_id}/dispatch/send")
    def post_decision_dispatch_send(decision_id: str, request: Request) -> JSONResponse:
        """Send the draft to the office mailbox. Staff and admin only — an
        executive approves but does not send, and their Send is a loud 403.
        A sent dispatch is never resent (409 with the earlier record); a
        missing office mailbox refuses with a clear message; a provider
        failure is recorded on the row and answered 503. Nothing is ever
        sent without this click, and nothing is ever sent to a student."""
        institution_id = request_institution(request)
        user = request.scope["cabinet_user"]
        # The middleware's prefix rule lets staff, executive, and admin
        # POST under /decisions/ (compose is open to all three); sending is
        # narrower, and the refusal is logged like any other.
        if user["role"] not in (ROLE_STAFF, ROLE_ADMIN):
            return dispatch_refused(
                request,
                403,
                "only a staff member or an administrator can send an "
                "approved message; the executive approves, a named person "
                "sends",
            )
        # One Send at a time per institution: the status read, the provider
        # call and the sent mark happen under the same lock the ask path uses,
        # so two staff members clicking at once can never deliver twice.
        with ask_lock_for(institution_id):
            context = dispatch_context(institution_id, decision_id)
            if context is None:
                raise HTTPException(
                    status_code=404, detail=f"unknown decision_id {decision_id!r}"
                )
            _, _, _, dataset_id, task_id = context
            row = store.dispatch_for_task(
                institution_id, task_id, dataset_id=dataset_id
            )
            if row is None:
                return dispatch_refused(
                    request,
                    409,
                    "no draft exists for this decision; prepare the message "
                    "first (POST /decisions/{decision_id}/dispatch)",
                )
            if row["status"] == "sent":
                refused = dispatch_refused(
                    request,
                    409,
                    "this message was already sent; it will not be sent again",
                )
                # The earlier record rides along so the caller sees exactly
                # what was sent, by whom, and when.
                body = json.loads(bytes(refused.body).decode("utf-8"))
                body["dispatch"] = dispatch_body(row)
                return JSONResponse(status_code=409, content=body)
            contact = store.office_contact(institution_id, str(row["to_office"]))
            if contact is None:
                return dispatch_refused(
                    request,
                    409,
                    f"no mailbox is configured for the {row['to_office']} "
                    "office; an administrator can add one with "
                    "PUT /admin/offices before anything is sent",
                )
            institution = store.institution_by_id(institution_id)
            assert institution is not None  # the session's own institution
            try:
                provider = outbound_from_env(
                    outbox_dir=store.path.parent / "outbox", production=production
                )
            except RuntimeError as exc:
                # The configuration changed after startup; still loud.
                store.mark_dispatch_failed(
                    institution_id, int(row["id"]), error=str(exc)
                )
                store.audit_append(
                    institution_id,
                    "task.send_failed",
                    actor=str(user["email"]),
                    payload={
                        "dispatch_id": int(row["id"]),
                        "task_id": task_id,
                        "decision_id": decision_id,
                        "to_office": row["to_office"],
                        "error": str(exc),
                    },
                )
                return JSONResponse(status_code=503, content={"detail": str(exc)})
            try:
                provider_ref = provider.send(
                    to=contact,
                    subject=str(row["subject"]),
                    body=str(row["body"]),
                    dispatch_id=int(row["id"]),
                    institution_slug=str(institution["slug"]),
                )
            except OutboundError as exc:
                store.mark_dispatch_failed(
                    institution_id, int(row["id"]), error=str(exc)
                )
                # The attempt is on the chain: a provider that delivered and
                # then timed out is not invisible, so a retry is a decision.
                store.audit_append(
                    institution_id,
                    "task.send_failed",
                    actor=str(user["email"]),
                    payload={
                        "dispatch_id": int(row["id"]),
                        "task_id": task_id,
                        "decision_id": decision_id,
                        "to_office": row["to_office"],
                        "provider": provider.name,
                        "error": str(exc),
                    },
                )
                return JSONResponse(status_code=503, content={"detail": str(exc)})
            sent = store.mark_dispatch_sent(
                institution_id,
                int(row["id"]),
                sent_by=str(user["email"]),
                provider=provider.name,
                provider_ref=provider_ref,
            )
            if sent is None:
                # A concurrent Send committed first; this one is the duplicate.
                fresh = store.dispatch_by_id(institution_id, int(row["id"]))
                return JSONResponse(
                    status_code=409,
                    content={
                        "detail": "this message was already sent; it will not "
                        "be sent again",
                        "dispatch": dispatch_body(fresh) if fresh is not None else None,
                    },
                )
            event = store.audit_append(
                institution_id,
                "task.sent",
                actor=str(user["email"]),
                payload={
                    "dispatch_id": sent["id"],
                    "task_id": task_id,
                    "decision_id": decision_id,
                    "to_office": sent["to_office"],
                    "provider": provider.name,
                    "provider_ref": provider_ref,
                },
            )
            return JSONResponse(
                content={"dispatch": dispatch_body(sent), "event_id": event["id"]}
            )

        # -- institution admin: office contacts (the dispatch address book) ------
        #
        # Same tenancy rule as datasets and users: the institution comes from
        # the session. The address book maps office names to office mailboxes;
        # it is the only place a dispatch recipient can come from, so a message
        # can never be addressed to a student.

    @app.get("/admin/offices")
    def get_admin_offices(request: Request) -> dict[str, Any]:
        institution_id = request_institution(request)
        return {"offices": store.office_contacts_for(institution_id)}

    @app.put("/admin/offices")
    def put_admin_offices(body: OfficesPutRequest, request: Request) -> JSONResponse:
        """Replace the institution's office address book. Every entry is
        validated before anything is stored; the change is one
        admin.changed audit event naming the offices, never the addresses'
        contents beyond that list."""
        institution_id = request_institution(request)
        admin = request.scope["cabinet_user"]
        contacts: list[tuple[str, str]] = []
        errors: list[str] = []
        seen: set[str] = set()
        for entry in body.offices:
            office = entry.office.strip()
            email = entry.email.strip()
            if not office:
                errors.append("an office name must not be empty")
                continue
            if office in seen:
                errors.append(f"office {office!r} appears twice")
                continue
            if not EMAIL_RE.match(email):
                errors.append(
                    f"{email!r} is not a mailbox address (expected name@domain.tld)"
                )
                continue
            seen.add(office)
            contacts.append((office, email))
        if errors:
            return JSONResponse(
                status_code=422,
                content={
                    "detail": "the office address book failed validation",
                    "errors": errors,
                },
            )
        store.set_office_contacts(institution_id, contacts)
        store.audit_append(
            institution_id,
            "admin.changed",
            actor=str(admin["id"]),
            payload={
                "action": "office_contacts",
                "by": str(admin["email"]),
                "offices": sorted(office for office, _ in contacts),
            },
        )
        return JSONResponse(
            content={"offices": store.office_contacts_for(institution_id)}
        )

    # -- institution admin: datasets ----------------------------------------
    #
    # All of these manage the caller's own institution only — the
    # institution comes from the session, never from the client, and a
    # dataset id that belongs to another institution is a 404 (not 403), so
    # existence never leaks across tenants.

    def dataset_body(dataset: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": dataset["id"],
            "name": dataset["name"],
            "uploaded_by": dataset["uploaded_by"],
            "uploaded_at": dataset["uploaded_at"],
            "sha256": dataset["sha256"],
            "row_counts": json.loads(dataset["row_counts"]),
            "is_active": bool(dataset["is_active"]),
        }

    @app.get("/admin/datasets")
    def get_admin_datasets(request: Request) -> dict[str, Any]:
        """The caller's institution's datasets (soft-deleted ones are
        hidden; they are purged after the retention window)."""
        institution_id = request_institution(request)
        return {
            "datasets": [
                dataset_body(row) for row in store.datasets_for(institution_id)
            ]
        }

    @app.post("/admin/datasets")
    async def post_admin_dataset(request: Request) -> JSONResponse:
        """Upload a dataset in the SCHEMA.md shape (up to 20 MB).

        The document is validated before anything is stored: unknown fields,
        obvious PII columns, and non-pseudonymous student ids are rejected
        with every problem listed; counseling fields are allowed but flagged
        as "present, will always be refused". The new dataset starts
        inactive; activating it recomputes the institution's findings.

        Only the body read is async; the validation, hashing, and storage
        run in the threadpool (they are synchronous and up to 20 MB of
        work), so a slow upload never blocks the event loop — /health keeps
        answering while an upload is in flight.
        """
        raw = await request.body()
        return await run_in_threadpool(_store_uploaded_dataset, request, raw)

    def _store_uploaded_dataset(request: Request, raw: bytes) -> JSONResponse:
        institution_id = request_institution(request)
        user = request.scope["cabinet_user"]
        try:
            report = validate_upload(raw)
        except UploadError as exc:
            return JSONResponse(
                status_code=422,
                content={
                    "detail": "the dataset failed validation",
                    "errors": exc.errors,
                },
            )
        # validate_upload already decoded the body; reuse its document
        # instead of decoding again.
        meta = report.document.get("meta")
        name = (
            str(meta.get("title")).strip()
            if isinstance(meta, dict) and meta.get("title")
            else "Uploaded dataset"
        )
        dataset = store.add_dataset(
            institution_id,
            name=name,
            raw=raw,
            uploaded_by=str(user["email"]),
            row_counts=report.row_counts,
        )
        store.audit_append(
            institution_id,
            "dataset.uploaded",
            actor=str(user["id"]),
            payload={
                "dataset_id": dataset["id"],
                "name": dataset["name"],
                "sha256": dataset["sha256"],
                "row_counts": report.row_counts,
                "fictional": report.fictional,
                "counseling": report.counseling_note,
            },
        )
        return JSONResponse(
            status_code=201,
            content={
                "dataset": dataset_body(dataset),
                "validation": {
                    "row_counts": report.row_counts,
                    "counseling": report.counseling_note,
                    "fictional": report.fictional,
                },
            },
        )

    @app.post("/admin/datasets/{dataset_id}/activate")
    def post_admin_dataset_activate(dataset_id: int, request: Request) -> JSONResponse:
        """Make one of the institution's datasets active: the findings,
        briefings, decisions, and caches recompute from it on the next
        request. The previously active dataset stays until deleted."""
        institution_id = request_institution(request)
        user = request.scope["cabinet_user"]
        dataset = store.dataset_row(institution_id, dataset_id)
        if dataset is None:
            return JSONResponse(
                status_code=404,
                content={"detail": f"unknown dataset id {dataset_id}"},
            )
        store.set_active_dataset(institution_id, dataset_id)
        invalidate_runtime(institution_id)
        store.audit_append(
            institution_id,
            "dataset.activated",
            actor=str(user["id"]),
            payload={
                "dataset_id": dataset_id,
                "name": dataset["name"],
                "sha256": dataset["sha256"],
            },
        )
        dataset = store.dataset_row(institution_id, dataset_id)
        assert dataset is not None
        return JSONResponse(content={"dataset": dataset_body(dataset)})

    @app.delete("/admin/datasets/{dataset_id}")
    def delete_admin_dataset(dataset_id: int, request: Request) -> JSONResponse:
        """Soft-delete a dataset. The row stays for the retention window
        (30 days; ``make purge-deleted`` hard-deletes after it); the active
        dataset cannot be deleted — activate another first."""
        institution_id = request_institution(request)
        user = request.scope["cabinet_user"]
        dataset = store.dataset_row(institution_id, dataset_id)
        if dataset is None:
            return JSONResponse(
                status_code=404,
                content={"detail": f"unknown dataset id {dataset_id}"},
            )
        if dataset["is_active"]:
            return JSONResponse(
                status_code=409,
                content={
                    "detail": (
                        "the active dataset cannot be deleted; activate "
                        "another dataset first"
                    )
                },
            )
        store.soft_delete_dataset(institution_id, dataset_id)
        store.audit_append(
            institution_id,
            "dataset.deleted",
            actor=str(user["id"]),
            payload={"dataset_id": dataset_id, "name": dataset["name"]},
        )
        return JSONResponse(content={"deleted": dataset_id, "purge_after_days": 30})

    # -- institution admin: users -------------------------------------------
    #
    # Same tenancy rule as the datasets: the institution comes from the
    # session, and a user id from another institution is a 404, never a 403.
    # The one-time password exists only in the POST /admin/users response —
    # it is generated like `make user`, never stored (the store keeps the
    # scrypt hash), never logged, and never in an audit payload.

    def admin_user_body(user_row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": user_row["id"],
            "email": user_row["email"],
            "role": user_row["role"],
            "disabled": bool(user_row["disabled"]),
            "created_at": user_row["created_at"],
        }

    def admin_changed(
        institution_id: int, action: str, target: dict[str, Any], by: dict[str, Any]
    ) -> None:
        """One admin.changed event; the payload never carries a password."""
        store.audit_append(
            institution_id,
            "admin.changed",
            actor=str(by["id"]),
            payload={
                "action": action,
                "target_user_id": target["id"],
                "role": target["role"],
                "by": str(by["email"]),
            },
        )

    @app.get("/admin/users")
    def get_admin_users(request: Request) -> list[dict[str, Any]]:
        """Every user of the caller's institution (the admin's own row
        included; the UI marks it "you")."""
        institution_id = request_institution(request)
        return [admin_user_body(row) for row in store.users_for(institution_id)]

    @app.post("/admin/users")
    def post_admin_user(body: AdminUserCreateRequest, request: Request) -> JSONResponse:
        """Create one user in the caller's institution with a generated
        one-time password — the same code path as `make user`
        (``generate_password`` + ``create_user``), so the CLI and the API
        agree. The password appears only in this response; the audit event
        records the action without it."""
        institution_id = request_institution(request)
        admin = request.scope["cabinet_user"]
        email = body.email.strip().lower()
        if not email:
            raise HTTPException(status_code=422, detail="email must not be empty")
        if body.role not in USER_ROLES:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"unknown role {body.role!r}; "
                    f"expected one of {', '.join(USER_ROLES)}"
                ),
            )
        password = generate_password()
        try:
            user_id = store.create_user(
                email, password, body.role, institution_id=institution_id
            )
        except ValueError as exc:
            status = 409 if "already exists" in str(exc) else 422
            return JSONResponse(status_code=status, content={"detail": str(exc)})
        target = store.user_in_institution(institution_id, user_id)
        assert target is not None  # just created
        admin_changed(institution_id, "created", target, admin)
        return JSONResponse(
            status_code=201,
            content={
                "id": user_id,
                "email": email,
                "role": body.role,
                "one_time_password": password,
            },
        )

    def set_user_disabled_route(
        user_id: int, disabled: bool, request: Request
    ) -> JSONResponse:
        """Disable or enable one of the institution's users. Idempotent: a
        user already in the requested state answers 200 with changed=false
        and no new audit event. An admin cannot disable their own account,
        and the institution's last enabled admin cannot be disabled — both
        are 409."""
        institution_id = request_institution(request)
        admin = request.scope["cabinet_user"]
        target = store.user_in_institution(institution_id, user_id)
        if target is None:
            return JSONResponse(
                status_code=404, content={"detail": f"unknown user id {user_id}"}
            )
        if disabled:
            if int(target["id"]) == int(admin["id"]):
                return JSONResponse(
                    status_code=409,
                    content={
                        "detail": "an administrator cannot disable their own account"
                    },
                )
            if (
                not target["disabled"]
                and target["role"] == "admin"
                and store.enabled_admin_count(institution_id) <= 1
            ):
                return JSONResponse(
                    status_code=409,
                    content={
                        "detail": (
                            "the institution's last enabled administrator "
                            "cannot be disabled"
                        )
                    },
                )
        if bool(target["disabled"]) == disabled:
            return JSONResponse(
                content={"user": admin_user_body(target), "changed": False}
            )
        updated = store.set_user_disabled(institution_id, user_id, disabled)
        assert updated is not None
        admin_changed(
            institution_id, "disabled" if disabled else "enabled", updated, admin
        )
        return JSONResponse(content={"user": admin_user_body(updated), "changed": True})

    @app.post("/admin/users/{user_id}/disable")
    def post_admin_user_disable(user_id: int, request: Request) -> JSONResponse:
        return set_user_disabled_route(user_id, True, request)

    @app.post("/admin/users/{user_id}/enable")
    def post_admin_user_enable(user_id: int, request: Request) -> JSONResponse:
        return set_user_disabled_route(user_id, False, request)

    @app.patch("/admin/users/{user_id}")
    def patch_admin_user(
        user_id: int, body: AdminUserRoleRequest, request: Request
    ) -> JSONResponse:
        """Change one user's role. Idempotent (the current role answers 200
        with changed=false); the institution's last enabled admin cannot be
        demoted — 409."""
        institution_id = request_institution(request)
        admin = request.scope["cabinet_user"]
        if body.role not in USER_ROLES:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"unknown role {body.role!r}; "
                    f"expected one of {', '.join(USER_ROLES)}"
                ),
            )
        target = store.user_in_institution(institution_id, user_id)
        if target is None:
            return JSONResponse(
                status_code=404, content={"detail": f"unknown user id {user_id}"}
            )
        if target["role"] == body.role:
            return JSONResponse(
                content={"user": admin_user_body(target), "changed": False}
            )
        if (
            not target["disabled"]
            and target["role"] == "admin"
            and body.role != "admin"
            and store.enabled_admin_count(institution_id) <= 1
        ):
            return JSONResponse(
                status_code=409,
                content={
                    "detail": (
                        "the institution's last enabled administrator cannot be demoted"
                    )
                },
            )
        updated = store.set_user_role(institution_id, user_id, body.role)
        assert updated is not None
        admin_changed(institution_id, "role_changed", updated, admin)
        return JSONResponse(content={"user": admin_user_body(updated), "changed": True})

    # The built UI, served by the same process. Mounted after every API
    # route so an API path always wins over the static mount; a missing
    # directory means development without a build, and /ready reports it.
    if ui_dist.is_dir():
        app.mount("/", SpaStaticFiles(directory=ui_dist, html=True), name="ui")

    return app
