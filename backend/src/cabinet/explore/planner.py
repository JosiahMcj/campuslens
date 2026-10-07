"""Planner: a question becomes a plan, a list of steps ``{analysis_id, params}``.

A parameter may be a value from the catalog or a reference to an earlier
step's top row, ``{"from_step": 0, "column": "major"}``, so "Which major has
the lowest GPA? In that major, what is historically the hardest class?" runs
the second analysis on whatever major the first one ranked lowest.

Two planners sit behind ``plan_question``:

- the model planner (through the provider interface, role
  ``explore_planner``), which receives ONLY the compact catalog
  (``cabinet.explore.compact``: analysis ids with a one-line purpose, their
  parameters and value types, the measures and groupings, the majors and
  colleges, the term range) and the question with any typed id or long
  number replaced. It writes a short reasoning sentence (discarded) and a
  plan; courses, subjects, terms and instructors are free text that code
  resolves to catalog values before ``validate_plan`` checks the plan. With
  a live provider it plans first (``CABINET_EXPLORE_PLANNER``, default
  ``model-first``), within its own time budget
  (``CABINET_EXPLORE_PLANNER_TIMEOUT``, default 20 s);
- the rule planner (keywords, synonyms, and patterns over the catalog's
  allowed values), used in replay and fake modes, in tests, and whenever the
  model's plan cannot be used: a timeout, an unavailable provider, invalid
  JSON, an unknown id, a value that resolves to nothing in the catalog, or a
  bad reference. ``rules-first`` asks the model only for questions the rules
  cannot map, and ``rules-only`` never asks it.

Validated model plans are recorded like golden runs, keyed by the question
and the catalog hash, so replay serves them offline: written to
``var/replay/explore/`` (or ``CABINET_REPLAY_DIR``) when ``CABINET_RECORD`` is
set, read from each replay directory's ``explore/`` folder (the committed
golden run lives in ``data/golden/explore/``). An existing recording is
never overwritten unless ``CABINET_RECORD=overwrite``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cabinet.explore import general
from cabinet.explore.catalog import (
    ANALYSIS_BY_ID,
    INSTRUCTOR_ROLES,
    Analysis,
    AnalysisError,
    Catalog,
    Param,
    Vocab,
)
from cabinet.explore.compact import (
    Unresolved,
    compact_catalog,
    fiscal_year,
    resolve_plan,
)
from cabinet.explore.privacy import (
    count_form,
    historical_form,
    redact_question,
    safe_text,
)
from cabinet.provider import (
    ENV_RECORD,
    Provider,
    ProviderUnavailable,
    RecordingProvider,
    replay_dir_from_env,
    replay_dirs_from_env,
)

logger = logging.getLogger(__name__)

PLANNER_ROLE = "explore_planner"
MAX_STEPS = 4
RECORDING_SUBDIR = "explore"

UNANSWERABLE_MESSAGE = "CampusLens can't answer that from the approved analyses yet."
GREETING_MESSAGE = (
    "Hi, I'm CampusLens. Ask me about your students, courses and majors, "
    "and I'll answer from the records."
)

_SMALL_TALK_RE = re.compile(
    r"^\s*(?:(?:hi|hello|hey|hiya|howdy|yo|greetings|good\s+(?:morning|afternoon|evening))"
    r"(?:\s+(?:there|campus\s*lens|everyone))?"
    r"|thanks?(?:\s+you)?|thank\s+you(?:\s+so\s+much)?|help|what\s+can\s+you\s+do"
    r"|who\s+are\s+you|what\s+(?:is|are)\s+(?:this|you|campus\s*lens)|how\s+does\s+this\s+work)"
    r"\s*[!.?]*\s*$",
    re.IGNORECASE,
)


def is_small_talk(question: str) -> bool:
    """A greeting, a thank-you, or "what can you do": answered at once with
    example questions, never planned and never sent to a model."""
    return _SMALL_TALK_RE.match(question) is not None

# The owner's example, answered in one plan of three chained steps.
OWNER_EXAMPLE = (
    "Which major has the lowest GPA? In that major, what is historically the hardest "
    "class, and which instructor has historically taught it?"
)

# The owner's short form of the same question, one sentence with commas.
OWNER_SHORT = (
    "Which major has the lowest GPA, what is its hardest class, and who has taught it?"
)

# GET /explore/catalog lists these; the unanswerable message suggests the
# three nearest.
EXAMPLE_QUESTIONS: tuple[str, ...] = (
    OWNER_EXAMPLE,
    "Which majors have the highest average GPA?",
    "What is the average GPA by college?",
    "Which required courses in Nursing have the highest DFW rate?",
    "How has the DFW rate in Organic Chemistry I changed by term?",
    "Who has taught Organic Chemistry I?",
    "What is the first-generation equity gap in College Algebra?",
    "Which major grew fastest from Fall 2020 to Fall 2025?",
    "How much did continuing spring registration change in Spring 2026?",
    "Which term had the largest gap between online and in-person withdrawal rates?",
    "Which majors have the highest probation rates?",
    "Which offices hold the most active holds?",
    "What majors have the highest dropout rate?",
    "What is first-year retention by first-generation status?",
    "What is the 6-year graduation rate for Pell students by college?",
    "How many international students are in Nursing?",
    "What is the average GPA of athletes vs non-athletes?",
)

# Phrasings the rule planner maps, with the analyses each plan must run (in
# order). Tests assert every one; docs/EXPLORE.md lists them as examples.
RULE_PHRASINGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (OWNER_EXAMPLE, ("gpa_by_major", "dfw_by_course", "course_instructors")),
    (OWNER_SHORT, ("gpa_by_major", "dfw_by_course", "course_instructors")),
    ("Which major has the lowest GPA?", ("gpa_by_major",)),
    ("Which majors have the highest average GPA?", ("gpa_by_major",)),
    ("What is the average GPA in Nursing?", ("gpa_by_major",)),
    ("What is the average GPA by college?", ("gpa_by_college",)),
    ("Which college has the lowest grade point average?", ("gpa_by_college",)),
    (
        "What are the hardest classes for Mechanical Engineering majors?",
        ("dfw_by_course",),
    ),
    (
        "Which required courses in Nursing have the highest DFW rate?",
        ("dfw_by_course",),
    ),
    ("Which 1000-level courses have the highest DFW rates?", ("dfw_by_course",)),
    ("What are the easiest MATH courses?", ("dfw_by_course",)),
    ("What is the DFW rate in MEEN 3310?", ("course_dfw_trend",)),
    (
        "How has the DFW rate in Organic Chemistry I changed by term?",
        ("course_dfw_trend",),
    ),
    ("Who has taught Organic Chemistry I?", ("course_instructors",)),
    (
        "Which instructors taught MEEN 3310, and what were their DFW rates?",
        ("course_instructors",),
    ),
    ("What has Alicia Shelby taught?", ("instructor_history",)),
    ("Show the teaching history of I-0003.", ("instructor_history",)),
    ("What is the first-generation equity gap in College Algebra?", ("equity_gap",)),
    ("Is there a Pell gap in Nursing?", ("equity_gap",)),
    ("How do DFW rates in MATH 1314 differ by residency?", ("equity_gap",)),
    ("Which major grew fastest from Fall 2020 to Fall 2025?", ("headcount_growth",)),
    ("Which majors shrank the most since 2020?", ("headcount_growth",)),
    ("How has Computer Science headcount grown?", ("headcount_growth",)),
    ("How many CS students are enrolled?", ("measure_by_group",)),
    ("How many students are enrolled this semester?", ("measure_by_group",)),
    ("What's our biggest major?", ("measure_by_group",)),
    ("What was enrollment by term?", ("enrollment_by_term",)),
    ("How many students were enrolled in Nursing each fall?", ("enrollment_by_term",)),
    (
        "How much did continuing spring registration change in Spring 2026?",
        ("continuing_registration_change",),
    ),
    (
        "Is spring-to-spring continuing registration down?",
        ("continuing_registration_change",),
    ),
    (
        "Which term had the largest gap between online and in-person withdrawal rates?",
        ("withdrawal_by_modality",),
    ),
    ("Do online courses have higher withdrawal rates?", ("withdrawal_by_modality",)),
    (
        "Which course has the highest withdrawal rate online?",
        ("withdrawal_by_course_modality",),
    ),
    (
        "What online classes do students withdraw from most?",
        ("withdrawal_by_course_modality",),
    ),
    (
        "Show the online withdrawal rate by course.",
        ("withdrawal_by_course_modality",),
    ),
    ("Which majors have the highest probation rates?", ("standing_by_major",)),
    (
        "What share of Mechanical Engineering students were suspended?",
        ("standing_by_major",),
    ),
    ("How many students graduated in each major in 2024-2025?", ("graduations",)),
    ("How many Nursing graduates were there each year?", ("graduations",)),
    ("Which offices hold the most active holds?", ("holds_by_office",)),
    ("How much do students owe on financial holds?", ("holds_by_office",)),
    ("Which majors have the lowest advising coverage?", ("advising_coverage",)),
    (
        "What share of Biology students saw an advisor in Fall 2025?",
        ("advising_coverage",),
    ),
    ("How many credit hours were attempted each term?", ("credit_hours_by_term",)),
    (
        "What is the hardest class in Chemistry, and who taught it?",
        ("dfw_by_course", "course_instructors"),
    ),
    ("what majors have teh highest drop out rate", ("measure_by_group",)),
    ("Retention by first-gen status", ("measure_by_group",)),
    ("Graduation rate for Pell students by college", ("measure_by_group",)),
    ("How many international students are in Nursing?", ("measure_by_group",)),
    ("Average GPA of athletes vs non-athletes", ("measure_by_group",)),
    (
        "Which major has the highest dropout rate, and what is its hardest class?",
        ("measure_by_group", "dfw_by_course"),
    ),
    (
        "Which course has the highest DFW rate in Computer Science, and how has it "
        "changed by "
        "term?",
        ("dfw_by_course", "course_dfw_trend"),
    ),
)


class PlanInvalid(ValueError):
    """A plan that does not validate against the catalog."""


class PlanDeclined(Exception):
    """The model answered, validly, that no approved analysis answers the
    question (``{"steps": []}``)."""


@dataclass(frozen=True)
class Ref:
    """A parameter taken from an earlier step's top row."""

    from_step: int
    column: str


@dataclass(frozen=True)
class Step:
    analysis_id: str
    params: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        return {
            "analysis_id": self.analysis_id,
            "params": {
                k: (
                    {"from_step": v.from_step, "column": v.column}
                    if isinstance(v, Ref)
                    else v
                )
                for k, v in self.params.items()
            },
        }


@dataclass(frozen=True)
class PlanOutcome:
    """``steps`` is None when the question cannot be mapped. ``planner`` is
    ``rule``, ``model``, or ``recorded``; ``fallback_reason`` says why the
    model planner was not used when one was tried."""

    steps: list[Step] | None
    planner: str
    fallback_reason: str | None = None
    # Plain sentences about what the rule planner could not apply: a named
    # term or college the chosen analysis does not filter by, or a part of
    # the question no analysis answered.
    notes: tuple[str, ...] = ()
    # The model read the catalog and planned nothing (``{"steps": []}``).
    declined: bool = False


# The instruction the model planner gets with a forward-looking question
# (in the user message, after the question; the catalog stays the same).
FORWARD_HINT = (
    "CampusLens does not forecast. Plan the closest measures from the historical "
    "records for the group the question names: for example a past dropout, "
    "stop-out, retention or graduation rate, a headcount now, or enrollment by "
    "term. Never plan anything per student."
)


# --- validation --------------------------------------------------------------

_TERM_KINDS = ("term", "advising_term")


def _entity_matches(param: Param, entity: str | None) -> bool:
    if entity is None:
        return False
    if param.kind in _TERM_KINDS:
        return entity == "term"
    return param.kind == entity


def validate_plan(raw: Any, catalog: Catalog) -> list[Step]:
    """A plan object -> steps, or PlanInvalid. Checks every analysis id,
    parameter name, and value against the catalog, every reference against
    an earlier step's chainable columns, and the required parameters."""
    if not isinstance(raw, dict) or set(raw) != {"steps"}:
        raise PlanInvalid('the plan must be an object with exactly one key, "steps"')
    items = raw["steps"]
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_STEPS:
        raise PlanInvalid(f"the plan needs 1 to {MAX_STEPS} steps")
    steps: list[Step] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict) or set(item) - {"analysis_id", "params"}:
            raise PlanInvalid(f"step {index} must have only analysis_id and params")
        analysis_id = item.get("analysis_id")
        analysis = (
            ANALYSIS_BY_ID.get(analysis_id) if isinstance(analysis_id, str) else None
        )
        if analysis is None:
            raise PlanInvalid(f"step {index} names an unknown analysis")
        params_raw = item.get("params", {})
        if not isinstance(params_raw, dict):
            raise PlanInvalid(f"step {index} params must be an object")
        params: dict[str, Any] = {}
        for name, value in params_raw.items():
            param = analysis.param(name)
            if param is None:
                raise PlanInvalid(
                    f"step {index}: {analysis.id} has no parameter {name!r}"
                )
            if value is None:
                continue
            if isinstance(value, dict):
                if set(value) != {"from_step", "column"}:
                    raise PlanInvalid(
                        f"step {index}: a reference needs from_step and column"
                    )
                source = value["from_step"]
                if (
                    not isinstance(source, int)
                    or isinstance(source, bool)
                    or not 0 <= source < index
                ):
                    raise PlanInvalid(
                        f"step {index}: a reference must point to an earlier step"
                    )
                earlier = ANALYSIS_BY_ID[steps[source].analysis_id]
                column = earlier.column(str(value["column"]))
                if column is None or not _entity_matches(param, column.entity):
                    raise PlanInvalid(
                        f"step {index}: {earlier.id} has no {param.kind} column "
                        f"{value['column']!r}"
                    )
                params[name] = Ref(source, column.key)
                continue
            if not catalog.is_allowed(param, value):
                raise PlanInvalid(
                    f"step {index}: {value!r} is not an allowed {param.name}"
                )
            try:
                params[name] = catalog.normalize(param, value)
            except AnalysisError as exc:
                raise PlanInvalid(f"step {index}: {exc}") from None
        for param in analysis.params:
            if param.required and param.name not in params:
                raise PlanInvalid(f"step {index}: {analysis.id} needs {param.name}")
        if analysis.id == "equity_gap" and not ({"course", "major"} & set(params)):
            raise PlanInvalid(f"step {index}: equity_gap needs a course or a major")
        if analysis.id == general.ANALYSIS_ID:
            if "then_by" in params and "group_by" not in params:
                params["group_by"] = params.pop("then_by")
            groups = [g for g in (params.get("group_by"), params.get("then_by")) if g]
            if any(isinstance(g, Ref) for g in groups):
                raise PlanInvalid(f"step {index}: a grouping cannot be a reference")
            filters = {k: params[k] for k in general.GROUPING_KEYS if k in params}
            try:
                general.check_request(str(params.get("measure")), groups, filters)
            except general.GeneralError as exc:
                raise PlanInvalid(f"step {index}: {exc}") from None
        steps.append(Step(analysis.id, params))
    return steps


# --- recordings --------------------------------------------------------------


def normalize_question(question: str) -> str:
    """The question as a recording key and as written to disk: any id or
    long number a person typed is replaced first (``redact_question``)."""
    return " ".join(redact_question(question).lower().split()).rstrip(" ?.!")


def recording_key(question: str, catalog: Catalog) -> str:
    material = json.dumps(
        {"question": normalize_question(question), "catalog_sha256": catalog.hash},
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def recording_filename(question: str, catalog: Catalog) -> str:
    return f"{PLANNER_ROLE}-{recording_key(question, catalog)}.json"


def load_recorded_plan(question: str, catalog: Catalog) -> list[Step] | None:
    """A recorded plan from the replay search path, validated again."""
    name = recording_filename(question, catalog)
    for directory in replay_dirs_from_env():
        path = directory / RECORDING_SUBDIR / name
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return validate_plan(data["plan"], catalog)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning("ignoring recorded plan %s: %s", path, exc)
    return None


def save_recorded_plan(
    question: str, catalog: Catalog, steps: list[Step], provider: Provider
) -> Path | None:
    """Write a validated model plan; keeps an existing one unless
    CABINET_RECORD=overwrite. Returns the path written, or None."""
    directory = replay_dir_from_env() / RECORDING_SUBDIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / recording_filename(question, catalog)
    if path.exists() and os.environ.get(ENV_RECORD) != "overwrite":
        logger.info("keeping existing plan recording %s", path)
        return None
    payload = {
        "role": PLANNER_ROLE,
        "question": normalize_question(question),
        "catalog_sha256": catalog.hash,
        "provider": provider.name,
        "model_label": provider.model_label,
        "recorded_at": datetime.now(UTC).isoformat(),
        "plan": {"steps": [s.to_json() for s in steps]},
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path


# --- the model planner -------------------------------------------------------


def uses_model(provider: Provider) -> bool:
    """The model plans and rewrites only with a live provider; replay and
    fake modes use the rule planner and the template answer."""
    return provider.name not in ("fake", "replay")


def parse_model_plan(text: str, catalog: Catalog) -> list[Step]:
    """The model's answer -> validated steps, or PlanInvalid. Takes the one
    JSON object in the text (a code fence or a lead-in line around it is
    ignored), drops the ``reasoning`` sentence, resolves free-text values to
    catalog values, and validates the plan."""
    stripped = text.strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, re.DOTALL)
    if fence:
        stripped = fence.group(1)
    start, end = stripped.find("{"), stripped.rfind("}")
    if start >= 0 and end > start:
        stripped = stripped[start : end + 1]
    try:
        raw = json.loads(stripped)
    except ValueError as exc:
        raise PlanInvalid(f"the model's plan is not JSON: {exc}") from None
    if isinstance(raw, dict) and raw.get("steps") == [] and set(raw) <= {
        "steps",
        "reasoning",
    }:
        raise PlanDeclined()
    try:
        raw = resolve_plan(raw, catalog)
    except (TypeError, AttributeError, KeyError) as exc:  # an odd shape
        raise PlanInvalid(f"the model's plan has an unexpected shape: {exc}") from None
    except Unresolved as exc:
        raise PlanInvalid(
            f"the model's plan names something we cannot find: {exc}"
        ) from None
    return validate_plan(raw, catalog)


_VOCAB_CACHE: dict[str, frozenset[str]] = {}


def planner_vocabulary(catalog: Catalog) -> frozenset[str]:
    """Words the model planner already reads in the compact catalog and the
    rule planner's vocabulary (measure, grouping and typo words): allowed in
    the question the model receives."""
    cached = _VOCAB_CACHE.get(catalog.hash)
    if cached is None:
        text = compact_catalog(catalog) + " " + " ".join(_TYPOS)
        cached = frozenset(w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'-]*", text))
        _VOCAB_CACHE.clear()
        _VOCAB_CACHE[catalog.hash] = cached
    return cached


def model_plan(
    question: str,
    catalog: Catalog,
    provider: Provider,
    role: str = "staff",
    hint: str | None = None,
) -> list[Step]:
    """The model planner. Raises PlanInvalid or ProviderUnavailable. The model
    receives the question with any typed id or long number replaced, and the
    compact catalog, the same for every role: it lists no instructor, course,
    or student. ``role`` is kept for callers; it changes nothing the model
    sees."""
    del role
    payload = {
        # Only allow-listed words reach the model: any other word (a possible
        # name) is "[name]", an email or handle "[id]" (privacy.safe_text).
        "question": safe_text(
            question, catalog.known_names, planner_vocabulary(catalog)
        ),
        "catalog": compact_catalog(catalog),
    }
    if hint:
        payload["hint"] = hint
    explanation = provider.explain(payload, PLANNER_ROLE)
    steps = parse_model_plan(explanation.text, catalog)
    if isinstance(provider, RecordingProvider):
        save_recorded_plan(question, catalog, steps, provider)
    return steps


ENV_PLANNER_ORDER = "CABINET_EXPLORE_PLANNER"
RULES_FIRST = "rules-first"
MODEL_FIRST = "model-first"
RULES_ONLY = "rules-only"


def planner_order_from_env() -> str:
    """``CABINET_EXPLORE_PLANNER``: ``model-first`` (the default with a live
    model) asks the model every time and falls back to the rules;
    ``rules-first`` asks the model only for a question the rule planner
    cannot map; ``rules-only`` never asks the model to plan. Anything else is
    logged and read as the default."""
    raw = os.environ.get(ENV_PLANNER_ORDER, "").strip().lower()
    if raw in ("", MODEL_FIRST):
        return MODEL_FIRST
    if raw == RULES_FIRST:
        return RULES_FIRST
    if raw == RULES_ONLY:
        return RULES_ONLY
    logger.warning(
        "%s=%r is not %s, %s or %s; using %s",
        ENV_PLANNER_ORDER,
        raw,
        MODEL_FIRST,
        RULES_FIRST,
        RULES_ONLY,
        MODEL_FIRST,
    )
    return MODEL_FIRST


def _try_model(
    question: str,
    catalog: Catalog,
    provider: Provider,
    role: str = "staff",
    hint: str | None = None,
) -> tuple[list[Step] | None, str | None]:
    """(model steps, None), or (None, the plain reason the model was not used)."""
    try:
        return model_plan(question, catalog, provider, role, hint), None
    except ProviderUnavailable as exc:
        reason = f"the model planner was unavailable: {exc.reason}"
    except PlanInvalid as exc:
        reason = f"the model's plan was rejected: {exc}"
    logger.warning("explore model planner not used: %s", reason)
    return None, reason


def plan_question(
    question: str,
    catalog: Catalog,
    provider: Provider,
    role: str = "staff",
    *,
    forward: bool = False,
) -> PlanOutcome:
    """The plan for one question (call only after the privacy check).

    Replay serves a recorded plan first. With a live provider the default
    order is model first: the model reads the compact catalog and plans,
    and the rule planner answers whenever the model's plan cannot be used
    (docs/EXPLORE-EVAL.md has the measurements behind this order).
    ``rules-first`` and ``rules-only`` keep the earlier orders. Fake and
    replay modes never call a model.

    ``forward``: the question asks what will happen. The rules read
    ``historical_form`` of it (``forward_rule_plan_detail``) and go first
    even in the model-first order; the model, when asked, gets the question
    with ``FORWARD_HINT``."""
    rules = forward_rule_plan_detail if forward else rule_plan_detail
    hint = FORWARD_HINT if forward else None
    if provider.name == "replay":
        recorded = load_recorded_plan(question, catalog)
        if recorded is not None:
            return PlanOutcome(recorded, "recorded")
    if not uses_model(provider):
        steps, notes = rules(question, catalog)
        return PlanOutcome(steps, "rule", None, notes)
    order = planner_order_from_env()
    if forward and order == MODEL_FIRST:
        # The rules read a forward question as history by a fixed table
        # (12 of 12 on the forward evaluation set, the model 8 of 12: it
        # planned the hold rate for "have holds and will drop"), so they go
        # first and the model plans only what they cannot map.
        order = RULES_FIRST
    if order == RULES_ONLY:
        # Never ask a model to plan: a question the rules cannot map gets
        # the example questions at once (a live demo on a slow model).
        steps, notes = rules(question, catalog)
        return PlanOutcome(steps, "rule", None, notes)
    if order == RULES_FIRST:
        steps, notes = rules(question, catalog)
        if steps is not None:
            return PlanOutcome(steps, "rule", None, notes)
        try:
            model_steps, reason = _try_model(question, catalog, provider, role, hint)
        except PlanDeclined:
            return PlanOutcome(None, "model", declined=True)
        if model_steps is not None:
            return PlanOutcome(model_steps, "model")
        return PlanOutcome(None, "rule", reason, ())
    try:
        model_steps, reason = _try_model(question, catalog, provider, role, hint)
    except PlanDeclined:
        # The model read the catalog and found no analysis for the question;
        # the rules are not asked to stretch one onto it.
        return PlanOutcome(None, "model", declined=True)
    if model_steps is not None:
        return PlanOutcome(model_steps, "model")
    steps, notes = rules(question, catalog)
    return PlanOutcome(steps, "rule", reason, notes)


_SCOPE_PARAMS = frozenset(
    {"major", "college", "course", "subject", "major_required", "level"}
    | {k for k in general.GROUPING_KEYS if k != "term"}
)


def scoped_to_a_group(steps: list[Step]) -> bool:
    """Every step names a group of students (a major, a course, first-gen
    students, ...), not the whole school: the totals for "students like" one
    student the question named."""
    return all(
        any(key in _SCOPE_PARAMS for key in step.params if key not in ("group_by",))
        for step in steps
    )


def forward_rule_plan_detail(
    question: str, catalog: Catalog, *, count_now: bool = True
) -> tuple[list[Step] | None, tuple[str, ...]]:
    """The rule plan for a forward-looking question, read as history: the
    historical measure ("will drop" is the dropout rate, with the stop-out
    rate beside it), after a count now when the question asks how many
    ("how many students have holds and will drop" also counts the students
    with a hold this term, unless ``count_now`` is False)."""
    steps, notes = rule_plan_detail(historical_form(question), catalog)
    if steps is None:
        return None, notes
    chained = any(isinstance(v, Ref) for s in steps for v in s.params.values())
    count = count_form(question) if count_now else None
    if count and not chained:
        counted, _ = rule_plan_detail(count, catalog)
        if (
            counted is not None
            and len(counted) == 1
            and counted[0].analysis_id == general.ANALYSIS_ID
            and counted[0].params.get("measure") == "headcount"
            and counted[0] not in steps
        ):
            steps = counted + steps
    extra: list[Step] = []
    for step in steps:
        if (
            step.analysis_id != general.ANALYSIS_ID
            or step.params.get("measure") != "dropout_rate"
        ):
            continue
        params = {**step.params, "measure": "stop_out_rate"}
        try:
            stop_out = validate_plan(
                {"steps": [Step(general.ANALYSIS_ID, params).to_json()]}, catalog
            )
        except PlanInvalid:
            continue
        if stop_out[0] not in steps:
            extra.extend(stop_out)
    return (steps + extra)[:MAX_STEPS], notes


# --- the rule planner --------------------------------------------------------

_MAJOR_SYNONYMS = {
    "comp sci": "CSCI",
    "cs": "CSCI",
    "mechanical": "MEEN",
    "mech e": "MEEN",
    "mechanical engineers": "MEEN",
    "civil": "CVEN",
    "electrical": "ELEN",
    "math": "MATH",
    "maths": "MATH",
    "business": "BUAD",
    "bio": "BIOL",
    "chem": "CHEM",
    "psych": "PSYC",
    "nurses": "NURS",
    "education": "EDEL",
    "art": "ARTS",
    "it": "",
}
_SUBJECT_SYNONYMS = {"math": "MATH", "maths": "MATH", "chem": "CHEM", "bio": "BIOL"}
_COLLEGE_SYNONYMS = {
    "engineering college": "CEC",
    "college of engineering": "CEC",
    "business school": "COB",
    "business college": "COB",
    "nursing college": "CNH",
    "education college": "COE",
    "arts and sciences": "CAS",
    "social sciences": "CSB",
    "behavioral sciences": "CSB",
    "theology college": "CTA",
}

_REFERENCE_RE = re.compile(
    r"\b(?:that|this|the same|it|its|those|these|them|there|"
    r"their|they)\b",
    re.I,
)
_LOW_RE = re.compile(
    r"\b(?:lowest|least|worst|smallest|fewest|bottom|easiest|slowest|"
    r"weakest|poorest|lower)\b",
    re.I,
)
_HIGH_RE = re.compile(
    r"\b(?:highest|most|best|top|largest|biggest|hardest|toughest|"
    r"fastest|greatest|strongest|higher)\b",
    re.I,
)


@dataclass
class _Entities:
    majors: list[str]
    colleges: list[str]
    subjects: list[str]
    courses: list[str]
    instructors: list[str]
    terms: list[str]
    academic_years: list[str]
    years: list[int]
    groups: list[str]
    levels: list[str]
    seasons: list[str]


def _phrase_re(phrase: str) -> re.Pattern[str]:
    return re.compile(
        r"(?<![\w-])" + re.escape(phrase).replace(r"\ ", r"\s+") + r"(?![\w-])", re.I
    )


class _Matcher:
    """Precompiled vocabulary for one catalog."""

    def __init__(self, catalog: Catalog) -> None:
        v = catalog.vocab
        self.vocab = v
        majors = {name.lower(): code for code, name in v.majors.items()}
        majors.update({k: c for k, c in _MAJOR_SYNONYMS.items() if c})
        self.majors = sorted(
            ((_phrase_re(n), c, len(n)) for n, c in majors.items()), key=lambda t: -t[2]
        )
        colleges = {name.lower(): code for code, name in v.colleges.items()}
        colleges.update(_COLLEGE_SYNONYMS)
        self.colleges = sorted(
            ((_phrase_re(n), c, len(n)) for n, c in colleges.items()),
            key=lambda t: -t[2],
        )
        subjects = {name.lower(): code for code, name in v.subjects.items()}
        subjects.update(_SUBJECT_SYNONYMS)
        self.subjects = sorted(
            ((_phrase_re(n), c, len(n)) for n, c in subjects.items()),
            key=lambda t: -t[2],
        )
        titles: dict[str, list[str]] = {}
        for course, title in v.courses.items():
            titles.setdefault(title, []).append(course)
        self.titles: list[tuple[re.Pattern[str], list[str], int]] = []
        for title, courses in titles.items():
            if " " in title:
                pattern = _phrase_re(title.lower())
            else:  # a one-word title matches only as typed, capitalized
                pattern = re.compile(r"(?<![\w-])" + re.escape(title) + r"(?![\w-])")
            self.titles.append((pattern, courses, len(title)))
        self.titles.sort(key=lambda t: -t[2])
        self.instructor_names = sorted(
            (
                (_phrase_re(name.lower()), inst, len(name))
                for inst, name in v.instructors.items()
            ),
            key=lambda t: -t[2],
        )
        last_names: dict[str, list[str]] = {}
        for inst, name in v.instructors.items():
            last_names.setdefault(name.split()[-1].lower(), []).append(inst)
        self.last_names = {n: ids[0] for n, ids in last_names.items() if len(ids) == 1}
        self.term_by_name = {name.lower(): code for code, name in v.terms.items()}

    def extract(self, text: str) -> _Entities:
        taken: list[tuple[int, int]] = []

        def free(span: tuple[int, int]) -> bool:
            return all(span[1] <= a or span[0] >= b for a, b in taken)

        def scan(
            patterns: list[tuple[re.Pattern[str], Any, int]],
        ) -> list[tuple[int, Any]]:
            found: list[tuple[int, Any]] = []
            for pattern, value, _ in patterns:
                for match in pattern.finditer(text):
                    if free(match.span()):
                        taken.append(match.span())
                        found.append((match.start(), value))
            return sorted(found, key=lambda f: f[0])

        def ordered(found: list[tuple[int, Any]]) -> list[Any]:
            return list(
                dict.fromkeys(value for _, value in sorted(found, key=lambda f: f[0]))
            )

        v = self.vocab
        courses_found: list[tuple[int, Any]] = []
        for match in re.finditer(r"\b([A-Za-z]{2,4})\s?-?(\d{4})\b", text):
            course = f"{match.group(1).upper()} {match.group(2)}"
            if course in v.courses:
                taken.append(match.span())
                courses_found.append((match.start(), course))
        instructors_found: list[tuple[int, Any]] = []
        for match in re.finditer(r"\bI-\d{4}\b", text, re.I):
            inst = match.group(0).upper()
            if inst in v.instructors:
                taken.append(match.span())
                instructors_found.append((match.start(), inst))
        instructors_found += scan(self.instructor_names)
        for match in re.finditer(
            r"\b(?:professor|prof\.?|dr\.?|instructor)\s+([a-z'-]+)", text, re.I
        ):
            by_name = self.last_names.get(match.group(1).lower().removesuffix("'s"))
            if by_name and free(match.span()):
                taken.append(match.span())
                instructors_found.append((match.start(), by_name))
        terms_found: list[tuple[int, Any]] = []
        for match in re.finditer(r"\b(fall|spring|summer)\s+(20\d\d)\b", text, re.I):
            code = self.term_by_name.get(f"{match.group(1).lower()} {match.group(2)}")
            if code:
                taken.append(match.span())
                terms_found.append((match.start(), code))
        for match in re.finditer(r"\b20\d\d[123]0\b", text):
            if match.group(0) in v.terms:
                taken.append(match.span())
                terms_found.append((match.start(), match.group(0)))
        years_ac: list[tuple[int, Any]] = []
        for match in re.finditer(r"\b(20\d\d)\s*[-–/]\s*(?:20)?(\d\d)\b", text):
            year = f"{match.group(1)}-20{match.group(2)}"
            if year in v.academic_years and free(match.span()):
                taken.append(match.span())
                years_ac.append((match.start(), year))
        titled = scan(self.titles)
        colleges = scan(self.colleges)
        majors = scan(self.majors)
        # Subjects share names with majors; they are read without taking spans.
        subjects: list[tuple[int, Any]] = []
        for pattern, code, _ in self.subjects:
            for match in pattern.finditer(text):
                subjects.append((match.start(), code))
        for match in re.finditer(r"\b([A-Z]{2,4})\b", text):
            if match.group(1) in v.subjects:
                subjects.append((match.start(), match.group(1)))
                if match.group(1) in v.majors and free(match.span()):
                    majors.append((match.start(), match.group(1)))
        # A title several courses share ("Senior Design I") resolves to the
        # one in a subject the question names, else the first by id.
        named = {code for _, code in subjects} | {code for _, code in majors}
        for start, candidates in titled:
            preferred = [c for c in candidates if c.split()[0] in named]
            courses_found.append((start, (preferred or candidates)[0]))
        years = [
            int(m.group(0))
            for m in re.finditer(r"\b20[12]\d\b", text)
            if free(m.span())
        ]
        groups: list[tuple[int, Any]] = []
        for group_re, group in (
            (r"first[- ]?gen", "first_generation"),
            (r"\bpell\b", "pell"),
            (r"residen|in[- ]state|out[- ]of[- ]state|international", "residency"),
            (
                r"cohort|entering class|entry year|year of entry|entering year",
                "entry_cohort",
            ),
            (r"\btransfers?\b|first[- ]time", "entry_type"),
        ):
            for match in re.finditer(group_re, text, re.I):
                groups.append((match.start(), group))
        levels = [
            m.group(1) + "000"
            for m in re.finditer(r"\b([1-4])000[- ]?level\b", text, re.I)
        ]
        if re.search(
            r"\b(?:freshman|first[- ]year|introductory|intro)[- ]level\b", text, re.I
        ):
            levels.append("1000")
        seasons = [
            m.group(1).capitalize()
            for m in re.finditer(r"\b(fall|spring|summer)s?\b(?!\s+20\d\d)", text, re.I)
        ]
        return _Entities(
            majors=ordered(majors),
            colleges=ordered(colleges),
            subjects=ordered(subjects),
            courses=ordered(courses_found),
            instructors=ordered(instructors_found),
            terms=ordered(terms_found),
            academic_years=ordered(years_ac),
            years=years,
            groups=ordered(groups),
            levels=levels,
            seasons=list(dict.fromkeys(seasons)),
        )


_MATCHERS: dict[str, _Matcher] = {}


def _matcher(catalog: Catalog) -> _Matcher:
    matcher = _MATCHERS.get(catalog.hash)
    if matcher is None:
        _MATCHERS.clear()
        matcher = _MATCHERS[catalog.hash] = _Matcher(catalog)
    return matcher


# Common slips of the keyboard, corrected before planning ("teh" -> "the").
_TYPOS = {
    "teh": "the",
    "hte": "the",
    "waht": "what",
    "whta": "what",
    "wich": "which",
    "whcih": "which",
    "hwo": "how",
    "taugh": "taught",
    "tuaght": "taught",
    "hardets": "hardest",
    "lowset": "lowest",
    "lowets": "lowest",
    "majro": "major",
    "gpa's": "GPAs",
    "hw": "how",
    "grads": "graduates",
}
_TYPO_RE = re.compile(r"\b(" + "|".join(map(re.escape, _TYPOS)) + r")\b", re.I)


# A conversational lead-in before the question itself ("no, how many ...",
# "actually, ..."), dropped before planning.
_LEAD_IN_WORDS = (
    r"no(?!\.)|nope|nah|actually|sorry|ok|okay|well|wait|hmm|i mean|i meant|so"
)
_LEAD_IN_RE = re.compile(
    # Only before a comma, a question word, or another lead-in: "no, how
    # many ...", "actually what ..."; never "No students on probation?".
    rf"^\s*(?:(?:{_LEAD_IN_WORDS})(?:\s*[,.!:;]+\s*|\s+(?=(?:{_LEAD_IN_WORDS}|how|"
    r"what|whats|what's|which|who|where|when|why|is|are|do|does|did|can|could|"
    r"show|list|tell|give)\b)))+",
    re.I,
)


def _fix_typos(question: str) -> str:
    question = _LEAD_IN_RE.sub("", question) or question
    return _TYPO_RE.sub(lambda m: _TYPOS[m.group(1).lower()], question)


_NEXT_QUESTION = (
    r"(?=(?:which|what|what's|whats|who|whom|how|where|when|is|are|did|does|do|"
    r"has|have)\b)"
)


def _clauses(question: str) -> list[str]:
    """The question split into the parts a step can answer: at question
    marks, semicolons, sentence ends, a colon, and before a new question word
    after a comma or an "and" ("Which major has the lowest GPA, what is its
    hardest class, and who has taught it?" is three parts)."""
    parts = re.split(
        r"\?|;|:\s+|\.\s+|\.$|,\s*(?:and\s+then\s+|then\s+|and\s+)?"
        + _NEXT_QUESTION
        + r"|,\s*(?:and\s+)?(?=its\b)"
        # A lead-in that points back ("..., and in that major what is ...")
        # starts a new part, so the earlier ranking's words stay with it.
        + r"|,\s*(?:and\s+)?(?=(?:then\s+)?(?:in|for|within|among|of)\s+"
        + r"(?:that|this|the\s+same|those|these|it)\b)"
        + r"|\s+and\s+then\s+|\s+and\s+"
        # After a bare "and", only a wh-word starts a new part: "taught MEEN
        # 3310 and have the highest DFW rates" stays one part.
        + r"(?=(?:(?:historically|also|then|now|and)\s+)?"
        + r"(?:which|what|what's|whats|who|whom|how|where|when)\b)",
        question.strip(),
        flags=re.I,
    )
    return [p.strip() for p in parts if p and p.strip()]


def _has(pattern: str, text: str) -> bool:
    return re.search(pattern, text, re.I) is not None


class _ClausePlanner:
    def __init__(self, matcher: _Matcher, question_entities: _Entities) -> None:
        self.m = matcher
        self.q = question_entities
        self.steps: list[Step] = []
        # Set when a clause needs no step because an earlier table already
        # answers it ("and what were their DFW rates?").
        self.already_shown = False

    def chain(self, kind: str) -> Ref | None:
        for index in range(len(self.steps) - 1, -1, -1):
            analysis = ANALYSIS_BY_ID[self.steps[index].analysis_id]
            for column in analysis.columns:
                if column.entity == kind:
                    return Ref(index, column.key)
        return None

    def entity(
        self, kind: str, found: list[str], text: str, *, required: bool
    ) -> str | Ref | None:
        """Clause entity, else a reference to an earlier step (when the
        clause refers back, or the value is required), else the question's."""
        if found:
            return found[0]
        refers = _REFERENCE_RE.search(text) is not None
        if refers or required:
            ref = self.chain(kind)
            if ref is not None:
                return ref
        question_found: list[str] = getattr(self.q, kind + "s")
        return question_found[0] if question_found else None

    def term_range(self, e: _Entities) -> tuple[str | None, str | None]:
        terms = list(e.terms)
        if not terms:
            terms = [
                f"{year + 1}10"
                for year in e.years
                if f"{year + 1}10" in self.m.vocab.terms
            ]
        if len(terms) >= 2:
            return terms[0], terms[-1]
        return (terms[0], None) if terms else (None, None)

    def plan(self, text: str) -> Step | None:  # noqa: C901 - one rule per analysis
        budget = _budget_step(text, self.m.vocab)
        if budget is not None:
            return budget
        e = self.m.extract(text)
        low = _LOW_RE.search(text) is not None
        high = _HIGH_RE.search(text) is not None
        p: dict[str, Any] = {}

        if (
            _has(r"\bcontinuing\b|\breturning\b", text)
            and _has(r"regist", text)
            or _has(r"spring[- ]to[- ]spring", text)
        ):
            if e.terms:
                p["term"] = e.terms[0]
            return Step("continuing_registration_change", p)

        online_words = r"online|modalit|in[- ]person|hybrid|delivery|teaching mode"
        if (
            _has(r"withdr", text)
            and _has(r"\bonline\b", text)
            and _has(
                r"\b(?:which|what)\s+(?:\w+\s+){0,2}(?:courses?|class(?:es)?)\b|"
                r"\b(?:by|per|each|every|for\s+each)\s+(?:courses?|class(?:es)?)\b|"
                r"\bcourse[- ]by[- ]course\b",
                text,
            )
        ):
            # Ranks courses ("Which course has the highest withdrawal rate
            # online?"); "Do online courses have higher withdrawal rates?"
            # names no ranking of courses and stays with the term comparison.
            if e.subjects:
                p["subject"] = e.subjects[0]
            p["order"] = (
                "lowest_first"
                if _has(r"lowest|least|fewest", text) and not high
                else "highest_first"
            )
            return Step("withdrawal_by_course_modality", p)

        if _has(r"withdr", text) and _has(online_words, text):
            if e.terms:
                p["term"] = e.terms[0]
            if _has(r"largest|biggest|widest|most|highest|which term|when", text):
                p["order"] = "largest_gap"
            return Step("withdrawal_by_modality", p)

        general_params = _general_params(text, e, self.m.vocab)
        if general_params is not None:
            return Step(general.ANALYSIS_ID, general_params)

        groups = e.groups or (
            [] if not _has(r"equity|gap|disparit", text) else ["first_generation"]
        )
        if groups and (_has(r"equity|gap|disparit|differ|dfw|fail|rate", text)):
            course = self.entity("course", e.courses, text, required=False)
            major = (
                None if course else self.entity("major", e.majors, text, required=False)
            )
            if course or major:
                p["group"] = groups[0]
                if course:
                    p["course"] = course
                else:
                    p["major"] = major
                return Step("equity_gap", p)

        if e.instructors and not _has(r"\bwho\b", text):
            return Step("instructor_history", {"instructor": e.instructors[0]})

        # "What has that instructor taught?" after a step listing instructors.
        if (
            _REFERENCE_RE.search(text)
            and not e.courses
            and _has(r"\b(?:instructors?|professors?|faculty|teachers?|she|he)\b", text)
            and _has(r"\btaught\b|\bteach(?:es|ing)?\b|\bcourses\b|\bhistory\b", text)
        ):
            ref = self.chain("instructor")
            if ref is not None:
                return Step("instructor_history", {"instructor": ref})

        if _has(
            r"\b(?:instructors?|professors?|faculty|teachers?|who)\b", text
        ) and _has(
            r"\btaught\b|\bteach(?:es|ing)?\b|\binstructors?\b|\bprofessors?\b", text
        ):
            course = self.entity("course", e.courses, text, required=True)
            if course is not None:
                return Step("course_instructors", {"course": course})

        dfw_words = (
            r"dfw|d/f/w|\bfail|pass rate|hardest|toughest|difficult|easiest|drop rate"
        )
        trend_words = (
            r"\btrend|by term|each term|per term|term by term|over time|"
            r"over the years"
        )
        refers = _REFERENCE_RE.search(text) is not None
        if (
            refers
            and self.steps
            and not (e.courses or e.majors or e.subjects or e.levels)
            and _has(dfw_words, text)
            and ANALYSIS_BY_ID[self.steps[-1].analysis_id].column("dfw_rate")
        ):
            # "what were their DFW rates": the last table already shows them.
            self.already_shown = True
            return None
        if _has(dfw_words, text) or _has(trend_words, text):
            course = (
                e.courses[0] if e.courses else self.chain("course") if refers else None
            )
            if course is not None and not _has(
                r"\bwhich\s+(?:courses?|class(?:es)?)\b|"
                r"\bwhat\s+(?:courses?|class(?:es)?)\b",
                text,
            ):
                return Step("course_dfw_trend", {"course": course})
            if _has(
                r"\bcourses?\b|\bclass(?:es)?\b|hardest|easiest|toughest|dfw", text
            ):
                # "MATH courses" or "chem courses" (a subject code or short
                # name) ranks the subject's courses; a major named in words
                # ("hardest class in Mechanical Engineering") ranks the
                # courses that major requires.
                coded_subject = any(
                    re.search(r"\b" + re.escape(code) + r"\b", text)
                    for code in e.subjects
                ) or any(_has(rf"\b{short}\b", text) for short in _SUBJECT_SYNONYMS)
                major = (
                    e.majors[0]
                    if e.majors
                    and not (
                        coded_subject
                        and _has(r"\b(?:courses?|class(?:es)?)\b", text)
                        and not _has(r"\bmajors?\b|requir", text)
                    )
                    else None
                )
                if major is None and refers and not e.subjects:
                    major = self.chain("major")
                if major is not None:
                    p["major_required"] = major
                elif e.subjects:
                    p["subject"] = e.subjects[0]
                if e.levels:
                    p["level"] = e.levels[0]
                start, end = self.term_range(e)
                if start and _has(r"\bsince\b|\bfrom\b|\bafter\b", text):
                    p["term_from"] = start
                if end:
                    p["term_to"] = end
                p["order"] = (
                    "lowest_first"
                    if _has(r"easiest|lowest|least", text)
                    else "highest_first"
                )
                return Step("dfw_by_course", p)
            if _has(dfw_words, text):
                return None  # a grade question with no course to rank or name

        if _has(r"probation|suspen|academic standing|good standing", text):
            self._scope(p, e, text, term=True)
            p["order"] = "lowest_first" if low and not high else "highest_first"
            return Step("standing_by_major", p)

        if _has(r"graduat", text):
            if e.majors:
                p["major"] = e.majors[0]
            if e.colleges:
                p["college"] = e.colleges[0]
            if e.academic_years:
                p["academic_year"] = e.academic_years[0]
            elif e.years:
                year = f"{e.years[0] - 1}-{e.years[0]}"
                if year in self.m.vocab.academic_years:
                    p["academic_year"] = year
            yearly = _has(
                r"each year|per year|by year|over time|every year|trend|each academic "
                r"year|over the years|annually",
                text,
            ) and not _has(r"each major|by major|per major|which majors?", text)
            if yearly or (e.majors and "academic_year" not in p):
                p["group_by"] = "year"
            return Step("graduations", p)

        if _has(r"\bholds?\b", text):
            if _has(r"active|current|outstanding|unresolved|open|still", text):
                p["active_only"] = "yes"
            for word, category in (
                ("financ", "financial"),
                ("owe", "financial"),
                ("balance", "financial"),
                ("registrar", "registrar"),
                ("transcript", "registrar"),
                ("advising", "advising"),
                ("library", "library"),
                ("student life", "student_life"),
                ("conduct", "student_life"),
            ):
                if _has(word, text) and category in self.m.vocab.hold_categories:
                    p["category"] = category
                    break
            if e.terms:
                p["term"] = e.terms[0]
            return Step("holds_by_office", p)

        if _has(r"advis|appointment", text):
            self._scope(p, e, text, term=False)
            if e.terms and e.terms[0] in [
                t for t, s in self.m.vocab.term_season.items() if s != "Summer"
            ]:
                p["term"] = e.terms[0]
            p["order"] = "highest_first" if high and not low else "lowest_first"
            return Step("advising_coverage", p)

        if _has(
            r"credit hours?|\bcredits\b|hours attempted|hours earned|attempted hours",
            text,
        ):
            self._scope(p, e, text, term=False)
            if e.seasons:
                p["season"] = e.seasons[0]
            return Step("credit_hours_by_term", p)

        if _has(
            r"\bgr[eo]w|growth|growing|shr[aiu]nk|shrinking|declin|lost the most|"
            r"gained",
            text,
        ) and (e.majors or _has(r"\bmajors?\b|programs?|departments?|headcount", text)):
            if e.majors:
                p["major"] = e.majors[0]
            if e.colleges:
                p["college"] = e.colleges[0]
            start, end = self.term_range(e)
            if start:
                p["start_term"] = start
            if end:
                p["end_term"] = end
            if _has(r"slowest|shr[aiu]nk|shrinking|declin|lost", text):
                p["order"] = "slowest_first"
            return Step("headcount_growth", p)

        if _has(_HEADCOUNT_WORDS, text):
            if not _has(_TREND_WORDS, text) and not e.seasons:
                # "How many CS students are enrolled?", "total enrollment":
                # a count now (the latest fall or spring term), or in the
                # one term named.
                counts = self._headcount(e, text)
                # Two or more terms ("Fall 2024 vs Fall 2025", "last year"):
                # one count per term, each its own step.
                for params in counts[:-1]:
                    self.steps.append(Step(general.ANALYSIS_ID, params))
                return Step(general.ANALYSIS_ID, counts[-1])
            self._scope(p, e, text, term=False)
            if e.seasons:
                p["season"] = e.seasons[0]
            return Step("enrollment_by_term", p)

        if _has(r"\bgpas?\b|grade point|\bgrades\b", text) or (
            _has(r"perform", text)
            and _has(r"\bmajors?\b|\bprograms?\b|\bcolleges?\b", text)
        ):
            if e.colleges or (
                _has(r"\bcolleges?\b", text)
                and not e.majors
                and not _has(r"\bmajors?\b", text)
            ):
                if e.colleges:
                    p["college"] = e.colleges[0]
                p["order"] = "highest_first" if high and not low else "lowest_first"
                return Step("gpa_by_college", p)
            ranking = _has(r"\bwhich\s+majors?\b|\bwhat\s+majors?\b", text) or (
                _has(r"\bmajors\b", text) and (low or high or len(e.majors) != 1)
            )
            if e.majors and not ranking:
                # "How do Nursing majors perform?" is about Nursing.
                p["major"] = e.majors[0]
            p["order"] = "highest_first" if high and not low else "lowest_first"
            return Step("gpa_by_major", p)
        return None

    def _headcount(self, e: _Entities, text: str) -> list[dict[str, Any]]:
        p: dict[str, Any] = {"measure": "headcount"}
        ranked = re.search(_BIGGEST_WORDS, text, re.I)
        if ranked:
            ranks_colleges = "college" in ranked.group(0).lower()
            p["group_by"] = "college" if ranks_colleges else "major"
        else:
            for key in ("major", "college"):
                if _has(_GROUPING_WORDS[key], text):
                    p["group_by"] = key
                    break
        if ranked:
            p["order"] = "lowest_first" if _has(r"smallest|fewest", text) else (
                "highest_first"
            )
        if e.majors and p.get("group_by") != "major":
            p["major"] = e.majors[0]
        if e.colleges and p.get("group_by") != "college":
            p["college"] = e.colleges[0]
        v = self.m.vocab
        terms = sorted(set(e.terms))
        if _has(_LAST_YEAR_WORDS, text) and not terms:
            current_year = v.term_year[_current_regular_term_of(v)]
            years = list(v.academic_years)
            if years.index(current_year) > 0:
                last = years[years.index(current_year) - 1]
                terms = [
                    t
                    for t, y in v.term_year.items()
                    if y == last and v.term_season[t] != "Summer"
                ]
        if not terms:
            return [p]
        room = MAX_STEPS - len(self.steps)
        return [{**p, "term_from": t, "term_to": t} for t in terms[-room:]]

    def _scope(self, p: dict[str, Any], e: _Entities, text: str, *, term: bool) -> None:
        if e.majors and not _has(
            r"\bwhich\s+majors?\b|\bwhat\s+majors?\b|\bby major\b", text
        ):
            p["major"] = e.majors[0]
        if e.colleges:
            p["college"] = e.colleges[0]
        if term and e.terms:
            p["term"] = e.terms[0]


# A count of students ("how many CS students are there", "total enrollment",
# "what's our biggest major"), and the words that make it a trend over terms.
_HEADCOUNT_WORDS = (
    r"enrol|headcount|student count|"
    r"\b(?:how many|number of|count of)\b(?:\s+[\w-]+){0,4}?\s+"
    r"(?:students|majors|undergrads|undergraduates|kids|people)\b|"
    r"(?:biggest|largest|smallest|most popular)\s+(?:majors?|programs?|colleges?)|"
    r"\b(?:which|what)\s+(?:majors?|programs?|colleges?)\s+(?:has|have|is|are)\s+"
    r"(?:the\s+)?(?:most|fewest|biggest|largest|smallest)\s+"
    r"(?:students|majors|people|enrollment)\b"
)
_TREND_WORDS = (
    r"\b(?:each|every|per|by)\s+(?:term|semester|year|fall|spring|summer)s?\b|"
    r"over time|over the years|\btrend|histor|\bchang|\bsince\b|term[- ]by[- ]term|"
    r"year over year|\bgr[eo]w"
)
_BIGGEST_WORDS = (
    r"(?:biggest|largest|smallest|most popular)\s+(?:majors?|programs?|colleges?)|"
    r"\b(?:which|what)\s+(?:majors?|programs?|colleges?)\s+(?:has|have|is|are)\s+"
    r"(?:the\s+)?(?:most|fewest|biggest|largest|smallest)\s+"
    r"(?:students|majors|people|enrollment)\b"
)
# Two terms, or last year: a count per term, never one term alone.
_LAST_YEAR_WORDS = r"\b(?:last|previous|prior)\s+(?:academic\s+)?year\b"

# --- the general analysis: measure words, grouping words, filter words ------

# --- the university's own budget (cabinet.explore.finance) ------------------

_FY_WORDS = re.compile(
    r"\b(?:fy\s?\d{2,4}|fiscal(?: year)?\s+\d{4}|(?:this|current|last|previous|"
    r"prior)(?: fiscal)? year|(?:19|20)\d{2}\s*[-–/]\s*(?:19|20)?\d{2})\b",
    re.I,
)
_TUITION_WORDS = (
    r"discount(?:ing)? rate|tuition discount|\bdiscount(?:ing)?\b|net tuition|"
    r"gross tuition|tuition revenue|institutional aid"
)
_REVENUE_WORDS = (
    r"\brevenues?\b|\bincome\b|where (?:does|did) (?:our|the) money come from|"
    r"\bgifts?\b|\bendowment\b|\bgrants? revenue"
)
_BUDGET_WORDS = (
    r"\bbudget(?:s|ed)?\b|over ?spen|under ?spen|\bspending\b|\bexpenses?\b|"
    r"\bexpenditures?\b|\bdeficit\b|\bsurplus\b"
)
_OVER_WORDS = (
    r"over (?:the |their |its |our )?budget|over ?spen|exceed\w* (?:the |their |its )?"
    r"budget|went over|ran over"
)


def _fiscal_year_in(text: str, v: Vocab) -> str | None:
    match = _FY_WORDS.search(text)
    if match is None:
        return None
    return fiscal_year(match.group(0), v.fiscal_years)


def _budget_step(text: str, v: Vocab) -> Step | None:
    """The university's budget, revenue or tuition discount, when the clause
    asks about them (never about a student's balance)."""
    if _has(r"past[- ]due|overdue|owe|balance|payment|\bpaid\b|\bpay\b|"
            r"student accounts?|collection", text):
        return None
    p: dict[str, Any] = {}
    year = _fiscal_year_in(text, v)
    if _has(_TUITION_WORDS, text):
        if year is not None:
            p["fiscal_year"] = year
        return Step("tuition_discount", p)
    if _has(_BUDGET_WORDS, text) or _has(r"budget vs|vs\.? budget|against budget", text):
        if year is not None:
            p["fiscal_year"] = year
        if _has(r"department|cost cent|office|\bunits?\b|program", text):
            p["by"] = "cost_center"
        elif _has(r"categor|by type|kind of", text):
            p["by"] = "category"
        elif _has(r"\bfunds?\b", text):
            p["by"] = "fund"
        if _has(_OVER_WORDS, text):
            p["over_budget"] = "yes"
            p["order"] = "highest_first"
        elif _has(r"under (?:the |their |its |our )?budget|underspen", text):
            p["order"] = "lowest_first"
        return Step("budget_vs_actual", p)
    if _has(_REVENUE_WORDS, text) and not _has(r"\bstudents?\b", text):
        if year is not None:
            p["fiscal_year"] = year
        return Step("revenue_by_source", p)
    return None


# Measures only the general analysis computes, in the order they are tried.
_NEW_MEASURES: tuple[tuple[str, str], ...] = (
    # Student accounts (the billing tables), before any word they share.
    (
        "past_due_90_students",
        r"(?:more than|over|beyond|at least|past) (?:90|ninety) days|90\+ days|"
        r"(?:90|ninety) days (?:or more )?(?:past[- ]due|late|overdue|delinquent)",
    ),
    (
        "avg_balance_owed",
        r"average (?:past[- ]due |overdue |outstanding )?(?:balance|amount owed|debt)|"
        r"average (?:amount )?(?:owed|past[- ]due)",
    ),
    (
        "past_due_students",
        r"how many (?:students )?(?:are |were )?(?:past[- ]due|overdue|delinquent|"
        r"behind)|(?:number|count) of (?:students )?(?:past[- ]due|overdue|"
        r"delinquent)|students (?:who are |that are )?(?:past[- ]due|overdue|"
        r"delinquent|behind on)",
    ),
    (
        "on_time_payment_rate",
        r"on[- ]time payment|pa(?:y|id|ying) on time|pay(?:ing)? late|late payments?",
    ),
    ("payment_plan_share", r"payment plans?|installment plans?|installments"),
    ("collection_rate", r"collection rate|collect(?:ed|ions?)\b"),
    (
        "past_due_balance",
        r"past[- ]due|overdue|delinquen|outstanding balances?|accounts? receivable|"
        r"\baging\b|how much (?:is |do students )?(?:owed|owe)",
    ),
    (
        "time_to_degree",
        r"time[- ]to[- ](?:degree|graduat)|years? to (?:a )?(?:degree|"
        r"graduat)|how long (?:does it take|do (?:students|they|\w+ majors) take|"
        r"it takes)"
        r"|how many years",
    ),
    (
        "transfer_out_rate",
        r"transfer(?:red|ring)?[- ]out|transfer(?:red)? (?:out|away|"
        r"to (?:another|other))|leave for another",
    ),
    (
        "dropout_rate",
        r"drop(?:ped|ping)?[- ]?outs?\b|dropped out|drop out|attrition|"
        r"leav(?:e|ing) without (?:a )?degree|left without (?:a )?degree",
    ),
    (
        "stop_out_rate",
        r"stop(?:ped|ping)?[- ]?outs?\b|stopped out|stop out|"
        r"(?:did ?n[o']t|do not|don't) (?:come back|return) (?:the )?next "
        r"(?:term|semester)",
    ),
    (
        "grad_rate_4yr",
        r"(?:4|four)[- ]years? grad|graduat\w* (?:with)?in (?:4|four) years|"
        r"on[- ]time grad|graduat\w* on time",
    ),
    (
        "grad_rate_6yr",
        r"(?:6|six)[- ]years? grad|graduat\w* (?:with)?in (?:6|six) years|"
        r"graduation rates?|grad rates?|(?<!credit )completion rates?",
    ),
    (
        "retention_rate",
        r"retention|\bretain(?:ed)?\b|persist(?:ence)?|"
        r"(?:come|came|coming) back (?:for|their|a) (?:second|sophomore)|"
        r"return(?:ed|ing)? (?:for|their|a) (?:second|sophomore)|second[- ]year return",
    ),
    (
        "major_change_rate",
        r"chang(?:e|ed|es|ing) (?:their |of |a )?majors?|"
        r"switch(?:ed|es|ing)? (?:their )?majors?|major chang|"
        r"(?:switch|chang|transfer)\w* out of (?:the |their |a )?(?:major|program)|"
        r"(?:switch|chang)\w* out of\b",
    ),
    (
        "credit_completion_rate",
        r"credit completion|completion of (?:attempted )?credits|"
        r"credits? (?:earned|completed) (?:vs\.?|versus|of|out of|against) "
        r"(?:credits? )?attempted|earn the credits they attempt",
    ),
    (
        "avg_credits_attempted",
        r"average (?:credit|course) load|average (?:credit )?hours "
        r"(?:attempted|per term)|average credits attempted|credit load",
    ),
    (
        "avg_credits_earned",
        r"average (?:cumulative )?credits earned|credits earned "
        r"on average|average (?:number of )?credits",
    ),
)
# Shares of an attribute ("what share of students are Pell recipients").
_SHARE_WORDS = r"\bshare\b|percent|percentage|proportion|fraction|\bhow many of\b"
_SHARES: tuple[tuple[str, str], ...] = (
    ("pell_share", r"\bpell\b|need[- ]based|low[- ]income"),
    ("first_gen_share", r"first[- ]?gen"),
    ("international_share", r"international"),
    ("part_time_share", r"part[- ]time"),
    (
        "on_campus_share",
        r"on[- ]campus|in (?:university|campus) housing|residence halls?",
    ),
)
# Measures existing analyses answer by major, college, course, or term; the
# general analysis takes them only with a grouping or filter they lack.
_OLD_MEASURES: tuple[tuple[str, str], ...] = (
    (
        "dfw_rate",
        r"\bdfw\b|d/f/w|fail(?:ure|ing)? rates?|\bfail\b|pass rates?|"
        r"\bfails?\b",
    ),
    ("withdrawal_rate", r"withdr[ae]w|withdrawals?"),
    ("probation_rate", r"probation"),
    ("suspension_rate", r"suspen"),
    ("advising_rate", r"advis|appointment"),
    ("hold_rate", r"\bholds?\b"),
    ("graduates", r"graduates|graduated|graduations"),
    ("avg_gpa", r"\bgpas?\b|grade point|\bgrades\b"),
    (
        "headcount",
        r"how many|number of|headcount|enrol|count of|\bcount\b|"
        r"how large|how big|size of",
    ),
)

# Students with a hold ("how many students have holds", "students with an
# active hold"): the hold grouping's filter, never the hold-rate measure.
_HOLD_FILTER = (
    r"\b(?:with|have|has|having|had|carry(?:ing)?)\s+(?:an?\s+|any\s+)?"
    r"(?:active\s+|current\s+|open\s+|outstanding\s+|unresolved\s+)?holds?\b"
    r"|\b(?:on|under)\s+(?:an?\s+)?(?:active\s+)?hold\b"
)

_HOLD_GROUP_WORDS = (
    _HOLD_FILTER
    + r"|\b(?:have|has|having|had|with)\s+no\s+holds?\b|\bwithout\s+(?:a\s+|any\s+)?"
    r"holds?\b|\bno\s+holds?\b|\bhold\s+status\b|\bby\s+holds?\b"
    r"|\bwith\s+(?:and|or|vs\.?|versus)\s+without\s+(?:a\s+|any\s+)?holds?\b"
)

# Grouping words: (grouping, as a grouping, as filter values).
_GROUPING_WORDS: dict[str, str] = {
    "major": r"\b(?:which|what)\s+(?:\w+\s+)?(?:majors?|programs?)\b|"
    r"\b(?:status|by\s+\w+)\s+and\s+(?:by\s+)?majors?\b|"
    r"\b(?:by|per|each|every|across|among)\s+(?:the\s+)?(?:majors?|programs?)\b|"
    r"\bmajors? (?:have|has|with|had)\b",
    "college": r"\b(?:which|what)\s+colleges?\b|\b(?:by|per|each|every|across)\s+"
    r"(?:the\s+)?colleges?\b",
    "class_level": r"class (?:level|year|standing)|by (?:year in school|level)|"
    r"freshmen,? sophomores|by class\b",
    "term": r"\b(?:by|per|each|every)\s+(?:term|semester)\b|term[- ]by[- ]term|"
    r"over time|\btrend\b|each year|by year|per year|over the years|year over year",
    "entry_cohort": r"\bcohorts?\b|entering class(?:es)?|entry year|by year of entry|"
    r"entering year",
    "residency": r"residen(?:cy|t status)|in[- ]state (?:vs\.?|versus|and|or) "
    r"out[- ]of[- ]state|out[- ]of[- ]state (?:vs\.?|versus|and|or) in[- ]state",
    "first_generation": r"first[- ]?gen(?:eration)?(?: college)?(?: students?)? status|"
    r"first[- ]?gen(?:eration)? (?:vs\.?|versus|and|or|compared)|"
    r"\b(?:vs\.?|versus|and|or) (?:first[- ]?gen|continuing[- ]gen)|"
    r"continuing[- ]generation",
    "pell": r"pell status|pell (?:vs\.?|versus|and|or) (?:non[- ]?pell|not)|"
    r"non[- ]?pell|"
    r"without pell|need[- ]based aid status",
    "gender": r"\bgender\b|\bsex\b|men (?:and|vs\.?|versus|or) women|"
    r"women (?:and|vs\.?|versus|or) men|male (?:and|vs\.?|versus|or) female|"
    r"female (?:and|vs\.?|versus|or) male",
    "race_ethnicity": r"\brac(?:e|es|ial)\b|ethnic",
    "age_band": r"\bage\b|ages\b|older students|adult learners|non[- ]?traditional",
    "admit_type": r"admit type|admission type|entry type|"
    r"transfers? (?:vs\.?|versus|and|or) (?:first[- ]time|freshmen|native)|"
    r"first[- ]time (?:vs\.?|versus|and|or) transfers?",
    "load": r"full[- ]time (?:vs\.?|versus|and|or) part[- ]time|part[- ]time (?:vs\.?|"
    r"versus|and|or) full[- ]time|enrollment (?:status|intensity)|course load status",
    "housing": r"\bhousing\b|on[- ]campus (?:vs\.?|versus|and|or) off[- ]campus|"
    r"off[- ]campus (?:vs\.?|versus|and|or) on[- ]campus|where (?:they|students) live|"
    r"commuters? (?:vs\.?|versus|and|or)|residential (?:vs\.?|versus|and|or) commuter",
    "athlete": r"athlet\w* (?:vs\.?|versus|and|or|compared (?:with|to)) non|"
    r"non[- ]?athlet|athlete status|by athlet|athletics status",
    "honors": r"honors (?:vs\.?|versus|and|or|compared (?:with|to)) "
    r"(?:non|other|regular)|non[- ]?honors|honors status|by honors",
    "modality": r"modalit|in[- ]person (?:vs\.?|versus|and|or) online|"
    r"online (?:vs\.?|versus|and|or) in[- ]person|delivery mode",
    "aging": r"\baging\b|by (?:days|how long|how far) past[- ]due|"
    r"how (?:long|far) past[- ]due|days past[- ]due (?:buckets?|bands?|groups?)",
    "hold": r"hold status|by holds?\b|with (?:and|or|vs\.?|versus) without "
    r"(?:a |any )?holds?|holds? (?:vs\.?|versus|and|or) (?:no|without) holds?",
}
# Filter words: grouping -> [(pattern, value)], tried in order.
_FILTER_WORDS: dict[str, tuple[tuple[str, str], ...]] = {
    "residency": (
        (r"\binternational\b", "international"),
        (r"out[- ]of[- ]state", "out_of_state"),
        (r"\bin[- ]state\b", "in_state"),
    ),
    "first_generation": (
        (r"continuing[- ]gen", "continuing_generation"),
        (r"first[- ]?gen", "first_generation"),
    ),
    "pell": (
        (r"non[- ]?pell|without (?:a )?pell|no pell", "no_pell"),
        (r"\bpell\b|need[- ]based aid|low[- ]income", "pell"),
    ),
    "gender": (
        (r"\bwomen\b|\bfemales?\b", "female"),
        (r"\bmen\b|\bmales?\b", "male"),
    ),
    "race_ethnicity": (
        (r"hispanic|latin[oax]", "hispanic"),
        (r"\bblack\b|african[- ]american", "black"),
        (r"\basian\b", "asian"),
        (r"\bwhite\b", "white"),
        (r"native american|american indian|alaska native", "american_indian"),
        (r"pacific islander|native hawaiian", "pacific_islander"),
        (r"two or more races|multiracial|multi-racial", "two_or_more"),
        (r"nonresident", "nonresident"),
    ),
    "admit_type": (
        (r"\btransfer (?:students?|admits?|entrants?)\b|\btransfers\b", "transfer"),
        (r"first[- ]time (?:students?|freshmen|entrants?)", "first_time"),
    ),
    "load": (
        (r"part[- ]time", "part_time"),
        (r"full[- ]time", "full_time"),
    ),
    "housing": (
        (r"off[- ]campus|commuter", "off_campus"),
        (
            r"on[- ]campus|residential students|"
            r"live in (?:the )?(?:dorms|residence halls)",
            "on_campus",
        ),
    ),
    "athlete": (
        (r"non[- ]?athlet", "non_athlete"),
        (r"athlet", "athlete"),
    ),
    "honors": (
        (r"non[- ]?honors|not in (?:the )?honors", "non_honors"),
        (r"\bhonors\b|\bhonours\b", "honors"),
    ),
    "class_level": (
        (r"\bfreshm[ae]n\b|first[- ]year students", "Freshman"),
        (r"\bsophomores?\b", "Sophomore"),
        (r"\bjuniors?\b", "Junior"),
        (r"\bseniors?\b", "Senior"),
    ),
    "age_band": (
        (r"(?:35|thirty[- ]five) (?:and|or) (?:over|older)", "35_plus"),
        (r"adult learners|25 and (?:over|older)|older students", "25_34"),
    ),
    "modality": (
        (r"\bonline\b", "online"),
        (r"\bhybrid\b", "hybrid"),
        (r"in[- ]person", "in_person"),
    ),
    "hold": (
        (r"without (?:a |any )?holds?|\bno holds?\b", "no_hold"),
        (_HOLD_FILTER, "hold"),
    ),
}
# Groupings existing analyses already answer for D, F or withdrawal rates
# in a course or a major (the equity gap).
_EQUITY_GROUPS = {"first_generation", "pell", "residency", "entry_cohort", "admit_type"}


def _detect_measure(text: str) -> tuple[str | None, bool]:
    """(measure id, whether only the general analysis computes it)."""
    # Hold words that name the group ("students with holds", "no hold", "by
    # hold status"), not the hold-rate measure.
    text = re.sub(_HOLD_GROUP_WORDS, " ", text, flags=re.I)
    for measure, pattern in _NEW_MEASURES:
        if _has(pattern, text):
            return measure, True
    if _has(_SHARE_WORDS, text) and not _has(r"\bhow many\b(?! of)", text):
        for measure, pattern in _SHARES:
            if _has(pattern, text):
                return measure, True
    for measure, pattern in _OLD_MEASURES:
        if _has(pattern, text):
            return measure, False
    return None, False


def _general_params(text: str, e: _Entities, v: Vocab) -> dict[str, Any] | None:
    """Parameters of ``measure_by_group`` for a clause, or None when the
    clause belongs to another analysis (or to none)."""
    measure_id, new = _detect_measure(text)
    if measure_id is None:
        return None
    # A course title that is itself a grouping phrase ("Race and Ethnicity")
    # is read as the grouping here.
    courses = [
        c
        for c in e.courses
        if not any(
            re.search(pat, v.courses.get(c, ""), re.I)
            for pat in _GROUPING_WORDS.values()
        )
    ]
    measure = general.MEASURES[measure_id]
    allowed = set(general.allowed_groupings(measure))
    cohort_like = measure.scope in ("latest", "cohort")
    groups: list[tuple[int, str]] = []
    for key, pattern in _GROUPING_WORDS.items():
        match = re.search(pattern, text, re.I)
        if match is None:
            continue
        if key == "term" and cohort_like:
            key = "entry_cohort"
        if key in allowed and all(g != key for _, g in groups):
            groups.append((match.start(), key))
    groups.sort()
    filters: dict[str, str] = {}
    share_attr = {
        "pell_share": "pell",
        "first_gen_share": "first_generation",
        "international_share": "residency",
        "part_time_share": "load",
        "on_campus_share": "housing",
        "hold_rate": "hold",
    }.get(measure_id)
    for key, words in _FILTER_WORDS.items():
        if key not in allowed or key == share_attr:
            continue
        if any(g == key for _, g in groups):
            continue
        for pattern, value in words:
            if _has(pattern, text):
                filters[key] = value
                break
    # "first-time students" is the cohort measures' own population.
    if (
        cohort_like
        and measure.unit == "cohort"
        and filters.get("admit_type") == "first_time"
    ):
        del filters["admit_type"]
    if measure.unit == "cohort":
        filters.pop("class_level", None)
    # "Fall 2021 cohort", "students who entered in 2021": an entry cohort.
    cohort_match = re.search(
        r"(?:fall\s+)?(20\d\d)\s+(?:cohort|entering class|entrants|freshman class)|"
        r"(?:entered|entering|started|starting)\s+(?:in\s+)?(?:fall\s+)?(20\d\d)",
        text,
        re.I,
    )
    if cohort_match and "entry_cohort" in allowed:
        year = int(cohort_match.group(1) or cohort_match.group(2))
        cohort = f"{year}-{year + 1}"
        if cohort in v.entry_cohorts:
            groups = [(at, g) for at, g in groups if g != "entry_cohort"]
            filters["entry_cohort"] = cohort
    group_keys = [g for _, g in groups]
    # Section modality is new for the D, F or withdrawal rate (the older
    # analyses compare modalities only for withdrawals).
    skip = ("major", "college", "term", *_EQUITY_GROUPS) + (
        () if measure_id == "dfw_rate" else ("modality",)
    )
    new_attr = any(k not in skip for k in group_keys + list(filters))
    old_group = any(k in _EQUITY_GROUPS for k in group_keys + list(filters))
    if not new:
        if courses:
            return None  # a course question: the course analyses answer it
        if measure_id == "dfw_rate" and old_group and e.majors and not new_attr:
            return None  # the equity gap in a major
        if not (new_attr or old_group):
            return None
        if measure_id in ("headcount", "graduates") and not (new_attr or old_group):
            return None
    elif courses and measure_id in ("dfw_rate", "withdrawal_rate"):
        return None
    params: dict[str, Any] = {"measure": measure_id}
    # A binary attribute named with no grouping ("average GPA of athletes")
    # is compared with the rest; a count ("how many athletes") is filtered.
    if not group_keys and filters and measure_id != "headcount":
        compare = [
            k
            for k in filters
            if k
            in (
                "athlete",
                "honors",
                "first_generation",
                "pell",
                "gender",
                "admit_type",
                "load",
                "housing",
                "hold",
            )
        ]
        if compare and not e.majors and not e.colleges:
            key = compare[0]
            group_keys.append(key)
            del filters[key]
    if e.majors and "major" not in group_keys and "major" in allowed:
        filters["major"] = e.majors[0]
    if e.colleges and "college" not in group_keys and "college" in allowed:
        filters["college"] = e.colleges[0]
    for key, slot in zip(group_keys[:2], ("group_by", "then_by"), strict=False):
        params[slot] = key
    params.update(filters)
    if measure.scope not in ("latest", "cohort"):
        terms = list(e.terms)
        if not terms and e.years and not cohort_match:
            terms = [f"{y + 1}10" for y in e.years if f"{y + 1}10" in v.terms]
        if len(terms) >= 2:
            params["term_from"], params["term_to"] = terms[0], terms[-1]
        elif terms and _has(r"\bsince\b|\bfrom\b|\bafter\b", text):
            params["term_from"] = terms[0]
        elif terms:
            params["term_from"] = params["term_to"] = terms[0]
    low = _LOW_RE.search(text) is not None
    high = _HIGH_RE.search(text) is not None
    if low and not high:
        params["order"] = "lowest_first"
    elif high and not low:
        params["order"] = "highest_first"
    if "major" in group_keys and (low or high):
        params["top"] = 10
    return params


def rule_plan(question: str, catalog: Catalog) -> list[Step] | None:
    """The deterministic planner. None when no clause maps to an analysis."""
    return rule_plan_detail(question, catalog)[0]


_QUESTION_WORD_RE = re.compile(
    r"\b(?:which|what|who|whom|how|when|where|why|is|are|does|do|did|has|have|show|"
    r"list|tell)\b",
    re.I,
)


def _ignored(step: Step, e: _Entities, catalog: Catalog) -> list[str]:
    """Named terms, years, and colleges the step does not use."""
    used = {str(v) for v in step.params.values() if not isinstance(v, Ref)}
    v = catalog.vocab
    out = [v.terms[t] for t in e.terms if t not in used]
    if not e.terms:
        for year in e.years:
            forms = {
                str(year),
                f"{year + 1}10",
                f"{year - 1}-{year}",
                f"{year}-{year + 1}",
            }
            if not forms & used:
                out.append(str(year))
    out += [y for y in e.academic_years if y not in used]
    out += [v.colleges[c] for c in e.colleges if c not in used]
    if not out:
        return []
    title = ANALYSIS_BY_ID[step.analysis_id].title.lower()
    return [
        f"The question names {', '.join(out)}, which {title} does not filter by, "
        "so that part of the question was not applied."
    ]


def rule_plan_detail(
    question: str, catalog: Catalog
) -> tuple[list[Step] | None, tuple[str, ...]]:
    """(steps, notes): the rule plan, plus a plain sentence for every named
    term or college a step could not apply and every part of the question no
    analysis answered (so a partial answer never passes for a full one)."""
    question = _fix_typos(question)
    matcher = _matcher(catalog)
    planner = _ClausePlanner(matcher, matcher.extract(question))
    notes: list[str] = []
    unanswered: list[str] = []
    carry = ""
    for clause in _clauses(question):
        clause = f"{carry}, {clause}" if carry else clause
        carry = ""
        planner.already_shown = False
        step = planner.plan(clause)
        if step is None:
            if planner.already_shown:
                continue
            if _QUESTION_WORD_RE.search(clause):
                unanswered.append(clause)
            else:  # "In Nursing, which ...": the lead-in belongs to what follows
                carry = clause
            continue
        if step in planner.steps:
            continue
        notes += _ignored(step, matcher.extract(clause), catalog)
        planner.steps.append(step)
        if len(planner.steps) == MAX_STEPS:
            break
    if carry:  # a trailing lead-in ("For Nursing") no step used
        unanswered.append(carry)
    if not planner.steps:
        return None, ()
    for clause in unanswered:
        notes.append(
            f'No approved analysis answered this part of the question: "{clause}".'
        )
    try:
        steps = validate_plan({"steps": [s.to_json() for s in planner.steps]}, catalog)
    except PlanInvalid as exc:  # a rule produced something the catalog refuses
        logger.warning("rule plan failed validation: %s", exc)
        return None, ()
    return steps, tuple(notes)


# --- suggestions -------------------------------------------------------------

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOP = {
    "the",
    "a",
    "an",
    "of",
    "in",
    "and",
    "is",
    "what",
    "which",
    "how",
    "has",
    "have",
    "by",
    "to",
    "for",
    "did",
    "do",
    "does",
    "was",
    "were",
    "are",
    "it",
    "that",
    "who",
}


def nearest_examples(question: str, n: int = 3) -> list[str]:
    """The ``n`` example questions sharing the most words with ``question``."""
    words = set(_WORD_RE.findall(question.lower())) - _STOP

    def score(example: str) -> tuple[float, int]:
        other = set(_WORD_RE.findall(example.lower())) - _STOP
        overlap = len(words & other) / (len(words | other) or 1)
        return (-overlap, EXAMPLE_QUESTIONS.index(example))

    return sorted(EXAMPLE_QUESTIONS, key=score)[:n]


def describe_analysis(analysis: Analysis) -> dict[str, str]:
    return {
        "id": analysis.id,
        "title": analysis.title,
        "description": analysis.description,
    }


# --- what the question was taken to ask ----------------------------------------

_REF_WORDS = {
    "major": "that major",
    "college": "that college",
    "course": "that course",
    "term": "that term",
    "instructor": "that instructor",
}


def _current_regular_term(catalog: Catalog) -> str:
    return _current_regular_term_of(catalog.vocab)


def _current_regular_term_of(v: Vocab) -> str:
    regular = [t for t, s in v.term_season.items() if s != "Summer"]
    return regular[-1] if regular else next(reversed(v.terms))


def _value_words(param: Param, value: Any, catalog: Catalog) -> str:
    if isinstance(value, Ref):
        return _REF_WORDS.get(param.kind, "the earlier result")
    v = catalog.vocab
    if param.kind == "major":
        return v.majors.get(value, str(value))
    if param.kind == "college":
        return v.colleges.get(value, str(value))
    if param.kind == "subject":
        return f"{v.subjects.get(value, value)} courses"
    if param.kind == "course":
        return v.course_label(value)
    if param.kind in ("term", "advising_term"):
        return v.terms.get(value, str(value))
    return catalog.plain(param, value)


def _general_understood(step: Step, catalog: Catalog) -> str:
    p = step.params
    measure = general.MEASURES[str(p.get("measure"))]
    labels = [
        general.GROUPINGS[key].values.get(str(p[key]), str(p[key]))
        for key in general.GROUPING_KEYS
        if key not in ("major", "college", "term", "entry_cohort") and key in p
    ]
    places = [
        _value_words(ANALYSIS_BY_ID[step.analysis_id].param(key), p[key], catalog)  # type: ignore[arg-type]
        for key in ("major", "college")
        if key in p
    ]
    if "entry_cohort" in p and not isinstance(p["entry_cohort"], Ref):
        labels.append(f"students who entered in {p['entry_cohort']}")
    groups = [
        general.GROUPINGS[str(g)].noun
        for g in (p.get("group_by"), p.get("then_by"))
        if isinstance(g, str)
    ]
    # The window the analysis really reads (general.term_window), never
    # the one the plan named: a term measure reads one term, and a fixed or
    # cohort measure reads all the records.
    terms = catalog.vocab.terms
    start, end = p.get("term_from"), p.get("term_to")
    window = general.term_window(
        measure,
        "term" in (p.get("group_by"), p.get("then_by")),
        start if isinstance(start, str) else None,
        end if isinstance(end, str) else None,
        catalog.vocab,
    )
    if not window.applies:
        when = "over all the records" if (start or end) else ""
    elif window.term_from == window.term_to:
        when = f"in {terms[window.term_from]}"
        if window.term_from == _current_regular_term(catalog) and not (start or end):
            when += ", the current term"
    elif window.default and measure.scope != "term":
        when = ""
    else:
        when = f"from {terms[window.term_from]} to {terms[window.term_to]}"
    if measure.id == "headcount":
        who = " and ".join(labels) if labels else "Students"
        if places and not labels:
            who = f"{places[0]} students"
        elif places:
            who = f"{who} in {places[0]}"
        text = f"{who[:1].upper()}{who[1:]} enrolled"
    else:
        text = measure.label[:1].upper() + measure.label[1:]
        scope = labels + places
        if scope:
            text += " for " + ", ".join(scope)
    if groups:
        text += " by " + " and ".join(groups)
    if when:
        text += " " + when
    return text


def understood(steps: list[Step], catalog: Catalog, role: str = "staff") -> str:
    """What the plan answers, in plain words, built from the validated plan
    (never from the model's own text): "Computer Science students enrolled in
    Spring 2026, the current term". Several steps read "...; then ..."."""
    parts: list[str] = []
    for step in steps:
        analysis = ANALYSIS_BY_ID[step.analysis_id]
        if analysis.id == general.ANALYSIS_ID:
            parts.append(_general_understood(step, catalog))
            continue
        text = analysis.title
        order = step.params.get("order")
        if order in ("lowest_first", "highest_first"):
            text += ", " + (
                "lowest first" if order == "lowest_first" else "highest first"
            )
        scope = []
        for param in analysis.params:
            if param.name not in step.params or param.kind == "choice":
                continue
            if param.kind == "instructor" and role not in INSTRUCTOR_ROLES:
                continue
            words = _value_words(param, step.params[param.name], catalog)
            if param.name == "major_required":
                words = f"courses {words} requires"
            scope.append(words)
        if scope:
            text += " (" + "; ".join(scope) + ")"
        parts.append(text)
    # "...; then students enrolled in Fall 2025" (a name stays capitalized).
    later = [
        "students" + part[len("Students") :] if part.startswith("Students ") else part
        for part in parts[1:]
    ]
    return "; then ".join(parts[:1] + later)
