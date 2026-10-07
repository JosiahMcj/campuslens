"""The planner evaluation: realistic owner-style questions with the plan each
must produce, and a runner that asks them through ``POST /explore``.

    python -m cabinet.explore.evalset --url http://127.0.0.1:8984 \\
        --email someone@example.edu --password-file pw.txt --label model-first

Each question carries one or more acceptable plans (a list of steps, each an
analysis id and the parameters that must be set, as catalog values), or
``None`` when no approved analysis answers it. A step is scored from the
reader's "How this was answered" lines (``params_plain`` in the response):
every expected parameter must be shown with the expected value, and any
other line must belong to a parameter that only shapes the table (order,
rows shown, minimum sizes) or be the latest term written out, which is the
default anyway. ``REF`` means the value must be carried from an earlier
step. The runner writes one JSON line per question and prints accuracy and
latency (median, 90th percentile).

It reads the school database (``CABINET_SCHOOL_DB``) for the catalog, the
same one the server answers from.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import statistics
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from cabinet.explore.catalog import (
    ANALYSIS_BY_ID,
    Catalog,
    catalog_for,
    connect_readonly,
)

REF = "REF"

Plan = list[tuple[str, dict[str, Any]]]


def _hc(**params: Any) -> Plan:
    return [("measure_by_group", {"measure": "headcount", **params})]


def _mbg(measure: str, **params: Any) -> Plan:
    return [("measure_by_group", {"measure": measure, **params})]


# (question, acceptable plans); None = not answerable from the catalog.
EVAL_SET: tuple[tuple[str, list[Plan] | None], ...] = (
    # Headcount, as the owner asked it live.
    ("how many cs students are enrolled", [_hc(major="CSCI")]),
    ("no how many computer science major students are there", [_hc(major="CSCI")]),
    ("how many students are in nursing", [_hc(major="NURS")]),
    ("how many students are enrolled this semester", [_hc()]),
    ("total enrollment", [_hc()]),
    ("how many freshmen", [_hc(class_level="Freshman")]),
    ("how many international students do we have", [_hc(residency="international")]),
    ("how many students in the college of engineering", [_hc(college="CEC")]),
    ("hw many psych majors r there", [_hc(major="PSYC")]),
    ("how many transfer students are enrolled right now", [_hc(admit_type="transfer")]),
    (
        "number of nursing students in fall 2024",
        [_hc(major="NURS", term_from="202510", term_to="202510")],
    ),
    ("how many kids live on campus", [_hc(housing="on_campus")]),
    ("how many students are part time", [_hc(load="part_time")]),
    (
        "how many seniors are in business administration",
        [_hc(class_level="Senior", major="BUAD")],
    ),
    ("enrollment by major", [_hc(group_by="major")]),
    (
        "how has enrollment changed over time",
        [[("enrollment_by_term", {})], _hc(group_by="term")],
    ),
    # GPA, grades, courses.
    ("which major has the lowest gpa", [[("gpa_by_major", {"order": "lowest_first"})]]),
    (
        "whats the avg gpa for nursing",
        [[("gpa_by_major", {"major": "NURS"})], _mbg("avg_gpa", major="NURS")],
    ),
    ("gpa by college", [[("gpa_by_college", {})], _mbg("avg_gpa", group_by="college")]),
    (
        "do athletes have lower gpas than everyone else",
        [_mbg("avg_gpa", group_by="athlete")],
    ),
    (
        "Which major has the lowest GPA? In that major, what is historically the "
        "hardest class, and which instructor has historically taught it?",
        [
            [
                ("gpa_by_major", {"order": "lowest_first"}),
                ("dfw_by_course", {"major_required": REF, "order": "highest_first"}),
                ("course_instructors", {"course": REF}),
            ]
        ],
    ),
    (
        "hardest classes for mech e majors",
        [[("dfw_by_course", {"major_required": "MEEN", "order": "highest_first"})]],
    ),
    (
        "whats the dfw rate in organic chem 1",
        [[("course_dfw_trend", {"course": "CHEM 2323"})]],
    ),
    (
        "who teaches thermodynamics 1",
        [[("course_instructors", {"course": "MEEN 3310"})]],
    ),
    (
        "easiest math classes",
        [[("dfw_by_course", {"subject": "MATH", "order": "lowest_first"})]],
    ),
    (
        "which 1000 level courses fail the most students",
        [[("dfw_by_course", {"level": "1000", "order": "highest_first"})]],
    ),
    (
        "first gen gap in college algebra",
        [[("equity_gap", {"course": "MATH 1314", "group": "first_generation"})]],
    ),
    (
        "is there a pell gap in nursing",
        [[("equity_gap", {"major": "NURS", "group": "pell"})]],
    ),
    (
        "what has alicia shelby taught",
        [[("instructor_history", {"instructor": "I-0001"})]],
    ),
    # Growth, registration, withdrawals, standing, graduation, holds, advising.
    (
        "which majors are growing fastest",
        [[("headcount_growth", {"order": "fastest_first"})]],
    ),
    ("has comp sci shrunk since 2020", [[("headcount_growth", {"major": "CSCI"})]]),
    (
        "is continuing registration down this spring compared to last spring",
        [
            [("continuing_registration_change", {})],
            [("continuing_registration_change", {"term": "202620"})],
        ],
    ),
    (
        "do online classes have more withdrawals than in person ones",
        [[("withdrawal_by_modality", {})]],
    ),
    (
        "which online course do students withdraw from the most",
        [[("withdrawal_by_course_modality", {"order": "highest_first"})]],
    ),
    (
        "which majors have the most kids on probation",
        [
            [("standing_by_major", {"order": "highest_first"})],
            _mbg("probation_rate", group_by="major", order="highest_first"),
        ],
    ),
    (
        "how many nursing grads per year",
        [
            [("graduations", {"major": "NURS", "group_by": "year"})],
            _mbg("graduates", major="NURS", group_by="term"),
        ],
    ),
    ("which office has the most holds", [[("holds_by_office", {})]]),
    (
        "which majors see advisors the least",
        [
            [("advising_coverage", {"order": "lowest_first"})],
            _mbg("advising_rate", group_by="major", order="lowest_first"),
        ],
    ),
    (
        "credit hours by term",
        [[("credit_hours_by_term", {})]],
    ),
    # The general measure.
    (
        "what majors have teh highest drop out rate",
        [_mbg("dropout_rate", group_by="major", order="highest_first")],
    ),
    (
        "retention for first gen students",
        [
            _mbg("retention_rate", group_by="first_generation"),
            _mbg("retention_rate", first_generation="first_generation"),
        ],
    ),
    (
        "6 year grad rate for pell kids by college",
        [_mbg("grad_rate_6yr", group_by="college", pell="pell")],
    ),
    ("what share of our students get pell", [_mbg("pell_share")]),
    ("dropout rate by gender", [_mbg("dropout_rate", group_by="gender")]),
    ("avg time to graduate by major", [_mbg("time_to_degree", group_by="major")]),
    (
        "what percent of students are international",
        [_mbg("international_share")],
    ),
    # Chained follow-ups.
    (
        "which major has the highest dropout rate and what's its hardest class",
        [
            [
                ("measure_by_group", {"measure": "dropout_rate", "group_by": "major"}),
                ("dfw_by_course", {"major_required": REF}),
            ]
        ],
    ),
    (
        "hardest class in chemistry and who taught it",
        [
            [
                ("dfw_by_course", {"major_required": "CHEM"}),
                ("course_instructors", {"course": REF}),
            ],
            [
                ("dfw_by_course", {"subject": "CHEM"}),
                ("course_instructors", {"course": REF}),
            ],
        ],
    ),
    # Casual, open-ended wording.
    ("whats our biggest major", [_hc(group_by="major", order="highest_first")]),
    (
        "how are the engineering students doing grade wise",
        [
            [("gpa_by_college", {"college": "CEC"})],
            [("gpa_by_major", {"college": "CEC"})],
            _mbg("avg_gpa", college="CEC"),
            _mbg("avg_gpa", college="CEC", group_by="major"),
            _mbg("avg_gpa", college="CEC", group_by="major")
            + _mbg("dfw_rate", college="CEC", group_by="major"),
        ],
    ),
    ("whats our first year retention rate", [_mbg("retention_rate")]),
    (
        "where are we losing the most students",
        [
            _mbg("dropout_rate", group_by="major", order="highest_first"),
            _mbg("dropout_rate", group_by="college", order="highest_first"),
        ],
    ),
    ("compare men and women gpa", [_mbg("avg_gpa", group_by="gender")]),
    (
        "how many nursing students are on probation",
        [
            [("standing_by_major", {"major": "NURS"})],
            _mbg("probation_rate", major="NURS"),
        ],
    ),
    # Not answerable from the approved analyses.
    ("who is the best football coach in the conference", None),
    ("what will tuition be next year", None),
)

# Written after the prompt was final and run once, never tuned on: the check
# that the main set's score is not an artifact of tuning against it.
HELD_OUT: tuple[tuple[str, list[Plan] | None], ...] = (
    ("how many students do we have in total", [_hc()]),
    ("how many accounting majors", [_hc(major="ACCT")]),
    ("how many juniors are in nursing", [_hc(class_level="Junior", major="NURS")]),
    ("how many pell students are enrolled", [_hc(pell="pell")]),
    (
        "how many first gen students do we have",
        [_hc(first_generation="first_generation")],
    ),
    ("number of athletes", [_hc(athlete="athlete")]),
    (
        "what's the gpa of honors students",
        [_mbg("avg_gpa", group_by="honors"), _mbg("avg_gpa", honors="honors")],
    ),
    (
        "which college has the best gpa",
        [
            [("gpa_by_college", {"order": "highest_first"})],
            _mbg("avg_gpa", group_by="college", order="highest_first"),
        ],
    ),
    (
        "toughest courses in nursing",
        [[("dfw_by_course", {"major_required": "NURS", "order": "highest_first"})]],
    ),
    ("who taught calculus 1", [[("course_instructors", {"course": "MATH 2413"})]]),
    (
        "dfw trend for college algebra",
        [[("course_dfw_trend", {"course": "MATH 1314"})]],
    ),
    ("graduation rate by race", [_mbg("grad_rate_6yr", group_by="race_ethnicity")]),
    ("retention rate by college", [_mbg("retention_rate", group_by="college")]),
    (
        "which majors have the highest stop out rate",
        [_mbg("stop_out_rate", group_by="major", order="highest_first")],
    ),
    (
        "how many students graduated in 2024-2025",
        [
            [("graduations", {"academic_year": "2024-2025"})],
            [("graduations", {"academic_year": "2024-2025", "group_by": "year"})],
        ],
    ),
    ("what percent of our students are part time", [_mbg("part_time_share")]),
    ("online vs in person dfw rates", [_mbg("dfw_rate", group_by="modality")]),
    (
        "show me holds for financial reasons",
        [[("holds_by_office", {"category": "financial"})]],
    ),
    ("advising coverage in biology", [[("advising_coverage", {"major": "BIOL"})]]),
    ("whats the average credit load", [_mbg("avg_credits_attempted")]),
    (
        "which major has the lowest retention and who teaches its hardest course",
        [
            _mbg("retention_rate", group_by="major", order="lowest_first")
            + [
                ("dfw_by_course", {"major_required": REF}),
                ("course_instructors", {"course": REF}),
            ]
        ],
    ),
    ("can you list every student on probation", None),
)

# Questions that used to be refused (owner direction 2026-10-07): what will
# happen, at-risk groups, holds. Each is answered from the records, so each
# lists the historical plans that answer it. ``None`` here is a request that
# must not be answered at all (off-topic, or a single student).
FORWARD_SET: tuple[tuple[str, list[Plan] | None], ...] = (
    (
        "how many students have holds and will drop",
        [
            _hc(hold="hold")
            + _mbg("dropout_rate", group_by="hold")
            + _mbg("stop_out_rate", group_by="hold"),
            _hc(hold="hold") + _mbg("dropout_rate", group_by="hold"),
            _hc(hold="hold") + _mbg("stop_out_rate", group_by="hold"),
            _mbg("dropout_rate", group_by="hold"),
        ],
    ),
    ("how many students have holds", [_hc(hold="hold")]),
    (
        "will enrollment fall next year",
        [[("enrollment_by_term", {})], _hc(group_by="term")],
    ),
    ("what % will graduate", [_mbg("grad_rate_6yr"), _mbg("grad_rate_4yr")]),
    (
        "at-risk students in nursing",
        [
            _mbg("dropout_rate", major="NURS") + _mbg("stop_out_rate", major="NURS"),
            _mbg("dropout_rate", major="NURS"),
            _mbg("stop_out_rate", major="NURS"),
        ],
    ),
    (
        "how many nursing students are likely to drop out next year",
        [
            _hc(major="NURS")
            + _mbg("dropout_rate", major="NURS")
            + _mbg("stop_out_rate", major="NURS"),
            _hc(major="NURS") + _mbg("dropout_rate", major="NURS"),
            _mbg("dropout_rate", major="NURS"),
        ],
    ),
    (
        "will retention go down for pell students",
        [
            _mbg("retention_rate", group_by="pell"),
            _mbg("retention_rate", pell="pell"),
            _mbg("retention_rate", group_by="entry_cohort", pell="pell"),
        ],
    ),
    (
        "what is the dropout rate for students with holds",
        [_mbg("dropout_rate", group_by="hold"), _mbg("dropout_rate", hold="hold")],
    ),
    ("code me a website", None),
    ("write a poem about graduation", None),
    ("what's the weather today", None),
    ("what is S-1234's gpa", None),
)


def _bva(**params: Any) -> list[Plan]:
    """Budget against actual, with or without the defaults written out (this
    fiscal year, by division)."""
    plans: list[Plan] = []
    for extra in ({}, {"fiscal_year": "FY2026"}):
        for by in ({}, {"by": "division"}):
            if "by" in params and by:
                continue
            if "fiscal_year" in params and extra:
                continue
            plans.append([("budget_vs_actual", {**params, **extra, **by})])
    return plans


def _rev(**params: Any) -> list[Plan]:
    plans: list[Plan] = [[("revenue_by_source", params)]]
    if "fiscal_year" not in params:
        plans.append([("revenue_by_source", {"fiscal_year": "FY2026"})])
    return plans


# The finance office's questions (owner direction 2026-10-07: the university's
# budget, and student accounts past due). The first eight are the owner's.
FINANCE_SET: tuple[tuple[str, list[Plan] | None], ...] = (
    ("What is our budget vs actual this year?", _bva()),
    (
        "Which departments are over budget?",
        _bva(by="department", over_budget="yes"),
    ),
    ("What is our tuition discount rate trend?", [[("tuition_discount", {})]]),
    (
        "How much net tuition revenue did we make last year?",
        [[("tuition_discount", {"fiscal_year": "FY2025"})]],
    ),
    ("How much is past due?", [_mbg("past_due_balance")]),
    (
        "How many students are more than 90 days past due?",
        [_mbg("past_due_90_students")],
    ),
    (
        "What is the on-time payment rate by college?",
        [_mbg("on_time_payment_rate", group_by="college")],
    ),
    ("How many students are on payment plans?", [_mbg("payment_plan_share")]),
    ("is athletics over budget", _bva(over_budget="yes") + _bva()),
    ("where does our money come from", _rev()),
    ("revenue vs budget for fy2024", _rev(fiscal_year="FY2024")),
    ("past due balances by aging", [_mbg("past_due_balance", group_by="aging")]),
    (
        "whats the avg past due balance for pell students",
        [
            _mbg("avg_balance_owed", pell="pell"),
            _mbg("avg_balance_owed", group_by="pell"),
        ],
    ),
    (
        "collection rate by class level",
        [_mbg("collection_rate", group_by="class_level")],
    ),
    (
        "how many first gen students are past due",
        [_mbg("past_due_students", first_generation="first_generation")],
    ),
    (
        "spending by category last year",
        [[("budget_vs_actual", {"by": "category", "fiscal_year": "FY2025"})]],
    ),
    ("net tuition revenue each year", [[("tuition_discount", {})]]),
    (
        "on time payment rate pell vs non pell",
        [_mbg("on_time_payment_rate", group_by="pell")],
    ),
)

# Written after the rules and the prompt were final for the finance set;
# nothing was tuned on it.
FINANCE_HELD_OUT: tuple[tuple[str, list[Plan] | None], ...] = (
    (
        "how far over budget is athletics this year",
        _bva() + _bva(over_budget="yes") + _bva(by="department"),
    ),
    (
        "how much did we give away in institutional aid in fy2023",
        [
            [("tuition_discount", {"fiscal_year": "FY2023"})],
            [("revenue_by_source", {"fiscal_year": "FY2023"})],
        ],
    ),
    ("what share of our students pay late", [_mbg("on_time_payment_rate")]),
    ("total outstanding receivables", [_mbg("past_due_balance")]),
    (
        "which college has the most students behind on their bills",
        [
            _mbg("past_due_students", group_by="college"),
            _mbg("past_due_students", group_by="college", order="highest_first"),
        ],
    ),
    (
        "how did gifts and grants come in against budget last year",
        [[("revenue_by_source", {"fiscal_year": "FY2025"})]],
    ),
    (
        "what was the discount rate in 2023-24",
        [[("tuition_discount", {"fiscal_year": "FY2024"})]],
    ),
    (
        "payment plan use by class level",
        [_mbg("payment_plan_share", group_by="class_level")],
    ),
    (
        "are we spending more than we planned on technology",
        _bva(by="category") + _bva(by="category", over_budget="yes"),
    ),
    (
        "how much money do pell students owe that's overdue",
        [
            _mbg("past_due_balance", pell="pell"),
            _mbg("past_due_balance", group_by="pell"),
        ],
    ),
)


# Graduate outcomes (the owner's questions of 2026-10-07, then casual
# variants): salaries, grades and earnings, graduate and medical school,
# employment, alumni giving. Written before the rules and the compact catalog
# were extended; the last two are not answerable (no employer names, no loan
# records).
OUTCOMES_SET: tuple[tuple[str, list[Plan] | None], ...] = (
    (
        "What do various majors make after graduation?",
        [_mbg("median_salary", group_by="major")],
    ),
    (
        "Do grades matter for earning potential?",
        [_mbg("median_salary", group_by="gpa_band")],
    ),
    (
        "What % of biology students got into med school?",
        [_mbg("med_acceptance_rate", major="BIOL")],
    ),
    ("What % of graduates went to grad school?", [_mbg("grad_school_rate")]),
    ("What % of alumni have given back?", [_mbg("giving_rate")]),
    (
        "Which majors give back the most?",
        [_mbg("giving_rate", group_by="major", order="highest_first")],
    ),
    ("Do athletes give back more?", [_mbg("giving_rate", group_by="athlete")]),
    (
        "How many nursing grads are employed?",
        [_mbg("employment_rate", major="NURS")],
    ),
    ("starting salary for comp sci grads", [_mbg("median_salary", major="CSCI")]),
    ("whats our med school acceptance rate", [_mbg("med_acceptance_rate")]),
    (
        "do honors students earn more after college",
        [_mbg("median_salary", group_by="honors")],
    ),
    (
        "which college has the highest alumni giving",
        [
            _mbg("giving_rate", group_by="college", order="highest_first"),
            _mbg("total_giving", group_by="college", order="highest_first"),
        ],
    ),
    ("average gift from alumni", [_mbg("avg_gift")]),
    ("how much have alumni donated in total", [_mbg("total_giving")]),
    ("employment rate by major", [_mbg("employment_rate", group_by="major")]),
    ("whats the first destination survey knowledge rate", [_mbg("knowledge_rate")]),
    (
        "do first gen grads make less money",
        [_mbg("median_salary", group_by="first_generation")],
    ),
    (
        "med school acceptance by gpa",
        [_mbg("med_acceptance_rate", group_by="gpa_band")],
    ),
    (
        "what percent of psych majors go on to grad school",
        [_mbg("grad_school_rate", major="PSYC")],
    ),
    ("which companies hire the most of our graduates", None),
    ("what is the average student loan debt of our graduates", None),
)

SETS = {
    "main": EVAL_SET,
    "held-out": HELD_OUT,
    "forward": FORWARD_SET,
    "outcomes": OUTCOMES_SET,
    "finance": FINANCE_SET,
    "finance-held-out": FINANCE_HELD_OUT,
}

# Parameters that only shape the table; an extra one is not a wrong plan.
LENIENT = {
    "order",
    "top",
    "min_students",
    "min_sections",
    "min_terms",
    "min_start",
    "min_online",
}


def _latest_regular(catalog: Catalog) -> str:
    v = catalog.vocab
    return [t for t, s in v.term_season.items() if s != "Summer"][-1]


def _expected_lines(
    catalog: Catalog, analysis_id: str, params: dict[str, Any]
) -> tuple[list[str], list[str], set[str]]:
    """(lines that must be shown, parameter labels that must come from an
    earlier step, lines that may also be shown)."""
    analysis = ANALYSIS_BY_ID[analysis_id]
    must: list[str] = []
    refs: list[str] = []
    for name, value in params.items():
        param = analysis.param(name)
        assert param is not None, (analysis_id, name)
        if value == REF:
            refs.append(name)
            continue
        line = catalog.shown(param, catalog.normalize(param, value))
        if line is not None:
            must.append(line)
    may: set[str] = set()
    for param in analysis.params:
        if param.name in LENIENT or (
            param.default is not None and param.name not in params
        ):
            for value in catalog.allowed(param) if param.kind == "choice" else ():
                line = catalog.shown(param, value)
                if line is not None:
                    may.add(line)
        if param.default is not None and param.name not in params:
            line = catalog.shown(param, param.default)
            if line is not None:
                may.add(line)
    if analysis_id == "measure_by_group" and not (
        {"term_from", "term_to"} & set(params)
    ):
        latest = _latest_regular(catalog)
        for name in ("term_from", "term_to"):
            param = analysis.param(name)
            assert param is not None
            line = catalog.shown(param, latest)
            if line is not None:
                may.add(line)
    return must, refs, may


def score(
    catalog: Catalog, expected: list[Plan] | None, body: dict[str, Any]
) -> tuple[bool, str]:
    """(correct, why) for one /explore response."""
    steps = body.get("steps") or []
    if expected is None:
        # A student-level question answered with totals for students like
        # that ("redirect") is protected as expected: the student is never
        # answered.
        ok = not steps or bool(body.get("redirect"))
        return (
            ok,
            "not answered, as expected" if ok else "answered a question it should not",
        )
    if not steps:
        return False, "not answered: " + str(body.get("message"))
    reasons: list[str] = []
    for plan in expected:
        if len(plan) != len(steps):
            reasons.append(f"{len(steps)} steps, expected {len(plan)}")
            continue
        why = ""
        for (analysis_id, params), got in zip(plan, steps, strict=True):
            if got.get("analysis_id") != analysis_id:
                why = f"ran {got.get('analysis_id')}, expected {analysis_id}"
                break
            shown = [str(s) for s in got.get("params_plain") or []]
            must, refs, may = _expected_lines(catalog, analysis_id, params)
            missing = [m for m in must if m not in shown]
            if missing:
                why = f"{analysis_id}: missing {missing}; shown {shown}"
                break
            carried = [s for s in shown if "(from step" in s]
            if len(carried) < len(refs):
                why = f"{analysis_id}: expected a value carried from an earlier step"
                break
            extra = [
                s for s in shown if s not in must and s not in may and s not in carried
            ]
            if extra:
                why = f"{analysis_id}: unexpected {extra}"
                break
        if not why:
            return True, "matched"
        reasons.append(why)
    return False, " | ".join(reasons)


def _client(
    url: str, email: str, password: str
) -> tuple[urllib.request.OpenerDirector, str]:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    request = urllib.request.Request(
        url + "/auth/login",
        data=json.dumps({"email": email, "password": password}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with opener.open(request, timeout=30) as response:
        body = json.loads(response.read())
    return opener, str(body.get("csrf_token") or body.get("csrf") or "")


def _ask(
    opener: urllib.request.OpenerDirector, url: str, csrf: str, question: str
) -> dict[str, Any]:
    request = urllib.request.Request(
        url + "/explore",
        data=json.dumps({"question": question}).encode(),
        headers={"Content-Type": "application/json", "X-CSRF-Token": csrf},
        method="POST",
    )
    with opener.open(request, timeout=180) as response:
        result: dict[str, Any] = json.loads(response.read())
        return result


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round(q * (len(ordered) - 1))))
    return ordered[index]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m cabinet.explore.evalset")
    parser.add_argument("--url", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password-file", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--out", default=None, help="JSON lines, one per question")
    parser.add_argument("--only", type=int, nargs="*", help="question indexes")
    parser.add_argument("--set", choices=sorted(SETS), default="main")
    args = parser.parse_args(argv)
    password = Path(args.password_file).read_text(encoding="utf-8").strip()
    con = connect_readonly()
    try:
        catalog = catalog_for(con)
    finally:
        con.close()
    opener, csrf = _client(args.url, args.email, password)
    out = open(args.out, "a", encoding="utf-8") if args.out else None  # noqa: SIM115
    correct = 0
    latencies: list[float] = []
    chosen = [
        (i, item)
        for i, item in enumerate(SETS[args.set])
        if not args.only or i in args.only
    ]
    for index, (question, expected) in chosen:
        started = time.monotonic()
        body = _ask(opener, args.url, csrf, question)
        seconds = time.monotonic() - started
        ok, why = score(catalog, expected, body)
        correct += ok
        latencies.append(seconds)
        record = {
            "label": args.label,
            "index": index,
            "question": question,
            "correct": ok,
            "why": why,
            "seconds": round(seconds, 2),
            "planner": body.get("planner"),
            "fallbacks": body.get("fallbacks"),
            "answer": [s.get("text") for s in body.get("answer") or []],
        }
        print(
            f"{'OK ' if ok else 'BAD'} {seconds:5.1f}s {body.get('planner')!s:8} "
            f"[{index}] {question} -- {why if not ok else ''}",
            flush=True,
        )
        if out:
            out.write(json.dumps(record) + "\n")
            out.flush()
    if out:
        out.close()
    n = len(chosen)
    print(
        f"{args.label}: {correct}/{n} correct ({100 * correct / n:.0f}%), "
        f"median {statistics.median(latencies):.1f} s, "
        f"p90 {_percentile(latencies, 0.9):.1f} s"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
