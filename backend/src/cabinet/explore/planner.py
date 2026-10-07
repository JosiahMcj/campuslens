"""Planner: a question becomes a plan, a list of steps ``{analysis_id, params}``.

A parameter may be a value from the catalog or a reference to an earlier
step's top row, ``{"from_step": 0, "column": "major"}``, so "Which major has
the lowest GPA? In that major, what is historically the hardest class?" runs
the second analysis on whatever major the first one ranked lowest.

Two planners sit behind ``plan_question``:

- the rule planner (keywords, synonyms, and patterns over the catalog's
  allowed values), used in replay and fake modes, in tests, and with a live
  provider for every question it can map (``CABINET_EXPLORE_PLANNER``,
  default ``rules-first``);
- the model planner (through the provider interface, role
  ``explore_planner``), which receives ONLY the catalog spec (analysis ids,
  titles, parameter names and allowed values) and the question, and must
  return JSON that ``validate_plan`` accepts. It plans the questions the
  rules cannot map, or every question with ``model-first``. Invalid JSON, an
  unknown id, a value outside the catalog, a bad reference, or an
  unavailable provider all fall back to the rule planner.

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

from cabinet.explore.catalog import (
    ANALYSIS_BY_ID,
    Analysis,
    AnalysisError,
    Catalog,
    Param,
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

UNANSWERABLE_MESSAGE = "The Cabinet can't answer that from the approved analyses yet."

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
    (
        "Which course has the highest DFW rate in Computer Science, and how has it "
        "changed by "
        "term?",
        ("dfw_by_course", "course_dfw_trend"),
    ),
)


class PlanInvalid(ValueError):
    """A plan that does not validate against the catalog."""


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
        steps.append(Step(analysis.id, params))
    return steps


# --- recordings --------------------------------------------------------------


def normalize_question(question: str) -> str:
    return " ".join(question.lower().split()).rstrip(" ?.!")


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
    stripped = text.strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, re.DOTALL)
    if fence:
        stripped = fence.group(1)
    try:
        raw = json.loads(stripped)
    except ValueError as exc:
        raise PlanInvalid(f"the model's plan is not JSON: {exc}") from None
    return validate_plan(raw, catalog)


def model_plan(question: str, catalog: Catalog, provider: Provider) -> list[Step]:
    """The model planner. Raises PlanInvalid or ProviderUnavailable."""
    payload = {"question": question, "catalog": catalog.spec()}
    explanation = provider.explain(payload, PLANNER_ROLE)
    steps = parse_model_plan(explanation.text, catalog)
    if isinstance(provider, RecordingProvider):
        save_recorded_plan(question, catalog, steps, provider)
    return steps


ENV_PLANNER_ORDER = "CABINET_EXPLORE_PLANNER"
RULES_FIRST = "rules-first"
MODEL_FIRST = "model-first"


def planner_order_from_env() -> str:
    """``CABINET_EXPLORE_PLANNER``: ``rules-first`` (the default) asks the model
    only for a question the rule planner cannot map; ``model-first`` asks the
    model every time and falls back to the rules. Anything else is logged and
    read as the default."""
    raw = os.environ.get(ENV_PLANNER_ORDER, "").strip().lower()
    if raw in ("", RULES_FIRST):
        return RULES_FIRST
    if raw == MODEL_FIRST:
        return MODEL_FIRST
    logger.warning(
        "%s=%r is not %s or %s; using %s",
        ENV_PLANNER_ORDER,
        raw,
        RULES_FIRST,
        MODEL_FIRST,
        RULES_FIRST,
    )
    return RULES_FIRST


def _try_model(
    question: str, catalog: Catalog, provider: Provider
) -> tuple[list[Step] | None, str | None]:
    """(model steps, None), or (None, the plain reason the model was not used)."""
    try:
        return model_plan(question, catalog, provider), None
    except ProviderUnavailable as exc:
        reason = f"the model planner was unavailable: {exc.reason}"
    except PlanInvalid as exc:
        reason = f"the model's plan was rejected: {exc}"
    logger.warning("explore model planner not used: %s", reason)
    return None, reason


def plan_question(question: str, catalog: Catalog, provider: Provider) -> PlanOutcome:
    """The plan for one question (call only after the refusal check).

    Replay serves a recorded plan first. With a live provider the default
    order is rules first: the reviewed rule planner answers every question
    it can map, and the model plans only the rest (we measured a local model
    taking longer than the request budget to read the catalog, and the rules
    reach every planted fact). ``CABINET_EXPLORE_PLANNER=model-first`` asks
    the model first and falls back to the rules. Fake and replay modes never
    call a model."""
    if provider.name == "replay":
        recorded = load_recorded_plan(question, catalog)
        if recorded is not None:
            return PlanOutcome(recorded, "recorded")
    if not uses_model(provider):
        steps, notes = rule_plan_detail(question, catalog)
        return PlanOutcome(steps, "rule", None, notes)
    if planner_order_from_env() == RULES_FIRST:
        steps, notes = rule_plan_detail(question, catalog)
        if steps is not None:
            return PlanOutcome(steps, "rule", None, notes)
        model_steps, reason = _try_model(question, catalog, provider)
        if model_steps is not None:
            return PlanOutcome(model_steps, "model")
        return PlanOutcome(None, "rule", reason, ())
    model_steps, reason = _try_model(question, catalog, provider)
    if model_steps is not None:
        return PlanOutcome(model_steps, "model")
    steps, notes = rule_plan_detail(question, catalog)
    return PlanOutcome(steps, "rule", reason, notes)


# --- the rule planner --------------------------------------------------------

_MAJOR_SYNONYMS = {
    "comp sci": "CSCI",
    "cs": "CSCI",
    "computer engineering": "CSCI",
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
}
_TYPO_RE = re.compile(r"\b(" + "|".join(map(re.escape, _TYPOS)) + r")\b", re.I)


def _fix_typos(question: str) -> str:
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
        + r"|\s+and\s+then\s+|\s+and\s+"
        # After a bare "and", only a wh-word starts a new part: "taught MEEN
        # 3310 and have the highest DFW rates" stays one part.
        + r"(?=(?:which|what|what's|whats|who|whom|how|where|when)\b)",
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
            r"\bgr[eo]w|growth|growing|shr[ai]nk|shrinking|declin|lost the most|gained",
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
            if _has(r"slowest|shr[ai]nk|shrinking|declin|lost", text):
                p["order"] = "slowest_first"
            return Step("headcount_growth", p)

        if _has(
            r"enrol|headcount|how many students|student count|number of students", text
        ):
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

    def _scope(self, p: dict[str, Any], e: _Entities, text: str, *, term: bool) -> None:
        if e.majors and not _has(
            r"\bwhich\s+majors?\b|\bwhat\s+majors?\b|\bby major\b", text
        ):
            p["major"] = e.majors[0]
        if e.colleges:
            p["college"] = e.colleges[0]
        if term and e.terms:
            p["term"] = e.terms[0]


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
