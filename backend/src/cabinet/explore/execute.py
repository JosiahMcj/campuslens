"""Executor: runs a plan's steps in code and returns aggregate tables.

Chained parameters resolve from an earlier step's top row. The privacy rules
apply here: instructor-level analyses give their rows only to the executive
and admin roles (staff and reviewers get the course as a whole, with a
sentence saying so), and every result is scanned for student ids before it
leaves this module.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from cabinet.counseling import SUPPRESSED_DISPLAY
from cabinet.explore.catalog import (
    ANALYSIS_BY_ID,
    COURSE_SUMMARY_COLUMNS,
    INSTRUCTOR_ROLES,
    Analysis,
    AnalysisError,
    Catalog,
    Column,
    course_summary,
    may_run,
)
from cabinet.explore.planner import Ref, Step

INSTRUCTOR_WITHHELD_NOTE = (
    "Instructor-level results are available to the executive and admin roles only, so "
    "this table shows the course as a whole."
)
INSTRUCTOR_HISTORY_WITHHELD_NOTE = (
    "Instructor-level results are available to the executive and admin roles only."
)
COURSE_SUMMARY_FIELDS = (
    "sections.course_id",
    "sections.term_code",
    "section_instructors.instructor_id (counted)",
    "final_grades.grade",
)

# Any student id in a result is a defect, never an answer.
STUDENT_ID_RE = re.compile(r"\bS-\d+\b")


class StudentIdInResult(RuntimeError):
    """A result carried a student id. It is never returned."""


@dataclass
class StepResult:
    index: int
    analysis: Analysis
    params: dict[str, Any]
    params_plain: list[str]
    fields_read: tuple[str, ...]
    columns: tuple[Column, ...]
    rows: list[dict[str, Any]]
    notes: list[str] = field(default_factory=list)
    instructor_rows_withheld: bool = False
    error: str | None = None
    # The parameters as a reader sees them ("How this was answered"); the
    # exact record (codes included) stays in ``params_plain``.
    params_shown: list[str] = field(default_factory=list)
    # Withheld cells (row, column) the analysis marked as themselves under
    # 10; any other withheld cell protects a neighbour and may be large.
    small_cells: frozenset[tuple[int, str]] = frozenset()

    def table(self) -> dict[str, Any]:
        """``{columns: [{key, label}], rows: [[cell, ...]]}`` in column order."""
        return {
            "columns": [{"key": c.key, "label": c.label} for c in self.columns],
            "rows": [[row.get(c.key) for c in self.columns] for row in self.rows],
        }

    def cell(self, row: int, key: str) -> Any:
        return self.rows[row].get(key)


# Called once per step before it reads anything: (index, analysis id, fields
# read, instructor rows withheld). The API writes data.granted from it.
StepHook = Callable[[int, str, tuple[str, ...], bool], None]


INSTRUCTOR_PARAM_WITHHELD = "an instructor (names are shown to the executive and admin)"


def _resolve(
    step: Step,
    analysis: Analysis,
    done: list[StepResult],
    catalog: Catalog,
    role: str = "executive",
) -> tuple[dict[str, Any], list[str], list[str]]:
    params: dict[str, Any] = {}
    plain: list[str] = []
    shown: list[str] = []
    for param in analysis.params:
        value = step.params.get(param.name, param.default)
        source: str | None = None
        if isinstance(value, Ref):
            earlier = done[value.from_step]
            if earlier.error or not earlier.rows:
                raise AnalysisError(
                    f"step {value.from_step + 1} had no result to carry forward"
                )
            value = earlier.rows[0].get(value.column)
            if value is None or value == SUPPRESSED_DISPLAY:
                raise AnalysisError(
                    f"step {earlier.index + 1}'s top row has no "
                    f"{param.label.lower()} to carry forward"
                )
            source = f" (from step {earlier.index + 1})"
        if value is None:
            if param.required:
                raise AnalysisError(f"{analysis.title} needs a {param.label.lower()}")
            continue
        if not catalog.is_allowed(param, value):
            raise AnalysisError(f"{value!r} is not an allowed {param.label.lower()}")
        value = catalog.normalize(param, value)
        params[param.name] = value
        if param.kind == "instructor" and role not in INSTRUCTOR_ROLES:
            # Neither the id nor the (fictional) name reaches another role.
            plain.append(f"{param.label}: {INSTRUCTOR_PARAM_WITHHELD}")
            shown.append(f"{param.label}: {INSTRUCTOR_PARAM_WITHHELD}")
            continue
        plain.append(f"{param.label}: {catalog.plain(param, value)}{source or ''}")
        reader = catalog.shown(param, value)
        if reader is not None:
            shown.append(reader + (source or ""))
    return params, plain, shown


def _check_no_student_ids(result: StepResult) -> None:
    blob = json.dumps(
        {
            "rows": result.rows,
            "notes": result.notes,
            "params": result.params_plain,
            "shown": result.params_shown,
        },
        default=str,
    )
    if STUDENT_ID_RE.search(blob):
        raise StudentIdInResult(
            f"{result.analysis.id} returned a student id and was refused"
        )


def execute(
    steps: list[Step],
    con: sqlite3.Connection,
    catalog: Catalog,
    role: str,
    on_step: StepHook | None = None,
) -> list[StepResult]:
    """Run every step in order. A step whose parameters cannot be resolved
    (an earlier step came back empty) is returned with ``error`` set and no
    rows; later steps that depend on it fail the same way."""
    done: list[StepResult] = []
    for index, step in enumerate(steps):
        analysis = ANALYSIS_BY_ID[step.analysis_id]
        if not may_run(analysis, role):
            # The API refuses such a plan before it runs; this is the backstop.
            done.append(
                StepResult(
                    index,
                    analysis,
                    {},
                    [],
                    (),
                    analysis.columns,
                    [],
                    error="this analysis is not available to your role",
                )
            )
            continue
        withheld = analysis.instructor_level and role not in INSTRUCTOR_ROLES
        try:
            params, plain, shown = _resolve(step, analysis, done, catalog, role)
        except AnalysisError as exc:
            done.append(
                StepResult(
                    index,
                    analysis,
                    {},
                    [],
                    analysis.fields_read,
                    analysis.columns,
                    [],
                    error=str(exc),
                )
            )
            continue
        fields: tuple[str, ...]
        columns: tuple[Column, ...]
        if withheld and analysis.id == "course_instructors":
            fields, columns = COURSE_SUMMARY_FIELDS, COURSE_SUMMARY_COLUMNS
        elif withheld:
            fields, columns = (), analysis.columns
        else:
            fields, columns = analysis.fields_read, analysis.columns
        if on_step is not None:
            on_step(index, analysis.id, fields, withheld)
        try:
            if withheld and analysis.id == "course_instructors":
                outcome = course_summary(con, params["course"], catalog.vocab)
                outcome.notes.insert(0, INSTRUCTOR_WITHHELD_NOTE)
            elif withheld:
                outcome = None
            else:
                outcome = analysis.run(con, params, catalog.vocab)
        except AnalysisError as exc:
            done.append(
                StepResult(
                    index,
                    analysis,
                    params,
                    plain,
                    fields,
                    columns,
                    [],
                    error=str(exc),
                    params_shown=shown,
                )
            )
            continue
        rows = [
            {k: val for k, val in row.items() if not k.startswith("_")}
            for row in (outcome.rows if outcome else [])
        ]
        if outcome is not None and outcome.columns is not None:
            columns = outcome.columns
        notes = outcome.notes if outcome else [INSTRUCTOR_HISTORY_WITHHELD_NOTE]
        result = StepResult(
            index,
            analysis,
            params,
            plain,
            fields,
            columns,
            rows,
            notes,
            instructor_rows_withheld=withheld,
            params_shown=shown,
            small_cells=frozenset(outcome.small) if outcome else frozenset(),
        )
        _check_no_student_ids(result)
        done.append(result)
    return done
