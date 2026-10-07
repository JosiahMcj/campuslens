"""The per-account inbox: alerts one person sends another before a meeting.

- ``GET  /inbox`` — the caller's received and sent messages (newest first,
  up to 200 each) and the unread count.
- ``GET  /inbox/recipients[?kind=&ref=]`` — the enabled accounts of the
  caller's institution the caller may send to (everyone but themselves),
  narrowed to the people allowed to read that attachment.
- ``POST /inbox`` — send one alert: ``recipient_id``, a short ``note``
  (required, at most 1,000 characters, stored as typed, never sent to a
  model), an optional ``review_by`` date (YYYY-MM-DD) and an optional
  ``source`` it points at:

  - ``{"kind": "finding", "ref": "M5"}`` — a briefing figure. Only the ref
    is stored: whenever the message is shown, the figure's title, value and
    definition are re-read from the institution's current findings, never
    its student ids, and a figure that no longer exists is "no longer
    available". The counseling aggregate (M9) is never sent.
  - ``{"kind": "overview", "ref": "finance:open_balance"}`` — one figure of a
    department overview (``cabinet.departments``), likewise re-read on show.
  - ``{"kind": "chart", "ref": "chart=retention&college=ENG&at=2024-2025"}`` —
    a Data page chart, optionally one term (``at``) and one group
    (``series``) on it. Only the ref is stored: whenever the message is
    shown, the series is computed again for the READER's role through
    ``cabinet.dashboards`` (withheld points stay withheld), and a role that
    may not read that chart, or narrow or split it that way, sees "not
    available" instead.
  - ``{"kind": "explore", "question": ..., "answer": [...]}`` — an Explore
    answer as the sender quoted it (Explore keeps no copy of its answers):
    the question and up to six sentences, each redacted like a question and
    labelled "quoted by the sender". When the quote names an instructor
    (an id, a titled surname, a full name, Explore's "(fictional)" label)
    and the recipient may not see instructor-level results, only the
    question travels.

  BOTH people must be allowed to read the attachment (a briefing figure:
  a role that reads the briefing; an overview figure: a role that reads
  that department; an Explore answer: a role that uses Explore), or the
  send is a logged 403. The same rule applies again on every read.

  One ``inbox.sent`` event. Rate limited by its own per-session and per-IP
  bucket (``CABINET_RATE_INBOX_PER_MIN``, default 10).
- ``POST /inbox/{id}/read`` and ``POST /inbox/{id}/reviewed`` — the
  recipient opens the alert or marks it reviewed (reviewed implies read).
  Idempotent (``changed: false`` and no event the second time). Anyone but
  the recipient gets a 404, so message ids do not leak. One ``inbox.read``
  or ``inbox.reviewed`` event.

**Approved decisions reach the owning department.** When the president
approves a leadership decision (``POST /decisions/approve``), the server
sends one message to every enabled account of the department that owns the
follow-up (``DEPARTMENT_ROLES``: Financial Aid -> aid, Bursar -> finance,
Registrar -> registrar, Student Success -> studentlife), from the approver,
with ``source_ref`` ``decision:<decision id>``. Only the reference, the
dataset it was approved on and the proposed deadline are stored: the
decision's title, the approved follow-up and its figures are re-read from
the current findings whenever the message is shown, never a student row,
and the message is "no longer available" once another dataset is active.
These rows are stored with ``source_kind`` 'note' (the table's CHECK
predates them) and shown as kind ``decision``. One ``inbox.sent`` event per
recipient with ``reason: "decision.approved"``. The department marks it
**Acknowledged** (``POST /inbox/{id}/reviewed``); the president sees each
delivery's state on the decision card (``GET /decisions/{id}/dispatch``,
``department_inbox``). Nothing leaves the app.

The audit events carry ids, roles and the source kind and ref; never the note
text and never a student id (the events outlive any message). The tables are
scoped by institution: a recipient id from another institution is a 404.

- ``GET /admin/sessions`` — sign-in activity for IT (and the admin and the
  president): per account, how many sessions are live and when it was last
  seen. Never a session id or a token.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from cabinet.counseling import M9_ID
from cabinet.dashboards import chart_attachment, chart_readable, parse_chart_ref
from cabinet.departments import may_read, tile_snapshot
from cabinet.explore.catalog import (
    INSTRUCTOR_ROLES,
    SchoolDataMissing,
    connect_readonly,
    school_db_path,
)
from cabinet.explore.privacy import redact_question
from cabinet.questions import (
    DEMO_DECISION_ID,
    UNRESOLVED_HOLDS_DECISION_ID,
    find_decision,
)
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


def _finding_view(
    request: Request, institution_id: int, ref: str
) -> dict[str, Any] | None:
    """A briefing figure as words, from the institution's CURRENT findings:
    None when it no longer exists (a new dataset, a purge) or is the
    counseling aggregate (M9), which never travels in an alert."""
    if ref == M9_ID:
        return None
    runtime = request.app.state.runtime_for(institution_id)
    finding = runtime.findings.get(ref) if ref else None
    if not isinstance(finding, dict) or "title" not in finding:
        return None
    figure: dict[str, Any] = {key: finding.get(key) for key in _FINDING_FIELDS}
    figure["dataset"] = runtime.dataset.get("name")
    return figure


# --- approved decisions -> the owning department ----------------------------------

DECISION_REF_PREFIX = "decision:"
# The office that owns a decision's follow-up -> the account roles that are
# that department.
DEPARTMENT_ROLES: dict[str, tuple[str, ...]] = {
    "Financial Aid": ("aid",),
    "Bursar": ("finance",),
    "Student Accounts": ("finance",),
    "Registrar": ("registrar",),
    "Student Success": ("studentlife",),
    "Student Life": ("studentlife",),
}
# The briefing figures behind each decision, re-read whenever it is shown.
DECISION_FIGURES: dict[str, tuple[str, ...]] = {
    DEMO_DECISION_ID: ("M3",),
    UNRESOLVED_HOLDS_DECISION_ID: ("M5", "M3"),
}


def department_roles(office: str) -> tuple[str, ...]:
    return DEPARTMENT_ROLES.get(office, ())


def _is_decision(row: dict[str, Any]) -> bool:
    return str(row["source_kind"]) == "note" and str(
        row["source_ref"] or ""
    ).startswith(DECISION_REF_PREFIX)


def notify_decision(
    store: CabinetStore,
    institution_id: int,
    approver: dict[str, Any],
    decision: dict[str, Any],
    dataset_id: int,
    due: str | None,
) -> list[dict[str, Any]]:
    """One inbox message per enabled account of the department that owns an
    approved decision's follow-up, from the approver; each audited as
    ``inbox.sent`` with ``reason: "decision.approved"``. Returns the
    deliveries (empty when the department has no account)."""
    office = str(decision["follow_up"]["office"])
    roles = department_roles(office)
    ref = DECISION_REF_PREFIX + str(decision["id"])
    note = (
        f"Leadership approved: {decision['title']}. The {office} office is "
        "asked to carry out the follow-up below. Mark it acknowledged once "
        "your office has reviewed it."
    )
    snapshot = json.dumps(
        {"decision_id": decision["id"], "dataset_id": dataset_id, "due": due}
    )
    recipients = [
        row
        for row in store.users_for(institution_id)
        if not row["disabled"]
        and row["role"] in roles
        and int(row["id"]) != int(approver["id"])
    ]
    for recipient in recipients:
        with store._lock:
            cursor = store._conn.execute(
                "INSERT INTO inbox_messages (institution_id, sender_id, recipient_id,"
                " note, review_by, source_kind, source_ref, snapshot, created_at)"
                " VALUES (?, ?, ?, ?, ?, 'note', ?, ?, ?)",
                (
                    institution_id,
                    int(approver["id"]),
                    int(recipient["id"]),
                    note,
                    due,
                    ref,
                    snapshot,
                    _now(),
                ),
            )
            store._conn.commit()
            message_id = int(cursor.lastrowid or 0)
        store.audit_append(
            institution_id,
            "inbox.sent",
            actor=str(approver["email"]),
            payload={
                "message_id": message_id,
                "recipient_id": int(recipient["id"]),
                "recipient_role": recipient["role"],
                "source_kind": "decision",
                "source_ref": ref,
                "reason": "decision.approved",
                "decision_id": decision["id"],
                "has_review_by": due is not None,
            },
        )
    return decision_deliveries(store, institution_id, str(decision["id"]), dataset_id)


def decision_deliveries(
    store: CabinetStore,
    institution_id: int,
    decision_id: str,
    dataset_id: int | None = None,
) -> list[dict[str, Any]]:
    """Where an approved decision was delivered: per recipient, when it
    arrived, was opened and was acknowledged (newest first)."""
    people = {int(p["id"]): p for p in store.users_for(institution_id)}
    rows = _rows(
        store,
        "SELECT * FROM inbox_messages WHERE institution_id = ? AND source_kind = 'note'"
        " AND source_ref = ? ORDER BY id DESC LIMIT ?",
        (institution_id, DECISION_REF_PREFIX + decision_id, LIST_LIMIT),
    )
    return [
        {
            "message_id": int(row["id"]),
            "to": _person(people.get(int(row["recipient_id"]))),
            "created_at": row["created_at"],
            "read_at": row["read_at"],
            "acknowledged_at": row["reviewed_at"],
        }
        for row in rows
        if dataset_id is None
        or int(json.loads(row["snapshot"] or "{}").get("dataset_id") or -1)
        == dataset_id
    ]


def _decision_view(request: Request, row: dict[str, Any]) -> dict[str, Any] | None:
    """An approved decision as words, re-read from the CURRENT findings: its
    title, the approved follow-up, the deadline and its figures. None once
    the dataset it was approved on is no longer the active one."""
    stored = json.loads(row["snapshot"]) if row["snapshot"] else {}
    decision_id = str(row["source_ref"])[len(DECISION_REF_PREFIX) :]
    runtime = request.app.state.runtime_for(int(row["institution_id"]))
    if int(stored.get("dataset_id") or -1) != int(runtime.dataset["id"]):
        return None
    found = find_decision(runtime.findings, decision_id)
    if found is None:
        return None
    _, decision = found
    figures = [
        figure
        for ref in DECISION_FIGURES.get(decision_id, ())
        if (figure := _finding_view(request, int(row["institution_id"]), ref))
        is not None
    ]
    return {
        "decision_id": decision_id,
        "title": decision["title"],
        "office": decision["follow_up"]["office"],
        "action": decision["follow_up"]["description"],
        "due": stored.get("due"),
        "figures": figures,
        "dataset": runtime.dataset.get("name"),
    }


def _resolve(
    request: Request, row: dict[str, Any], viewer_role: str
) -> tuple[dict[str, Any] | None, bool]:
    """(snapshot, available) for one message as ``viewer_role`` may see it.

    Figures are never frozen: a finding or an overview figure is re-read
    from the live data every time the message is shown, so a figure that
    was withdrawn, purged or is no longer authorized simply disappears
    ("no longer available"). An Explore attachment is the sender's quote,
    stored at send time; it is shown only to a role that may use Explore.
    """
    kind = str(row["source_kind"])
    ref = str(row["source_ref"] or "")
    if _is_decision(row):
        if viewer_role not in READ_ROLES:
            return None, False
        view = _decision_view(request, row)
        return view, view is not None
    if kind == "note":
        return None, True
    if not attachment_allowed(kind, ref, viewer_role):
        return None, False
    if kind == "finding":
        figure = _finding_view(request, int(row["institution_id"]), ref)
        return figure, figure is not None
    if kind == "overview":
        department, _, tile = ref.partition(":")
        try:
            tile_view = tile_snapshot(department, tile)
        except SchoolDataMissing:
            tile_view = None
        return tile_view, tile_view is not None
    if kind == "chart":
        try:
            chart_view = chart_attachment(ref)
        except SchoolDataMissing:
            chart_view = None
        return chart_view, chart_view is not None
    snapshot = json.loads(row["snapshot"]) if row["snapshot"] else None
    return snapshot, snapshot is not None


def _message_body(
    request: Request,
    row: dict[str, Any],
    people: dict[int, dict[str, Any]],
    viewer_role: str,
) -> dict[str, Any]:
    snapshot, available = _resolve(request, row, viewer_role)
    return {
        "id": int(row["id"]),
        "from": _person(people.get(int(row["sender_id"]))),
        "to": _person(people.get(int(row["recipient_id"]))),
        "note": row["note"],
        "review_by": row["review_by"],
        "source_kind": "decision" if _is_decision(row) else row["source_kind"],
        "source_ref": row["source_ref"],
        "snapshot": snapshot,
        "attachment_available": available,
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
        "received": [
            _message_body(request, row, people, str(user["role"])) for row in received
        ],
        "sent": [
            _message_body(request, row, people, str(user["role"])) for row in sent
        ],
        "unread": int(unread),
    }


def attachment_allowed(kind: str, ref: str, role: str) -> bool:
    """Whether ``role`` may read an attachment of this kind: a briefing
    figure needs a role that reads the briefing, an overview figure a role
    that reads that department's overview, an Explore answer a role that
    uses Explore. A plain note goes to anyone."""
    if kind == "note":
        return True
    if kind == "finding":
        return role in READ_ROLES
    if kind == "overview":
        return may_read(role, ref.partition(":")[0])
    if kind == "explore":
        return role in EXPLORE_ROLES
    if kind == "chart":
        return chart_readable(ref, role)
    return False


@router.get("/inbox/recipients")
def get_recipients(
    request: Request, kind: str = "note", ref: str = ""
) -> list[dict[str, Any]]:
    """Everyone the caller may send to, narrowed (with ``kind`` and ``ref``)
    to the people allowed to read that attachment."""
    user = _user(request)
    return [
        {"id": int(row["id"]), "email": row["email"], "role": row["role"]}
        for row in _store(request).users_for(int(user["institution_id"]))
        if not row["disabled"]
        and int(row["id"]) != int(user["id"])
        and attachment_allowed(kind, ref, str(row["role"]))
    ]


_instructor_names: dict[tuple[str, int], tuple[frozenset[str], frozenset[str]]] = {}

# Instructor ids (I-0001), a title before a surname ("Dr. Shelby"), and
# Explore's "(fictional)" label on every instructor name.
_INSTRUCTOR_ID_RE = re.compile(r"\bI-\d{2,}\b")
_TITLED_RE = re.compile(
    r"\b(?:Dr|Prof|Professor|Instructor|Mr|Mrs|Ms)\.?\s+([A-Z][A-Za-z'\u2019-]+)"
)


def _names_an_instructor(sentences: list[str]) -> bool:
    """True when an answer sentence names an instructor: an instructor id,
    Explore's "(fictional)" label, a full name or a titled surname from the
    school data's instructor list, or any title-plus-surname at all when the
    school data cannot be read (withheld, never leaked)."""
    if not sentences:
        return False
    text = " ".join(sentences)
    if "(fictional)" in text or _INSTRUCTOR_ID_RE.search(text):
        return True
    titled = {match.group(1) for match in _TITLED_RE.finditer(text)}
    try:
        path = school_db_path().expanduser()
        key = (str(path), path.stat().st_mtime_ns)
        names = _instructor_names.get(key)
        if names is None:
            con = connect_readonly(path)
            try:
                rows = con.execute(
                    "SELECT first_name, last_name FROM instructors"
                ).fetchall()
            finally:
                con.close()
            names = (
                frozenset(f"{row[0]} {row[1]}" for row in rows),
                frozenset(str(row[1]) for row in rows),
            )
            _instructor_names[key] = names
    except (OSError, SchoolDataMissing, sqlite3.Error):
        return True
    full, last = names
    return bool(titled & last) or any(name in text for name in full)


def _snapshot(
    request: Request,
    source: InboxSource,
    sender: dict[str, Any],
    recipient: dict[str, Any],
) -> tuple[str | None, dict[str, Any] | None] | JSONResponse:
    """(source_ref, stored snapshot) checked against BOTH people, or the
    refusal. Figures store only their ref (they are re-read whenever the
    message is shown); an Explore answer stores the sender's quote."""
    role = str(sender["role"])
    kind = source.kind
    if kind not in ("note", "finding", "overview", "explore", "chart"):
        return _error(422, f"unknown source kind {kind!r}")
    if kind == "note":
        return None, None
    if kind == "finding":
        ref = (source.ref or "").strip()
    elif kind == "overview":
        department, _, tile = (source.ref or "").partition(":")
        ref = f"{department}:{tile}"
    elif kind == "chart":
        parsed = parse_chart_ref((source.ref or "").strip())
        if parsed is None:
            return _error(422, f"unknown chart {source.ref!r}")
        ref = parsed.text()
    else:
        ref = ""
    if not attachment_allowed(kind, ref, role):
        return _refused(request, f"this role may not attach a {kind} source")
    if not attachment_allowed(kind, ref, str(recipient["role"])):
        return _refused(request, f"the recipient's role may not read a {kind} source")
    if kind == "finding":
        if ref == M9_ID:
            return _error(422, "the counseling figure is never sent in an alert")
        if _finding_view(request, int(sender["institution_id"]), ref) is None:
            return _error(422, f"unknown figure {ref!r}")
        return ref, None
    if kind == "overview":
        try:
            found = tile_snapshot(*ref.split(":", 1))
        except SchoolDataMissing as exc:
            return _error(503, exc.args[0])
        if found is None:
            return _error(422, f"unknown overview figure {source.ref!r}")
        return ref, None
    if kind == "chart":
        try:
            found_chart = chart_attachment(ref)
        except SchoolDataMissing as exc:
            return _error(503, exc.args[0])
        if found_chart is None:
            return _error(422, f"unknown chart {source.ref!r}")
        return ref, None
    question = " ".join((source.question or "").split())
    if not question or len(question) > QUESTION_MAX_CHARS:
        return _error(
            422,
            "an Explore alert needs the question "
            f"(at most {QUESTION_MAX_CHARS} characters)",
        )
    answer = [
        redact_question(" ".join(sentence.split()))
        for sentence in (source.answer or [])
        if sentence.strip()
    ]
    if len(answer) > ANSWER_MAX_SENTENCES or any(
        len(sentence) > SENTENCE_MAX_CHARS for sentence in answer
    ):
        return _error(
            422,
            f"an Explore alert carries at most {ANSWER_MAX_SENTENCES} answer "
            f"sentences of {SENTENCE_MAX_CHARS} characters",
        )
    # Explore keeps no copy of its answers, so the text is the sender's
    # quote: redacted like a question, labelled as quoted, and dropped when
    # it names an instructor for a recipient who may not see instructors.
    withheld = recipient["role"] not in INSTRUCTOR_ROLES and _names_an_instructor(
        answer
    )
    return None, {
        "question": redact_question(question),
        "answer": [] if withheld else answer,
        "answer_withheld": withheld and bool(answer),
        "quoted_by_sender": True,
    }


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
    return JSONResponse(
        status_code=201, content=_message_body(request, row, people, str(user["role"]))
    )


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
        content={
            "message": _message_body(request, row, people, str(user["role"])),
            "changed": changed,
        }
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
