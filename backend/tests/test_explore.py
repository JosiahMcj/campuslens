"""Tests for Explore: governed specific questions over Demonstration University.

Most tests run against the generator at --scale 0.05 in a tmp directory
(generated once per session, never the repo's var/). They cover every
analysis returning aggregates only (no S- id anywhere in any response),
suppression below 10 students, the owner's chained example, the instructor
role rule, refusals recorded before any planning, the rule planner's
phrasings, the model planner's fallbacks and recordings, the numeral
validator on the writer, the audit events, and the hash chain.

The full-scale check (``test_full_scale_*``) runs only with
CABINET_EXPLORE_FULL=1 against var/school/school.db (``make explore-check``,
after ``make school-data``): the owner's example must return Mechanical
Engineering, MEEN 3310 Thermodynamics I, and I-0001 Alicia Shelby (fictional)
with the VERIFY.md values, and five more planted facts must come back from
plain-English questions.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cabinet.analysts import OutputRejected
from cabinet.api import create_app
from cabinet.audit import verify_db
from cabinet.auth import AuthStore
from cabinet.explore.answer import (
    MAX_SENTENCES,
    SOURCE_MODEL,
    SOURCE_TEMPLATE,
    check_sentence,
    template_answer,
    write_answer,
)
from cabinet.explore.catalog import (
    ANALYSES,
    ANALYSIS_BY_ID,
    NOT_INSTALLED_MESSAGE,
    Catalog,
    catalog_for,
    connect_readonly,
)
from cabinet.explore.execute import StepResult, execute
from cabinet.explore.planner import (
    EXAMPLE_QUESTIONS,
    OWNER_EXAMPLE,
    OWNER_SHORT,
    RULE_PHRASINGS,
    UNANSWERABLE_MESSAGE,
    PlanInvalid,
    Ref,
    Step,
    load_recorded_plan,
    plan_question,
    rule_plan,
    rule_plan_detail,
    validate_plan,
)
from cabinet.explore.privacy import refusal_for
from cabinet.provider import (
    Explanation,
    FakeProvider,
    RecordingProvider,
    ReplayProvider,
)
from conftest import make_authenticated_client

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATE = REPO_ROOT / "data" / "school" / "generate.py"
FULL_DB = REPO_ROOT / "var" / "school" / "school.db"
STUDENT_ID = re.compile(r"\bS-\d+")

REFUSED_QUESTIONS = (
    "Which students are in counseling?",
    "What is S-1234's GPA?",
    "Which students will drop out?",
    "Who has talked to the chaplain this term?",
    "Predict whether student 100245 will graduate.",
)


# --- fixtures ----------------------------------------------------------------


@pytest.fixture(scope="session")
def school_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("school") / "school.db"
    proc = subprocess.run(
        [sys.executable, str(GENERATE), "--scale", "0.05", "--out", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return out


@pytest.fixture(autouse=True)
def _school_env(school_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CABINET_SCHOOL_DB", str(school_db))
    monkeypatch.setenv("CABINET_PROVIDER", "replay")
    monkeypatch.delenv("CABINET_RECORD", raising=False)


@pytest.fixture
def con(school_db: Path) -> Iterator[sqlite3.Connection]:
    connection = connect_readonly(school_db)
    yield connection
    connection.close()


@pytest.fixture
def catalog(con: sqlite3.Connection, school_db: Path) -> Catalog:
    return catalog_for(con, school_db)


@pytest.fixture
def app() -> FastAPI:
    return create_app()


def _client(app: FastAPI, role: str) -> TestClient:
    return make_authenticated_client(app, role=role)


def _ask(client: TestClient, question: str) -> dict[str, Any]:
    response = client.post("/explore", json={"question": question})
    assert response.status_code == 200, response.text
    assert not STUDENT_ID.search(response.text), response.text
    body: dict[str, Any] = response.json()
    return body


def _events(app: FastAPI, event_type: str | None = None) -> list[dict[str, Any]]:
    store: AuthStore = app.state.auth
    institution = store.institution_by_slug("bootstrap")
    assert institution is not None
    return store.audit_events(int(institution["id"]), event_type)


def _sample_params(analysis_id: str) -> dict[str, Any]:
    samples: dict[str, dict[str, Any]] = {
        "course_dfw_trend": {"course": "MEEN 3310"},
        "course_instructors": {"course": "MEEN 3310"},
        "instructor_history": {"instructor": "I-0001"},
        "equity_gap": {"course": "MATH 1314"},
    }
    return samples.get(analysis_id, {})


class StubProvider:
    """A live-looking provider (name ``chat``) that answers with fixed text
    per role and records what it received."""

    name = "chat"
    model_label = "stub"

    def __init__(self, texts: dict[str, str]) -> None:
        self.texts = texts
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def explain(self, findings: dict[str, Any], role: str) -> Explanation:
        self.calls.append((role, findings))
        return Explanation(self.texts[role], self.name, self.model_label)


# --- the catalog and the executor --------------------------------------------


def test_catalog_has_the_sixteen_analyses(catalog: Catalog) -> None:
    assert len(ANALYSES) == 16
    for analysis in ANALYSES:
        assert analysis.title and analysis.description and analysis.fields_read
        assert analysis.columns
        for param in analysis.params:
            assert catalog.allowed(param) or param.kind != "choice"
    assert {a.id for a in ANALYSES if a.instructor_level} == {
        "course_instructors",
        "instructor_history",
    }


def test_every_analysis_runs_and_returns_aggregates_only(
    con: sqlite3.Connection, catalog: Catalog
) -> None:
    for analysis in ANALYSES:
        steps = [Step(analysis.id, _sample_params(analysis.id))]
        results = execute(steps, con, catalog, "executive")
        assert results[0].error is None, (analysis.id, results[0].error)
        blob = json.dumps([r.table() for r in results] + [r.notes for r in results])
        assert not STUDENT_ID.search(blob), analysis.id
        keys = {c.key for c in results[0].columns}
        assert "student_id" not in keys
        for row in results[0].rows:
            assert set(row) <= keys, analysis.id


def test_every_rule_phrasing_answers_over_the_api_without_student_ids(
    app: FastAPI,
) -> None:
    client = _client(app, "executive")
    for question, expected in RULE_PHRASINGS:
        body = _ask(client, question)
        assert body["refused"] is False, question
        assert [s["analysis_id"] for s in body["steps"]] == list(expected), question
        assert 1 <= len(body["answer"]) <= 4, question
        assert body["source"] == SOURCE_TEMPLATE
        for step in body["steps"]:
            assert step["aggregate_only"] is True
            assert step["fields_read"]


def test_groups_under_ten_students_are_withheld_and_not_ranked(
    con: sqlite3.Connection, catalog: Catalog
) -> None:
    # Per term: the DFW cells are withheld exactly when the term had fewer
    # than 10 distinct graded students (recomputed here from raw rows).
    course = "MEEN 3310"
    truth = dict(
        con.execute(
            """SELECT s.term_code, COUNT(DISTINCT r.student_id)
               FROM sections s JOIN section_registrations r USING (section_id)
               JOIN final_grades g USING (registration_id)
               WHERE s.course_id = ? AND g.grade IN
                 ('A','A-','B+','B','B-','C+','C','C-','D+','D','F','W')
               GROUP BY s.term_code""",
            (course,),
        ).fetchall()
    )
    result = execute(
        [Step("course_dfw_trend", {"course": course})], con, catalog, "executive"
    )[0]
    withheld = 0
    for row in result.rows:
        if row["term"] == "All terms":
            continue
        small = truth[row["term"]] < 10
        withheld += small
        assert (row["dfw_rate"] == "fewer than 10") == small, row
        assert (row["dfw"] == "fewer than 10") == small, row
    assert withheld > 0  # the reduced scale always has small terms
    # Rankings: no ranked major has fewer than 10 students, and a note
    # says how many were withheld.
    ranked = execute(
        [Step("gpa_by_major", {"min_students": 10})], con, catalog, "executive"
    )[0]
    counts = dict(
        con.execute(
            """WITH last AS (SELECT r.student_id, r.program_code
               FROM student_term_records r
                 WHERE r.term_code = (SELECT MAX(term_code) FROM student_term_records r2
                                      WHERE r2.student_id = r.student_id)
                   AND r.cumulative_gpa IS NOT NULL)
               SELECT ap.major_code, COUNT(*) FROM last
               JOIN academic_programs ap USING (program_code) GROUP BY 1"""
        ).fetchall()
    )
    small_majors = {m for m, n in counts.items() if n < 10}
    assert small_majors
    shown = {row["major"] for row in ranked.rows}
    assert not shown & small_majors
    assert all(row["students"] >= 10 for row in ranked.rows)
    assert any("fewer than 10" in note for note in ranked.notes)


def test_the_owner_example_is_one_chained_plan(
    con: sqlite3.Connection, catalog: Catalog, app: FastAPI
) -> None:
    steps = rule_plan(OWNER_EXAMPLE, catalog)
    assert steps is not None
    assert [s.analysis_id for s in steps] == [
        "gpa_by_major",
        "dfw_by_course",
        "course_instructors",
    ]
    assert steps[0].params["order"] == "lowest_first"
    assert steps[1].params["major_required"] == Ref(0, "major")
    assert steps[2].params["course"] == Ref(1, "course")
    results = execute(steps, con, catalog, "executive")
    lowest = results[0].rows[0]["major"]
    assert results[1].params["major_required"] == lowest
    assert results[2].params["course"] == results[1].rows[0]["course"]
    assert any("(from step 1)" in line for line in results[1].params_plain)
    body = _ask(_client(app, "executive"), OWNER_EXAMPLE)
    assert len(body["steps"]) == 3
    assert body["steps"][2]["table"]["columns"][0]["key"] == "instructor"
    # Every claim points at a real cell holding a digit.
    for sentence in body["answer"]:
        for claim in sentence["claims"]:
            table = body["steps"][claim["table"]]["table"]
            keys = [c["key"] for c in table["columns"]]
            cell = table["rows"][claim["row"]][keys.index(claim["column"])]
            assert re.search(r"\d", str(cell))


@pytest.mark.parametrize("role", ["staff", "reviewer"])
def test_staff_and_reviewers_get_the_course_without_instructor_rows(
    app: FastAPI, role: str
) -> None:
    body = _ask(_client(app, role), OWNER_EXAMPLE)
    third = body["steps"][2]
    keys = [c["key"] for c in third["table"]["columns"]]
    assert "instructor" not in keys and "name" not in keys
    assert third["instructor_rows_withheld"] is True
    assert "executive and admin roles only" in third["notes"][0]
    assert "executive and admin roles only" in " ".join(
        s["text"] for s in body["answer"]
    )
    assert "fictional" not in json.dumps(third["table"])
    refused = [
        e
        for e in _events(app, "data.refused")
        if e["payload"].get("category") == "instructor_level"
    ]
    assert refused and refused[-1]["payload"]["role"] == role
    history = _ask(_client(app, role), "What has Alicia Shelby taught?")
    assert history["steps"][0]["table"]["rows"] == []
    assert "executive and admin roles only" in history["answer"][0]["text"]


@pytest.mark.parametrize("role", ["executive", "admin"])
def test_executive_and_admin_get_instructor_rows(app: FastAPI, role: str) -> None:
    body = _ask(_client(app, role), "Who has taught MEEN 3310?")
    rows = body["steps"][0]["table"]["rows"]
    assert rows and all(str(r[0]).startswith("I-") for r in rows)
    assert all("(fictional)" in str(r[1]) for r in rows)


def test_aid_role_and_missing_csrf_are_refused(app: FastAPI) -> None:
    aid = _client(app, "aid")
    assert (
        aid.post(
            "/explore", json={"question": "What is enrollment by term?"}
        ).status_code
        == 403
    )
    assert aid.get("/explore/catalog").status_code == 403
    executive = _client(app, "executive")
    del executive.headers["X-CSRF-Token"]
    assert executive.post("/explore", json={"question": "x"}).status_code == 403


@pytest.mark.parametrize("question", REFUSED_QUESTIONS)
def test_refusals_are_recorded_before_any_planning(
    app: FastAPI, question: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cabinet.explore.api as explore_api

    def never(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("planning or a provider ran for a refused question")

    monkeypatch.setattr(explore_api, "plan_question", never)
    monkeypatch.setattr(explore_api, "provider_from_env", never)
    before = len(_events(app))
    body = _ask(_client(app, "executive"), question)
    assert body["refused"] is True and body["steps"] == [] and body["answer"] == []
    assert body["message"]
    new = _events(app)[before:]
    types = [e["type"] for e in new]
    assert types == ["question.asked", "data.refused"]
    assert "S-1234" not in json.dumps(new)
    assert new[1]["payload"]["category"] in (
        "counseling",
        "individual_student",
        "prediction",
    )


def test_refusal_rules() -> None:
    for question in REFUSED_QUESTIONS:
        assert refusal_for(question) is not None, question
    for question, _ in RULE_PHRASINGS:
        assert refusal_for(question) is None, question
    assert refusal_for("What is the GPA in Christian Ministry?") is None


def test_unmappable_question_suggests_three_examples(app: FastAPI) -> None:
    body = _ask(_client(app, "executive"), "What is the weather on campus?")
    assert body["refused"] is False
    assert body["message"] == UNANSWERABLE_MESSAGE
    assert len(body["suggestions"]) == 3
    assert set(body["suggestions"]) <= set(EXAMPLE_QUESTIONS)
    answered = _events(app, "explore.answered")[-1]["payload"]
    assert answered["answered"] is False


def test_catalog_route(app: FastAPI) -> None:
    body = _client(app, "staff").get("/explore/catalog").json()
    assert len(body["analyses"]) == 16
    assert all(set(a) == {"id", "title", "description"} for a in body["analyses"])
    assert len(body["examples"]) == 12
    assert body["fictional"] is True


def test_missing_school_database_reports_and_nothing_else(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CABINET_SCHOOL_DB", str(tmp_path / "absent.db"))
    client = _client(app, "executive")
    before = len(_events(app))
    for response in (
        client.post("/explore", json={"question": OWNER_EXAMPLE}),
        client.get("/explore/catalog"),
    ):
        assert response.status_code == 503
        assert response.json() == {"available": False, "message": NOT_INSTALLED_MESSAGE}
    assert len(_events(app)) == before


# --- the planners ------------------------------------------------------------


def test_rule_planner_maps_every_phrasing(catalog: Catalog) -> None:
    assert len(RULE_PHRASINGS) >= 30
    covered: set[str] = set()
    for question, expected in RULE_PHRASINGS:
        steps = rule_plan(question, catalog)
        assert steps is not None, question
        assert tuple(s.analysis_id for s in steps) == expected, question
        covered.update(expected)
    assert covered == set(ANALYSIS_BY_ID)


# The eight planted facts in data/school/VERIFY.md, asked the way people ask:
# other words ("worst", "toughest", "lowest-performing", "who teaches",
# "historically") and typos ("teh"). Each maps to the plan that reaches the
# planted fact: analysis ids in order, with the parameters that matter.
_Plan = list[tuple[str, dict[str, Any]]]
_OWNER_CHAIN: _Plan = [
    ("gpa_by_major", {"order": "lowest_first"}),
    (
        "dfw_by_course",
        {
            "major_required": {"from_step": 0, "column": "major"},
            "order": "highest_first",
        },
    ),
    ("course_instructors", {"course": {"from_step": 1, "column": "course"}}),
]
_LOWEST_GPA: _Plan = [("gpa_by_major", {"order": "lowest_first"})]
_MEEN_HARDEST: _Plan = [
    ("dfw_by_course", {"major_required": "MEEN", "order": "highest_first"})
]
_THERMO: _Plan = [("course_instructors", {"course": "MEEN 3310"})]
_OCHEM_TREND: _Plan = [("course_dfw_trend", {"course": "CHEM 2323"})]
_OCHEM_TEACHERS: _Plan = [("course_instructors", {"course": "CHEM 2323"})]
_ALGEBRA_GAP: _Plan = [
    ("equity_gap", {"group": "first_generation", "course": "MATH 1314"})
]
_SPRING: _Plan = [("continuing_registration_change", {})]
_SPRING_2026: _Plan = [("continuing_registration_change", {"term": "202620"})]
_GROWTH: _Plan = [("headcount_growth", {})]
_ONLINE: _Plan = [("withdrawal_by_modality", {})]
_ONLINE_LARGEST: _Plan = [("withdrawal_by_modality", {"order": "largest_gap"})]

PLANTED_VARIANTS: list[tuple[str, _Plan]] = [
    # 1 to 3 chained: the owner's question
    (
        "Which major has the lowest GPA, what is its hardest class, and who has "
        "taught it?",
        _OWNER_CHAIN,
    ),
    (
        "Which major has the worst GPA, what's its toughest class, and who teaches it?",
        _OWNER_CHAIN,
    ),
    (
        "What is teh lowest-performing major, its hardest required course, and who "
        "has historically taught that course?",
        _OWNER_CHAIN,
    ),
    ("Lowest GPA major: what is its hardest course and who taught it?", _OWNER_CHAIN),
    (
        "Which major has the lowest GPA and what is its hardest class and who "
        "taught it?",
        _OWNER_CHAIN,
    ),
    # 1. the lowest-GPA major
    ("Which major has the lowest average GPA?", _LOWEST_GPA),
    ("What major has the worst GPA?", _LOWEST_GPA),
    ("Which is the lowest-performing major by GPA?", _LOWEST_GPA),
    ("Which major has teh lowest grade point average?", _LOWEST_GPA),
    ("Which program has the lowest cumulative GPA?", _LOWEST_GPA),
    # 2. the hardest required course in Mechanical Engineering
    (
        "What is the hardest required class for Mechanical Engineering majors?",
        _MEEN_HARDEST,
    ),
    (
        "Which Mechanical Engineering required course has historically been the "
        "toughest?",
        _MEEN_HARDEST,
    ),
    (
        "What's the worst DFW rate among required courses in Mechanical Engineering?",
        _MEEN_HARDEST,
    ),
    (
        "In Mechanical Engineering, which required course do students fail most?",
        _MEEN_HARDEST,
    ),
    ("Hardest class in mechanical engineering?", _MEEN_HARDEST),
    # 3. who has taught Thermodynamics I
    ("Who teaches Thermodynamics I?", _THERMO),
    ("Who has historically taught MEEN 3310?", _THERMO),
    ("Which professors have taught Thermodynamics I?", _THERMO),
    ("who taught teh Thermodynamics I sections?", _THERMO),
    ("Which instructors have taught MEEN 3310?", _THERMO),
    # 4. Organic Chemistry I before and after the instructor change
    ("How did the DFW rate in Organic Chemistry I change over time?", _OCHEM_TREND),
    ("Show the DFW trend for CHEM 2323 by term.", _OCHEM_TREND),
    ("What is the DFW rate in Organic Chemistry I each term?", _OCHEM_TREND),
    ("Who has taught Organic Chemistry I historically?", _OCHEM_TEACHERS),
    (
        "Did Organic Chemistry I get easier after the instructor changed?",
        _OCHEM_TEACHERS,
    ),
    # 5. the first-generation gap in College Algebra
    ("How big is the first-gen gap in College Algebra?", _ALGEBRA_GAP),
    ("Do first-generation students fail College Algebra more often?", _ALGEBRA_GAP),
    (
        "What is the equity gap for first generation students in MATH 1314?",
        _ALGEBRA_GAP,
    ),
    ("Is there a first-gen disparity in teh College Algebra DFW rate?", _ALGEBRA_GAP),
    # 6. continuing spring registration
    (
        "How did continuing student registration for Spring 2026 compare with last "
        "spring?",
        _SPRING_2026,
    ),
    ("Did continuing registration drop this spring?", _SPRING),
    (
        "How many continuing students registered in Spring 2026 versus Spring 2025?",
        _SPRING_2026,
    ),
    ("What was the change in returning student registration for spring?", _SPRING),
    # 7. the fastest-growing major
    (
        "Which major has grown the fastest since Fall 2020?",
        [("headcount_growth", {"start_term": "202110"})],
    ),
    (
        "Which program grew most between Fall 2020 and Fall 2025?",
        [("headcount_growth", {"start_term": "202110", "end_term": "202610"})],
    ),
    ("What is the fastest-growing major?", _GROWTH),
    # 8. online against in-person withdrawals
    ("Which term had the worst online withdrawal gap?", _ONLINE_LARGEST),
    ("Are online withdrawal rates higher than in-person ones?", _ONLINE),
    (
        "When was the gap between online and in-person withdrawals largest?",
        _ONLINE_LARGEST,
    ),
    (
        "Which semester had teh biggest online vs in-person withdrawal gap?",
        _ONLINE_LARGEST,
    ),
]


def test_forty_variants_of_the_planted_questions_map_to_the_right_plan(
    catalog: Catalog,
) -> None:
    assert len(PLANTED_VARIANTS) == 40
    for question, expected in PLANTED_VARIANTS:
        assert refusal_for(question, catalog.title_names) is None, question
        steps = rule_plan(question, catalog)
        assert steps is not None, question
        got = [s.to_json() for s in steps]
        assert [g["analysis_id"] for g in got] == [e[0] for e in expected], question
        for step, (_, params) in zip(got, expected, strict=True):
            for name, value in params.items():
                assert step["params"].get(name) == value, (question, name)


def test_model_planner_receives_only_the_catalog_and_the_question(
    catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CABINET_EXPLORE_PLANNER", "model-first")
    plan = {
        "steps": [{"analysis_id": "gpa_by_major", "params": {"order": "highest_first"}}]
    }
    stub = StubProvider({"explore_planner": json.dumps(plan)})
    outcome = plan_question("Which majors have the best grades?", catalog, stub)
    assert outcome.planner == "model"
    assert outcome.steps == [Step("gpa_by_major", {"order": "highest_first"})]
    role, payload = stub.calls[0]
    assert role == "explore_planner"
    assert set(payload) == {"question", "catalog"}
    assert set(payload["catalog"]) == {"analyses", "value_lists"}
    assert not STUDENT_ID.search(json.dumps(payload))


@pytest.mark.parametrize(
    "text",
    [
        "Sure! Here is the plan: gpa_by_major",
        json.dumps({"steps": [{"analysis_id": "student_rows", "params": {}}]}),
        json.dumps(
            {"steps": [{"analysis_id": "gpa_by_major", "params": {"major": "ZZZZ"}}]}
        ),
        json.dumps(
            {
                "steps": [
                    {
                        "analysis_id": "course_instructors",
                        "params": {"course": {"from_step": 0, "column": "course"}},
                    }
                ]
            }
        ),
        json.dumps(
            {
                "steps": [
                    {"analysis_id": "gpa_by_major", "params": {}},
                    {
                        "analysis_id": "course_instructors",
                        "params": {"course": {"from_step": 0, "column": "major"}},
                    },
                ]
            }
        ),
        json.dumps({"steps": []}),
        json.dumps(
            {
                "steps": [{"analysis_id": "gpa_by_major", "params": {}}],
                "sql": "SELECT student_id FROM students",
            }
        ),
    ],
)
def test_invalid_model_plans_fall_back_to_the_rule_planner(
    catalog: Catalog, text: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CABINET_EXPLORE_PLANNER", "model-first")
    stub = StubProvider({"explore_planner": text})
    outcome = plan_question(OWNER_EXAMPLE, catalog, stub)
    assert outcome.planner == "rule"
    assert outcome.fallback_reason
    assert outcome.steps == rule_plan(OWNER_EXAMPLE, catalog)


def test_clause_splitting_edge_cases(catalog: Catalog) -> None:
    def plan(question: str) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
        steps, notes = rule_plan_detail(question, catalog)
        return [s.to_json() for s in steps or []], notes

    # "and" before a verb stays one part; no campus-wide course ranking.
    steps, notes = plan(
        "Which instructors have taught MEEN 3310 and have the highest DFW rates?"
    )
    assert [s["analysis_id"] for s in steps] == ["course_instructors"] and not notes
    # A trailing lead-in no step used is named, never dropped silently.
    steps, notes = plan("Which major has the lowest GPA? For Nursing")
    assert [s["analysis_id"] for s in steps] == ["gpa_by_major"]
    assert any('"For Nursing"' in note for note in notes)
    # A part an earlier table already answers needs no "unanswered" note.
    steps, notes = plan(
        "Which instructors taught MEEN 3310, and what were their DFW rates?"
    )
    assert [s["analysis_id"] for s in steps] == ["course_instructors"] and not notes
    # One named major is about that major, not a ranking of all of them.
    steps, _ = plan("How do Nursing majors perform?")
    assert steps == [
        {
            "analysis_id": "gpa_by_major",
            "params": {"major": "NURS", "order": "lowest_first"},
        }
    ]
    steps, _ = plan("Which majors have the highest GPA?")
    assert "major" not in steps[0]["params"]


def test_rules_first_is_the_default_and_asks_the_model_only_when_rules_cannot(
    catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CABINET_EXPLORE_PLANNER", raising=False)
    plan = {"steps": [{"analysis_id": "gpa_by_college", "params": {}}]}
    stub = StubProvider({"explore_planner": json.dumps(plan)})
    # A question the rules map never reaches the model.
    outcome = plan_question(OWNER_SHORT, catalog, stub)
    assert outcome.planner == "rule"
    assert outcome.fallback_reason is None
    assert [s.analysis_id for s in outcome.steps or []] == [
        "gpa_by_major",
        "dfw_by_course",
        "course_instructors",
    ]
    assert stub.calls == []
    # One the rules cannot map goes to the model, and its plan is validated.
    unmapped = "Tell me something surprising about this university"
    assert rule_plan(unmapped, catalog) is None
    outcome = plan_question(unmapped, catalog, stub)
    assert outcome.planner == "model"
    assert outcome.steps == [Step("gpa_by_college", {})]
    # A rejected model plan leaves the question unmapped, with the reason.
    bad = StubProvider({"explore_planner": "not a plan"})
    outcome = plan_question(unmapped, catalog, bad)
    assert outcome.steps is None
    assert outcome.fallback_reason and "rejected" in outcome.fallback_reason
    # An unknown setting reads as the default.
    monkeypatch.setenv("CABINET_EXPLORE_PLANNER", "sometimes")
    assert plan_question(OWNER_SHORT, catalog, stub).planner == "rule"


def test_validate_plan_accepts_references_and_checks_kinds(catalog: Catalog) -> None:
    steps = validate_plan(
        {
            "steps": [
                {"analysis_id": "dfw_by_course", "params": {"major_required": "MEEN"}},
                {
                    "analysis_id": "course_dfw_trend",
                    "params": {"course": {"from_step": 0, "column": "course"}},
                },
            ]
        },
        catalog,
    )
    assert steps[1].params["course"] == Ref(0, "course")
    with pytest.raises(PlanInvalid):
        validate_plan(
            {"steps": [{"analysis_id": "course_dfw_trend", "params": {}}]}, catalog
        )


def test_model_plans_are_recorded_and_replayed(
    catalog: Catalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replay_dir = tmp_path / "replay"
    monkeypatch.setenv("CABINET_REPLAY_DIR", str(replay_dir))
    monkeypatch.setenv("CABINET_RECORD", "1")
    monkeypatch.setenv("CABINET_EXPLORE_PLANNER", "model-first")
    question = "Which majors have the best grades?"
    first = {
        "steps": [{"analysis_id": "gpa_by_major", "params": {"order": "highest_first"}}]
    }
    recorder = RecordingProvider(StubProvider({"explore_planner": json.dumps(first)}))
    assert plan_question(question, catalog, recorder).planner == "model"
    files = list((replay_dir / "explore").glob("*.json"))
    assert len(files) == 1
    # Kept, not overwritten, without CABINET_RECORD=overwrite.
    second = {"steps": [{"analysis_id": "gpa_by_college", "params": {}}]}
    recorder = RecordingProvider(StubProvider({"explore_planner": json.dumps(second)}))
    plan_question(question, catalog, recorder)
    assert load_recorded_plan(question, catalog) == validate_plan(first, catalog)
    monkeypatch.setenv("CABINET_RECORD", "overwrite")
    plan_question(question, catalog, recorder)
    assert load_recorded_plan(question, catalog) == validate_plan(second, catalog)
    # Replay serves the recording offline, keyed by the question.
    replayed = plan_question(question + "  ", catalog, ReplayProvider())
    assert replayed.planner == "recorded"
    assert replayed.steps == validate_plan(second, catalog)
    # Fake mode uses the rule planner and never calls a model.
    assert plan_question(question, catalog, FakeProvider()).planner == "rule"


# --- the answer writer and the numeral validator -----------------------------


def _owner_results(con: sqlite3.Connection, catalog: Catalog) -> list[StepResult]:
    steps = rule_plan(OWNER_EXAMPLE, catalog)
    assert steps is not None
    return execute(steps, con, catalog, "executive")


def test_template_answer_takes_every_number_from_a_cell(
    con: sqlite3.Connection, catalog: Catalog
) -> None:
    for question, _ in RULE_PHRASINGS:
        steps = rule_plan(question, catalog)
        assert steps is not None
        results = execute(steps, con, catalog, "executive")
        for sentence in template_answer(results):
            check_sentence(sentence.text, results)
            numbers = re.findall(r"\d", sentence.text)
            assert not numbers or sentence.claims, sentence.text


def test_numeral_validator_rejects_an_invented_number(
    con: sqlite3.Connection, catalog: Catalog
) -> None:
    results = _owner_results(con, catalog)
    top = results[0].rows[0]
    check_sentence(f"{top['major_name']} averages {top['avg_gpa']}.", results)
    with pytest.raises(OutputRejected):
        check_sentence(f"{top['major_name']} averages 1.234.", results)
    with pytest.raises(OutputRejected):
        check_sentence("Student S-100001 has the lowest GPA.", results)
    rate = results[1].rows[0]["dfw_rate"]
    check_sentence(f"Its DFW rate is {rate} %.", results)


def test_model_rewrite_is_validated_or_replaced_by_the_template(
    con: sqlite3.Connection, catalog: Catalog
) -> None:
    results = _owner_results(con, catalog)
    top = results[0].rows[0]
    template = [s.text for s in template_answer(results)]
    reworded = [
        f"{top['major_name']} has the lowest average GPA, "
        f"{top['avg_gpa']}, across {top['students']} students.",
        *template[1:],
    ]
    stub = StubProvider({"explore_writer": json.dumps({"sentences": reworded})})
    answer, source, reason = write_answer(results, stub)
    assert source == SOURCE_MODEL and reason is None
    assert answer[0].claims[0] == {"table": 0, "row": 0, "column": "avg_gpa"}
    role, payload = stub.calls[0]
    assert role == "explore_writer" and set(payload) == {"tables"}
    # A rewording that drops a fact the computed answer states is not used:
    # here the hardest course and its instructors are left out.
    partial = StubProvider({"explore_writer": json.dumps({"sentences": reworded[:1]})})
    answer, source, reason = write_answer(results, partial)
    assert source == SOURCE_TEMPLATE and reason and "left out" in reason
    # Instructor ids are not figures: the name alone is enough.
    no_ids = [re.sub(r"\bI-\d{4}\s+", "", text) for text in reworded]
    stub = StubProvider({"explore_writer": json.dumps({"sentences": no_ids})})
    assert write_answer(results, stub)[1] == SOURCE_MODEL
    # So is one that keeps the numbers but drops the instructor's name.
    nameless = [
        text.replace("Alicia Shelby (fictional)", "One instructor") for text in reworded
    ]
    if nameless != reworded:
        stub = StubProvider({"explore_writer": json.dumps({"sentences": nameless})})
        assert write_answer(results, stub)[1] == SOURCE_TEMPLATE
    for invented in ("The lowest average GPA is 1.99.", "S-100001 struggles most."):
        bad = StubProvider({"explore_writer": json.dumps({"sentences": [invented]})})
        answer, source, reason = write_answer(results, bad)
        assert source == SOURCE_TEMPLATE and reason
        assert answer == template_answer(results)
    prose = StubProvider({"explore_writer": "Mechanical Engineering is lowest."})
    assert write_answer(results, prose)[1] == SOURCE_TEMPLATE


# --- audit -------------------------------------------------------------------


def test_audit_events_and_chain(app: FastAPI) -> None:
    client = _client(app, "executive")
    before = len(_events(app))
    _ask(client, OWNER_EXAMPLE)
    _ask(client, "Which students are in counseling?")
    new = _events(app)[before:]
    assert [e["type"] for e in new] == [
        "question.asked",
        "data.granted",
        "data.granted",
        "data.granted",
        "explore.answered",
        "question.asked",
        "data.refused",
    ]
    grants = [e["payload"] for e in new if e["type"] == "data.granted"]
    assert [g["analysis_id"] for g in grants] == [
        "gpa_by_major",
        "dfw_by_course",
        "course_instructors",
    ]
    assert all(g["aggregate_only"] is True and g["fields_read"] for g in grants)
    answered = new[4]["payload"]
    assert answered["steps"] == ["gpa_by_major", "dfw_by_course", "course_instructors"]
    assert all(isinstance(n, int) for n in answered["row_counts"])
    assert answered["planner"] == "rule" and answered["writer"] == "template"
    assert set(answered) == {
        "task_id",
        "question_event_id",
        "steps",
        "row_counts",
        "planner",
        "writer",
        "answered",
    }
    store: AuthStore = app.state.auth
    assert verify_db(store.path) == []


# --- the full-scale check (make explore-check) -------------------------------

FULL = pytest.mark.skipif(
    os.environ.get("CABINET_EXPLORE_FULL") != "1",
    reason="full-scale check: run make school-data, then make explore-check",
)


@pytest.fixture
def full_env(monkeypatch: pytest.MonkeyPatch) -> None:
    if not FULL_DB.is_file():
        pytest.fail(f"{FULL_DB} is missing; run make school-data first")
    monkeypatch.setenv("CABINET_SCHOOL_DB", str(FULL_DB))
    con = connect_readonly(FULL_DB)
    try:
        scale = con.execute("SELECT value FROM meta WHERE key = 'scale'").fetchone()[0]
    finally:
        con.close()
    assert scale == "1.0", "the full-scale check needs the scale 1.0 database"


def _texts(body: dict[str, Any]) -> str:
    return " ".join(s["text"] for s in body["answer"])


@FULL
@pytest.mark.parametrize("question", [OWNER_EXAMPLE, OWNER_SHORT])
def test_full_scale_owner_example(full_env: None, app: FastAPI, question: str) -> None:
    body = _ask(_client(app, "executive"), question)
    gpa, hardest, instructors = (s["table"] for s in body["steps"])
    assert gpa["rows"][0][:2] == ["MEEN", "Mechanical Engineering"]
    assert gpa["rows"][0][3:] == [250, 2.623]
    assert hardest["rows"][0] == ["MEEN 3310", "Thermodynamics I", 10, 10, 91, 38, 41.8]
    assert hardest["rows"][1][0] == "MEEN 3350" and hardest["rows"][1][-1] == 34.1
    assert instructors["rows"][0] == [
        "I-0001",
        "Alicia Shelby (fictional)",
        "Professor",
        7,
        7,
        "Fall 2021",
        "Spring 2026",
        60,
        34,
        56.7,
    ]
    assert instructors["rows"][1][:5] == [
        "I-0002",
        "Anthony Jennings (fictional)",
        "Associate Professor",
        3,
        3,
    ]
    assert instructors["rows"][1][-3:] == [31, 4, 12.9]
    assert [s["text"] for s in body["answer"]] == [
        "Mechanical Engineering has the lowest average cumulative GPA, 2.623 "
        "across 250 "
        "students.",
        "In Mechanical Engineering, the historically hardest required course is "
        "MEEN 3310 "
        "Thermodynamics I, with a DFW rate of 41.8 % (38 of 91 graded "
        "registrations over "
        "10 sections).",
        "I-0001 Alicia Shelby (fictional) has taught it most: 7 sections in 7 "
        "terms, with "
        "a DFW rate of 56.7 %.",
        "I-0002 Anthony Jennings (fictional) taught 3 sections, with a DFW rate of "
        "12.9 %.",
    ]


@FULL
@pytest.mark.parametrize(
    ("question", "expected"),
    [
        (
            "Which major grew fastest from Fall 2020 to Fall 2025?",
            ["Computer Science", "65", "137", "110.8 %"],
        ),
        (
            "How much did continuing spring registration change in Spring 2026?",
            ["2,073", "2,178", "−4.8 %"],
        ),
        (
            "What is the first-generation equity gap in College Algebra?",
            ["42.8 %", "15.6 %", "27.2 points"],
        ),
        (
            "Which term had the largest gap between online and in-person "
            "withdrawal rates?",
            ["Spring 2021", "16.7 %", "4.3 %", "12.4 points"],
        ),
        (
            "Who has taught Organic Chemistry I?",
            [
                "I-0004 Chloe Merriweather (fictional)",
                "8 sections",
                "14.5 %",
                "I-0003 Naomi Faraday (fictional)",
                "7 sections",
                "47.3 %",
            ],
        ),
    ],
)
def test_full_scale_planted_facts(
    full_env: None, app: FastAPI, question: str, expected: list[str]
) -> None:
    text = _texts(_ask(_client(app, "executive"), question))
    for fragment in expected:
        assert fragment in text, (fragment, text)


def test_explore_is_under_the_ask_rate_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CABINET_RATE_ASK_PER_MIN", "2")
    client = _client(create_app(), "executive")
    codes = [
        client.post(
            "/explore", json={"question": "What was enrollment by term?"}
        ).status_code
        for _ in range(3)
    ]
    assert codes == [200, 200, 429]


# --- regressions from the fresh-context review ---------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "How much did continuing spring registration change in Spring 2021?",
        "Which major grew fastest from Fall 2025 to Fall 2025?",
        "Who has taught Risk Management and Insurance?",
        "What is the DFW rate in PSYC 4320 Theories of Counseling?",
    ],
)
def test_step_errors_and_risky_titles_answer_without_a_500(
    app: FastAPI, question: str
) -> None:
    body = _ask(_client(app, "executive"), question)
    assert body["refused"] is False
    assert body["answer"]
    answered = _events(app, "explore.answered")[-1]["payload"]
    assert answered["answered"] is True


def test_withheld_figures_cannot_be_recovered_by_subtraction(
    con: sqlite3.Connection, catalog: Catalog
) -> None:
    def withheld_rows(result: StepResult) -> int:
        return sum(1 for row in result.rows if row.get("dfw_rate") == "fewer than 10")

    for course in sorted(catalog.vocab.courses)[::7]:
        instructors = execute(
            [Step("course_instructors", {"course": course})], con, catalog, "executive"
        )[0]
        if len(instructors.rows) > 1:
            assert withheld_rows(instructors) != 1, course
        trend = execute(
            [Step("course_dfw_trend", {"course": course})], con, catalog, "executive"
        )[0]
        terms = [r for r in trend.rows if r["term"] != "All terms"]
        if len(terms) > 1:
            assert sum(r["dfw_rate"] == "fewer than 10" for r in terms) != 1, course
        # The two instructor analyses agree on every withheld row.
        for row in instructors.rows:
            history = execute(
                [
                    Step(
                        "instructor_history",
                        {"instructor": row["instructor"], "top": 50},
                    )
                ],
                con,
                catalog,
                "executive",
            )[0]
            mine = next(r for r in history.rows if r["course"] == course)
            assert mine["dfw_rate"] == row["dfw_rate"], (course, row["instructor"])
    enrollment = execute(
        [Step("enrollment_by_term", {"major": "ACCT", "season": "Spring"})],
        con,
        catalog,
        "executive",
    )[0]
    for row in enrollment.rows:
        cells = [row["students"], row["new_students"], row["continuing"]]
        withheld = [c == "fewer than 10" for c in cells]
        assert all(withheld) or not any(withheld), row


@pytest.mark.parametrize(
    "question",
    [
        "Do students who pray have higher GPAs?",
        "Does chapel attendance affect GPA?",
        "How many students see a psychologist?",
        "Which 5 students have the most holds?",
        "Top 10 students by GPA",
        "Rank students by GPA",
        "Who will be suspended next term?",
        "Which first-generation students failed MATH 1314?",
        "Tell me about the student with id 100001",
        "gpa of id 100001",
    ],
)
def test_more_refusals(question: str) -> None:
    assert refusal_for(question) is not None


@pytest.mark.parametrize(
    "question",
    [
        "What is the DFW rate in PSYC 4320 Theories of Counseling?",
        "How many graduates did Nursing's 2024 class have?",
        "What were Biology's 2023 numbers?",
        "How many at-risk courses are there?",
    ],
)
def test_no_false_refusals(question: str, catalog: Catalog) -> None:
    assert refusal_for(question, catalog.title_names) is None


def test_audit_log_never_stores_a_typed_id(app: FastAPI) -> None:
    for question in (
        "What is the GPA of student 100001?",
        "What is the GPA of S_100001?",
        "What is the GPA of S–100001?",
    ):
        _ask(_client(app, "executive"), question)
    asked = json.dumps([e["payload"] for e in _events(app, "question.asked")])
    assert "100001" not in asked


def test_float_values_in_a_model_plan_are_invalid(catalog: Catalog) -> None:
    for params in ({"top": 5.0}, {"min_students": 10.0}):
        with pytest.raises(PlanInvalid):
            validate_plan(
                {"steps": [{"analysis_id": "gpa_by_major", "params": params}]}, catalog
            )
    stub = StubProvider(
        {
            "explore_planner": json.dumps(
                {"steps": [{"analysis_id": "gpa_by_major", "params": {"top": 5.0}}]}
            )
        }
    )
    assert plan_question(OWNER_EXAMPLE, catalog, stub).planner == "rule"


def test_model_rewrite_cannot_swap_numbers_between_rows(
    con: sqlite3.Connection, catalog: Catalog
) -> None:
    results = execute([Step("gpa_by_major", {})], con, catalog, "executive")
    first, second = results[0].rows[0], results[0].rows[1]
    swapped = (
        f"{first['major_name']} has {second['students']} students and "
        f"{second['major_name']} has {first['students']}."
    )
    if first["students"] != second["students"]:
        stub = StubProvider({"explore_writer": json.dumps({"sentences": [swapped]})})
        assert write_answer(results, stub)[1] == SOURCE_TEMPLATE
    honest = (
        f"{first['major_name']} has {first['students']} students and "
        f"{second['major_name']} has {second['students']}."
    )
    # The rewording keeps the computed answer's own facts and adds the row.
    template = [s.text for s in template_answer(results)]
    sentences = [*template[: MAX_SENTENCES - 1], honest]
    stub = StubProvider({"explore_writer": json.dumps({"sentences": sentences})})
    answer, source, _ = write_answer(results, stub)
    assert source == SOURCE_MODEL
    assert {c["row"] for c in answer[-1].claims} == {0, 1}


def test_planner_says_what_it_did_not_apply(app: FastAPI, catalog: Catalog) -> None:
    client = _client(app, "executive")
    body = _ask(client, "Which major has the highest GPA in Fall 2023?")
    assert any("Fall 2023" in note for note in body["notes"])
    body = _ask(client, "Which major has the lowest GPA? Who has taught it?")
    assert any("No approved analysis answered" in note for note in body["notes"])
    body = _ask(client, "How many students failed Calc?")
    assert body["message"] == UNANSWERABLE_MESSAGE
    steps = rule_plan("Who taught MEEN 3310? What has that instructor taught?", catalog)
    assert steps is not None
    assert [s.analysis_id for s in steps] == [
        "course_instructors",
        "instructor_history",
    ]
    assert steps[1].params["instructor"] == Ref(0, "instructor")
    college = rule_plan("What is the average GPA in the College of Business?", catalog)
    assert college == [
        Step("gpa_by_college", {"college": "COB", "order": "lowest_first"})
    ]
