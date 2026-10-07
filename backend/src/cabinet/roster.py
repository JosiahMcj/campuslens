"""The demonstration student directory and its name search.

``GET /students/search?q=`` finds students by name (or exact student id) in
``data/roster/students.json``, a seeded, fictional directory written by
``data/roster/generate_students.py``. It is separate from the governed
dataset the briefing is computed from, and it is the one place a person's
name appears, so it is deliberately narrow:

- **Who.** The executive and the admin only (``ROW_ROLES``, the same roles
  that may open the records behind a figure). No AI employee ever receives
  the directory; nothing here is sent to a model.
- **When.** Only while the institution's active dataset is the fictional
  demonstration set. With real data in use the route answers 404: a real
  institution's names are not in this file and must not be mixed with it.
- **Record.** Every search writes one ``student.searched`` audit event
  naming the person who searched and how many students matched. The search
  text and the students' ids are never logged, like the rest of the log.

Nothing is computed in the UI: the percentages and the GPA change arrive as
display strings from here.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from cabinet.store import CabinetStore

ENV_ROSTER = "CABINET_ROSTER"
DEFAULT_ROSTER_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "roster" / "students.json"
)
MIN_QUERY_CHARS = 2
MAX_QUERY_CHARS = 80
RESULT_LIMIT = 25

router = APIRouter()

_lock = threading.Lock()
_cache_key: tuple[str, int, int] | None = None
_cache_rows: list[dict[str, Any]] = []


def roster_path() -> Path:
    override = os.environ.get(ENV_ROSTER, "").strip()
    return Path(override) if override else DEFAULT_ROSTER_PATH


def load_roster() -> list[dict[str, Any]] | None:
    """The directory rows, or None when no directory file is present. Read
    once and kept until the file changes."""
    path = roster_path()
    try:
        stat = path.stat()
    except OSError:
        return None
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    global _cache_key, _cache_rows
    with _lock:
        if _cache_key != key:
            rows = json.loads(path.read_text(encoding="utf-8"))
            _cache_rows = [_prepare(row) for row in rows]
            _cache_key = key
        return _cache_rows


def _prepare(row: dict[str, Any]) -> dict[str, Any]:
    name = str(row["name"])
    return {**row, "_tokens": tuple(name.lower().split()), "_name": name.lower()}


def _signed(value: float) -> str:
    """A GPA change with a real minus sign: "+0.07", "−0.81", "0.00"."""
    rounded = round(value, 2)
    if rounded == 0:
        return "0.00"
    return f"{'+' if rounded > 0 else '−'}{abs(rounded):.2f}"


def student_body(row: dict[str, Any]) -> dict[str, Any]:
    current = float(row["currentGPA"])
    previous = float(row["previousGPA"])
    progress = float(row["degreeProgress"])
    return {
        "student_id": row["studentId"],
        "name": row["name"],
        "program": row["program"],
        "degree_progress": progress,
        "degree_progress_display": f"{round(progress * 100)}%",
        "current_gpa_display": f"{current:.2f}",
        "previous_gpa_display": f"{previous:.2f}",
        "gpa_change_display": _signed(current - previous),
        "holds": list(row["holds"]),
        "advisor": row["advisor"],
    }


def search_roster(rows: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Students whose name matches: every word typed must start a word of
    the name ("obi am" finds Obinna Amadi). An exact student id also
    matches. Names whose words start with the typed words in the same order
    come first, then the rest, each by name."""
    text = " ".join(query.lower().split())
    words = text.split()
    hits = [
        row
        for row in rows
        if str(row["studentId"]).lower() == text
        or all(any(part.startswith(word) for part in row["_tokens"]) for word in words)
    ]

    def in_order(row: dict[str, Any]) -> bool:
        tokens = row["_tokens"]
        return len(words) <= len(tokens) and all(
            tokens[index].startswith(word) for index, word in enumerate(words)
        )

    hits.sort(key=lambda row: (not in_order(row), row["_name"]))
    return hits


@router.get("/students/search")
def get_students_search(
    request: Request, q: str = Query(default="", max_length=MAX_QUERY_CHARS)
) -> dict[str, Any]:
    store: CabinetStore = request.app.state.auth
    user = request.scope["cabinet_user"]
    institution_id = int(user["institution_id"])
    runtime = request.app.state.runtime_for(institution_id)
    rows = load_roster()
    if rows is None or not runtime.fictional:
        raise HTTPException(
            status_code=404,
            detail=(
                "the student directory is demonstration data and is only "
                "available with the fictional dataset"
            ),
        )
    query = q.strip()
    if len(query) < MIN_QUERY_CHARS:
        raise HTTPException(
            status_code=422,
            detail=f"type at least {MIN_QUERY_CHARS} letters of a name",
        )
    hits = search_roster(rows, query)
    shown = hits[:RESULT_LIMIT]
    event = store.audit_append(
        institution_id,
        "student.searched",
        actor=str(user["email"]),
        payload={
            "matches": len(hits),
            "shown": len(shown),
            "directory": "demonstration",
        },
    )
    return {
        "query": query,
        "total": len(hits),
        "shown": len(shown),
        "directory_size": len(rows),
        "fictional": True,
        "event_id": event["id"],
        "students": [student_body(row) for row in shown],
    }


__all__ = ["load_roster", "roster_path", "router", "search_roster", "student_body"]
