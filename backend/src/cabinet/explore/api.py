"""The Explore routes: ``POST /explore`` and ``GET /explore/catalog``.

Roles, CSRF, and the ask rate bucket are enforced by the security middleware
(``cabinet.security``: executive, staff, admin, and reviewer; the aid role is
a 403). The store comes from ``app.state.auth`` and the caller from the
session, so the router needs no arguments. Audit events land on the
caller's institution chain:

- ``question.asked`` for every question (a student-id-shaped token in it is
  replaced before it is recorded);
- ``data.refused`` when the question is refused before planning (counseling,
  an individual student, a prediction about one), and when instructor rows
  are withheld from a role;
- ``data.granted`` per step, before the step reads anything: the analysis id,
  the fields it reads, ``aggregate_only: true``;
- ``explore.answered`` once per answer: step ids and row counts, never values.

The school database is one shared demonstration university; every
institution's users query the same fictional data.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import re
import threading
from collections.abc import AsyncIterator, Callable
from typing import Any

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from cabinet.counseling import SUPPRESSED_DISPLAY
from cabinet.explore import general
from cabinet.explore.answer import SOURCE_MODEL, write_answer
from cabinet.explore.catalog import (
    ANALYSES,
    INSTRUCTOR_ROLES,
    SchoolDataMissing,
    catalog_for,
    connect_readonly,
)
from cabinet.explore.execute import StepResult, execute
from cabinet.explore.planner import (
    EXAMPLE_QUESTIONS,
    UNANSWERABLE_MESSAGE,
    describe_analysis,
    nearest_examples,
    plan_question,
    uses_model,
)
from cabinet.explore.privacy import redact_question, refusal_for
from cabinet.provider import provider_from_env

logger = logging.getLogger(__name__)

MAX_QUESTION_CHARS = 500
UNFINISHED_MESSAGE = (
    "CampusLens could not finish this answer. The question was recorded, and "
    "nothing was guessed."
)
EXPLORE_ACTOR = "chief_of_staff"

router = APIRouter()

ANALYSES_BY_ID = {a.id: a for a in ANALYSES}
_READING_CACHE: dict[str, str] = {}
_YEAR_WORDS = {
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
}


def _reading_text(con: Any) -> str:
    """The whole school's size and span (computed once per database)."""
    institution = str(
        con.execute("SELECT value FROM meta WHERE key = 'institution'").fetchone()[0]
    )
    key = institution + str(
        con.execute("SELECT value FROM meta WHERE key = 'generator_version'").fetchone()
    )
    if key not in _READING_CACHE:
        students = int(con.execute("SELECT COUNT(*) FROM students").fetchone()[0])
        first, last, years = con.execute(
            "SELECT (SELECT name FROM academic_periods ORDER BY sequence LIMIT 1), "
            "(SELECT name FROM academic_periods ORDER BY sequence DESC LIMIT 1), "
            "(SELECT COUNT(DISTINCT academic_year) FROM academic_periods)"
        ).fetchone()
        # Named apart from the briefing's one-term snapshot: this is every
        # year of the school records.
        span = _YEAR_WORDS.get(int(years), f"{int(years):,}")
        noun = "year" if int(years) == 1 else "years"
        _READING_CACHE[key] = (
            f"Reading {span} {noun} of student records (fictional data): "
            f"{students:,} students, {first} to {last}"
        )
    return _READING_CACHE[key]


class ExploreRequest(BaseModel):
    question: str


def _missing() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"available": False, "message": SchoolDataMissing().args[0]},
    )


def _table_json(step: StepResult) -> dict[str, Any]:
    """The step's table, each column with its kind (count, gpa, pct, ...), so
    the screen formats a GPA with two decimals and a rate with one."""
    table = step.table()
    kinds = {c.key: c.kind for c in step.columns}
    table["columns"] = [
        {**column, "kind": kinds.get(str(column["key"]), "text")}
        for column in table["columns"]
    ]
    return table


def _step_json(step: StepResult) -> dict[str, Any]:
    out: dict[str, Any] = {
        "analysis_id": step.analysis.id,
        "title": step.analysis.title,
        # The parameters a reader cares about, in plain words; the exact
        # record (codes, row limits) stays in the step and the CLI.
        "params_plain": step.params_shown,
        # The fields this step used (the audit event keeps the analysis's
        # full list): for the general measure, its inputs and groupings only.
        "fields_read": list(
            general.fields_used(step.params)
            if step.analysis.id == general.ANALYSIS_ID and not step.error
            else step.fields_read
        ),
        "aggregate_only": True,
        "table": _table_json(step),
        "notes": step.notes,
    }
    if step.instructor_rows_withheld:
        out["instructor_rows_withheld"] = True
    if step.error:
        out["error"] = step.error
    return out


@router.get("/explore/catalog")
def get_explore_catalog() -> JSONResponse:
    try:
        con = connect_readonly()
    except SchoolDataMissing:
        return _missing()
    try:
        catalog = catalog_for(con)
        return JSONResponse(
            content={
                "institution": catalog.vocab.institution,
                "fictional": True,
                "analyses": [describe_analysis(a) for a in ANALYSES],
                "examples": list(EXAMPLE_QUESTIONS),
                "instructor_roles": list(INSTRUCTOR_ROLES),
            }
        )
    finally:
        con.close()


@router.post("/explore")
async def post_explore(body: ExploreRequest, request: Request) -> JSONResponse:
    # The work reads SQLite and may call the provider, so it runs in the
    # threadpool, never on the event loop.
    return await run_in_threadpool(_explore, body, request)


# --- the live trace ------------------------------------------------------------

# One event of the live trace, as the pipeline really reaches each point.
# Events carry plain words and figures already computed and suppressed:
# never a student row, an id, or the question's text.
Emit = Callable[[dict[str, Any]], None]
STREAM_MEDIA_TYPE = "application/x-ndjson"
_STREAM_END = object()


def _no_emit(_event: dict[str, Any]) -> None:
    return None


@router.post("/explore/stream", response_model=None)
async def post_explore_stream(
    body: ExploreRequest, request: Request
) -> StreamingResponse | JSONResponse:
    """The same answer as POST /explore, streamed as newline-delimited JSON
    events while it is worked on (``understood``, ``plan``, ``reading``,
    ``step``, ``suppression``, ``writing``, ``verifying``), ending in
    ``done`` with the body POST /explore returns, ``refused``, or ``error``.
    A request /explore would answer with a status other than 200 (an empty
    or too long question, the school data missing) gets that same plain
    JSON response."""
    invalid = _invalid(body)
    if invalid is not None:
        return invalid
    try:
        connect_readonly().close()
    except SchoolDataMissing:
        return _missing()
    events: queue.Queue[Any] = queue.Queue()

    def work() -> None:
        try:
            response = _explore(body, request, emit=events.put)
            content = json.loads(bytes(response.body))
            if content.get("refused"):
                events.put({"type": "refused", "response": content})
            elif response.status_code != 200 or "answer" not in content:
                events.put({"type": "error", "message": UNFINISHED_MESSAGE})
            else:
                events.put({"type": "done", "response": content})
        except Exception:  # a defect: logged, never a stack trace on the wire
            logger.exception("explore stream could not finish")
            events.put({"type": "error", "message": UNFINISHED_MESSAGE})
        finally:
            events.put(_STREAM_END)

    async def lines() -> AsyncIterator[bytes]:
        thread = threading.Thread(target=work, name="explore-stream", daemon=True)
        thread.start()
        while True:
            item = await asyncio.to_thread(events.get)
            if item is _STREAM_END:
                break
            yield (json.dumps(item, ensure_ascii=False) + "\n").encode("utf-8")

    return StreamingResponse(
        lines(),
        media_type=STREAM_MEDIA_TYPE,
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


def _invalid(body: ExploreRequest) -> JSONResponse | None:
    question = " ".join(body.question.split())
    if not question:
        return JSONResponse(status_code=422, content={"detail": "ask a question"})
    if len(question) > MAX_QUESTION_CHARS:
        return JSONResponse(
            status_code=422,
            content={
                "detail": f"a question is at most {MAX_QUESTION_CHARS} characters"
            },
        )
    return None


def _understood(steps: list[Any], catalog: Any) -> str:
    """What the question was taken to ask, in plain words, from the plan."""
    return _describe(steps[0])


def _describe(first: Any) -> str:
    """One planned step in plain words ("Dropout rate by major")."""
    analysis = ANALYSES_BY_ID[first.analysis_id]
    if first.analysis_id == general.ANALYSIS_ID:
        measure = general.MEASURES[str(first.params.get("measure"))]
        text = measure.label[:1].upper() + measure.label[1:]
        groups = [
            general.GROUPINGS[str(g)].noun
            for g in (first.params.get("group_by"), first.params.get("then_by"))
            if isinstance(g, str)
        ]
        if groups:
            text += " by " + " and ".join(groups)
        return text
    text = analysis.title
    order = first.params.get("order")
    if order in ("lowest_first", "highest_first"):
        text += ", " + ("lowest first" if order == "lowest_first" else "highest first")
    return text


def _withheld_count(step: Any) -> int:
    """Groups withheld in a step, from its notes and its withheld cells."""
    count = 0
    for note in step.notes:
        match = re.match(
            r"(?:Rates are withheld for |D, F or withdrawal rates are "
            r"withheld for )?(\d+) ",
            note,
        )
        if match and "withheld" in note:
            count += int(match.group(1))
    if count == 0:
        count = sum(
            1 for row in step.rows if any(v == SUPPRESSED_DISPLAY for v in row.values())
        )
    if count == 0 and any(n.startswith("The figure is withheld") for n in step.notes):
        count = 1
    return count


def _explore(
    body: ExploreRequest, request: Request, emit: Emit = _no_emit
) -> JSONResponse:
    invalid = _invalid(body)
    if invalid is not None:
        return invalid
    question = " ".join(body.question.split())
    user = request.scope["cabinet_user"]
    role = str(user["role"])
    store = request.app.state.auth
    try:
        con = connect_readonly()
    except SchoolDataMissing:
        return _missing()
    try:
        catalog = catalog_for(con)
        audit = store.audit_for(int(user["institution_id"]))
        asked = audit.append(
            "question.asked",
            actor=str(user["email"]),
            payload={
                "question": redact_question(question),
                "route": "/explore",
                "role": role,
            },
        )
        task_id = f"explore-{asked['id']}"

        refusal = refusal_for(question, catalog.title_names)
        if refusal is not None:
            category, message = refusal
            audit.append(
                "data.refused",
                actor=EXPLORE_ACTOR,
                payload={
                    "task_id": task_id,
                    "question_event_id": asked["id"],
                    "category": category,
                    "reason": message,
                    "before": "planning and any model call",
                },
            )
            return JSONResponse(
                content={
                    "refused": True,
                    "message": message,
                    "answer": [],
                    "steps": [],
                    "source": None,
                }
            )

        provider = provider_from_env()
        emit(
            {
                "type": "planning",
                "text": "Matching the question to the approved analyses",
            }
        )
        outcome = plan_question(question, catalog, provider, role)
        if outcome.steps is None:
            audit.append(
                "explore.answered",
                actor=EXPLORE_ACTOR,
                payload={
                    "task_id": task_id,
                    "question_event_id": asked["id"],
                    "steps": [],
                    "row_counts": [],
                    "planner": outcome.planner,
                    "writer": None,
                    "answered": False,
                },
            )
            return JSONResponse(
                content={
                    "refused": False,
                    "message": UNANSWERABLE_MESSAGE,
                    "answer": [],
                    "steps": [],
                    "suggestions": nearest_examples(question),
                    "source": None,
                }
            )

        planned = outcome.steps
        emit({"type": "understood", "text": _understood(planned, catalog)})
        emit(
            {
                "type": "plan",
                "steps": [_describe(s) for s in planned],
                "planner": "model" if outcome.planner == "model" else "rules",
            }
        )
        emit({"type": "reading", "text": _reading_text(con)})

        def granted(
            index: int, analysis_id: str, fields: tuple[str, ...], withheld: bool
        ) -> None:
            emit(
                {
                    "type": "step",
                    "index": index,
                    "total": len(planned),
                    "title": _describe(planned[index]),
                }
            )
            audit.append(
                "data.granted",
                actor=EXPLORE_ACTOR,
                payload={
                    "task_id": task_id,
                    "step": index,
                    "analysis_id": analysis_id,
                    "fields_read": list(fields),
                    "aggregate_only": True,
                },
            )
            if withheld:
                audit.append(
                    "data.refused",
                    actor=EXPLORE_ACTOR,
                    payload={
                        "task_id": task_id,
                        "step": index,
                        "analysis_id": analysis_id,
                        "category": "instructor_level",
                        "role": role,
                        "reason": (
                            "Instructor-level rows are available to the "
                            "executive and admin roles only."
                        ),
                    },
                )

        try:
            steps = execute(outcome.steps, con, catalog, role, on_step=granted)
            withheld = sum(_withheld_count(s) for s in steps)
            if withheld:
                emit(
                    {
                        "type": "suppression",
                        "count": withheld,
                        "text": f"Withheld {withheld} small "
                        f"{'group' if withheld == 1 else 'groups'} "
                        f"({SUPPRESSED_DISPLAY} students) to protect privacy",
                    }
                )
            emit({"type": "writing", "text": "Writing the answer from the tables"})
            answer, source, writer_fallback = write_answer(
                steps, provider if uses_model(provider) else None
            )
            claims = sum(len(s.claims) for s in answer)
            emit(
                {
                    "type": "verifying",
                    "checked": claims,
                    "matched": claims,
                    "text": f"Checked {claims} "
                    f"{'number' if claims == 1 else 'numbers'} against the tables",
                }
            )
        except Exception:  # a defect: logged, recorded, never a 500 or a guess
            logger.exception("explore could not finish task %s", task_id)
            audit.append(
                "explore.answered",
                actor=EXPLORE_ACTOR,
                payload={
                    "task_id": task_id,
                    "question_event_id": asked["id"],
                    "steps": [s.analysis_id for s in outcome.steps],
                    "row_counts": [],
                    "planner": outcome.planner,
                    "writer": None,
                    "answered": False,
                },
            )
            return JSONResponse(
                content={
                    "refused": False,
                    "message": UNFINISHED_MESSAGE,
                    "answer": [],
                    "steps": [],
                    "source": None,
                }
            )
        audit.append(
            "explore.answered",
            actor=EXPLORE_ACTOR,
            payload={
                "task_id": task_id,
                "question_event_id": asked["id"],
                "steps": [s.analysis.id for s in steps],
                "row_counts": [len(s.rows) for s in steps],
                "planner": outcome.planner,
                "writer": "model" if source == SOURCE_MODEL else "template",
                "answered": True,
            },
        )
        return JSONResponse(
            content={
                "refused": False,
                "answer": [s.to_json() for s in answer],
                "steps": [_step_json(s) for s in steps],
                "source": source,
                "planner": outcome.planner,
                "notes": list(outcome.notes),
                "fallbacks": [
                    r for r in (outcome.fallback_reason, writer_fallback) if r
                ],
                "institution": catalog.vocab.institution,
                "fictional": True,
            }
        )
    finally:
        con.close()
