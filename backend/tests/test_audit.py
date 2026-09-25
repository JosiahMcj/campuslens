"""Tests for the audit log's append protocol and torn-tail repair.

Covers: two AuditLog handles on one file never hand out the same id (the
append path re-checks the file size and reloads before assigning an id), and
the torn-tail repair shortens the file with a single ``os.truncate`` — never
a truncate-to-zero-then-rewrite, so a crash mid-repair cannot blank the log.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cabinet.audit import AuditLog, read_events


def _event_line(event_id: int) -> str:
    return (
        json.dumps(
            {
                "id": event_id,
                "ts": "2026-09-25T00:00:00+00:00",
                "type": "task.created",
                "actor": "test",
                "payload": {},
            }
        )
        + "\n"
    )


def test_two_handles_on_one_file_never_duplicate_ids(tmp_path: Path) -> None:
    """Two AuditLog instances appending alternately (the API and a tool run)
    assign strictly increasing, never-duplicated ids."""
    path = tmp_path / "events.jsonl"
    first = AuditLog(path)
    second = AuditLog(path)

    ids: list[int] = []
    for index in range(6):
        handle = first if index % 2 == 0 else second
        ids.append(
            handle.append("task.created", actor="test", payload={"n": index})["id"]
        )
    # One more append from the first handle reloads the second's writes
    # before assigning its next id.
    ids.append(first.append("task.created", actor="test", payload={"n": 6})["id"])

    assert ids == [1, 2, 3, 4, 5, 6, 7]
    # The file agrees, and an appending handle's view picks up the other's
    # events (a handle that has not appended since refreshes on its next one).
    assert [e["id"] for e in read_events(path)] == ids
    assert [e["id"] for e in first.events()] == ids
    assert [e["id"] for e in second.events()] == ids[:-1]


def test_torn_tail_repair_truncates_in_place_never_rewrites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The repair copies the torn bytes to a sidecar, then shortens the file
    with one os.truncate to a NON-ZERO size — the log file is never rewritten
    with write_bytes (which truncates to zero first and would lose the whole
    log to a crash mid-repair)."""
    path = tmp_path / "events.jsonl"
    good = _event_line(1) + _event_line(2)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(good.encode("utf-8") + b'{"id": 3, "tor')  # torn tail

    truncates: list[tuple[object, int]] = []
    real_truncate = os.truncate

    def spy_truncate(target: object, size: int) -> None:
        truncates.append((target, size))
        real_truncate(target, size)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "truncate", spy_truncate)

    real_write_bytes = Path.write_bytes

    def spy_write_bytes(self: Path, data: bytes) -> int:
        assert self != path, "the audit log itself must never be rewritten"
        return real_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", spy_write_bytes)

    log = AuditLog(path)

    assert [event_id for (_target, event_id) in truncates] == [len(good.encode())]
    assert all(size > 0 for (_target, size) in truncates)
    assert path.read_bytes() == good.encode("utf-8")
    sidecars = list(tmp_path.glob("events.jsonl.torn-*"))
    assert len(sidecars) == 1
    assert sidecars[0].read_bytes() == b'{"id": 3, "tor'
    # The log keeps working after the repair.
    assert [e["id"] for e in log.events()] == [1, 2]
    assert log.append("task.created", actor="test", payload={})["id"] == 3
