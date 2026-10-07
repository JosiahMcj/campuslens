"""The compact catalog the model planner reads, and the code that turns the
model's free-text values back into catalog values.

The full catalog spec (``Catalog.spec``) lists every major, college,
subject, course title, term and instructor: about 17,000 tokens, which a
local model takes longer than a request may wait to read. The planner gets
this instead (under 3,000 tokens):

- every analysis with a one-line purpose and its parameters, each with its
  allowed choices or its value type;
- the measures and groupings of ``measure_by_group``, one line each;
- the majors and colleges (code and name), the term range and the current
  term, and a few short synonyms;
- the planning rules and a handful of worked examples.

It never lists courses, subjects, instructors or individual terms. The model
writes those as free text ("course": "Organic Chemistry I" or "CHEM 2323",
"major": "Computer Science", "term": "Fall 2024"), and ``resolve_plan``
maps each to a catalog value with the same vocabulary the rule planner uses
(codes, names, titles, synonyms, close spellings) before ``validate_plan``
checks the plan. A value that resolves to nothing is left as written, so
validation rejects the plan and explore falls back to the rule planner.

The model also writes a short ``reasoning`` sentence before the steps (one
or two sentences about what the person is asking). It helps a small model
plan and is discarded: nothing shows it, records it, or logs it.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

from cabinet.explore import general
from cabinet.explore.catalog import ANALYSES, ANALYSIS_BY_ID, Catalog, Param

# One line per analysis: what it answers, in the planner's words.
PURPOSE: dict[str, str] = {
    "gpa_by_major": "average cumulative GPA of each major, ranked; or one major's GPA",
    "gpa_by_college": "average cumulative GPA of each college, ranked",
    "dfw_by_course": "courses ranked by D, F or withdrawal rate (hardest or "
    "easiest classes); filter by the courses a major requires (major_required), "
    "a subject, or a course level",
    "course_dfw_trend": "one named course's D, F or withdrawal rate in each term",
    "course_instructors": "who taught one course, with each instructor's D, F "
    "or withdrawal rate",
    "instructor_history": "the courses one named instructor taught",
    "equity_gap": "D, F or withdrawal rate in one course or one major's courses, "
    "by student group (equity gap)",
    "headcount_growth": "each major's headcount in two terms and its growth or "
    "decline, ranked",
    "enrollment_by_term": "students enrolled in EVERY term (a trend over time), new "
    "and continuing; optionally one major, college or season",
    "continuing_registration_change": "continuing students registered in a term "
    "against the same term a year earlier",
    "withdrawal_by_modality": "withdrawal rate online against in person, by term",
    "withdrawal_by_course_modality": "courses ranked by their online withdrawal rate",
    "standing_by_major": "share of each major's students on probation or "
    "suspension, ranked",
    "graduations": "number of graduates by major (ranked) or by academic year",
    "holds_by_office": "holds on student accounts by office, with amounts owed",
    "advising_coverage": "share of each major's students who saw an advisor in a "
    "term, ranked",
    "credit_hours_by_term": "credit hours attempted and earned in each term",
    "measure_by_group": "ONE measure (below) for all students, or broken down by "
    "up to two groupings, with filters; counts and rates for any student group",
}

# One line per measure of measure_by_group.
MEASURE_LINES: dict[str, str] = {
    "headcount": "number of students enrolled (default: the current term)",
    "avg_gpa": "average cumulative GPA",
    "avg_credits_earned": "average credits earned",
    "dropout_rate": "share who left without a degree and did not come back",
    "transfer_out_rate": "share who left and enrolled at another college",
    "major_change_rate": "share who changed major",
    "pell_share": "share of students with a Pell grant",
    "first_gen_share": "share of students who are first-generation",
    "international_share": "share of students who are international",
    "part_time_share": "share of students who are part-time",
    "on_campus_share": "share of students living on campus",
    "probation_rate": "share of student terms on academic probation",
    "suspension_rate": "share of student terms ending in suspension",
    "stop_out_rate": "share who skipped the next fall or spring term",
    "credit_completion_rate": "credits earned over credits attempted",
    "avg_credits_attempted": "average credit load per term",
    "advising_rate": "share of student terms with an advising appointment",
    "hold_rate": "share of student terms with a hold",
    "retention_rate": "first-year retention (first-time fall starters back the "
    "next fall)",
    "grad_rate_4yr": "4-year graduation rate",
    "grad_rate_6yr": "6-year graduation rate (the usual 'graduation rate')",
    "time_to_degree": "average years to graduate",
    "graduates": "number of graduates",
    "dfw_rate": "D, F or withdrawal rate of course registrations",
    "withdrawal_rate": "course withdrawal rate",
}

# Short names people use, beyond the names in the lists.
SYNONYMS = (
    "CS, comp sci = Computer Science; mech e, mechanical = Mechanical "
    "Engineering; psych = Psychology; bio = Biology; chem = Chemistry; nurses = "
    "Nursing; business = Business Administration; engineering (the college) = "
    "College of Engineering and Computing; freshmen, first-years = class_level "
    "Freshman; grads = graduates; kids = students; DFW = D, F or withdrawal; "
    "first gen = first_generation; this semester, now, currently = the current "
    "term"
)

# Worked examples. None of these questions is in the evaluation set.
EXAMPLES: tuple[tuple[str, str], ...] = (
    (
        "how many history majors are there",
        '{"reasoning": "A count of History majors enrolled now.", "steps": '
        '[{"analysis_id": "measure_by_group", "params": {"measure": "headcount", '
        '"major": "History"}}]}',
    ),
    (
        "how many sophomores were enrolled each term",
        '{"reasoning": "Sophomore counts over time.", "steps": '
        '[{"analysis_id": "measure_by_group", "params": {"measure": "headcount", '
        '"class_level": "Sophomore", "group_by": "term"}}]}',
    ),
    (
        "which major has the most students on probation, and what is its "
        "toughest course and who teaches it",
        '{"reasoning": "Rank majors by probation, then the hardest course that '
        'major requires, then that course\'s instructors.", "steps": '
        '[{"analysis_id": "standing_by_major", "params": {"order": '
        '"highest_first"}}, {"analysis_id": "dfw_by_course", "params": '
        '{"major_required": {"from_step": 0, "column": "major"}}}, '
        '{"analysis_id": "course_instructors", "params": {"course": '
        '{"from_step": 1, "column": "course"}}}]}',
    ),
    (
        "stop out rate for out of state women",
        '{"reasoning": "One rate for one group: out-of-state women.", "steps": '
        '[{"analysis_id": "measure_by_group", "params": {"measure": '
        '"stop_out_rate", "residency": "out_of_state", "gender": "female"}}]}',
    ),
    (
        "how did calculus 2 do over the years",
        '{"reasoning": "One course\'s D, F or withdrawal rate by term.", "steps": '
        '[{"analysis_id": "course_dfw_trend", "params": {"course": "Calculus II"}}]}',
    ),
    (
        "what's the parking situation",
        '{"reasoning": "No approved analysis covers parking.", "steps": []}',
    ),
)

RULES = (
    "Rules:\n"
    "- A count of students now ('how many X students', 'how many X majors', "
    "'total enrollment', 'how big is X') is measure_by_group with measure "
    "headcount and a filter for X (major, college, class_level, residency, "
    "admit_type, load, housing, athlete, ...). Leave the term out: it defaults to "
    "the current term. Use enrollment_by_term only when the question asks for "
    "every term or a trend.\n"
    "- 'by X', 'for each X', 'X vs Y', 'compare' set group_by. A ranking ('which "
    "major has the highest', 'what majors ... most', 'where are we losing') "
    "needs group_by (major unless another grouping is named) and order; order "
    "alone ranks nothing. A single group ('for Pell students', 'in Nursing') is "
    "a filter, not a grouping. To cover a college or several majors use one step "
    "with a college filter or group_by, never one step per major.\n"
    "- order highest_first puts the highest rate first (hardest courses, most "
    "failing, most students); lowest_first the lowest (easiest).\n"
    "- A share or percent of students who are X uses the matching *_share "
    "measure.\n"
    "- Students with a hold ('how many students have holds', 'dropout rate for "
    "students with holds') is the hold filter or grouping (hold: hold) on "
    "headcount or the asked measure; hold_rate is only for 'hold rate'.\n"
    "- Course difficulty: hardest/easiest classes is dfw_by_course; one named "
    "course's rate is course_dfw_trend; who taught a course is "
    "course_instructors.\n"
    "- Set a term only when the question names one; headcount_growth compares "
    "Fall 2020 with Fall 2025 unless other terms are named. Name terms like "
    '"Fall 2024"; courses by code or title; majors and '
    "colleges by name or code from the lists; instructors as written.\n"
    "- A later step may use an earlier step's top row: "
    '{"from_step": <index from 0>, "column": "<column>"} (columns: major, '
    "college, course, term, instructor).\n"
    "- Leave out every parameter the question does not ask for. Never invent "
    "numbers.\n"
    '- If no analysis answers the question, give "steps": [].'
)

INTRO = (
    "You plan answers to a university leader's question. Choose analyses from "
    "this catalog and set their parameters; code computes every number. Answer "
    'with one JSON object only: {"reasoning": "<one short sentence: what the '
    'person wants>", "steps": [{"analysis_id": "...", "params": {...}}]}, 1 to '
    "4 steps, no other text."
)

_KIND_TYPES = {
    "major": "major",
    "college": "college",
    "subject": "subject (code or name)",
    "course": "course (code or title)",
    "term": "term",
    "advising_term": "term (fall or spring)",
    "instructor": "instructor name",
    "academic_year": "academic year, e.g. 2024-2025",
    "entry_cohort": "entry cohort, e.g. 2021-2022",
}


def _param_text(param: Param, catalog: Catalog) -> str:
    if param.kind == "choice":
        kind = "|".join(str(c) for c in param.choices)
    elif param.kind == "category":
        kind = "|".join(catalog.vocab.hold_categories)
    else:
        kind = _KIND_TYPES.get(param.kind, param.kind)
    if param.default is not None and param.name != "top":
        default = param.default
        if param.kind in ("term", "advising_term"):
            default = catalog.vocab.terms.get(str(default), default)
        kind += f" (default {default})"
    return f"{param.name}{'*' if param.required else ''}: {kind}"


def _current_term(catalog: Catalog) -> str:
    v = catalog.vocab
    regular = [t for t, s in v.term_season.items() if s != "Summer"]
    return regular[-1] if regular else next(reversed(v.terms))


_COMPACT_CACHE: dict[str, str] = {}


def compact_catalog(catalog: Catalog) -> str:
    """The catalog as the model planner reads it (see the module docstring).
    The same for every question and every role, so a server can keep it
    cached between questions."""
    cached = _COMPACT_CACHE.get(catalog.hash)
    if cached is None:
        _COMPACT_CACHE.clear()
        cached = _COMPACT_CACHE[catalog.hash] = _build(catalog)
    return cached


def _build(catalog: Catalog) -> str:
    v = catalog.vocab
    lines = [INTRO, "", "ANALYSES (id: purpose. params; * = required):"]
    filter_keys = set(general.GROUPING_KEYS) - {"term"}
    for analysis in ANALYSES:
        params = [
            "then_by: same as group_by"
            if p.name == "then_by"
            else _param_text(p, catalog)
            for p in analysis.params
            if not (analysis.id == general.ANALYSIS_ID and p.name in filter_keys)
            and not p.name.startswith("min_")
        ]
        if analysis.id == general.ANALYSIS_ID:
            params.insert(3, "any grouping below as a filter: one of its values")
        purpose = PURPOSE.get(analysis.id, analysis.title)
        lines.append(f"- {analysis.id}: {purpose}. " + "; ".join(params))
    lines.append("")
    lines.append("MEASURES for measure_by_group:")
    for measure in general.MEASURES.values():
        lines.append(f"- {measure.id}: {MEASURE_LINES.get(measure.id, measure.label)}")
    lines.append("")
    lines.append("GROUPINGS (group_by/then_by, or a filter with one value):")
    for key, grouping in general.GROUPINGS.items():
        values = [k for k in grouping.values if k != general.NOT_RECORDED]
        if key in ("major", "college", "term"):
            values_text = {"major": "a major", "college": "a college", "term": "-"}[key]
        elif key == "entry_cohort":
            values_text = "e.g. 2021-2022"
        else:
            values_text = "|".join(values)
        lines.append(f"- {key}: {values_text}")
    lines.append("")
    lines.append(
        "MAJORS: " + "; ".join(f"{code} {name}" for code, name in v.majors.items())
    )
    lines.append(
        "COLLEGES: " + "; ".join(f"{code} {name}" for code, name in v.colleges.items())
    )
    terms = list(v.terms.values())
    current = v.terms[_current_term(catalog)]
    lines.append(
        f"TERMS: {terms[0]} to {terms[-1]} (Fall, Spring, Summer); the current "
        f"term is {current}."
    )
    lines.append("SYNONYMS: " + SYNONYMS)
    lines.append("")
    lines.append(RULES)
    lines.append("")
    lines.append("EXAMPLES:")
    for question, answer in EXAMPLES:
        lines.append(f"Q: {question}\nA: {answer}")
    return "\n".join(lines)


# --- resolving the model's values ------------------------------------------------


class Unresolved(ValueError):
    """A value the model wrote that matches nothing in the catalog."""


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9& ]+", " ", text.lower()).split())


_ROMAN = {"1": "i", "2": "ii", "3": "iii", "4": "iv"}


def _title_forms(text: str) -> list[str]:
    """The text, plus the text with a trailing 1/2/3 written as I/II/III."""
    base = _norm(text)
    forms = [base]
    match = re.fullmatch(r"(.*\S)\s+([1-4])", base)
    if match:
        forms.append(f"{match.group(1)} {_ROMAN[match.group(2)]}")
    return forms


_NUMERAL_RE = re.compile(r"^(?:\d+|i|ii|iii|iv|v|vi)$")


def _numerals(text: str) -> list[str]:
    """The numbers and roman numerals in a normalized title, as roman
    numerals ("calculus 2" and "calculus ii" both give ["ii"])."""
    out = []
    for word in text.split():
        if _NUMERAL_RE.match(word):
            out.append(_ROMAN.get(word, word))
    return out


def _same_words(text: str, name: str) -> bool:
    """Every word of ``text`` is a close spelling of the word in the same
    place of ``name`` (a typo, never a different word: "Chemical
    Engineering" is not "Mechanical Engineering"), with the same numbers."""
    a, b = text.split(), name.split()
    if len(a) != len(b) or _numerals(text) != _numerals(name):
        return False
    return all(
        x == y or difflib.SequenceMatcher(None, x, y).ratio() >= 0.9
        for x, y in zip(a, b, strict=True)
    )


def _close(text: str, names: dict[str, str], cutoff: float = 0.85) -> str | None:
    """The code whose (normalized) name is a close spelling of ``text``: a
    typo in a word or two, never another word or another number."""
    hits = [
        h
        for h in difflib.get_close_matches(text, list(names), n=3, cutoff=cutoff)
        if _same_words(text, h)
    ][:2]
    if len(hits) == 1 or (
        len(hits) == 2
        and difflib.SequenceMatcher(None, text, hits[0]).ratio()
        > difflib.SequenceMatcher(None, text, hits[1]).ratio()
    ):
        return names[hits[0]]
    return None


def _named(
    text: str,
    codes: dict[str, str],
    synonyms: dict[str, str],
    *,
    strip: str = "",
) -> str | None:
    """A code from ``codes`` (code -> name) for a code, a name, a synonym, a
    name with a trailing word removed ("Nursing majors"), or a close spelling."""
    raw = text.strip()
    if raw.upper() in codes:
        return raw.upper()
    by_name = {_norm(name): code for code, name in codes.items()}
    by_name.update({_norm(k): c for k, c in synonyms.items() if c in codes})
    candidates = [_norm(raw)]
    if strip:
        stripped = re.sub(strip, "", _norm(raw)).strip()
        if stripped and stripped != candidates[0]:
            candidates.append(stripped)
    for candidate in candidates:
        if candidate in by_name:
            return by_name[candidate]
    for candidate in candidates:
        code = _close(candidate, by_name, cutoff=0.9)
        if code is not None:
            return code
    return None


def _major(text: str, catalog: Catalog) -> str | None:
    from cabinet.explore.planner import _MAJOR_SYNONYMS

    return _named(
        text,
        catalog.vocab.majors,
        {**_MAJOR_SYNONYMS, "computer science": "CSCI", "comp sci": "CSCI"},
        strip=r"\b(?:majors?|students?|program|department|degree)\b",
    )


def _college(text: str, catalog: Catalog) -> str | None:
    from cabinet.explore.planner import _COLLEGE_SYNONYMS

    v = catalog.vocab
    code = _named(
        text, v.colleges, dict(_COLLEGE_SYNONYMS), strip=r"\b(?:the|college|of)\b"
    )
    if code is not None:
        return code
    # "Engineering" or "Nursing college": the one college whose name has it.
    words = re.sub(r"\b(?:the|college|school|of|students?)\b", "", _norm(text)).split()
    if not words:
        return None
    hits = [
        c
        for c, name in v.colleges.items()
        if all(w in _norm(name).split() for w in words)
    ]
    return hits[0] if len(hits) == 1 else None


def _subject(text: str, catalog: Catalog) -> str | None:
    from cabinet.explore.planner import _SUBJECT_SYNONYMS

    return _named(
        text,
        catalog.vocab.subjects,
        dict(_SUBJECT_SYNONYMS),
        strip=r"\b(?:courses?|classes?|subject|department)\b",
    )


def _course(text: str, catalog: Catalog) -> str | None:
    v = catalog.vocab
    match = re.fullmatch(r"\s*([A-Za-z]{2,4})\s*-?\s*(\d{4})\b.*", text)
    if match:
        course = f"{match.group(1).upper()} {match.group(2)}"
        if course in v.courses:
            return course
    by_title: dict[str, list[str]] = {}
    for course, title in v.courses.items():
        by_title.setdefault(_norm(title), []).append(course)
    # A title several subjects share ("Senior Design I") names no one
    # course: it stays unresolved, unless the text names the subject too.
    unique = {t: c[0] for t, c in by_title.items() if len(c) == 1}
    for form in _title_forms(text):
        if form in by_title:
            return unique.get(form)
    for form in _title_forms(text):
        code = _close(form, {t: t for t in by_title}, cutoff=0.9)
        if code is not None:
            return unique.get(code)
    return None


def _instructor(text: str, catalog: Catalog) -> str | None:
    v = catalog.vocab
    raw = text.strip()
    if raw.upper() in v.instructors:
        return raw.upper()
    name = re.sub(r"^(?:professor|prof|dr|instructor)\.?\s+", "", _norm(raw))
    by_name = {_norm(n): i for i, n in v.instructors.items()}
    if name in by_name:
        return by_name[name]
    last = [i for i, n in v.instructors.items() if _norm(n).split()[-1] == name]
    return last[0] if len(last) == 1 else None


_CURRENT_WORDS = re.compile(
    r"^(?:current|latest|now|this (?:semester|term)|current (?:semester|term)|"
    r"most recent(?: term| semester)?)$"
)


def _term(text: str, catalog: Catalog, *, regular_only: bool = False) -> str | None:
    v = catalog.vocab
    raw = _norm(text)
    if raw in v.terms:
        return raw
    if _CURRENT_WORDS.match(raw):
        return _current_term(catalog)
    match = re.fullmatch(r"(fall|spring|summer)\s+(?:of\s+)?(\d{2}|\d{4})", raw)
    if match:
        year = match.group(2)
        year = year if len(year) == 4 else "20" + year
        name = f"{match.group(1).capitalize()} {year}"
        for code, term_name in v.terms.items():
            if term_name == name and not (
                regular_only and v.term_season.get(code) == "Summer"
            ):
                return code
    return None


def _year_range(text: str, options: tuple[str, ...]) -> str | None:
    raw = _norm(text)
    match = re.fullmatch(r"(?:fall\s+)?(\d{4})(?:\s+(?:20)?(\d{2}))?", raw)
    if match:
        start = int(match.group(1))
        value = f"{start}-{start + 1}"
        return value if value in options else None
    return None


def _academic_year(text: str, options: tuple[str, ...]) -> str | None:
    raw = text.strip()
    if raw in options:
        return raw
    match = re.fullmatch(r"(\d{4})\s*[-–/ ]\s*(?:20)?(\d{2})", raw)
    if match:
        value = f"{match.group(1)}-20{match.group(2)}"
        return value if value in options else None
    return _year_range(raw, options)


def _choice(param: Param, value: Any) -> Any:
    if any(str(c) == str(value) for c in param.choices):
        return value
    text = _norm(str(value))
    keyed = {_norm(str(c).replace("_", " ")): c for c in param.choices}
    if text in keyed:
        return keyed[text]
    labels = {_norm(str(label)): c for c, label in param.choice_labels.items()}
    if text in labels:
        return labels[text]
    singular = text.removesuffix("s")
    for form, choice in {**keyed, **labels}.items():
        if form.removesuffix("s") == singular:
            return choice
    return value


def _resolve_value(param: Param, value: Any, catalog: Catalog) -> Any:
    """The catalog value for one model-written value; Unresolved when a
    free-text value matches nothing."""
    if isinstance(value, dict) or value is None:
        return value  # a reference to an earlier step, or "use the default"
    if param.kind == "choice":
        return _choice(param, value)
    if catalog.is_allowed(param, value):
        return catalog.normalize(param, value)
    if not isinstance(value, str):
        raise Unresolved(f"{value!r} is not a {param.label.lower()}")
    v = catalog.vocab
    resolved: str | None
    if param.kind == "major":
        resolved = _major(value, catalog)
    elif param.kind == "college":
        resolved = _college(value, catalog)
    elif param.kind == "subject":
        resolved = _subject(value, catalog)
        if resolved is None:  # "Computer Science courses": the major's subject
            major = _major(value, catalog)
            resolved = major if major in v.subjects else None
    elif param.kind == "course":
        resolved = _course(value, catalog)
    elif param.kind == "instructor":
        resolved = _instructor(value, catalog)
    elif param.kind == "term":
        resolved = _term(value, catalog)
    elif param.kind == "advising_term":
        resolved = _term(value, catalog, regular_only=True)
    elif param.kind == "academic_year":
        resolved = _academic_year(value, v.academic_years)
    elif param.kind == "entry_cohort":
        resolved = _academic_year(value, v.entry_cohorts)
    elif param.kind == "category":
        resolved = next(
            (
                c
                for c in v.hold_categories
                if _norm(c.replace("_", " ")) == _norm(value)
            ),
            None,
        )
    else:
        resolved = None
    if resolved is None:
        raise Unresolved(f"{value!r} matched no {param.label.lower()}")
    return resolved


# Parameters that only shape a table; one an analysis does not take is dropped.
_DISPLAY_ONLY = {"order", "top"}
# Written for "no filter".
_NO_VALUE = {"", "all", "any", "none", "null", "default", "n a"}


def _round_top(param: Param, value: Any) -> Any:
    """A row count the catalog does not offer ("top": 1 or 3) becomes the
    smallest offered count that shows at least that many rows."""
    try:
        wanted = int(value)
    except (TypeError, ValueError):
        return value
    bigger = [c for c in param.choices if isinstance(c, int) and c >= wanted]
    return (
        min(bigger) if bigger else max(c for c in param.choices if isinstance(c, int))
    )


def resolve_plan(raw: Any, catalog: Catalog) -> Any:
    """The model's answer with the ``reasoning`` sentence removed and every
    free-text value mapped to a catalog value. Raises Unresolved for a value
    that matches nothing; anything else odd is left for ``validate_plan``."""
    if not isinstance(raw, dict):
        return raw
    plan = {k: v for k, v in raw.items() if k != "reasoning"}
    steps = plan.get("steps")
    if not isinstance(steps, list):
        return plan
    out_steps: list[Any] = []
    # Where each of the model's steps ended up, after a repeated table is
    # dropped, so a later step's reference still points at the right table.
    moved: dict[int, int] = {}
    for raw_index, step in enumerate(steps):
        if not isinstance(step, dict):
            moved[raw_index] = len(out_steps)
            out_steps.append(step)
            continue
        analysis = ANALYSIS_BY_ID.get(str(step.get("analysis_id")))
        params = step.get("params")
        if analysis is None or not isinstance(params, dict):
            moved[raw_index] = len(out_steps)
            out_steps.append(step)
            continue
        resolved: dict[str, Any] = {}
        params = {
            name: (
                {**value, "from_step": moved[value["from_step"]]}
                if isinstance(value, dict)
                and type(value.get("from_step")) is int
                and value["from_step"] in moved
                else value
            )
            for name, value in params.items()
        }
        for key in ("filter", "filters"):
            # {"filters": {"major": "Nursing"}}: the same filters, nested.
            nested = params.get(key)
            if isinstance(nested, dict) and key not in {
                p.name for p in analysis.params
            }:
                del params[key]
                for name, value in nested.items():
                    params.setdefault(name, value)
        if analysis.id == general.ANALYSIS_ID and "term" in params:
            # "term": "Fall 2024" on the general analysis is a one-term window.
            term = params.pop("term")
            params.setdefault("term_from", term)
            params.setdefault("term_to", term)
        for name, value in params.items():
            param = analysis.param(name)
            if param is None:
                if name in _DISPLAY_ONLY:
                    continue  # an order or row count this analysis does not take
                resolved[name] = value  # validate_plan names the bad parameter
                continue
            if isinstance(value, str) and _norm(value) in _NO_VALUE:
                continue  # "all", "any", "none": the parameter's default
            if name == "top" and value not in param.choices:
                value = _round_top(param, value)
            value = _resolve_value(param, value, catalog)
            if value is not None:
                resolved[name] = value
        if analysis.id == general.ANALYSIS_ID:
            _drop_default_window(resolved, catalog)
            _rank_something(resolved, steps, raw_index)
        candidate = {**step, "params": resolved}
        same = [
            i for i, earlier in enumerate(out_steps) if _same_table(candidate, earlier)
        ]
        if same and not _referenced(steps, raw_index):
            # The same table again, only ordered differently, and no later
            # step takes a value from it: asked once. A step a later step
            # reads from is kept, since its top row depends on its order.
            moved[raw_index] = same[0]
            continue
        moved[raw_index] = len(out_steps)
        out_steps.append(candidate)
    plan["steps"] = out_steps
    return plan


def _referenced(steps: list[Any], index: int) -> bool:
    """Whether a later step of the model's plan takes a value from step
    ``index``."""
    return any(
        isinstance(value, dict) and value.get("from_step") == index
        for later in steps[index + 1 :]
        if isinstance(later, dict) and isinstance(later.get("params"), dict)
        for value in later["params"].values()
    )


def _same_table(a: Any, b: Any) -> bool:
    """Two steps that compute the same table (the same analysis and
    parameters, apart from the order and the number of rows shown)."""
    if not (isinstance(a, dict) and isinstance(b, dict)):
        return False
    if a.get("analysis_id") != b.get("analysis_id"):
        return False

    def core(step: dict[str, Any]) -> dict[str, Any]:
        params = step.get("params")
        if not isinstance(params, dict):
            return {}
        return {k: v for k, v in params.items() if k not in _DISPLAY_ONLY}

    return core(a) == core(b)


def _rank_something(params: dict[str, Any], steps: list[Any], index: int) -> None:
    """A ranking with no grouping ranks one row. When the model asked for an
    order but no grouping, group by the column a later step takes from this
    one ("which major has the highest dropout rate, and its hardest class"),
    else by major, the unit people rank by when they name none ("where are
    we losing the most students")."""
    if params.get("group_by") or params.get("order") not in (
        "highest_first",
        "lowest_first",
    ):
        return
    if any(k in params for k in ("major", "college")):
        return  # one named major or college: nothing to rank
    referenced = [
        value.get("column")
        for later in steps[index + 1 :]
        if isinstance(later, dict) and isinstance(later.get("params"), dict)
        for value in later["params"].values()
        if isinstance(value, dict) and value.get("from_step") == index
    ]
    column = next((c for c in referenced if c in ("major", "college", "term")), None)
    params["group_by"] = column or "major"


def _drop_default_window(params: dict[str, Any], catalog: Catalog) -> None:
    """A one-term window on the current term is the measure's default for a
    term measure ("how many students now"); dropping it keeps the answer and
    its privacy checks identical to the plain question."""
    measure = general.MEASURES.get(str(params.get("measure")))
    current = _current_term(catalog)
    if (
        measure is not None
        and measure.scope == "term"
        and params.get("term_from") == current
        and params.get("term_to") in (current, None)
    ):
        params.pop("term_from", None)
        params.pop("term_to", None)
