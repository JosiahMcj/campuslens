"""A mock Ellucian Ethos Integration server for tests and demos.

An ``http.server.ThreadingHTTPServer`` on 127.0.0.1 bound to an ephemeral
port, run in a thread. It serves exactly what ``cabinet.ellucian`` expects:

- ``POST /auth`` — the API key (as the bearer credential) in, a session
  token out; a wrong key is a 401.
- ``GET /api/<resource>?offset=&limit=`` — paged JSON arrays with the
  versioned Hedtech media type requested; a missing or wrong bearer token is
  a 401.

Knobs (all off by default):

- ``rate_limit_once``: these resources answer one 429 with ``Retry-After``
  (``retry_after`` seconds) and then serve normally.
- ``expire_token_after``: after this many GETs have been served, every
  further GET answers 401 (the session token died mid-run).
- ``redirect``: resource path -> absolute URL; a GET on the resource answers
  302 with that Location (the connector must refuse to follow it).

The resources are generated from ``data/fixture.json`` in reverse of the
connector's mapping, so the round trip mock -> connector -> dataset is
checkable against the fixture's planted metrics. Deliberate plants:

- one person record carries a name, an email, an SSN, and a phone number
  (``PLANTED_*``). It is served on ``/api/persons``, which the connector
  must never request — the test suite asserts both that the path is never
  hit and that none of the planted strings reach the export;
- one person-holds record carries the unknown category ``PARKING``, which
  the connector maps to ``"other"`` with a warning;
- a 202520 academic period with per-student records, so the prior-year
  students' own ``comparison.prior_term_status`` has a real source.

Nothing here touches the network beyond 127.0.0.1, and nothing is written
to disk.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO_ROOT / "data" / "fixture.json"

# Test-fixture credentials, not real secrets; the mock checks them so the
# connector's auth path is exercised.
MOCK_API_KEY = "mock-ethos-api-key-not-a-real-secret"
MOCK_TOKEN = "mock-ethos-session-token"

# The planted identity that must never survive into an export.
PLANTED_NAME = "Zephyr Testperson"
PLANTED_EMAIL = "zephyr.testperson@example.edu"
PLANTED_SSN = "123-45-6789"
PLANTED_PHONE = "555-0100"


def load_fixture_document(path: Path = FIXTURE_PATH) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return document


def _guid(seq: int) -> str:
    """A deterministic UUID-shaped id, so two runs against the same key
    produce byte-identical pseudonyms."""
    return f"00000000-0000-4000-8000-{seq:012d}"


def person_guid(index: int) -> str:
    return _guid(100000 + index)


def advisor_guid(advisor_number: int) -> str:
    return _guid(200000 + advisor_number)


def period_guid(slot: int) -> str:
    return _guid(300000 + slot)


def _sap_record(
    seq: int, pid: str, period_slot: int, status: str, hours: int, on: str | None
) -> dict[str, Any]:
    return {
        "id": _guid(seq),
        "student": {"id": pid},
        "academicPeriod": {"id": period_guid(period_slot)},
        "registrationStatus": (
            "registered" if status == "registered" else "notRegistered"
        ),
        "creditHours": hours,
        "registeredOn": on,
    }


def build_ethos_resources(document: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Turn a SCHEMA.md dataset document into Ethos-shaped resources.

    This is the connector's mapping run in reverse: every fixture record
    becomes a person plus a students record plus one or two
    student-academic-periods records, holds become person-holds, and advising
    becomes a student-advisor-relationships row plus at most one completed
    student-appointments row. Continuing current-term students also get a
    prior-year period record so ``comparison.prior_term_status`` has a real
    source, and prior-year students get a 202520 record so their own
    comparison does too.
    """
    terms = document["terms"]
    periods = [
        {
            "id": period_guid(1),
            "code": terms["current"]["term"],
            "title": terms["current"]["name"],
            "startOn": terms["current"]["start_date"],
            "endOn": "2027-05-07",  # terms.current carries no end date
            "registration": {
                "openOn": terms["current"]["registration_open_date"],
                "closeOn": terms["current"]["registration_close_date"],
            },
        },
        {
            "id": period_guid(2),
            "code": terms["prior_year"]["term"],
            "title": terms["prior_year"]["name"],
            "startOn": terms["prior_year"]["start_date"],
            "endOn": "2026-05-08",  # terms.prior_year carries no end date
            "registration": {
                "openOn": terms["prior_year"]["registration_open_date"],
                "closeOn": terms["prior_year"]["registration_close_date"],
            },
        },
        {
            "id": period_guid(3),
            "code": terms["in_session"]["term"],
            "title": terms["in_session"]["name"],
            "startOn": terms["in_session"]["start_date"],
            "endOn": terms["in_session"]["end_date"],
        },
        {
            # The baseline term for the prior-year cohort's own comparison.
            "id": period_guid(4),
            "code": "202520",
            "title": "Spring 2025",
            "startOn": "2025-01-13",
            "endOn": "2025-05-09",
            "registration": {"openOn": "2024-11-04", "closeOn": "2024-12-20"},
        },
    ]

    persons: list[dict[str, Any]] = []
    students: list[dict[str, Any]] = []
    saps: list[dict[str, Any]] = []
    holds: list[dict[str, Any]] = []
    rels: list[dict[str, Any]] = []
    appointments: list[dict[str, Any]] = []

    current_records = list(document["students"])
    prior_records = list(document["prior_year_students"])
    records = current_records + prior_records
    for index, record in enumerate(records):
        pid = person_guid(index)
        person: dict[str, Any] = {
            "id": pid,
            "names": [{"firstName": f"First{index}", "lastName": f"Last{index}"}],
        }
        if index == 0:
            # The planted identity: the connector must never request this
            # resource at all, and none of it may reach the export.
            person["names"] = [
                {
                    "firstName": PLANTED_NAME.split()[0],
                    "lastName": PLANTED_NAME.split()[1],
                    "preference": "preferred",
                }
            ]
            person["emails"] = [{"address": PLANTED_EMAIL}]
            person["credentials"] = [{"type": {"detail": "ssn"}, "value": PLANTED_SSN}]
            person["phones"] = [{"number": PLANTED_PHONE}]
        persons.append(person)

        profile = record["profile"]
        students.append(
            {
                "id": _guid(400000 + index),
                "person": {"id": pid},
                "programs": [{"code": profile["program"]}],
                "academicLevel": {"code": profile["class_level"]},
                "continuing": profile["continuing"],
            }
        )

        enrollment = record["enrollment"]
        is_current = enrollment["term"] == terms["current"]["term"]
        saps.append(
            _sap_record(
                500000 + index,
                pid,
                1 if is_current else 2,
                enrollment["registration_status"],
                enrollment["registered_credit_hours"],
                enrollment["registration_date"],
            )
        )
        # The record for the term the comparison block talks about: the
        # prior-year period for continuing current-term students, the 202520
        # period for prior-year students.
        prior_status = record["comparison"]["prior_term_status"]
        if prior_status != "not_enrolled":
            saps.append(
                _sap_record(
                    600000 + index,
                    pid,
                    2 if is_current else 4,
                    prior_status,
                    0,
                    None,
                )
            )

        for hold_index, hold in enumerate(record["holds"]):
            holds.append(
                {
                    "id": _guid(700000 + index * 10 + hold_index),
                    "person": {"id": pid},
                    "type": {"category": str(hold["category"]).upper()},
                    "amount": float(hold["amount"]),
                    "organization": {"name": hold["responsible_office"]},
                    "placedOn": hold["hold_date"],
                    # The fixture stores only the resolved flag; a resolved
                    # hold gets its placement date as the release date.
                    "releasedOn": hold["hold_date"] if hold["resolved"] else None,
                }
            )

        advising = record["advising"]
        advisor_number = int(str(advising["advisor_id"]).rsplit("-", 1)[1])
        rels.append(
            {
                "id": _guid(800000 + index),
                "student": {"id": pid},
                "advisor": {"id": advisor_guid(advisor_number)},
            }
        )
        if advising["appointment_status"] == "completed":
            appointments.append(
                {
                    "id": _guid(900000 + index),
                    "student": {"id": pid},
                    "startOn": advising["last_appointment_date"],
                    "status": "completed",
                }
            )

    # The planted unknown-category hold, on a registered current-term student
    # so it can only ever affect M5 (never M2/M3/M4).
    holds.append(
        {
            "id": _guid(799999),
            "person": {"id": person_guid(0)},
            "type": {"category": "PARKING"},
            "amount": 35.0,
            "organization": {"name": "Campus Safety"},
            "placedOn": "2026-10-05",
            "releasedOn": None,
        }
    )

    return {
        "persons": persons,
        "students": students,
        "academic-periods": periods,
        "student-academic-periods": saps,
        "person-holds": holds,
        "student-advisor-relationships": rels,
        "student-appointments": appointments,
    }


class _MockState:
    def __init__(
        self,
        resources: dict[str, list[dict[str, Any]]],
        rate_limit_once: tuple[str, ...],
        retry_after: str,
        expire_token_after: int | None,
        redirect: dict[str, str],
    ) -> None:
        self.resources = resources
        self.rate_limit_pending = set(rate_limit_once)
        self.retry_after = retry_after
        self.expire_token_after = expire_token_after
        self.redirect = redirect
        self.get_counts: dict[str, int] = {}
        self.served_gets = 0
        self.auth_count = 0
        self.lock = threading.Lock()


def _make_handler(
    state: _MockState, api_key: str, token: str
) -> type[BaseHTTPRequestHandler]:
    class EthosMockHandler(BaseHTTPRequestHandler):
        """Answers /auth and /api/<resource> from the shared state."""

        def log_message(self, format: str, *args: Any) -> None:
            pass  # quiet: the mock never logs, like the connector

        def _send_json(
            self,
            status: int,
            body: Any,
            headers: dict[str, str] | None = None,
        ) -> None:
            payload = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self) -> None:
            if urlsplit(self.path).path != "/auth":
                self._send_json(404, {"errors": [{"code": "not.found"}]})
                return
            if self.headers.get("Authorization") != f"Bearer {api_key}":
                self._send_json(401, {"errors": [{"code": "unauthorized"}]})
                return
            with state.lock:
                state.auth_count += 1
            self._send_json(200, {"token": token})

        def do_GET(self) -> None:
            split = urlsplit(self.path)
            if not split.path.startswith("/api/"):
                self._send_json(404, {"errors": [{"code": "not.found"}]})
                return
            resource = split.path[len("/api/") :]
            if self.headers.get("Authorization") != f"Bearer {token}":
                self._send_json(401, {"errors": [{"code": "unauthorized"}]})
                return
            accept = self.headers.get("Accept", "")
            if "application/vnd.hedtech.integration.v" not in accept:
                self._send_json(406, {"errors": [{"code": "wrong.media.type"}]})
                return
            with state.lock:
                state.get_counts[resource] = state.get_counts.get(resource, 0) + 1
                expired = (
                    state.expire_token_after is not None
                    and state.served_gets >= state.expire_token_after
                )
                throttled = resource in state.rate_limit_pending
                if throttled:
                    state.rate_limit_pending.discard(resource)
            if expired:
                # The session token died mid-run; /auth would re-issue the
                # same token, which stays expired.
                self._send_json(401, {"errors": [{"code": "token.expired"}]})
                return
            if throttled:
                self._send_json(
                    429,
                    {"errors": [{"code": "rate.limited"}]},
                    headers={"Retry-After": state.retry_after},
                )
                return
            if resource in state.redirect:
                self._send_json(
                    302,
                    {"errors": [{"code": "moved"}]},
                    headers={"Location": state.redirect[resource]},
                )
                return
            records = state.resources.get(resource)
            if records is None:
                self._send_json(404, {"errors": [{"code": "unknown.resource"}]})
                return
            query = parse_qs(split.query)
            offset = int(query.get("offset", ["0"])[0])
            limit = int(query.get("limit", ["1000"])[0])
            with state.lock:
                state.served_gets += 1
            self._send_json(200, records[offset : offset + limit])

    return EthosMockHandler


class EthosMockServer:
    """Context manager: starts the mock in a thread, stops it on exit."""

    def __init__(
        self,
        resources: dict[str, list[dict[str, Any]]],
        *,
        api_key: str = MOCK_API_KEY,
        token: str = MOCK_TOKEN,
        rate_limit_once: tuple[str, ...] = (),
        retry_after: str = "2",
        expire_token_after: int | None = None,
        redirect: dict[str, str] | None = None,
    ) -> None:
        self.state = _MockState(
            resources, rate_limit_once, retry_after, expire_token_after, redirect or {}
        )
        handler = _make_handler(self.state, api_key, token)
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        if isinstance(host, bytes):
            host = host.decode("ascii")
        return f"http://{host}:{port}"

    def __enter__(self) -> EthosMockServer:
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
