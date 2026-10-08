"""The Interventions page and its governed outreach lists.

Routes (roles are admitted by ``cabinet.security``; the per-program office
role is checked here):

- ``GET   /interventions`` — every support program with its eligibility rule,
  offer, owner office, start term, reach and impact (aggregates only, from
  ``cabinet.interventions``), and the current term's outreach list state.
  Every role may read it.
- ``POST  /interventions/{program}/outreach`` — prepare the list of students
  the program's rule names in the current term. The executive, the admin, and
  the program's office role (the aid role for the theology funding bridge).
  The list waits for approval; nothing is sent to anyone. Idempotent per
  program and term. Logs ``outreach.prepared``.
- ``POST  /outreach/{id}/decision`` — an executive or admin approves or
  declines a pending list. Logs ``outreach.decided``.
- ``GET   /outreach/{id}`` — the list's rows (pseudonymous ids and the facts
  the rule used). Same roles as preparing. Logs ``outreach.viewed``.
- ``PATCH /outreach/{id}/rows/{row}`` — once approved, a person records how
  the offer went (not contacted, offered, accepted, declined). Logs
  ``outreach.updated``.

Support is offered, never imposed: a row is an invitation for a person to
make, and the statuses record the student's choice. Audit payloads carry
ids of lists and rows and counts, never a student id. Nothing here calls a
model.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from cabinet import interventions as iv
from cabinet.auth import ROLE_ADMIN, ROLE_EXECUTIVE
from cabinet.explore.catalog import SchoolDataMissing, connect_readonly, school_db_path
from cabinet.store import CabinetStore

router = APIRouter()

ROW_STATUSES: tuple[str, ...] = ("not_contacted", "offered", "accepted", "declined")
DECIDE_ROLES = (ROLE_EXECUTIVE, ROLE_ADMIN)
_MAX_ROW_ID = 2**63 - 1

PROGRAMS_MISSING = (
    "The demonstration university has no support programs yet. Run make "
    "school-data to rebuild it."
)


class OutreachDecision(BaseModel):
    decision: str = ""


class OutreachRowPatch(BaseModel):
    status: str = ""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _store(request: Request) -> CabinetStore:
    store: CabinetStore = request.app.state.auth
    return store


def _user(request: Request) -> dict[str, Any]:
    user: dict[str, Any] = request.scope["cabinet_user"]
    return user


def may_handle_rows(role: str, program_id: str) -> bool:
    """The roles allowed this program's per-student rows."""
    return role in DECIDE_ROLES or iv.OFFICE_ROLE.get(program_id) == role


def _school() -> sqlite3.Connection:
    try:
        return connect_readonly()
    except SchoolDataMissing as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None


# --- the overview, cached per school database file ----------------------------

_CACHE: dict[tuple[str, int], dict[str, Any]] = {}
_CACHE_LOCK = threading.Lock()


def overview(con: sqlite3.Connection) -> dict[str, Any]:
    path = school_db_path()
    try:
        key = (str(path.resolve()), path.stat().st_mtime_ns)
    except OSError:
        key = (str(path), 0)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
    if cached is not None:
        return cached
    try:
        programs = iv.programs(con)
    except iv.ProgramsMissing:
        raise HTTPException(status_code=503, detail=PROGRAMS_MISSING) from None
    current = iv.current_term(con)
    items: list[dict[str, Any]] = []
    for p in programs:
        reach = iv.reach(con, p["id"])
        impact = iv.impact(con, p["id"])
        reach.pop("program", None)
        impact.pop("program", None)
        # Which side of a withheld comparison is the small one is for the
        # Explore table's wording only; the page never needs it.
        for o in impact["outcomes"]:
            for c in o["comparisons"]:
                c.pop("small", None)
        items.append({**p, "reach": reach, "impact": impact})
    out: dict[str, Any] = {
        "current_term": current,
        "current_term_name": iv.term_names(con).get(current, current),
        "programs": items,
    }
    with _CACHE_LOCK:
        _CACHE.clear()
        _CACHE[key] = out
    return out


# --- store helpers (the cabinet database) ---------------------------------------


def _list_body(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row["id"],
        "program_id": row["program_id"],
        "term": row["term_code"],
        "status": row["status"],
        "count": row["student_count"],
        "prepared_by": row["prepared_by"],
        "prepared_at": row["prepared_at"],
        "decided_by": row["decided_by"],
        "decided_at": row["decided_at"],
    }


def _list_row(
    store: CabinetStore, institution_id: int, list_id: int
) -> sqlite3.Row | None:
    with store._lock:
        row: sqlite3.Row | None = store._conn.execute(
            "SELECT * FROM outreach_lists WHERE id = ? AND institution_id = ?",
            (list_id, institution_id),
        ).fetchone()
    return row


def lists_for_term(
    store: CabinetStore, institution_id: int, term: str
) -> dict[str, dict[str, Any]]:
    with store._lock:
        rows = store._conn.execute(
            "SELECT * FROM outreach_lists WHERE institution_id = ? AND term_code = ?",
            (institution_id, term),
        ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        body = _list_body(row)
        assert body is not None
        out[str(row["program_id"])] = body
    return out


def create_list(
    store: CabinetStore,
    institution_id: int,
    *,
    program_id: str,
    term: str,
    rows: list[tuple[str, dict[str, Any]]],
    prepared_by: str,
) -> tuple[dict[str, Any], bool]:
    """(the list, created?) — an existing list for the program and term is
    returned unchanged."""
    with store._lock:
        existing = store._conn.execute(
            "SELECT * FROM outreach_lists WHERE institution_id = ? AND "
            "program_id = ? AND term_code = ?",
            (institution_id, program_id, term),
        ).fetchone()
        if existing is not None:
            body = _list_body(existing)
            assert body is not None
            return body, False
        try:
            cursor = store._conn.execute(
                "INSERT INTO outreach_lists (institution_id, program_id, term_code,"
                " student_count, prepared_by, prepared_at) VALUES (?, ?, ?, ?, ?, ?)",
                (institution_id, program_id, term, len(rows), prepared_by, _now()),
            )
            list_id = int(cursor.lastrowid)  # type: ignore[arg-type]
            store._conn.executemany(
                "INSERT INTO outreach_rows (list_id, institution_id, student_id, facts)"
                " VALUES (?, ?, ?, ?)",
                [
                    (list_id, institution_id, sid, json.dumps(facts, sort_keys=True))
                    for sid, facts in rows
                ],
            )
            store._conn.commit()
        except Exception:
            store._conn.rollback()
            raise
        created = store._conn.execute(
            "SELECT * FROM outreach_lists WHERE id = ?", (list_id,)
        ).fetchone()
    body = _list_body(created)
    assert body is not None
    return body, True


def _rows(store: CabinetStore, list_id: int) -> list[dict[str, Any]]:
    with store._lock:
        rows = store._conn.execute(
            "SELECT id, student_id, facts, status, updated_by, updated_at "
            "FROM outreach_rows WHERE list_id = ? ORDER BY student_id",
            (list_id,),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "student_id": r["student_id"],
            "facts": json.loads(r["facts"]),
            "status": r["status"],
            "updated_by": r["updated_by"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]


# --- routes ------------------------------------------------------------------------


@router.get("/interventions")
def get_interventions(request: Request) -> dict[str, Any]:
    user = _user(request)
    role = str(user["role"])
    con = _school()
    try:
        body = overview(con)
    finally:
        con.close()
    lists = lists_for_term(
        _store(request), int(user["institution_id"]), body["current_term"]
    )
    programs = []
    for p in body["programs"]:
        programs.append(
            {
                **p,
                "outreach": lists.get(p["id"]),
                "can_prepare": may_handle_rows(role, p["id"]),
                "can_decide": role in DECIDE_ROLES,
                "fact_labels": [
                    {"key": k, "label": label} for k, label in iv.FACT_LABELS[p["id"]]
                ],
            }
        )
    return {
        "current_term": body["current_term"],
        "current_term_name": body["current_term_name"],
        "fictional": True,
        "row_statuses": list(ROW_STATUSES),
        "programs": programs,
    }


@router.post("/interventions/{program_id}/outreach")
def post_outreach(program_id: str, request: Request) -> JSONResponse:
    user = _user(request)
    institution_id = int(user["institution_id"])
    if program_id not in iv.PROGRAM_IDS:
        raise HTTPException(status_code=404, detail=f"no program {program_id!r}")
    if not may_handle_rows(str(user["role"]), program_id):
        return _refused(
            request,
            403,
            "Only the executive, an administrator, or the program's own office "
            "may prepare this outreach list.",
        )
    con = _school()
    try:
        try:
            iv.program(con, program_id)
        except (iv.ProgramsMissing, KeyError):
            # No program tables, or tables without this program (an older
            # school database): the same plain sentence either way.
            raise HTTPException(status_code=503, detail=PROGRAMS_MISSING) from None
        term = iv.current_term(con)
        rows = iv.eligible_students(con, program_id, term)
    finally:
        con.close()
    store = _store(request)
    body, created = create_list(
        store,
        institution_id,
        program_id=program_id,
        term=term,
        rows=rows,
        prepared_by=str(user["email"]),
    )
    content: dict[str, Any] = {"list": body, "created": created}
    if created:
        event = store.audit_append(
            institution_id,
            "outreach.prepared",
            actor=str(user["email"]),
            payload={
                "list_id": body["id"],
                "program_id": program_id,
                "term": term,
                "count": body["count"],
            },
        )
        content["event_id"] = event["id"]
    return JSONResponse(content=content)


def _row_id(value: int, what: str) -> int:
    if not 1 <= value <= _MAX_ROW_ID:
        raise HTTPException(status_code=404, detail=f"no {what} {value}")
    return value


def _refused(request: Request, status_code: int, detail: str) -> JSONResponse:
    user = _user(request)
    _store(request).audit_append(
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


def _owned_list(request: Request, list_id: int) -> sqlite3.Row:
    user = _user(request)
    row = _list_row(
        _store(request), int(user["institution_id"]), _row_id(list_id, "list")
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"no outreach list {list_id}")
    return row


@router.post("/outreach/{list_id}/decision")
def post_decision(
    list_id: int, body: OutreachDecision, request: Request
) -> JSONResponse:
    user = _user(request)
    if str(user["role"]) not in DECIDE_ROLES:
        return _refused(
            request, 403, "Only the executive or an administrator approves a list."
        )
    if body.decision not in ("approve", "decline"):
        return JSONResponse(
            status_code=422, content={"detail": "decision must be approve or decline"}
        )
    row = _owned_list(request, list_id)
    if row["status"] != "pending_approval":
        return JSONResponse(
            status_code=409,
            content={
                "detail": f"this list was already {row['status']}",
                "list": _list_body(row),
            },
        )
    status = "approved" if body.decision == "approve" else "declined"
    store = _store(request)
    with store._lock:
        cursor = store._conn.execute(
            "UPDATE outreach_lists SET status = ?, decided_by = ?, decided_at = ? "
            "WHERE id = ? AND status = 'pending_approval'",
            (status, str(user["email"]), _now(), row["id"]),
        )
        store._conn.commit()
        changed = cursor.rowcount
    if changed == 0:
        # Someone else decided it between our read and this write: their
        # decision stands, and no second event is written.
        current = _list_row(store, int(user["institution_id"]), int(row["id"]))
        return JSONResponse(
            status_code=409,
            content={
                "detail": "this list was already decided",
                "list": _list_body(current),
            },
        )
    updated = _list_row(store, int(user["institution_id"]), int(row["id"]))
    event = store.audit_append(
        int(user["institution_id"]),
        "outreach.decided",
        actor=str(user["email"]),
        payload={
            "list_id": row["id"],
            "program_id": row["program_id"],
            "term": row["term_code"],
            "count": row["student_count"],
            "decision": status,
        },
    )
    return JSONResponse(content={"list": _list_body(updated), "event_id": event["id"]})


@router.get("/outreach/{list_id}")
def get_outreach(list_id: int, request: Request) -> JSONResponse:
    user = _user(request)
    row = _owned_list(request, list_id)
    if not may_handle_rows(str(user["role"]), str(row["program_id"])):
        return _refused(
            request,
            403,
            "This outreach list names students; only the executive, an "
            "administrator, or the program's own office may open it.",
        )
    store = _store(request)
    rows = _rows(store, int(row["id"]))
    event = store.audit_append(
        int(user["institution_id"]),
        "outreach.viewed",
        actor=str(user["email"]),
        payload={
            "list_id": row["id"],
            "program_id": row["program_id"],
            "rows": len(rows),
        },
    )
    return JSONResponse(
        content={
            "list": _list_body(row),
            "rows": rows,
            "statuses": list(ROW_STATUSES),
            "fact_labels": [
                {"key": k, "label": label}
                for k, label in iv.FACT_LABELS.get(str(row["program_id"]), ())
            ],
            "event_id": event["id"],
        }
    )


@router.patch("/outreach/{list_id}/rows/{row_id}")
def patch_outreach_row(
    list_id: int, row_id: int, body: OutreachRowPatch, request: Request
) -> JSONResponse:
    user = _user(request)
    lst = _owned_list(request, list_id)
    if not may_handle_rows(str(user["role"]), str(lst["program_id"])):
        return _refused(
            request,
            403,
            "Only the executive, an administrator, or the program's own office "
            "may record outreach.",
        )
    if body.status not in ROW_STATUSES:
        return JSONResponse(
            status_code=422,
            content={"detail": f"status must be one of {', '.join(ROW_STATUSES)}"},
        )
    if lst["status"] != "approved":
        return JSONResponse(
            status_code=409,
            content={"detail": "Outreach is recorded only after the list is approved."},
        )
    store = _store(request)
    _row_id(row_id, "row")
    with store._lock:
        before = store._conn.execute(
            "SELECT status FROM outreach_rows WHERE id = ? AND list_id = ?",
            (row_id, lst["id"]),
        ).fetchone()
        if before is None:
            raise HTTPException(status_code=404, detail=f"no row {row_id}")
        store._conn.execute(
            "UPDATE outreach_rows SET status = ?, updated_by = ?, updated_at = ? "
            "WHERE id = ?",
            (body.status, str(user["email"]), _now(), row_id),
        )
        store._conn.commit()
    event = store.audit_append(
        int(user["institution_id"]),
        "outreach.updated",
        actor=str(user["email"]),
        payload={
            "list_id": lst["id"],
            "row_id": row_id,
            "status_from": before["status"],
            "status_to": body.status,
        },
    )
    return JSONResponse(
        content={"row_id": row_id, "status": body.status, "event_id": event["id"]}
    )


__all__ = ["ROW_STATUSES", "may_handle_rows", "overview", "router"]
