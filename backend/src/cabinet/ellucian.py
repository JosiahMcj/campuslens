"""Ellucian Ethos connector: the institution's SIS feeds the cabinet without any
identifiable student record ever entering it.

The connector runs at the institution's edge (``make import-ethos``). It talks
to the Ellucian Ethos Integration API, maps Ethos Education Data Model
resources to the SCHEMA.md dataset shape, pseudonymises student identifiers
with a keyed hash whose key never leaves the institution, drops every field
SCHEMA.md does not name, and emits an upload document that
``cabinet.datasets.validate_upload`` accepts unchanged. Nothing here touches a
live Ellucian tenant by itself: tests and demos run against
``backend/tests/ethos_mock.py``.

Ethos resources requested, an exact allow list (Education Data Model;
versions are overridable per tenant with ``CABINET_ETHOS_RESOURCES``, and
:func:`validate_resource_config` refuses any path but each resource's own):

- ``students`` (v6) — per person: ``person.id`` is the pseudonym source (the
  tenant-wide ``persons`` resource is never requested: every id the
  connector needs is already here, and pulling ``persons`` would move every
  name, email, credential, and phone across the wire for nothing). Also
  ``programs[0].code`` -> ``profile.program``, ``academicLevel.code``
  lowercased -> ``profile.class_level``, ``continuing`` (boolean) ->
  ``profile.continuing``.
- ``academic-periods`` (v3) — ``code``/``title``/``startOn``/``endOn`` and
  ``registration.openOn``/``registration.closeOn`` -> the three ``terms.*``
  objects. ``terms.current`` is the term named on the command line;
  ``terms.prior_year`` is the Banner-style prior-year code (``int(term) -
  100``); ``terms.in_session`` is the period with the latest ``startOn`` on or
  before the derived as-of date. ``prior_year_equivalent_date`` is the as-of
  date shifted back one year.
- ``student-academic-periods`` (v4) — per student per period:
  ``registrationStatus`` -> ``enrollment.registration_status`` (only
  ``registered`` maps to ``"registered"``; everything else is
  ``"not_registered"``), ``creditHours`` -> ``enrollment.registered_credit_hours``,
  ``registeredOn`` -> ``enrollment.registration_date``. A person's record for
  the prior-year period feeds ``comparison.prior_term_status`` on
  current-term rows (``"not_enrolled"`` when no such record exists); the
  same lookup one term further back fills it on prior-year rows.
- ``person-holds`` (v4) — ``type.category`` -> ``holds[].category`` through
  :data:`HOLD_CATEGORY_MAP` (an unknown category becomes ``"other"`` with a
  warning in the report), ``amount`` -> ``holds[].amount``,
  ``organization.name`` -> ``holds[].responsible_office``, ``placedOn`` ->
  ``holds[].hold_date``, ``releasedOn`` null or not -> ``holds[].resolved``.
- ``student-advisor-relationships`` (v2) — the advisor's person id ->
  ``advising.advisor_id``, pseudonymised with the ``A-`` prefix.
- ``student-appointments`` (v1) — the latest appointment with
  ``status == "completed"`` -> ``advising.last_appointment_date``;
  ``appointment_status`` is ``"completed"`` when one exists, else ``"none"``
  (cancelled appointments never count, per CONTRACTS.md M4).

Tenant assumption to confirm (docs/ELLUCIAN.md): the ``student``/``person``
reference ids in ``student-academic-periods``, ``person-holds``,
``student-advisor-relationships``, and ``student-appointments`` are the same
person ids that ``students[].person.id`` carries. When that does not hold,
no records join and the run refuses loudly ("the export would be empty")
instead of exporting zeros.

Resources that are never requested: everything not in
:data:`ALLOWED_RESOURCE_PATHS`. :func:`validate_resource_config` rejects,
before the first request, any configured path that is not exactly its
resource's allowed path, so persons, person emails and addresses,
emergency contacts, health records, counseling, spiritual care, financial
aid, and discipline are all refused, as are case tricks, trailing slashes,
query strings, and path traversal.

Transport rules: the base URL must be ``https`` (plain ``http`` is accepted
only for the 127.0.0.1/localhost mock), and redirects are never followed —
a 302 would otherwise forward the bearer credential to whatever host it
names. Error text is redacted against both the API key and the session
token, and neither is ever logged.

Pseudonymisation: ``student_id = "S-" + hmac_sha256(key, ethos_person_id)[:12]``
(and ``"A-"`` for advisor ids). The key comes from
``CABINET_PSEUDONYM_KEY_FILE`` — a file path, never an env value, never
stored, never printed, and refused when the file is group- or world-readable
(the docs say 0600). The mapping is one way: no reverse table is written
anywhere. Rotating or losing the key makes a re-import unlinkable to the
previous one.

Retention: the export file in ``var/exports/`` is a staging copy, removed
after a successful import (the stored dataset is the retained copy, purged
30 days after deletion); it is kept only on ``--dry-run``.

CLI (see RUNBOOK.md)::

    python -m cabinet.ellucian import --institution <slug> --term 202720
    python -m cabinet.ellucian import --institution <slug> --term 202720 --dry-run

Environment: ``CABINET_ETHOS_BASE_URL``, ``CABINET_ETHOS_API_KEY_FILE``,
``CABINET_PSEUDONYM_KEY_FILE``, ``CABINET_ETHOS_RESOURCES`` (JSON overrides),
``CABINET_ETHOS_TIMEZONE`` (default America/Chicago), ``CABINET_EXPORTS_DIR``
(default ``var/exports/``). A missing or unreadable key file is a one-line
refusal before any network access, and nothing is written. Every failure —
expected or not — prints one line, never a traceback.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_EXPORTS_DIR = REPO_ROOT / "var" / "exports"

ENV_BASE_URL = "CABINET_ETHOS_BASE_URL"
ENV_API_KEY_FILE = "CABINET_ETHOS_API_KEY_FILE"
ENV_PSEUDONYM_KEY_FILE = "CABINET_PSEUDONYM_KEY_FILE"
ENV_RESOURCES = "CABINET_ETHOS_RESOURCES"
ENV_TIMEZONE = "CABINET_ETHOS_TIMEZONE"
ENV_EXPORTS_DIR = "CABINET_EXPORTS_DIR"

DEFAULT_TIMEZONE = "America/Chicago"
DATE_FORMAT = "YYYY-MM-DD"

# Media type the tenant's Ethos Integration instance is assumed to answer:
# application/vnd.hedtech.integration.v<n>+json with per-resource <n>.
MEDIA_TYPE_TEMPLATE = "application/vnd.hedtech.integration.v{version}+json"

# The resources the connector requests, with assumed EDM versions;
# docs/ELLUCIAN.md says what to change per tenant. There is deliberately no
# `persons` entry: students[].person.id carries every id the connector needs.
DEFAULT_RESOURCES: dict[str, dict[str, Any]] = {
    "students": {"path": "students", "version": 6},
    "academic_periods": {"path": "academic-periods", "version": 3},
    "student_academic_periods": {"path": "student-academic-periods", "version": 4},
    "person_holds": {"path": "person-holds", "version": 4},
    "advisor_relationships": {"path": "student-advisor-relationships", "version": 2},
    "student_appointments": {"path": "student-appointments", "version": 1},
}

# The exact allow list: each connector resource may request its one Ethos
# resource path and nothing else. CABINET_ETHOS_RESOURCES may change a
# resource's version, never its path to anything outside this table, so
# persons, person-emails, person-addresses, person-emergency-contacts,
# health records, counseling, and every other resource are refused before
# the first request, however the configuration is edited.
ALLOWED_RESOURCE_PATHS: dict[str, str] = {
    key: str(spec["path"]) for key, spec in DEFAULT_RESOURCES.items()
}

# Ethos person-holds type categories -> the SCHEMA.md hold categories. An
# Ethos category absent from this table maps to "other" with a warning, so a
# tenant's local hold types surface in the report instead of silently
# vanishing or breaking the upload.
HOLD_CATEGORY_MAP = {
    "FINANCIAL": "financial",
    "ACADEMIC": "academic",
    "ADMINISTRATIVE": "administrative",
    "LIBRARY": "library",
}

REQUEST_TIMEOUT_SECONDS = 30.0
PAGE_SIZE = 200
# Hard cap: a runaway pager (or a tenant far bigger than expected) stops
# loudly here instead of streaming forever or silently truncating.
MAX_PAGES = 25
MAX_RETRIES = 4
BACKOFF_BASE_SECONDS = 0.5
BACKOFF_CAP_SECONDS = 8.0


class EthosError(RuntimeError):
    """A connector failure: configuration, transport, or mapping. The CLI
    prints the message as one line, never a traceback."""


@dataclass(frozen=True)
class ExportResult:
    """One built export: the SCHEMA.md document plus provenance and the
    warnings a human should read (unknown hold categories, skipped rows)."""

    document: dict[str, Any]
    warnings: list[str]
    row_counts: dict[str, int]
    resource_versions: dict[str, int]
    host: str


def _now() -> datetime:
    """The wall clock, in one place so tests can pin it."""
    return datetime.now(UTC)


def default_resources() -> dict[str, dict[str, Any]]:
    """A fresh copy of the default resource map (callers may mutate it)."""
    return {key: dict(spec) for key, spec in DEFAULT_RESOURCES.items()}


def validate_resource_config(resources: dict[str, dict[str, Any]]) -> None:
    """Refuse any configured resource that is not exactly on the allow list.

    Every key must be one of the connector's resources, and its path must be
    exactly that resource's path in :data:`ALLOWED_RESOURCE_PATHS` (so case
    tricks, trailing slashes, query strings, traversal, and every resource
    the connector does not need all fail); every version must be a positive
    int. Runs before the first request, whoever edited the configuration.
    """
    for key, spec in resources.items():
        allowed = ALLOWED_RESOURCE_PATHS.get(key)
        if allowed is None:
            raise EthosError(
                f"resource {key!r} is not one the connector requests; expected "
                "one of " + ", ".join(sorted(ALLOWED_RESOURCE_PATHS))
            )
        path = spec.get("path")
        if path != allowed:
            raise EthosError(
                f"resource {key!r}: path {path!r} is not on the allow list; "
                f"{key!r} may request only {allowed!r}, and nothing else is "
                "ever requested (no persons, person emails, addresses, "
                "emergency contacts, health, counseling, or financial-aid "
                "records; docs/ELLUCIAN.md)"
            )
        version = spec.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise EthosError(
                f"resource {key!r}: version must be a positive integer, "
                f"got {version!r}"
            )


def resources_from_env() -> dict[str, dict[str, Any]]:
    """The default resource map with ``CABINET_ETHOS_RESOURCES`` applied.

    The variable holds a JSON object keyed by the connector's resource names
    (``students``, ``academic_periods``, ...), each value an object with
    optional ``path`` and ``version`` overrides. Unknown keys are an error:
    a typo there would silently keep fetching the wrong resource. The merged
    map is validated before it is returned.
    """
    resources = default_resources()
    raw = os.environ.get(ENV_RESOURCES, "").strip()
    if not raw:
        return resources
    try:
        overrides: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EthosError(f"{ENV_RESOURCES} is not valid JSON ({exc})") from None
    if not isinstance(overrides, dict):
        raise EthosError(f"{ENV_RESOURCES} must be a JSON object")
    for key, override in overrides.items():
        if key not in resources:
            raise EthosError(
                f"{ENV_RESOURCES}: unknown resource {key!r}; expected one of "
                + ", ".join(sorted(resources))
            )
        if not isinstance(override, dict):
            raise EthosError(f"{ENV_RESOURCES}: {key!r} must be an object")
        for field_name in ("path", "version"):
            if field_name in override:
                resources[key][field_name] = override[field_name]
    validate_resource_config(resources)
    return resources


def read_key_file(env_var: str, *, what: str) -> bytes:
    """Read a secret key from the file the env var names.

    Keys come from files, never from env values, so they cannot leak through
    process listings or a dumped environment. The file must be private
    (group/world-readable is a refusal — the docs say 0600). Any failure is
    a one-line refusal; the key's content is never printed.
    """
    path = os.environ.get(env_var, "").strip()
    if not path:
        raise EthosError(
            f"{env_var} is not set; it must name a file holding the {what} "
            "(the key itself is never passed as an environment value)"
        )
    key_path = Path(path)
    try:
        mode = key_path.stat().st_mode
    except OSError as exc:
        raise EthosError(
            f"{env_var} names {path}, which cannot be read: {exc}"
        ) from None
    if mode & 0o077:
        raise EthosError(
            f"{env_var} names {path}, which is readable by group or others "
            f"(mode {mode & 0o777:04o}); a key file must be private: "
            f"chmod 0600 {path}"
        )
    try:
        key = key_path.read_bytes().strip()
    except OSError as exc:
        raise EthosError(
            f"{env_var} names {path}, which cannot be read: {exc}"
        ) from None
    if not key:
        raise EthosError(f"{env_var} names {path}, which is empty")
    return key


def pseudonym(key: bytes, ethos_id: str, *, prefix: str = "S") -> str:
    """``S-`` + the first 12 hex chars of HMAC-SHA256(key, ethos id).

    One way by construction: without the key (which never leaves the
    institution) the Ethos person id cannot be recovered, and no reverse
    table is written anywhere.
    """
    digest = hmac.new(key, ethos_id.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{prefix}-{digest[:12]}"


def _redact(text: str, *secrets_: str | None) -> str:
    """Guarantee a credential (API key, session token) never appears in an
    error message."""
    for secret in secrets_:
        if secret:
            text = text.replace(secret, "***")
    return text


class _RedirectRefused(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect: following one would forward the bearer
    credential to whatever host the redirect names, and the target's body
    would be accepted as records."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        raise EthosError(
            f"redirect refused (HTTP {code}); the connector never follows "
            "redirects, so the bearer credential cannot be forwarded to "
            "another host"
        )


class EthosClient:
    """Stdlib-only Ethos Integration client: token exchange and paged GETs.

    ``POST /auth`` trades the API key (sent as the bearer credential) for a
    session token; ``GET /api/<resource>?offset=&limit=`` pages through one
    resource with the versioned Hedtech media type. 429 and 5xx answers retry
    with exponential backoff (``Retry-After`` honored); a 401 is fatal (the
    run stops with one line rather than re-authenticating mid-export).
    Redirects are never followed and the base URL must be https (except the
    127.0.0.1/localhost mock). Paging stops at the hard page cap with a loud
    error — a truncated resource would silently corrupt every metric. The
    API key and token are never logged, and error text is redacted against
    both.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        resources: dict[str, dict[str, Any]] | None = None,
        *,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
        page_size: int = PAGE_SIZE,
        max_pages: int = MAX_PAGES,
        max_retries: int = MAX_RETRIES,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        split = urllib.parse.urlsplit(base_url.rstrip("/"))
        if split.scheme != "https" and split.hostname not in (
            "127.0.0.1",
            "localhost",
        ):
            raise EthosError(
                f"the Ethos base URL must use https (got "
                f"{split.scheme or 'no scheme'} for host "
                f"{split.hostname or '?'}); plain http is accepted only for "
                "the 127.0.0.1/localhost mock"
            )
        self.base_url = base_url.rstrip("/")
        self.host = split.netloc
        self._api_key = api_key
        self.resources = resources if resources is not None else default_resources()
        self.timeout = timeout
        self.page_size = page_size
        self.max_pages = max_pages
        self.max_retries = max_retries
        self._sleeper = sleeper
        self._token: str | None = None
        self._opener = urllib.request.build_opener(_RedirectRefused())

    def resource_versions(self) -> dict[str, int]:
        """Resource path -> version, for the export's provenance block."""
        return {
            str(spec["path"]): int(spec["version"]) for spec in self.resources.values()
        }

    def _authenticate(self) -> None:
        """Trade the API key for a session token (POST /auth)."""
        request = urllib.request.Request(
            self.base_url + "/auth",
            data=b"",
            headers={"Authorization": f"Bearer {self._api_key}"},
            method="POST",
        )
        body = self._open_with_retry(request, what="the Ethos token exchange")
        try:
            parsed: Any = json.loads(body)
        except json.JSONDecodeError:
            token: Any = body.strip()
        else:
            token = parsed.get("token") if isinstance(parsed, dict) else parsed
        if not isinstance(token, str) or not token:
            raise EthosError("the Ethos /auth response carried no token")
        self._token = token

    def _sleep(self, seconds: float) -> None:
        if seconds > 0:
            self._sleeper(seconds)

    def _retry_delay(self, exc: urllib.error.HTTPError, attempt: int) -> float:
        """Backoff for one failed attempt: the server's Retry-After when it
        sends one (capped), else exponential backoff."""
        retry_after = exc.headers.get("Retry-After") if exc.headers else None
        if retry_after is not None:
            try:
                return min(float(retry_after), 30.0)
            except ValueError:
                pass  # a date-shaped Retry-After: fall back to backoff
        backoff: float = min(BACKOFF_BASE_SECONDS * 2**attempt, BACKOFF_CAP_SECONDS)
        return backoff

    def _open_with_retry(self, request: urllib.request.Request, *, what: str) -> str:
        """Open one request, retrying 429/5xx with backoff. Timeouts are not
        retried (one attempt already spent the wall-time budget); other 4xx
        fail at once. An error message carries the status code plus at most
        one line of the server's body: whitespace collapsed, truncated, and
        redacted against both credentials."""
        reason = ""
        for attempt in range(self.max_retries + 1):
            try:
                with self._opener.open(request, timeout=self.timeout) as resp:
                    body: bytes = resp.read()
                    return body.decode("utf-8")
            except urllib.error.HTTPError as exc:
                # One sanitized line: a server error body could carry
                # newlines (log injection) or record text (kept short).
                detail = " ".join(
                    exc.read().decode("utf-8", errors="replace").split()
                )[:200]
                reason = _redact(
                    f"{what} failed: HTTP {exc.code} "
                    f"({detail.strip() or 'no detail'})",
                    self._api_key,
                    self._token,
                )
                retryable = exc.code == 429 or exc.code >= 500
                if not retryable or attempt == self.max_retries:
                    raise EthosError(reason) from None
                self._sleep(self._retry_delay(exc, attempt))
            except TimeoutError:
                raise EthosError(
                    f"{what} did not answer within {self.timeout:.0f} s"
                ) from None
            except urllib.error.URLError as exc:
                reason = _redact(
                    f"{what} failed: connection error ({exc.reason})",
                    self._api_key,
                    self._token,
                )
                if (
                    isinstance(exc.reason, TimeoutError)
                    or attempt == self.max_retries
                ):
                    raise EthosError(reason) from None
                self._sleep(
                    min(BACKOFF_BASE_SECONDS * 2**attempt, BACKOFF_CAP_SECONDS)
                )
        raise EthosError(reason)  # pragma: no cover - the loop exits above

    def _get_page(self, path: str, version: int, offset: int) -> list[Any]:
        """One page of one resource."""
        if self._token is None:
            self._authenticate()
        query = urllib.parse.urlencode({"offset": offset, "limit": self.page_size})
        url = f"{self.base_url}/api/{path}?{query}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": MEDIA_TYPE_TEMPLATE.format(version=version),
                "Authorization": f"Bearer {self._token}",
            },
            method="GET",
        )
        body = self._open_with_retry(request, what=f"GET /api/{path}")
        try:
            page: Any = json.loads(body)
        except json.JSONDecodeError:
            raise EthosError(
                f"GET /api/{path} returned a body that is not JSON"
            ) from None
        if not isinstance(page, list):
            raise EthosError(
                f"GET /api/{path} returned {type(page).__name__}, "
                "expected a JSON array page"
            )
        return page

    def fetch(self, resource_key: str) -> list[Any]:
        """Every page of one configured resource, up to the hard page cap.

        A full page exactly at the cap is treated as overflow: the next page
        might carry more rows, and silently truncating would produce wrong
        metrics, so the run stops loudly instead.
        """
        spec = self.resources[resource_key]
        path, version = str(spec["path"]), int(spec["version"])
        items: list[Any] = []
        offset = 0
        for page_number in range(1, self.max_pages + 1):
            page = self._get_page(path, version, offset)
            items.extend(page)
            if len(page) < self.page_size:
                return items
            if page_number == self.max_pages:
                raise EthosError(
                    f"resource {path!r} hit the page cap ({self.max_pages} "
                    f"pages of {self.page_size}); refusing to truncate the "
                    "export — raise the cap only after checking the tenant's "
                    "row counts"
                )
            offset += self.page_size
        return items  # pragma: no cover - the loop exits above


# -- mapping helpers ----------------------------------------------------------


def _ref_id(record: Any, key: str) -> str | None:
    """The id out of an Ethos nested reference (``{"person": {"id": ...}}``)."""
    if isinstance(record, dict):
        ref = record.get(key)
        if isinstance(ref, dict) and isinstance(ref.get("id"), str):
            return str(ref["id"])
    return None


def _opt_str(value: Any) -> str | None:
    return str(value) if isinstance(value, str) and value else None


def _required_str(obj: dict[str, Any], key: str, *, resource: str) -> str:
    """A required string field; a missing one is a named refusal, never a
    bare KeyError."""
    value = obj.get(key)
    if not isinstance(value, str) or not value:
        raise EthosError(f"a {resource} record is missing the field {key!r}")
    return value


def _parse_iso_date(value: Any, *, field: str) -> date:
    """An ISO date or datetime (any offset, or Z) -> a calendar date.

    Anything else is a named refusal. The message names the field, never the
    value: the value could carry record data.
    """
    if isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            return date.fromisoformat(text)
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(text).date()
        except ValueError:
            pass
    raise EthosError(f"{field}: expected an ISO date or datetime string")


def _opt_iso_date(value: Any, *, field: str) -> date | None:
    if value is None or value == "":
        return None
    return _parse_iso_date(value, field=field)


def _required_date(obj: dict[str, Any], key: str, *, resource: str) -> date:
    if key not in obj or obj[key] is None:
        raise EthosError(f"a {resource} record is missing the field {key!r}")
    return _parse_iso_date(obj[key], field=f"{resource}.{key}")


def _period_registration_date(
    period: dict[str, Any], key: str, *, required: bool
) -> date | None:
    registration = period.get("registration")
    raw = registration.get(key) if isinstance(registration, dict) else None
    if raw is None:
        if required:
            code = period.get("code", "?")
            raise EthosError(
                f"the academic-periods record for term {code} is missing "
                f"the field 'registration.{key}'"
            )
        return None
    return _parse_iso_date(raw, field=f"academic-periods.registration.{key}")


def _status(value: Any) -> str:
    """Ethos registration status -> the SCHEMA vocabulary. Only an explicit
    ``registered`` counts as registered; every other status (or none) is
    ``not_registered``, matching M2's ``≠ "registered"`` test."""
    if str(value).strip().lower() == "registered":
        return "registered"
    return "not_registered"


def _year_before(day: date) -> date:
    """Same calendar date one year earlier (Feb 29 clamps to Feb 28)."""
    try:
        return day.replace(year=day.year - 1)
    except ValueError:
        return day.replace(year=day.year - 1, day=28)


def _prior_term_code(term: str) -> str:
    """The Banner-style prior-year term code (202720 -> 202620)."""
    try:
        return str(int(term) - 100)
    except ValueError:
        raise EthosError(
            f"term code {term!r} is not numeric; the prior-year term is "
            "derived as int(term) - 100 (Banner-style codes), so a "
            "non-numeric code needs an explicit mapping per tenant"
        ) from None


def _period_index(periods: list[Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for period in periods:
        if isinstance(period, dict) and isinstance(period.get("id"), str):
            index[str(period["id"])] = period
    return index


def _enrollment_from(sap: dict[str, Any], period: dict[str, Any]) -> dict[str, Any]:
    hours = sap.get("creditHours", 0)
    registered = _opt_iso_date(
        sap.get("registeredOn"), field="student-academic-periods.registeredOn"
    )
    return {
        "term": str(period["code"]),
        "registration_status": _status(sap.get("registrationStatus")),
        "registered_credit_hours": int(hours)
        if isinstance(hours, (int, float))
        and not isinstance(hours, bool)
        else 0,
        "registration_date": registered.isoformat() if registered else None,
    }


def build_export_document(
    client: EthosClient,
    *,
    pseudonym_key: bytes,
    term: str,
    institution_name: str,
    timezone: str = DEFAULT_TIMEZONE,
    extracted_at: datetime | None = None,
) -> ExportResult:
    """Fetch the configured resources and build the SCHEMA.md document.

    The resource allow-list and deny list are validated before the first
    request. ``extracted_at`` is injectable so tests can pin it; the CLI
    lets it default to now (UTC).
    """
    validate_resource_config(client.resources)
    warnings: list[str] = []

    periods_raw = client.fetch("academic_periods")
    students_raw = client.fetch("students")
    saps_raw = client.fetch("student_academic_periods")
    holds_raw = client.fetch("person_holds")
    rels_raw = client.fetch("advisor_relationships")
    appointments_raw = client.fetch("student_appointments")

    periods = _period_index(periods_raw)
    by_code = {
        str(p["code"]): p for p in periods.values() if isinstance(p.get("code"), str)
    }
    current = by_code.get(term)
    if current is None:
        raise EthosError(
            f"no academic-period with code {term!r} on this tenant "
            f"(known codes: {', '.join(sorted(by_code)) or 'none'})"
        )
    prior_code = _prior_term_code(term)
    prior = by_code.get(prior_code)
    if prior is None:
        raise EthosError(
            f"no academic-period with the prior-year code {prior_code!r} "
            f"(derived from {term!r}) on this tenant"
        )
    # Required period fields fail with named refusals, never bare KeyErrors.
    current_start = _required_date(current, "startOn", resource="academic-periods")
    prior_start = _required_date(prior, "startOn", resource="academic-periods")
    current_open = _period_registration_date(current, "openOn", required=True)
    current_close = _period_registration_date(current, "closeOn", required=False)
    prior_open = _period_registration_date(prior, "openOn", required=True)
    prior_close = _period_registration_date(prior, "closeOn", required=False)

    # The pseudonym source: students[].person.id. The tenant-wide persons
    # resource is never requested — names, emails, credentials, and phones
    # never cross the wire at all. Persons without a students record
    # (employees, applicants) never appear.
    student_by_person: dict[str, dict[str, Any]] = {}
    person_ids: list[str] = []
    for record in students_raw:
        if not isinstance(record, dict):
            continue
        person_id = _ref_id(record, "person")
        if person_id is None:
            warnings.append("a students record without a person id was skipped")
            continue
        student_by_person[person_id] = record
        person_ids.append(person_id)

    saps_by_person_period: dict[str, dict[str, dict[str, Any]]] = {}
    for record in saps_raw:
        if not isinstance(record, dict):
            continue
        person_id = _ref_id(record, "student")
        period_id = _ref_id(record, "academicPeriod")
        if person_id is None or period_id is None:
            warnings.append(
                "a student-academic-periods record without a student or "
                "period reference was skipped"
            )
            continue
        saps_by_person_period.setdefault(person_id, {})[period_id] = record

    holds_by_person: dict[str, list[dict[str, Any]]] = {}
    for record in holds_raw:
        if not isinstance(record, dict):
            continue
        person_id = _ref_id(record, "person")
        if person_id is None:
            warnings.append("a person-holds record without a person id was skipped")
            continue
        category_raw = "ADMINISTRATIVE"
        hold_type = record.get("type")
        if isinstance(hold_type, dict) and isinstance(hold_type.get("category"), str):
            category_raw = str(hold_type["category"]).strip().upper()
        category = HOLD_CATEGORY_MAP.get(category_raw)
        if category is None:
            category = "other"
            warnings.append(
                f"person-holds type category {category_raw!r} is not in the "
                "mapping table; mapped to 'other'"
            )
        amount = record.get("amount", 0)
        office = record.get("organization")
        placed = _required_date(record, "placedOn", resource="person-holds")
        released = _opt_iso_date(
            record.get("releasedOn"), field="person-holds.releasedOn"
        )
        holds_by_person.setdefault(person_id, []).append(
            {
                "category": category,
                "amount": float(amount) if isinstance(amount, (int, float)) else 0.0,
                "responsible_office": (
                    str(office["name"])
                    if isinstance(office, dict) and office.get("name")
                    else "Unknown"
                ),
                "hold_date": placed.isoformat(),
                "resolved": released is not None,
            }
        )

    advisor_by_person: dict[str, str] = {}
    for record in rels_raw:
        if not isinstance(record, dict):
            continue
        person_id = _ref_id(record, "student")
        advisor_id = _ref_id(record, "advisor")
        if person_id is not None and advisor_id is not None:
            advisor_by_person[person_id] = advisor_id

    last_completed_by_person: dict[str, date] = {}
    for record in appointments_raw:
        if not isinstance(record, dict):
            continue
        if str(record.get("status", "")).strip().lower() != "completed":
            continue  # a cancelled appointment never counts (CONTRACTS.md M4)
        person_id = _ref_id(record, "student")
        day = _opt_iso_date(
            record.get("startOn"), field="student-appointments.startOn"
        )
        if person_id is None or day is None:
            continue
        previous = last_completed_by_person.get(person_id)
        if previous is None or day > previous:
            last_completed_by_person[person_id] = day

    # The as-of date is derived from the data (CONTRACTS.md §0): the latest
    # current-term registration date, never the wall clock.
    current_dates = [
        day
        for saps in saps_by_person_period.values()
        for period_id, sap in saps.items()
        if period_id == current["id"]
        for day in [
            _opt_iso_date(
                sap.get("registeredOn"),
                field="student-academic-periods.registeredOn",
            )
        ]
        if day is not None
    ]
    if not current_dates:
        raise EthosError(
            "no current-term student-academic-periods record carries a "
            "registration date; the as-of date cannot be derived"
        )
    as_of = max(current_dates)
    equivalent = _year_before(as_of)

    in_session: dict[str, Any] | None = None
    in_session_start: date | None = None
    for period in periods.values():
        start = _opt_iso_date(period.get("startOn"), field="academic-periods.startOn")
        if start is None or start > as_of or period["id"] == current["id"]:
            continue
        if in_session_start is None or start > in_session_start:
            in_session = period
            in_session_start = start
    if in_session is None:
        raise EthosError(
            "no academic period in session at the as-of date "
            f"({as_of.isoformat()})"
        )
    in_session_end = _required_date(in_session, "endOn", resource="academic-periods")

    def advising_for(person_id: str) -> dict[str, Any]:
        advisor_id = advisor_by_person.get(person_id)
        last = last_completed_by_person.get(person_id)
        return {
            "advisor_id": (
                pseudonym(pseudonym_key, advisor_id, prefix="A")
                if advisor_id
                else "A-unassigned"
            ),
            "last_appointment_date": last.isoformat() if last else None,
            "appointment_status": "completed" if last is not None else "none",
        }

    def profile_for(person_id: str) -> dict[str, Any]:
        record = student_by_person[person_id]
        program = "UNKNOWN"
        programs = record.get("programs")
        if isinstance(programs, list) and programs:
            first = programs[0]
            if isinstance(first, dict) and first.get("code"):
                program = str(first["code"])
        level = record.get("academicLevel")
        class_level = (
            str(level["code"]).strip().lower()
            if isinstance(level, dict) and level.get("code")
            else "unknown"
        )
        return {
            "student_id": pseudonym(pseudonym_key, person_id),
            "program": program,
            "class_level": class_level,
            "continuing": record.get("continuing") is True,
        }

    def prior_term_status(
        saps: dict[str, dict[str, Any]], baseline_code: str
    ) -> str:
        """The person's registration status in the baseline term: term code
        -> period -> period id -> the person's record for that period."""
        baseline = by_code.get(baseline_code)
        if baseline is None:
            return "not_enrolled"
        sap = saps.get(str(baseline["id"]))
        if sap is None:
            return "not_enrolled"
        return _status(sap.get("registrationStatus"))

    prior_prior_code = str(int(prior_code) - 100)
    students_out: list[dict[str, Any]] = []
    prior_out: list[dict[str, Any]] = []
    for person_id in person_ids:
        saps = saps_by_person_period.get(person_id, {})
        current_sap = saps.get(str(current["id"]))
        prior_sap = saps.get(str(prior["id"]))
        if current_sap is not None:
            students_out.append(
                {
                    "profile": profile_for(person_id),
                    "enrollment": _enrollment_from(current_sap, current),
                    "holds": holds_by_person.get(person_id, []),
                    "advising": advising_for(person_id),
                    "comparison": {
                        "prior_year_equivalent_date": equivalent.isoformat(),
                        "prior_term_status": prior_term_status(saps, prior_code),
                        "baseline": prior_code,
                    },
                }
            )
        elif prior_sap is not None:
            # Last year's cohort row: a person with a prior-year record and
            # no current-term record. Prior-year rows never carry holds.
            prior_out.append(
                {
                    "profile": profile_for(person_id),
                    "enrollment": _enrollment_from(prior_sap, prior),
                    "holds": [],
                    "advising": advising_for(person_id),
                    "comparison": {
                        "prior_year_equivalent_date": _year_before(
                            equivalent
                        ).isoformat(),
                        "prior_term_status": prior_term_status(
                            saps, prior_prior_code
                        ),
                        "baseline": prior_prior_code,
                    },
                }
            )

    if not students_out:
        raise EthosError(
            f"no student records for term {term!r}; the export would be "
            "empty. Check the tenant's id join: the student/person "
            "references in student-academic-periods, person-holds, "
            "student-advisor-relationships, and student-appointments must "
            "carry the same person ids as students[].person.id "
            "(docs/ELLUCIAN.md)"
        )

    extracted = extracted_at or _now()
    row_counts = {
        "students": len(students_out),
        "prior_year_students": len(prior_out),
    }
    resource_versions = client.resource_versions()
    document: dict[str, Any] = {
        "meta": {
            "title": (
                f"{institution_name} — Ethos export for "
                f"{current.get('title', term)} ({term})"
            ),
            "description": (
                "Exported from the institution's Ellucian Ethos Integration "
                "API at the edge by cabinet.ellucian; student identifiers "
                "are one-way keyed-hash pseudonyms whose key never leaves "
                "the institution."
            ),
            "fictional": False,
            "timezone": timezone,
            "date_format": DATE_FORMAT,
            "source": {
                "system": "Ellucian Ethos Integration API",
                "host": client.host,
                "term": term,
                "resource_versions": resource_versions,
                "extracted_at": extracted.isoformat(),
                "row_counts": row_counts,
            },
        },
        "terms": {
            "current": {
                "term": term,
                "name": str(current.get("title", term)),
                "start_date": current_start.isoformat(),
                "registration_open_date": (
                    current_open.isoformat() if current_open else None
                ),
                "registration_close_date": (
                    current_close.isoformat() if current_close else None
                ),
                "as_of_rule": (
                    "as_of = max(enrollment.registration_date) over "
                    "current-term rows (the 'students' list); never the "
                    "wall clock"
                ),
                "timezone": timezone,
            },
            "prior_year": {
                "term": prior_code,
                "name": str(prior.get("title", prior_code)),
                "start_date": prior_start.isoformat(),
                "registration_open_date": (
                    prior_open.isoformat() if prior_open else None
                ),
                "registration_close_date": (
                    prior_close.isoformat() if prior_close else None
                ),
                "prior_year_equivalent_date": equivalent.isoformat(),
                "as_of_rule": (
                    "prior-year window anchored to "
                    "prior_year_equivalent_date (the current-term as-of "
                    "date shifted back one year)"
                ),
                "timezone": timezone,
            },
            "in_session": {
                "term": str(in_session.get("code")),
                "name": str(in_session.get("title")),
                "start_date": (
                    in_session_start.isoformat() if in_session_start else None
                ),
                "end_date": in_session_end.isoformat(),
                "as_of_rule": (
                    "the academic period with the latest start date on or "
                    "before the as-of date"
                ),
                "timezone": timezone,
            },
        },
        "students": students_out,
        "prior_year_students": prior_out,
    }
    return ExportResult(
        document=document,
        warnings=warnings,
        row_counts=row_counts,
        resource_versions=resource_versions,
        host=client.host,
    )


def render_export(result: ExportResult) -> bytes:
    """Serialize the export document; keys sorted, matching the fixture's
    stored shape."""
    return (
        json.dumps(result.document, indent=2, sort_keys=True, ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


# -- CLI ----------------------------------------------------------------------


def _exports_dir(override: str | None) -> Path:
    if override:
        return Path(override)
    env = os.environ.get(ENV_EXPORTS_DIR, "").strip()
    return Path(env) if env else DEFAULT_EXPORTS_DIR


def _write_export(export_dir: Path, slug: str, term: str, raw: bytes) -> Path:
    """Write the export file (0600, create-exclusive).

    The name carries microseconds plus a short random suffix, so two imports
    in the same clock second cannot collide; a remaining FileExistsError
    becomes a one-line refusal after three attempts.
    """
    export_dir.mkdir(parents=True, exist_ok=True)
    stamp = _now().strftime("%Y%m%dT%H%M%S%f")
    for _ in range(3):
        path = export_dir / f"{slug}-{term}-{stamp}-{secrets.token_hex(3)}.json"
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        return path
    raise EthosError(
        f"could not create a unique export file in {export_dir} "
        "after 3 attempts"
    )


def _import(args: argparse.Namespace) -> int:
    """The import command body; failures raise EthosError for main() to
    print as one line."""
    from cabinet.auth import db_path_from_env
    from cabinet.datasets import UploadError, validate_upload
    from cabinet.provider import load_local_env
    from cabinet.store import CabinetStore

    load_local_env()
    # Keys and configuration first: a refusal here means nothing was
    # fetched and nothing was written.
    pseudonym_key = read_key_file(ENV_PSEUDONYM_KEY_FILE, what="pseudonym key")
    api_key = read_key_file(ENV_API_KEY_FILE, what="Ethos API key").decode("utf-8")
    base_url = os.environ.get(ENV_BASE_URL, "").strip()
    if not base_url:
        raise EthosError(
            f"{ENV_BASE_URL} is not set; it must name the institution's "
            "Ethos Integration base URL"
        )
    resources = resources_from_env()
    timezone = os.environ.get(ENV_TIMEZONE, "").strip() or DEFAULT_TIMEZONE

    slug = args.institution.strip().lower()
    institution_name = slug
    institution_id: int | None = None
    store: CabinetStore | None = None
    if not args.dry_run:
        # A set-but-empty CABINET_DB is a configuration error, one line.
        db_path = db_path_from_env()
        store = CabinetStore(db_path)
        institution = store.institution_by_slug(slug)
        if institution is None:
            raise EthosError(
                f"no institution with slug {slug!r} "
                "(create it with `make institution NAME=... SLUG=...`)"
            )
        institution_id = int(institution["id"])
        institution_name = str(institution["name"])

    client = EthosClient(base_url, api_key, resources)
    result = build_export_document(
        client,
        pseudonym_key=pseudonym_key,
        term=args.term.strip(),
        institution_name=institution_name,
        timezone=timezone,
    )
    raw = render_export(result)
    try:
        report = validate_upload(raw)
    except UploadError as exc:
        details = "; ".join(exc.errors)
        raise EthosError(
            f"the export failed validation; nothing written: {details}"
        ) from None

    export_dir = _exports_dir(args.export_dir)
    path = _write_export(export_dir, slug, args.term.strip(), raw)

    print(f"wrote {path} ({len(raw)} bytes)")
    print(
        "validation: ok — "
        f"row_counts={json.dumps(report.row_counts, sort_keys=True)}, "
        f"counseling={report.counseling_note}, fictional={report.fictional}"
    )
    if result.warnings:
        for warning in result.warnings:
            print(f"warning: {warning}")
    else:
        print("warnings: none")

    if args.dry_run:
        print("dry run: nothing uploaded; the export file is kept for review")
        return 0

    assert store is not None and institution_id is not None
    meta = report.document.get("meta")
    name = (
        str(meta.get("title")).strip()
        if isinstance(meta, dict) and meta.get("title")
        else f"Ethos import {slug} {args.term.strip()}"
    )
    try:
        dataset = store.add_dataset_with_audit(
            institution_id,
            name=name,
            raw=report.stored_raw,
            uploaded_by="ethos-import",
            audit_actor="system",
            audit_extra={
                "fictional": report.fictional,
                "counseling": report.counseling_note,
                "importer": "ethos",
            },
            row_counts=report.row_counts,
        )
    except Exception as exc:
        # The dataset and its audit event commit as one unit, so on any
        # failure the store holds no new dataset and "nothing was stored"
        # is true. The export copy stays, named here for recovery.
        raise EthosError(
            f"the upload failed ({type(exc).__name__}); nothing was "
            "stored — the dataset and its audit event roll back together. "
            f"The export copy is kept at {path}"
        ) from None
    # Retention: the export file is a staging copy. Once the dataset is
    # stored, the stored document is the retained copy (purged 30 days after
    # deletion), so the second copy in var/exports is removed; exports are
    # kept only on --dry-run.
    try:
        path.unlink()
    except OSError as exc:
        print(f"warning: could not remove the export copy {path}: {exc}")
    else:
        print(f"removed the export copy {path} (kept only on --dry-run)")
    print(
        f"uploaded dataset {dataset['id']} ({name!r}) to institution "
        f"{slug!r} — inactive until an admin activates it"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """``python -m cabinet.ellucian import --institution <slug> --term <code>``
    ``[--dry-run] [--export-dir DIR]``.

    Writes ``var/exports/<slug>-<term>-<timestamp>-<suffix>.json`` (0600),
    validates it with the same ``validate_upload`` the admin UI upload goes
    through, and — unless ``--dry-run`` — stores it with
    ``store.add_dataset`` (inactive until an admin activates it), audits the
    upload, and removes the export copy. Every failure prints one line,
    never a traceback.
    """
    parser = argparse.ArgumentParser(prog="python -m cabinet.ellucian")
    sub = parser.add_subparsers(dest="command", required=True)
    imp = sub.add_parser(
        "import", help="export one term from Ethos and upload it (inactive)"
    )
    imp.add_argument("--institution", required=True, help="institution slug")
    imp.add_argument("--term", required=True, help="academic period code")
    imp.add_argument(
        "--dry-run",
        action="store_true",
        help="write the export and print the validation report; upload nothing",
    )
    imp.add_argument(
        "--export-dir",
        default=None,
        help=f"export directory (default {ENV_EXPORTS_DIR} or var/exports/)",
    )
    args = parser.parse_args(argv)

    try:
        return _import(args)
    except EthosError as exc:
        print(f"import-ethos: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # The one-line promise holds even for a bug: name the exception
        # class only — the message could carry record data.
        print(
            f"import-ethos: unexpected {type(exc).__name__} "
            "(details suppressed); nothing was stored",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
