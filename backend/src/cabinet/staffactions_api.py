"""The staff action worklist routes (``cabinet.staffactions``).

- ``GET   /staff-actions`` — the active dataset's actions, created from the
  findings on first read, each with its notes, its history and the state of
  its message to the office. Every role may read; the aid role sees only
  Financial Aid's actions.
- ``PATCH /staff-actions/{id}`` — status, owner, due date (staff, admin).
  Every save carries ``expected_updated_at`` (422 without it) and is
  refused with 409 when the action changed since it was opened. One
  ``action.updated`` event per save.
- ``POST  /staff-actions/{id}/notes`` — add a note (staff, admin, and the
  executive's comment). One ``action.noted`` event, never the note text.
- ``POST  /staff-actions/{id}/send`` — a named staff member or admin sends
  the action to the office mailbox through the outbound seam (the outbox on
  disk by default; nothing leaves the machine). Counts and a sign-in link
  only. A sent message is never sent again (409); a failure is recorded on
  the message and answered 503, and the same request retries it.

The middleware's route table (``cabinet.security``) admits the roles; the
send route narrows itself to staff and admin like the decision dispatch, so
an executive's Send is a loud, logged 403.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from cabinet.auth import ENV_BIND, ROLE_ADMIN, ROLE_AID, ROLE_EXECUTIVE, ROLE_STAFF
from cabinet.outbound import OutboundError, outbound_from_env
from cabinet.questions import DISPATCH_CHANNEL
from cabinet.staffactions import (
    ACTION_NOTE_MAX_CHARS,
    ACTION_STATUSES,
    FINANCIAL_AID_OFFICE,
    TASK_PREFIX,
    action_specs,
    action_words,
    compose_message,
    valid_due_date,
)
from cabinet.store import CabinetStore, StaffActionConflict

ENV_PUBLIC_URL = "CABINET_PUBLIC_URL"

EDIT_ROLES = (ROLE_STAFF, ROLE_ADMIN)
NOTE_ROLES = (ROLE_STAFF, ROLE_ADMIN, ROLE_EXECUTIVE)
SEND_ROLES = (ROLE_STAFF, ROLE_ADMIN)
# The people an action can be given to: those who work the list.
ASSIGNABLE_ROLES = (ROLE_STAFF, ROLE_ADMIN, ROLE_AID)

# SQLite stores integers as signed 64-bit values; a larger path id names
# no row (and would raise OverflowError when bound).
_MAX_ROW_ID = 2**63 - 1

MISSING_VERSION_MESSAGE = (
    "Reload the action and send its updated_at as expected_updated_at "
    "(null for an action nobody has saved yet)."
)
STALE_MESSAGE = (
    "Someone else changed this action since you opened it. Your changes are "
    "kept; check the latest and save again."
)

router = APIRouter()


class StaffActionPatch(BaseModel):
    """A person's change to one action. A field left out is unchanged;
    ``owner`` null (or "") gives the action back to the office, and
    ``due_date`` null (or "") clears it."""

    status: str | None = None
    owner: str | None = None
    due_date: str | None = None
    expected_updated_at: str | None = None


class StaffActionNote(BaseModel):
    text: str = ""


def public_url() -> str | None:
    """Where people open CampusLens, for the message's sign-in link:
    ``CABINET_PUBLIC_URL``, else (outside production) the bind address,
    else None and the message names the page instead of linking it."""
    configured = os.environ.get(ENV_PUBLIC_URL, "").strip()
    if configured:
        return configured.rstrip("/")
    if os.environ.get("CABINET_ENV", "").strip().lower() == "production":
        return None
    bind = os.environ.get(ENV_BIND, "").strip() or "127.0.0.1:8910"
    return f"http://{bind}"


def _row_id(value: int) -> int:
    if not 1 <= value <= _MAX_ROW_ID:
        raise HTTPException(status_code=404, detail=f"no staff action {value}")
    return value


def _store(request: Request) -> CabinetStore:
    store: CabinetStore = request.app.state.auth
    return store


def _user(request: Request) -> dict[str, Any]:
    user: dict[str, Any] = request.scope["cabinet_user"]
    return user


def _refused(request: Request, status_code: int, detail: str) -> JSONResponse:
    """A refusal on the caller's audit chain (data.refused), never silent."""
    _log_refusal(request, detail)
    return JSONResponse(status_code=status_code, content={"detail": detail})


def _log_refusal(request: Request, detail: str) -> None:
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


def _message_body(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "status": row["status"],
        "to_office": row["to_office"],
        "subject": row["subject"],
        "body": row["body"],
        "sent_by": row["sent_by"],
        "sent_at": row["sent_at"],
        "error": row["error"],
    }


def _item_body(
    item: dict[str, Any],
    *,
    message: dict[str, Any] | None,
    mailbox: str | None,
) -> dict[str, Any]:
    count = item["count"] if isinstance(item["count"], int) else None
    words = action_words(str(item["finding_id"]), str(item["office"]), count)
    return {
        "id": int(item["id"]),
        "office": item["office"],
        "finding_id": item["finding_id"],
        "count": count,
        "title": words["title"],
        "what": words["what"],
        "noun": words["noun"],
        "status": item["status"],
        "owner": item["owner"],
        "due_date": item["due_date"],
        "updated_by": item["updated_by"],
        "updated_at": item["updated_at"],
        "created_at": item["created_at"],
        "notes": item.get("notes", []),
        "history": item.get("history", []),
        "office_mailbox": mailbox,
        "message": _message_body(message),
    }


def _active_dataset_id(request: Request, institution_id: int) -> tuple[int, Any]:
    runtime = request.app.state.runtime_for(institution_id)
    return int(runtime.dataset["id"]), runtime


def _visible(role: str, office: str) -> bool:
    return role != ROLE_AID or office == FINANCIAL_AID_OFFICE


def _one_item(request: Request, institution_id: int, action_id: int) -> dict[str, Any]:
    """The full body of one action of the active dataset (for answers)."""
    store = _store(request)
    dataset_id, _ = _active_dataset_id(request, institution_id)
    items = store.staff_actions_for(institution_id, dataset_id=dataset_id)
    item = next(entry for entry in items if int(entry["id"]) == action_id)
    message = store.dispatch_for_task(
        institution_id, f"{TASK_PREFIX}{item['action_key']}", dataset_id=dataset_id
    )
    return _item_body(
        item,
        message=message,
        mailbox=store.office_contact(institution_id, str(item["office"])),
    )


@router.get("/staff-actions")
def get_staff_actions(request: Request) -> dict[str, Any]:
    """The worklist for the active dataset. The actions are created from the
    findings on first read (idempotent), so every role sees the same list."""
    store = _store(request)
    user = _user(request)
    role = str(user["role"])
    institution_id = int(user["institution_id"])
    dataset_id, runtime = _active_dataset_id(request, institution_id)
    specs = action_specs(runtime.findings)
    store.ensure_staff_actions(
        institution_id,
        dataset_id=dataset_id,
        specs=[(s.key, s.office, s.finding_id, s.count) for s in specs],
    )
    items = store.staff_actions_for(institution_id, dataset_id=dataset_id)
    messages = store.dispatches_for_tasks(
        institution_id, dataset_id=dataset_id, prefix=TASK_PREFIX
    )
    mailboxes = {
        str(row["office"]): str(row["email"])
        for row in store.office_contacts_for(institution_id)
    }
    assignees = sorted(
        str(person["email"])
        for person in store.users_for(institution_id)
        if not person["disabled"] and person["role"] in ASSIGNABLE_ROLES
    )
    return {
        "dataset_id": dataset_id,
        "fictional": runtime.fictional,
        "statuses": list(ACTION_STATUSES),
        "note_max_chars": ACTION_NOTE_MAX_CHARS,
        "assignees": assignees,
        "can_edit": role in EDIT_ROLES,
        "can_note": role in NOTE_ROLES,
        "can_send": role in SEND_ROLES,
        "items": [
            _item_body(
                item,
                message=messages.get(f"{TASK_PREFIX}{item['action_key']}"),
                mailbox=mailboxes.get(str(item["office"])),
            )
            for item in items
            if _visible(role, str(item["office"]))
        ],
    }


@router.patch("/staff-actions/{action_id}")
def patch_staff_action(
    action_id: int, body: StaffActionPatch, request: Request
) -> JSONResponse:
    """Set an action's status, owner and/or due date (staff, admin)."""
    store = _store(request)
    user = _user(request)
    institution_id = int(user["institution_id"])
    fields = body.model_fields_set - {"expected_updated_at"}
    errors: list[str] = []
    if not fields:
        errors.append("send a status, an owner, a due date, or more than one")
    if "expected_updated_at" not in body.model_fields_set:
        errors.append(MISSING_VERSION_MESSAGE)
    changes: dict[str, str | None] = {}
    if "status" in fields:
        if body.status not in ACTION_STATUSES:
            errors.append(
                f"status {body.status!r} is not one of {', '.join(ACTION_STATUSES)}"
            )
        else:
            changes["status"] = body.status
    if "owner" in fields:
        owner = (body.owner or "").strip()
        if owner:
            people = {
                str(person["email"]).lower(): str(person["email"])
                for person in store.users_for(institution_id)
                if not person["disabled"] and person["role"] in ASSIGNABLE_ROLES
            }
            match = people.get(owner.lower())
            if match is None:
                errors.append(
                    f"{owner!r} is not a staff member, administrator or "
                    "Financial Aid user of this institution"
                )
            else:
                changes["owner"] = match
        else:
            changes["owner"] = None
    if "due_date" in fields:
        due = (body.due_date or "").strip()
        if due and not valid_due_date(due):
            errors.append(f"the due date {due!r} is not a date (YYYY-MM-DD)")
        else:
            changes["due_date"] = due or None
    if errors:
        return JSONResponse(
            status_code=422, content={"detail": "; ".join(errors), "errors": errors}
        )
    _row_id(action_id)
    try:
        change = store.update_staff_action(
            institution_id,
            action_id,
            changes=changes,
            updated_by=str(user["email"]),
            expected_updated_at=body.expected_updated_at,
        )
    except StaffActionConflict:
        return JSONResponse(
            status_code=409,
            content={
                "detail": STALE_MESSAGE,
                "item": _one_item(request, institution_id, action_id),
            },
        )
    if change is None:
        raise HTTPException(status_code=404, detail=f"no staff action {action_id}")
    before, after = change
    changed = sorted(field for field in changes if before[field] != after[field])
    event = store.audit_append(
        institution_id,
        "action.updated",
        actor=str(user["email"]),
        payload={
            "action_id": action_id,
            "office": after["office"],
            "finding_id": after["finding_id"],
            "fields": changed,
            "status_from": before["status"],
            "status_to": after["status"],
        },
    )
    return JSONResponse(
        content={
            "item": _one_item(request, institution_id, action_id),
            "event_id": event["id"],
        }
    )


@router.post("/staff-actions/{action_id}/notes")
def post_staff_action_note(
    action_id: int, body: StaffActionNote, request: Request
) -> JSONResponse:
    """Add a note to an action (staff, admin, executive). Stored exactly as
    typed; the audit event says a note was added, never what it says."""
    store = _store(request)
    user = _user(request)
    institution_id = int(user["institution_id"])
    if str(user["role"]) not in NOTE_ROLES:
        return _refused(
            request, 403, "only staff, an administrator or an executive can add a note"
        )
    text = body.text.strip()
    errors: list[str] = []
    if not text:
        errors.append("write a note first")
    elif len(text) > ACTION_NOTE_MAX_CHARS:
        errors.append(
            f"the note is {len(text):,} characters; the limit is "
            f"{ACTION_NOTE_MAX_CHARS:,}"
        )
    if errors:
        return JSONResponse(
            status_code=422, content={"detail": "; ".join(errors), "errors": errors}
        )
    _row_id(action_id)
    note = store.add_staff_action_note(
        institution_id, action_id, author=str(user["email"]), text=text
    )
    if note is None:
        raise HTTPException(status_code=404, detail=f"no staff action {action_id}")
    action = store.staff_action(institution_id, action_id)
    assert action is not None
    event = store.audit_append(
        institution_id,
        "action.noted",
        actor=str(user["email"]),
        payload={"action_id": action_id, "office": action["office"]},
    )
    return JSONResponse(
        content={
            "item": _one_item(request, institution_id, action_id),
            "event_id": event["id"],
        }
    )


@router.post("/staff-actions/{action_id}/send")
def post_staff_action_send(action_id: int, request: Request) -> JSONResponse:
    """Send one action to its office mailbox (staff, admin). Composed in code
    from the stored action: the counts and a sign-in link, never a student
    name or id. One message per action per dataset."""
    store = _store(request)
    user = _user(request)
    institution_id = int(user["institution_id"])
    if str(user["role"]) not in SEND_ROLES:
        return _refused(
            request,
            403,
            "only a staff member or an administrator can send an action to "
            "an office",
        )
    _row_id(action_id)
    with request.app.state.ask_lock_for(institution_id):
        action = store.staff_action(institution_id, action_id)
        if action is None:
            raise HTTPException(status_code=404, detail=f"no staff action {action_id}")
        dataset_id = int(action["dataset_id"])
        task_id = f"{TASK_PREFIX}{action['action_key']}"
        office = str(action["office"])
        existing = store.dispatch_for_task(
            institution_id, task_id, dataset_id=dataset_id
        )
        if existing is not None and existing["status"] == "sent":
            _log_refusal(request, "this action was already sent to the office")
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "this action was already sent to the office",
                    "item": _one_item(request, institution_id, action_id),
                },
            )
        contact = store.office_contact(institution_id, office)
        if contact is None:
            return _refused(
                request,
                409,
                f"no mailbox is set for the {office} office; an administrator "
                "can add one in Institution settings",
            )
        institution = store.institution_by_id(institution_id)
        assert institution is not None
        # Composed at the click, from the action as it stands: a retry after
        # a failure carries the current status, owner and due date.
        composed = compose_message(
            action,
            sender=str(user["email"]),
            institution_name=str(institution["name"]),
            sign_in_url=public_url(),
        )
        if existing is not None:
            existing = store.recompose_dispatch(
                institution_id,
                int(existing["id"]),
                subject=composed["subject"],
                body=composed["body"],
            ) or store.dispatch_for_task(
                institution_id, task_id, dataset_id=dataset_id
            )
            assert existing is not None
        else:
            existing = store.create_dispatch(
                institution_id,
                task_id=task_id,
                dataset_id=dataset_id,
                to_office=office,
                channel=DISPATCH_CHANNEL,
                subject=composed["subject"],
                body=composed["body"],
                created_by=str(user["email"]),
            ) or store.dispatch_for_task(
                institution_id, task_id, dataset_id=dataset_id
            )
            assert existing is not None
        dispatch_id = int(existing["id"])

        def failed(reason: str, provider_name: str | None) -> JSONResponse:
            store.mark_dispatch_failed(institution_id, dispatch_id, error=reason)
            store.record_staff_action_send(
                institution_id,
                action_id,
                actor=str(user["email"]),
                sent=False,
                detail=reason,
            )
            payload: dict[str, Any] = {
                "action_id": action_id,
                "office": office,
                "dispatch_id": dispatch_id,
                "error": reason,
            }
            if provider_name is not None:
                payload["provider"] = provider_name
            store.audit_append(
                institution_id,
                "action.send_failed",
                actor=str(user["email"]),
                payload=payload,
            )
            return JSONResponse(
                status_code=503,
                content={
                    "detail": f"The message was not sent: {reason}",
                    "item": _one_item(request, institution_id, action_id),
                },
            )

        try:
            provider = outbound_from_env(
                outbox_dir=store.path.parent / "outbox",
                production=bool(getattr(request.app.state, "production", False)),
            )
        except RuntimeError as exc:
            return failed(str(exc), None)
        try:
            provider_ref = provider.send(
                to=contact,
                subject=str(existing["subject"]),
                body=str(existing["body"]),
                dispatch_id=dispatch_id,
                institution_slug=str(institution["slug"]),
            )
        except OutboundError as exc:
            return failed(str(exc), provider.name)
        sent = store.mark_dispatch_sent(
            institution_id,
            dispatch_id,
            sent_by=str(user["email"]),
            provider=provider.name,
            provider_ref=provider_ref,
        )
        if sent is None:
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "this action was already sent to the office",
                    "item": _one_item(request, institution_id, action_id),
                },
            )
        store.record_staff_action_send(
            institution_id,
            action_id,
            actor=str(user["email"]),
            sent=True,
            detail=contact,
        )
        event = store.audit_append(
            institution_id,
            "action.sent",
            actor=str(user["email"]),
            payload={
                "action_id": action_id,
                "office": office,
                "dispatch_id": dispatch_id,
                "provider": provider.name,
                "provider_ref": provider_ref,
            },
        )
        return JSONResponse(
            content={
                "item": _one_item(request, institution_id, action_id),
                "event_id": event["id"],
            }
        )


__all__ = ["ENV_PUBLIC_URL", "public_url", "router"]
