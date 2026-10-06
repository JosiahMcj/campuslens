"""System and user prompts for the two explore roles.

``analysts.build_prompt`` hands these roles here, so the chat provider (and
any provider behind the same interface) needs no change. The planner prompt
carries only the catalog spec and the question; the writer prompt carries
only the computed tables.
"""

from __future__ import annotations

import json
from typing import Any

from cabinet.explore.answer import MAX_SENTENCES, WRITER_ROLE
from cabinet.explore.planner import MAX_STEPS, PLANNER_ROLE

EXPLORE_ROLES = (PLANNER_ROLE, WRITER_ROLE)

PLANNER_SYSTEM = (
    "You plan answers to a university leader's question using only the approved "
    "analyses in the catalog you are given. You never compute numbers and you never "
    "see data. Answer with a JSON object only, of the form "
    '{"steps": [{"analysis_id": "...", "params": {"name": value}}]}, with 1 to '
    f"{MAX_STEPS} steps and no markdown, code fences, other keys, or other text. "
    "Use only analysis ids, parameter names, and allowed values from the catalog. "
    "A parameter with allowed_list takes a key of that value list. Leave out a "
    "parameter to use its default. A later step may take a parameter from an "
    "earlier step's top row. Write "
    '{"from_step": <earlier step index, from 0>, "column": "<column>"} '
    "with one of that analysis's chainable_columns whose kind matches the "
    "parameter. If no approved analysis answers the question, answer "
    '{"steps": []}.'
)

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
        user = (
            f"Question: {payload['question']}\n"
            "Catalog of approved analyses:\n"
            + json.dumps(payload["catalog"], ensure_ascii=False, separators=(",", ":"))
        )
        return PLANNER_SYSTEM, user
    if role == WRITER_ROLE:
        user = "Computed tables:\n" + json.dumps(
            payload["tables"], ensure_ascii=False, indent=1
        )
        return WRITER_SYSTEM, user
    raise ValueError(f"no explore prompt for role {role!r}")
