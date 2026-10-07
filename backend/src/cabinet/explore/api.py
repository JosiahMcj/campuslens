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

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel

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


class ExploreRequest(BaseModel):
    question: str


def _missing() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"available": False, "message": SchoolDataMissing().args[0]},
    )


def _step_json(step: StepResult) -> dict[str, Any]:
    out: dict[str, Any] = {
        "analysis_id": step.analysis.id,
        "title": step.analysis.title,
        # The parameters a reader cares about, in plain words; the exact
        # record (codes, row limits) stays in the step and the CLI.
        "params_plain": step.params_shown,
        "fields_read": list(step.fields_read),
        "aggregate_only": True,
        "table": step.table(),
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


def _explore(body: ExploreRequest, request: Request) -> JSONResponse:
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
        outcome = plan_question(question, catalog, provider)
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

        def granted(
            index: int, analysis_id: str, fields: tuple[str, ...], withheld: bool
        ) -> None:
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
            answer, source, writer_fallback = write_answer(
                steps, provider if uses_model(provider) else None
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
