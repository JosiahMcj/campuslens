"""Append-only audit log (ROADMAP §3 layer 7), hash-chained.

The primary store is the ``audit_events`` table in
``var/cabinet.db`` (see ``cabinet.store``): one event chain per institution
(scope 0 is the platform chain for pre-auth events), each event with ``id``
(monotonic per institution), ``ts`` (ISO UTC), ``type``, ``actor``,
``payload``, ``prev_hash`` and ``hash``. This module keeps the chain math
(``event_hash``, ``verify_events``), the JSONL ``AuditLog`` (still used by
unit tests and as the export shape), and the CLI:

- ``python -m cabinet.audit verify <events.jsonl|cabinet.db>``
- ``python -m cabinet.audit export <cabinet.db> <out.jsonl> --institution
  <slug|id|platform>``

Each event carries ``prev_hash`` and ``hash``: a sha256 chain over the
canonical JSON of the event without its ``hash`` field, starting from a
fixed genesis (per institution). Editing, reordering, or inserting an
event breaks the chain; ``verify`` replays it and exits non-zero on any
break. (A chain cannot detect tail truncation — deleting whole trailing
events relinks cleanly — so retention of backups matters; see
docs/SECURITY.md.) Events written before the chain existed carry no
hashes; they verify as-is and the next appended event chains off a hash
computed from the legacy event's fields, so old logs keep working.

The JSONL parsing below is strict about tampering but tolerant of a torn
write: a malformed *final* line (the process was killed mid-append) is
skipped with a ``logging.warning`` and the next id still recovers; a
malformed line anywhere else raises a ``ValueError`` naming the line
number, because an append-only log can only gain a bad middle line by
tampering.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

EVENT_TYPES: tuple[str, ...] = (
    "question.asked",
    "task.assigned",
    "data.granted",
    "data.refused",
    "finding.produced",
    "briefing.produced",
    "decision.approved",
    "task.created",
    # Dataset administration (per institution).
    "dataset.uploaded",
    "dataset.activated",
    "dataset.deleted",
    # User administration (per institution). The one allowed extension
    # of the frozen vocabulary; see DECISIONS.md 2026-09-25. Payload:
    # {action, target_user_id, role, by} — never a password.
    "admin.changed",
    # The governed execution step (per institution). task.dispatched: a
    # draft message to the responsible office was composed in code from
    # the approved task's findings (payload: dispatch_id, task_id,
    # decision_id, to_office, subject). task.sent: a named staff member or
    # admin clicked Send and the provider accepted it (payload adds
    # provider and provider_ref; the actor is the sending user).
    "task.dispatched",
    # task.send_failed: a named person clicked Send and the provider refused
    # or failed; the chain keeps the attempt (error text, never a secret or
    # the message body) so a later retry is never a silent second delivery.
    "task.send_failed",
    "task.sent",
    # The Financial Aid review queue (per institution). aid.queued: a named
    # person prepared the queue for an authorized emergency-aid review
    # (payload: decision_id, dataset_id, count; never a student id).
    # aid.updated: a person in the aid role (or an admin) changed one row's
    # status or note (payload: aid_review_id, decision_id, status_from,
    # status_to, note_changed). Never the note text and never a student id,
    # since this log outlives the dataset purge that removes the row. The actor
    # of both is the acting user's email.
    "aid.queued",
    "aid.updated",
    # Explore (cabinet.explore): one answer to a governed question over the
    # school data. Payload: task_id, question_event_id, the analysis ids of
    # the steps, their row counts, the planner and writer used. Never a value.
    "explore.answered",
    # The staff action worklist (cabinet.staffactions). action.updated: a
    # person changed an action's status, owner or due date (payload:
    # action_id, office, finding_id, fields, status_from, status_to).
    # action.noted: a person added a note (payload: action_id, office; never
    # the note text). action.sent / action.send_failed: a named staff member
    # or admin sent the action to the office mailbox (payload: action_id,
    # office, dispatch_id, provider, provider_ref or error). Never a
    # student id: an action is an office and a count.
    "action.updated",
    "action.noted",
    "action.sent",
    "action.send_failed",
    # The demonstration student directory (cabinet.roster). student.searched:
    # a named executive or admin searched the directory by name (payload:
    # matches, shown, directory). Never the search text and never a student
    # id or name: the log records that a lookup happened and by whom.
    "student.searched",
    # The per-account inbox (cabinet.inbox). inbox.sent: a named person sent
    # an alert to another account (payload: message_id, recipient_id,
    # recipient_role, source_kind, source_ref, has_review_by). inbox.read /
    # inbox.reviewed: the recipient opened it / marked it reviewed (payload:
    # message_id, sender_id). Never the note text and never a student id.
    "inbox.sent",
    "inbox.read",
    "inbox.reviewed",
)

ENV_VAR = "CABINET_AUDIT_PATH"
DEFAULT_AUDIT_PATH = (
    Path(__file__).resolve().parents[3] / "var" / "audit" / "events.jsonl"
)

# The fixed start of the hash chain: the first event's prev_hash.
GENESIS_HASH = hashlib.sha256(b"cabinet-audit-chain-genesis-v1").hexdigest()


class AuditSink(Protocol):
    """The append-only interface the analysts, the permission gate, and the
    API write through. Implemented by the JSONL ``AuditLog`` below (unit
    tests, exports) and by ``cabinet.store.ScopedAudit`` (the database,
    one chain per institution)."""

    def append(
        self, event_type: str, *, actor: str, payload: dict[str, Any]
    ) -> dict[str, Any]: ...

    def append_once(
        self,
        event_type: str,
        match_field: str,
        match_value: Any,
        events: list[tuple[str, str, dict[str, Any]]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]: ...

    def events(self, event_type: str | None = None) -> list[dict[str, Any]]: ...


def event_hash(event: dict[str, Any]) -> str:
    """sha256 over the canonical event without its ``hash`` field.

    Canonical means sorted keys, tight separators, UTF-8 — the same
    serialization whoever computes it, so verification replays the exact
    bytes the writer hashed. ``prev_hash`` stays in the material, which is
    what links the chain.
    """
    material = {key: value for key, value in event.items() if key != "hash"}
    canonical = json.dumps(
        material, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_events(events: list[dict[str, Any]], *, scope: str = "") -> list[str]:
    """Replay the hash chain over already-parsed events; return the breaks.

    For every event: a stored ``hash`` must equal :func:`event_hash` of the
    event as stored, and a stored ``prev_hash`` must equal the previous
    event's hash (recomputed from its stored fields, so a tampered earlier
    event is caught even when the later link was recomputed honestly) — the
    first event's ``prev_hash`` must be the genesis. Legacy events without
    hash fields carry no links to check but still feed the next event's
    ``prev_hash``. ``scope`` prefixes break labels (e.g. the institution an
    events list belongs to when verifying a database).
    """
    breaks: list[str] = []
    for index, event in enumerate(events):
        label = f"{scope}line {index + 1} (id {event.get('id', '?')})"
        stored = event.get("hash")
        if stored is not None and stored != event_hash(event):
            breaks.append(f"{label}: stored hash does not match the event")
        if "prev_hash" in event:
            expected = (
                GENESIS_HASH if index == 0 else event_hash(events[index - 1])
            )
            if event["prev_hash"] != expected:
                breaks.append(f"{label}: prev_hash does not match the chain")
    return breaks


def verify_chain(path: str | Path) -> list[str]:
    """Replay the hash chain of a JSONL log; empty list means sound.

    A malformed line anywhere but at the torn tail is a break, same as at
    open time.
    """
    path = Path(path)
    try:
        events = read_events(path)
    except ValueError as exc:
        return [str(exc)]
    return verify_events(events)


def _db_events_by_scope(db_path: str | Path) -> dict[int, list[dict[str, Any]]]:
    """All audit events in a cabinet database, grouped by institution scope
    (0 is the platform scope), each list in id order."""
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if "audit_events" not in tables:
            return {}
        rows = conn.execute(
            "SELECT institution_id, id, ts, type, actor, payload, prev_hash, hash"
            " FROM audit_events ORDER BY institution_id, id"
        ).fetchall()
    finally:
        conn.close()
    scopes: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        event = {
            "id": int(row["id"]),
            "ts": row["ts"],
            "type": row["type"],
            "actor": row["actor"],
            "payload": json.loads(row["payload"]),
            "prev_hash": row["prev_hash"],
            "hash": row["hash"],
        }
        scopes.setdefault(int(row["institution_id"]), []).append(event)
    return scopes


def verify_db(db_path: str | Path) -> list[str]:
    """Verify every per-institution hash chain in a cabinet database.

    Each institution's events form their own chain off the genesis hash;
    the platform scope (0) likewise. The breaks name the scope.
    """
    breaks: list[str] = []
    for scope, events in _db_events_by_scope(db_path).items():
        label = f"institution {scope}: " if scope else "platform: "
        breaks.extend(verify_events(events, scope=label))
    return breaks


def export_db(
    db_path: str | Path, institution_id: int, out_path: str | Path
) -> int:
    """Write one institution's audit chain as JSONL (the file shape).

    Returns the number of events written. The export is a snapshot for
    review or archival; the database stays the primary store.
    """
    events = _db_events_by_scope(db_path).get(institution_id, [])
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return len(events)


def resolve_institution_scope(db_path: str | Path, selector: str) -> int:
    """The audit scope for ``--institution``: an institution slug or id, or
    ``platform`` for the pre-auth chain (scope 0)."""
    import sqlite3

    if selector.strip().lower() == "platform":
        return 0
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT id FROM institutions WHERE slug = ?", (selector.strip().lower(),)
        ).fetchone()
        if row is not None:
            return int(row[0])
        try:
            institution_id = int(selector)
        except ValueError:
            institution_id = -1
        row = conn.execute(
            "SELECT id FROM institutions WHERE id = ?", (institution_id,)
        ).fetchone()
        if row is not None:
            return int(row[0])
    finally:
        conn.close()
    raise ValueError(
        f"no institution matching {selector!r} (use a slug, an id, or 'platform')"
    )


def audit_path_from_env() -> Path:
    """The configured audit path: CABINET_AUDIT_PATH, else var/audit/events.jsonl."""
    override = os.environ.get(ENV_VAR)
    return Path(override) if override else DEFAULT_AUDIT_PATH


def read_events(path: Path) -> list[dict[str, Any]]:
    """All events in the file, in order. Missing file means no events.

    A malformed final line (a torn write) is skipped with a warning; a
    malformed line anywhere else raises ``ValueError`` naming the line.
    """
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    events: list[dict[str, Any]] = []
    for lineno, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line:
            continue
        event: Any = None
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            event = None
        if not isinstance(event, dict):
            if lineno == len(lines):
                logger.warning(
                    "%s: skipping malformed final audit line %d "
                    "(torn write from a killed process?)",
                    path,
                    lineno,
                )
                continue
            raise ValueError(
                f"{path}: malformed audit line {lineno}; the log is "
                "append-only, so a bad line here means tampering"
            )
        events.append(event)
    return events


class AuditLog:
    """An append-only handle on one JSONL audit file.

    The file is parsed once at construction into an in-memory index; every
    append updates both, so reads never re-parse the file per request. A
    second AuditLog on the same file (e.g. a tool run alongside the API) is
    tolerated: before each append the file size is re-checked, and if the
    file grew since this handle's last write it is re-read and the next id
    recomputed, so two writers can never hand out the same id.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._truncate_torn_tail()
        self._events = read_events(self._path)
        self._next_id = self._recover_next_id()
        self._last_size = self._file_size()

    def _file_size(self) -> int:
        """The log file's current size; -1 when it does not exist."""
        try:
            return self._path.stat().st_size
        except OSError:
            return -1

    def _recover_next_id(self) -> int:
        next_id = 1
        for event in self._events:
            event_id = event.get("id")
            if isinstance(event_id, int) and event_id >= next_id:
                next_id = event_id + 1
        return next_id

    def _truncate_torn_tail(self) -> None:
        """Remove a torn final fragment before any append.

        A process killed mid-append leaves bytes after the last newline.
        Left in place, the next append would be glued onto that fragment on
        the same line, and the *next* restart would read it as a malformed
        middle line — tampering — and refuse to start. The removed bytes are
        kept in a sibling ``<name>.torn-<timestamp>`` file. The repair itself
        is crash-safe: the torn bytes are copied to the sidecar first, then
        the file is shortened with a single ``os.truncate`` (never rewritten
        from zero, so a crash mid-repair cannot blank the log), and the
        directory is fsynced so the repair survives a crash.
        """
        try:
            raw = self._path.read_bytes()
        except OSError:
            return
        if not raw or raw.endswith(b"\n"):
            return
        last_newline = raw.rfind(b"\n")
        tail = raw[last_newline + 1 :]
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        torn_path = self._path.with_name(f"{self._path.name}.torn-{timestamp}")
        torn_path.write_bytes(tail)
        os.truncate(self._path, last_newline + 1)
        dir_fd = os.open(self._path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        logger.warning(
            "%s: truncated %d torn byte(s) from the end of the audit log "
            "(a killed process's partial write); kept a copy at %s",
            self._path,
            len(tail),
            torn_path,
        )

    @property
    def path(self) -> Path:
        return self._path

    def append(
        self, event_type: str, *, actor: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Append one event and return it. Refuses unknown event types."""
        with self._lock:
            return self._append_locked(event_type, actor=actor, payload=payload)

    def _append_locked(
        self, event_type: str, *, actor: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if event_type not in EVENT_TYPES:
            raise ValueError(
                f"unknown audit event type {event_type!r}; "
                f"expected one of {', '.join(EVENT_TYPES)}"
            )
        # A second writer on the same file (e.g. a tool run alongside the
        # API) appends without going through this handle. If the file grew
        # since our last write, reload before assigning the next id so the
        # two writers can never hand out the same id — and this handle's
        # view picks up the other writer's events.
        if self._file_size() != self._last_size:
            self._events = read_events(self._path)
            self._next_id = max(self._next_id, self._recover_next_id())
        prev_hash = (
            event_hash(self._events[-1]) if self._events else GENESIS_HASH
        )
        event: dict[str, Any] = {
            "id": self._next_id,
            "ts": datetime.now(UTC).isoformat(),
            "type": event_type,
            "actor": actor,
            "payload": payload,
            "prev_hash": prev_hash,
        }
        event["hash"] = event_hash(event)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._events.append(event)
        self._next_id += 1
        self._last_size = self._file_size()
        return event

    def _find_locked(
        self, event_type: str, match_field: str, match_value: Any
    ) -> dict[str, Any] | None:
        for event in self._events:
            if (
                event.get("type") == event_type
                and event.get("payload", {}).get(match_field) == match_value
            ):
                return event
        return None

    def append_once(
        self,
        event_type: str,
        match_field: str,
        match_value: Any,
        events: list[tuple[str, str, dict[str, Any]]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        """Atomic check-and-append for idempotent operations.

        If an event of ``event_type`` whose payload field ``match_field``
        equals ``match_value`` already exists, append nothing and return
        ``([], existing)``. Otherwise append every ``(type, actor, payload)``
        triple in ``events`` — still holding the lock, so a concurrent caller
        (FastAPI runs sync handlers in a threadpool) cannot interleave its own
        check between our check and our append — and return ``(appended,
        None)``.
        """
        with self._lock:
            existing = self._find_locked(event_type, match_field, match_value)
            if existing is not None:
                return [], existing
            appended = [
                self._append_locked(t, actor=actor, payload=payload)
                for t, actor, payload in events
            ]
            return appended, None

    def events(self, event_type: str | None = None) -> list[dict[str, Any]]:
        """All events in order, optionally filtered to one event type."""
        with self._lock:
            events = list(self._events)
        if event_type is not None:
            events = [e for e in events if e.get("type") == event_type]
        return events


def main(argv: list[str] | None = None) -> int:
    """Audit chain tooling.

    - ``python -m cabinet.audit verify <events.jsonl|cabinet.db>`` — replay
      the hash chain(s). A ``.jsonl`` path verifies the one chain in the
      file; anything else is treated as a cabinet database and every
      per-institution chain (plus the platform chain) is verified.
    - ``python -m cabinet.audit export <cabinet.db> <out.jsonl>
      --institution <slug|id|platform>`` — write one institution's chain as
      JSONL (the file shape) for review or archival.

    ``verify`` exits 0 when the chain(s) are sound, 1 on any break (each
    break prints on its own stderr line).
    """
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    command = args.pop(0)
    if command == "verify" and len(args) == 1:
        target = args[0]
        if target.endswith(".jsonl"):
            breaks = verify_chain(target)
        else:
            breaks = verify_db(target)
        if breaks:
            for line in breaks:
                print(f"audit chain broken: {line}", file=sys.stderr)
            return 1
        print(f"{target}: audit chain verified")
        return 0
    if command == "export" and len(args) >= 2:
        db_path, out_path = args[0], args[1]
        selector: str | None = None
        rest = args[2:]
        if rest[:1] == ["--institution"] and len(rest) == 2:
            selector = rest[1]
        elif rest:
            print(__doc__, file=sys.stderr)
            return 2
        if selector is None:
            print(
                "export: --institution <slug|id|platform> is required",
                file=sys.stderr,
            )
            return 2
        try:
            scope = resolve_institution_scope(db_path, selector)
        except ValueError as exc:
            print(f"export: {exc}", file=sys.stderr)
            return 1
        count = export_db(db_path, scope, out_path)
        print(f"exported {count} audit event(s) for {selector} to {out_path}")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
