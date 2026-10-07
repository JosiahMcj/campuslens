"""System and user prompts for the two explore roles.

``analysts.build_prompt`` hands these roles here, so the chat provider (and
any provider behind the same interface) needs no change. The planner prompt
carries only the compact catalog (``cabinet.explore.compact``) and the
question; the writer prompt carries only the computed tables.

The planner's system message is the compact catalog, the same text for
every question, and the user message is the question alone, so a server
that keeps the processed start of the last prompt answers the next question
without reading the catalog again.
"""

from __future__ import annotations

import json
from typing import Any

from cabinet.explore.answer import MAX_SENTENCES, WRITER_ROLE
from cabinet.explore.planner import PLANNER_ROLE

EXPLORE_ROLES = (PLANNER_ROLE, WRITER_ROLE)

WRITER_SYSTEM = (
    "You write the answer to a university leader's question from computed tables. "
    f"Write 1 to {MAX_SENTENCES} plain sentences for a busy executive. Every number "
    "you write must appear in a table cell exactly as given. Never compute, round, "
    "combine, or estimate a number, and do not mention table numbers. Write numbers "
    "as digits, and write rates from columns marked (%) with a percent sign. "
    "Instructor names are fictional, so say so when you name one. Students are "
    "people, never risk scores. Answer with a JSON object only: "
    '{"sentences": ["...", "..."]}.'
)


def build_explore_prompt(payload: dict[str, Any], role: str) -> tuple[str, str]:
    if role == PLANNER_ROLE:
        user = f"Q: {payload['question']}"
        if payload.get("hint"):
            user += f"\nNote: {payload['hint']}"
        return str(payload["catalog"]), user
    if role == WRITER_ROLE:
        user = "Computed tables:\n" + json.dumps(
            payload["tables"], ensure_ascii=False, indent=1
        )
        return WRITER_SYSTEM, user
    raise ValueError(f"no explore prompt for role {role!r}")
