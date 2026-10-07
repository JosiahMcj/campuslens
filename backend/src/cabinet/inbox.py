"""The per-account inbox: alerts one person sends another before a meeting.

- ``GET  /inbox`` — the caller's received and sent messages (newest first,
  up to 200 each) and the unread count.
- ``GET  /inbox/recipients`` — the enabled accounts of the caller's
  institution the caller may send to (everyone but themselves).
- ``POST /inbox`` — send one alert: ``recipient_id``, a short ``note``
  (required, at most 1,000 characters, stored as typed, never sent to a
  model), an optional ``review_by`` date (YYYY-MM-DD) and an optional
  ``source`` it points at:

  - ``{"kind": "finding", "ref": "M5"}`` — a briefing figure. The sender must
    be a role that reads the briefing. The server stores the figure's title,
    display value and definition from the active dataset, never its student
    ids (the president's own findings carry them; the snapshot does not).
  - ``{"kind": "overview", "ref": "finance:open_balance"}`` — one figure of a
    department overview (``cabinet.departments``), recomputed by the server
    for a sender who may read that department.
  - ``{"kind": "explore", "question": ..., "answer": [...]}`` — an Explore
    answer the sender saw: the question (student-id-shaped tokens redacted)
    and up to six answer sentences, which are aggregate by construction.
    When the answer names an instructor and the recipient may not see
    instructor-level results, only the question travels: the recipient asks
    it under their own role. A role that may not use Explore cannot attach one.

  One ``inbox.sent`` event. Rate limited like the other consequential POSTs.
- ``POST /inbox/{id}/read`` and ``POST /inbox/{id}/reviewed`` — the
  recipient opens the alert or marks it reviewed (reviewed implies read).
  Idempotent (``changed: false`` and no event the second time). Anyone but
  the recipient gets a 404, so message ids do not leak. One ``inbox.read``
  or ``inbox.reviewed`` event.

The audit events carry ids, roles and the source kind and ref; never the note
text and never a student id (the events outlive any message). The tables are
scoped by institution: a recipient id from another institution is a 404.

- ``GET /admin/sessions`` — sign-in activity for IT (and the admin and the
  president): per account, how many sessions are live and when it was last
  seen. Never a session id or a token.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from cabinet.departments import may_read, tile_snapshot
from cabinet.explore.catalog import (
    INSTRUCTOR_ROLES,
    SchoolDataMissing,
    connect_readonly,
    school_db_path,
)
from cabinet.explore.privacy import redact_question
from cabinet.security import EXPLORE_ROLES, READ_ROLES
from cabinet.staffactions import valid_due_date
from cabinet.store import CabinetStore

router = APIRouter()

NOTE_MAX_CHARS = 1000
QUESTION_MAX_CHARS = 500
ANSWER_MAX_SENTENCES = 6
SENTENCE_MAX_CHARS = 600
LIST_LIMIT = 200
_MAX_ROW_ID = 2**63 - 1

# The finding fields an alert may carry: the figure as words. Never row_ids,
# row_rules, hold_row_ids or any per-student structure.
_FINDING_FIELDS = ("id", "title", "display", "definition", "reason")


class InboxSource(BaseModel):
    kind: str = "note"
    ref: str | None = None
    question: str | None = None
    answer: list[str] | None = None


class InboxSend(BaseModel):
    recipient_id: int
    note: str = ""
    review_by: str | None = None
    source: InboxSource | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _store(request: Request) -> CabinetStore:
    store: CabinetStore = request.app.state.auth
    return store


def _user(request: Request) -> dict[str, Any]:
    user: dict[str, Any] = request.scope["cabinet_user"]
    return user


def _error(status: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detail})


def _refused(request: Request, detail: str) -> JSONResponse:
    """A logged 403 (data.refused on the caller's chain), like the gate's."""
    user = _user(request)
    _store(request).audit_append(
        int(user["institution_id"]),
        "data.refused",
        actor=str(user["id"]),
        payload={"reason": detail, "method": request.method, "path": request.url.path},
    )
    return _error(403, detail)


def _person(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {"id": int(row["id"]), "email": row["email"], "role": row["role"]}


def _message_body(
    row: dict[str, Any], people: dict[int, dict[str, Any]]
) -> dict[str, Any]:
    snapshot = json.loads(row["snapshot"]) if row["snapshot"] else None
    return {
        "id": int(row["id"]),
        "from": _person(people.get(int(row["sender_id"]))),
        "to": _person(people.get(int(row["recipient_id"]))),
        "note": row["note"],
        "review_by": row["review_by"],
        "source_kind": row["source_kind"],
        "source_ref": row["source_ref"],
        "snapshot": snapshot,
        "created_at": row["created_at"],
        "read_at": row["read_at"],
        "reviewed_at": row["reviewed_at"],
    }


def _rows(store: CabinetStore, sql: str, args: tuple[Any, ...]) -> list[dict[str, Any]]:
    with store._lock:
        return [dict(r) for r in store._conn.execute(sql, args).fetchall()]


@router.get("/inbox")
def get_inbox(request: Request) -> dict[str, Any]:
    user = _user(request)
    store = _store(request)
    institution_id = int(user["institution_id"])
    user_id = int(user["id"])
    received = _rows(
        store,
        "SELECT * FROM inbox_messages WHERE institution_id = ? AND recipient_id = ?"
        " ORDER BY id DESC LIMIT ?",
        (institution_id, user_id, LIST_LIMIT),
    )
    sent = _rows(
        store,
        "SELECT * FROM inbox_messages WHERE institution_id = ? AND sender_id = ?"
        " ORDER BY id DESC LIMIT ?",
        (institution_id, user_id, LIST_LIMIT),
    )
    unread = _rows(
        store,
        "SELECT COUNT(*) AS n FROM inbox_messages WHERE institution_id = ?"
        " AND recipient_id = ? AND read_at IS NULL",
        (institution_id, user_id),
    )[0]["n"]
    people = {int(row["id"]): row for row in store.users_for(institution_id)}
    return {
        "received": [_message_body(row, people) for row in received],
        "sent": [_message_body(row, people) for row in sent],
        "unread": int(unread),
    }


@router.get("/inbox/recipients")
def get_recipients(request: Request) -> list[dict[str, Any]]:
    user = _user(request)
    return [
        {"id": int(row["id"]), "email": row["email"], "role": row["role"]}
        for row in _store(request).users_for(int(user["institution_id"]))
        if not row["disabled"] and int(row["id"]) != int(user["id"])
    ]


_instructor_names: dict[tuple[str, int], frozenset[str]] = {}


def _names_an_instructor(sentences: list[str]) -> bool:
    """True when an answer sentence names an instructor: Explore labels every
    instructor name "(fictional)", and any full name from the school data's
    instructor list counts too. Without the school data, any answer from an
    instructor-level role is treated as naming one (withheld, never leaked)."""
    if not sentences:
        return False
    text = " ".join(sentences)
    if "(fictional)" in text:
        return True
    try:
        path = school_db_path().expanduser()
        key = (str(path), path.stat().st_mtime_ns)
        names = _instructor_names.get(key)
        if names is None:
            con = connect_readonly(path)
            try:
                rows = con.execute(
                    "SELECT first_name || ' ' || last_name FROM instructors"
                ).fetchall()
            finally:
                con.close()
            names = frozenset(str(row[0]) for row in rows)
            _instructor_names[key] = names
    except (OSError, SchoolDataMissing, sqlite3.Error):
        return True
    return any(name in text for name in names)


def _snapshot(
    request: Request,
    source: InboxSource,
    sender: dict[str, Any],
    recipient: dict[str, Any],
) -> tuple[str | None, dict[str, Any] | None] | JSONResponse:
    """(source_ref, snapshot) built by the server, or the refusal/error."""
    role = str(sender["role"])
    if source.kind == "note":
        return None, None
    if source.kind == "finding":
        if role not in READ_ROLES:
            return _refused(request, "this role does not read the briefing's figures")
        ref = (source.ref or "").strip()
        runtime = request.app.state.runtime_for(int(sender["institution_id"]))
        finding = runtime.findings.get(ref) if ref else None
        if not isinstance(finding, dict) or "title" not in finding:
            return _error(422, f"unknown figure {ref!r}")
        figure: dict[str, Any] = {key: finding.get(key) for key in _FINDING_FIELDS}
        figure["dataset"] = runtime.dataset.get("name")
        return ref, figure
    if source.kind == "overview":
        department, _, tile = (source.ref or "").partition(":")
        if not may_read(role, department):
            return _refused(
                request, "this role does not read that department's overview"
            )
        try:
            snapshot = tile_snapshot(department, tile)
        except SchoolDataMissing as exc:
            return _error(503, exc.args[0])
        if snapshot is None:
            return _error(422, f"unknown overview figure {source.ref!r}")
        return f"{department}:{tile}", snapshot
    if source.kind == "explore":
        if role not in EXPLORE_ROLES:
            return _refused(request, "this role does not use Explore")
        question = " ".join((source.question or "").split())
        if not question or len(question) > QUESTION_MAX_CHARS:
            return _error(
                422,
                "an Explore alert needs the question "
                f"(at most {QUESTION_MAX_CHARS} characters)",
            )
        answer = [" ".join(s.split()) for s in (source.answer or []) if s.strip()]
        if len(answer) > ANSWER_MAX_SENTENCES or any(
            len(s) > SENTENCE_MAX_CHARS for s in answer
        ):
            return _error(
                422,
                f"an Explore alert carries at most {ANSWER_MAX_SENTENCES} answer "
                f"sentences of {SENTENCE_MAX_CHARS} characters",
            )
        # Instructor-level wording stays with the roles allowed to see it:
        # an answer that names an instructor travels only between those roles.
        withheld = (
            role in INSTRUCTOR_ROLES
            and recipient["role"] not in INSTRUCTOR_ROLES
            and _names_an_instructor(answer)
        )
        return None, {
            "question": redact_question(question),
            "answer": [] if withheld else answer,
            "answer_withheld": withheld and bool(answer),
        }
    return _error(422, f"unknown source kind {source.kind!r}")


@router.post("/inbox")
def post_inbox(body: InboxSend, request: Request) -> JSONResponse:
    user = _user(request)
    store = _store(request)
    institution_id = int(user["institution_id"])
    note = body.note.strip()
    if not note:
        return _error(422, "write a short note so they know what to look at")
    if len(note) > NOTE_MAX_CHARS:
        return _error(422, f"the note is at most {NOTE_MAX_CHARS} characters")
    review_by = (body.review_by or "").strip() or None
    if review_by is not None and not valid_due_date(review_by):
        return _error(422, "review_by must be a date written YYYY-MM-DD")
    recipient = (
        store.user_in_institution(institution_id, body.recipient_id)
        if 1 <= body.recipient_id <= _MAX_ROW_ID
        else None
    )
    if recipient is None or recipient["disabled"]:
        return _error(404, f"unknown recipient {body.recipient_id}")
    if int(recipient["id"]) == int(user["id"]):
        return _error(422, "choose someone other than yourself")
    built = _snapshot(request, body.source or InboxSource(), user, recipient)
    if isinstance(built, JSONResponse):
        return built
    source_ref, snapshot = built
    kind = (body.source or InboxSource()).kind
    with store._lock:
        cursor = store._conn.execute(
            "INSERT INTO inbox_messages (institution_id, sender_id, recipient_id,"
            " note, review_by, source_kind, source_ref, snapshot, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                institution_id,
                int(user["id"]),
                int(recipient["id"]),
                note,
                review_by,
                kind,
                source_ref,
                json.dumps(snapshot, ensure_ascii=False)
                if snapshot is not None
                else None,
                _now(),
            ),
        )
        store._conn.commit()
        message_id = int(cursor.lastrowid or 0)
    store.audit_append(
        institution_id,
        "inbox.sent",
        actor=str(user["email"]),
        payload={
            "message_id": message_id,
            "recipient_id": int(recipient["id"]),
            "recipient_role": recipient["role"],
            "source_kind": kind,
            "source_ref": source_ref,
            "has_review_by": review_by is not None,
        },
    )
    people = {int(user["id"]): user, int(recipient["id"]): recipient}
    row = _rows(store, "SELECT * FROM inbox_messages WHERE id = ?", (message_id,))[0]
    return JSONResponse(status_code=201, content=_message_body(row, people))


def _mark(request: Request, message_id: int, reviewed: bool) -> JSONResponse:
    user = _user(request)
    store = _store(request)
    institution_id = int(user["institution_id"])
    missing = _error(404, f"no message {message_id}")
    if not 1 <= message_id <= _MAX_ROW_ID:
        return missing
    found = _rows(
        store,
        "SELECT * FROM inbox_messages WHERE id = ? AND institution_id = ?"
        " AND recipient_id = ?",
        (message_id, institution_id, int(user["id"])),
    )
    if not found:
        return missing
    row = found[0]
    column = "reviewed_at" if reviewed else "read_at"
    changed = row[column] is None
    if changed:
        now = _now()
        with store._lock:
            if reviewed:
                store._conn.execute(
                    "UPDATE inbox_messages SET reviewed_at = ?,"
                    " read_at = COALESCE(read_at, ?) WHERE id = ?",
                    (now, now, message_id),
                )
            else:
                store._conn.execute(
                    "UPDATE inbox_messages SET read_at = ? WHERE id = ?",
                    (now, message_id),
                )
            store._conn.commit()
        store.audit_append(
            institution_id,
            "inbox.reviewed" if reviewed else "inbox.read",
            actor=str(user["email"]),
            payload={"message_id": message_id, "sender_id": int(row["sender_id"])},
        )
        row = _rows(store, "SELECT * FROM inbox_messages WHERE id = ?", (message_id,))[
            0
        ]
    people = {int(p["id"]): p for p in store.users_for(institution_id)}
    return JSONResponse(
        content={"message": _message_body(row, people), "changed": changed}
    )


@router.post("/inbox/{message_id}/read")
def post_read(message_id: int, request: Request) -> JSONResponse:
    return _mark(request, message_id, reviewed=False)


@router.post("/inbox/{message_id}/reviewed")
def post_reviewed(message_id: int, request: Request) -> JSONResponse:
    return _mark(request, message_id, reviewed=True)


@router.get("/admin/sessions")
def get_sessions(request: Request) -> list[dict[str, Any]]:
    """Per account: live sessions and when it was last seen. No ids."""
    user = _user(request)
    store = _store(request)
    institution_id = int(user["institution_id"])
    now = _now()
    activity = {
        int(r["user_id"]): r
        for r in _rows(
            store,
            "SELECT s.user_id, SUM(s.expires_at > ?) AS live,"
            " MAX(s.last_seen) AS last_seen FROM sessions s"
            " JOIN users u ON u.id = s.user_id WHERE u.institution_id = ?"
            " GROUP BY s.user_id",
            (now, institution_id),
        )
    }
    out = []
    for row in store.users_for(institution_id):
        seen = activity.get(int(row["id"]))
        out.append(
            {
                "id": int(row["id"]),
                "email": row["email"],
                "role": row["role"],
                "disabled": bool(row["disabled"]),
                "live_sessions": int(seen["live"] or 0) if seen else 0,
                "last_seen": seen["last_seen"] if seen else None,
            }
        )
    return out
