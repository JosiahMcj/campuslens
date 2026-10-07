"""The model planner: the compact catalog it reads, the resolution of its
free-text values, its fallbacks to the rule planner, its time budget, and
the two questions the owner asked live (2026-10-07), answered through a stub
provider that returns the plan the model should write."""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import cabinet.provider as provider_module
from cabinet.api import create_app
from cabinet.explore import general
from cabinet.explore.answer import template_answer
from cabinet.explore.catalog import ANALYSES, Catalog, catalog_for, connect_readonly
from cabinet.explore.compact import compact_catalog, resolve_plan
from cabinet.explore.evalset import EVAL_SET, REF
from cabinet.explore.execute import StepResult, execute
from cabinet.explore.planner import (
    RULE_PHRASINGS,
    Step,
    plan_question,
    rule_plan,
    understood,
    validate_plan,
)
from cabinet.provider import (
    ChatProvider,
    Explanation,
    ProviderUnavailable,
    timeout_for_role,
)
from conftest import make_authenticated_client

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATE = REPO_ROOT / "data" / "school" / "generate.py"

OWNER_FIRST = "how many cs students are enrolled"
OWNER_SECOND = "no how many computer science major students are there"
HEADCOUNT_CSCI = {
    "steps": [
        {
            "analysis_id": "measure_by_group",
            "params": {"measure": "headcount", "major": "CSCI"},
        }
    ]
}


@pytest.fixture(scope="module")
def school_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("school-model") / "school.db"
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
    monkeypatch.delenv("CABINET_EXPLORE_PLANNER", raising=False)
    monkeypatch.delenv("CABINET_EXPLORE_PLANNER_TIMEOUT", raising=False)


@pytest.fixture
def con(school_db: Path) -> Iterator[sqlite3.Connection]:
    connection = connect_readonly(school_db)
    yield connection
    connection.close()


@pytest.fixture
def catalog(con: sqlite3.Connection, school_db: Path) -> Catalog:
    return catalog_for(con, school_db)


class StubProvider:
    """A live-looking provider that answers the planner with fixed text (or
    raises), and records what it received."""

    name = "chat"
    model_label = "stub"

    def __init__(self, text: str | Exception) -> None:
        self.text = text
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def explain(self, findings: dict[str, Any], role: str) -> Explanation:
        self.calls.append((role, findings))
        if isinstance(self.text, Exception):
            raise self.text
        return Explanation(self.text, self.name, self.model_label)


def _model_text(plan: dict[str, Any], reasoning: str = "What is asked.") -> str:
    return json.dumps({"reasoning": reasoning, **plan})


# --- the compact catalog ---------------------------------------------------------


def test_the_compact_catalog_is_small_and_complete(catalog: Catalog) -> None:
    text = compact_catalog(catalog)
    # About four characters a token: under 3,000 tokens, against about
    # 17,000 for the full spec.
    assert len(text) <= 12_000, len(text)
    assert len(json.dumps(catalog.spec())) > 4 * len(text)
    for analysis in ANALYSES:
        assert f"- {analysis.id}:" in text
    for measure in general.MEASURES:
        assert f"- {measure}:" in text
    for key in general.GROUPING_KEYS:
        assert f"- {key}:" in text
    for code, name in catalog.vocab.majors.items():
        assert f"{code} {name}" in text
    for code, name in catalog.vocab.colleges.items():
        assert f"{code} {name}" in text
    terms = list(catalog.vocab.terms.values())
    assert f"TERMS: {terms[0]} to {terms[-1]}" in text
    assert "CS, comp sci = Computer Science" in text
    # No instructor, and no course beyond the one worked example's.
    assert not any(name in text for name in catalog.vocab.instructors.values())
    assert not any(iid in text for iid in catalog.vocab.instructors)
    titles = [t for t in set(catalog.vocab.courses.values()) if len(t) > 6]
    assert {t for t in titles if t in text} <= {"Calculus I", "Calculus II"}
    # The same text every time (a server can keep it processed).
    assert compact_catalog(catalog) is text


def test_the_planner_prompt_is_the_catalog_then_the_question_alone(
    catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cabinet.analysts import build_prompt

    stub = StubProvider(_model_text(HEADCOUNT_CSCI))
    plan_question("How many CS students does S-100023 know?", catalog, stub, "staff")
    role, payload = stub.calls[0]
    assert role == "explore_planner"
    assert set(payload) == {"question", "catalog"}
    assert "S-100023" not in payload["question"]
    system, user = build_prompt(payload, role)
    assert system == compact_catalog(catalog)
    assert user == f"Q: {payload['question']}"


# --- resolving the model's free text -----------------------------------------------


def _resolved(catalog: Catalog, analysis_id: str, **params: Any) -> dict[str, Any]:
    plan = resolve_plan(
        {"reasoning": "x", "steps": [{"analysis_id": analysis_id, "params": params}]},
        catalog,
    )
    steps = validate_plan(plan, catalog)
    return steps[0].params


def test_free_text_values_resolve_to_catalog_values(catalog: Catalog) -> None:
    mbg = "measure_by_group"
    assert _resolved(catalog, mbg, measure="headcount", major="CS") == {
        "measure": "headcount",
        "major": "CSCI",
    }
    assert (
        _resolved(catalog, mbg, measure="headcount", major="computer science majors")[
            "major"
        ]
        == "CSCI"
    )
    assert (
        _resolved(catalog, mbg, measure="headcount", major="Nursng")["major"] == "NURS"
    )
    assert (
        _resolved(catalog, mbg, measure="headcount", college="Engineering")["college"]
        == "CEC"
    )
    got = _resolved(
        catalog,
        mbg,
        measure="headcount",
        class_level="Freshmen",
        residency="International",
    )
    assert got == {
        "measure": "headcount",
        "class_level": "Freshman",
        "residency": "international",
    }
    # A one-term window on the current term is the default: dropped.
    current = [t for t, s in catalog.vocab.term_season.items() if s != "Summer"][-1]
    name = catalog.vocab.terms[current]
    assert _resolved(
        catalog, mbg, measure="headcount", major="Nursing", term_from=name, term_to=name
    ) == {"measure": "headcount", "major": "NURS"}
    assert _resolved(catalog, mbg, measure="headcount", term="Fall 2024")[
        "term_from"
    ] == ("202510")
    assert _resolved(catalog, "course_dfw_trend", course="Organic Chemistry 1") == {
        "course": "CHEM 2323"
    }
    assert _resolved(catalog, "course_dfw_trend", course="meen3310") == {
        "course": "MEEN 3310"
    }
    assert _resolved(catalog, "course_instructors", course="thermodynamics i") == {
        "course": "MEEN 3310"
    }
    assert _resolved(catalog, "instructor_history", instructor="Alicia Shelby") == {
        "instructor": "I-0001"
    }
    assert _resolved(
        catalog, "dfw_by_course", subject="Math", order="lowest_first"
    ) == {
        "subject": "MATH",
        "order": "lowest_first",
    }
    assert _resolved(catalog, "graduations", academic_year="2024-25") == {
        "academic_year": "2024-2025"
    }
    # "top": 1 shows the smallest table that holds it; "all" means no filter;
    # an order on an analysis without one is dropped.
    assert _resolved(catalog, "gpa_by_major", top=1, college="all")["top"] == 5
    assert _resolved(catalog, "holds_by_office", order="highest_first") == {}


def test_a_ranking_without_a_grouping_ranks_what_the_next_step_uses(
    catalog: Catalog,
) -> None:
    plan = resolve_plan(
        {
            "steps": [
                {
                    "analysis_id": "measure_by_group",
                    "params": {"measure": "dropout_rate", "order": "highest_first"},
                },
                {
                    "analysis_id": "dfw_by_course",
                    "params": {"major_required": {"from_step": 0, "column": "major"}},
                },
            ]
        },
        catalog,
    )
    assert plan["steps"][0]["params"]["group_by"] == "major"
    # The same table twice, ordered both ways, is asked once.
    twice = resolve_plan(
        {
            "steps": [
                {"analysis_id": "gpa_by_major", "params": {"order": "lowest_first"}},
                {"analysis_id": "gpa_by_major", "params": {"order": "highest_first"}},
            ]
        },
        catalog,
    )
    assert len(twice["steps"]) == 1


# --- fallbacks to the rule planner ---------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        ProviderUnavailable(
            "the chat endpoint did not answer within 20 s", provider="chat"
        ),
        "I think you want the headcount.",
        '{"reasoning": "x", "steps": [{"analysis_id": "measure_by_group", '
        '"params": {"measure": "headcount", "major": "Underwater Basketry"}}]}',
        '{"reasoning": "x", "steps": [{"analysis_id": "student_list", "params": {}}]}',
        '{"reasoning": "x", "steps": []}',
    ],
)
def test_an_unusable_model_plan_falls_back_to_the_rules(
    catalog: Catalog, answer: str | Exception
) -> None:
    stub = StubProvider(answer)
    outcome = plan_question(OWNER_FIRST, catalog, stub)
    assert outcome.planner == "rule"
    assert outcome.fallback_reason
    assert outcome.steps == rule_plan(OWNER_FIRST, catalog)
    assert outcome.steps == [
        Step("measure_by_group", {"measure": "headcount", "major": "CSCI"})
    ]


def test_the_planner_has_its_own_time_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    assert timeout_for_role("explore_planner") == 20
    assert timeout_for_role("explore_writer") == 55
    assert timeout_for_role("chief_of_staff") == 55
    monkeypatch.setenv("CABINET_EXPLORE_PLANNER_TIMEOUT", "12.5")
    assert timeout_for_role("explore_planner") == 12.5
    for bad in ("0", "999", "soon"):
        monkeypatch.setenv("CABINET_EXPLORE_PLANNER_TIMEOUT", bad)
        assert timeout_for_role("explore_planner") == 20
    monkeypatch.delenv("CABINET_EXPLORE_PLANNER_TIMEOUT")

    seen: list[float] = []

    class _Response:
        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def read(self) -> bytes:
            body = {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": _model_text(HEADCOUNT_CSCI)},
                    }
                ]
            }
            return json.dumps(body).encode()

    def fake_urlopen(_request: Any, timeout: float) -> _Response:
        seen.append(timeout)
        return _Response()

    monkeypatch.setattr(provider_module, "_urlopen", fake_urlopen)
    monkeypatch.setenv("CABINET_LLM_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("CABINET_LLM_MODEL", "m")
    monkeypatch.setenv("CABINET_LLM_API_KEY", "k")
    ChatProvider().explain({"question": "q", "catalog": "c"}, "explore_planner")
    ChatProvider().explain({"tables": []}, "explore_writer")
    assert seen == [20, 55]

    def slow(_request: Any, timeout: float) -> _Response:
        raise TimeoutError

    monkeypatch.setattr(provider_module, "_urlopen", slow)
    with pytest.raises(ProviderUnavailable, match="within 20 s"):
        ChatProvider().explain({"question": "q", "catalog": "c"}, "explore_planner")


# --- the owner's two live questions --------------------------------------------------


def _answer(steps: list[Step], con: sqlite3.Connection, catalog: Catalog) -> str:
    results: list[StepResult] = execute(steps, con, catalog, "executive")
    return " ".join(s.text for s in template_answer(results))


@pytest.mark.parametrize("question", [OWNER_FIRST, OWNER_SECOND])
def test_the_owner_questions_count_computer_science_majors_now(
    question: str, catalog: Catalog, con: sqlite3.Connection
) -> None:
    stub = StubProvider(
        _model_text(
            {
                "steps": [
                    {
                        "analysis_id": "measure_by_group",
                        "params": {"measure": "headcount", "major": "Computer Science"},
                    }
                ]
            },
            "A count of Computer Science majors enrolled now.",
        )
    )
    outcome = plan_question(question, catalog, stub, "executive")
    assert outcome.planner == "model"
    assert outcome.steps == [
        Step("measure_by_group", {"measure": "headcount", "major": "CSCI"})
    ]
    # The rules alone reach the same plan.
    assert rule_plan(question, catalog) == outcome.steps
    current = [t for t, s in catalog.vocab.term_season.items() if s != "Summer"][-1]
    term = catalog.vocab.terms[current]
    assert understood(outcome.steps, catalog) == (
        f"Computer Science students enrolled in {term}, the current term"
    )
    text = _answer(outcome.steps, con, catalog)
    assert re.fullmatch(
        rf"(?:[\d,]+ students were enrolled in Computer Science in {term}\.|"
        r"That figure is withheld: .*)",
        text,
    ), text
    assert "withheld (fewer than" not in text


HEADCOUNT_PHRASINGS: tuple[tuple[str, dict[str, str]], ...] = (
    ("how many students are in nursing", {"major": "NURS"}),
    ("how many students are enrolled this semester", {}),
    ("total enrollment", {}),
    ("how many freshmen", {"class_level": "Freshman"}),
    ("how many international students do we have", {"residency": "international"}),
    ("hw many psych majors r there", {"major": "PSYC"}),
    (
        "number of nursing students in fall 2024",
        {"major": "NURS", "term_from": "202510", "term_to": "202510"},
    ),
)


@pytest.mark.parametrize(("question", "filters"), HEADCOUNT_PHRASINGS)
def test_headcount_questions_count_now_with_the_right_filter(
    question: str, filters: dict[str, str], catalog: Catalog
) -> None:
    assert rule_plan(question, catalog) == [
        Step("measure_by_group", {"measure": "headcount", **filters})
    ]


def test_a_trend_question_stays_with_enrollment_by_term(catalog: Catalog) -> None:
    for question in (
        "What was enrollment by term?",
        "How many students were enrolled in Nursing each fall?",
        "how has enrollment changed over time",
    ):
        steps = rule_plan(question, catalog)
        assert steps is not None and steps[0].analysis_id == "enrollment_by_term", (
            question
        )
    assert dict(RULE_PHRASINGS)["What was enrollment by term?"] == (
        "enrollment_by_term",
    )


# --- the answer's grammar for withheld counts --------------------------------------


def test_a_withheld_count_never_reads_withheld_students(
    con: sqlite3.Connection, catalog: Catalog
) -> None:
    seen_withheld_term = False
    for major in catalog.vocab.majors:
        for steps in (
            [Step("enrollment_by_term", {"major": major})],
            [Step("measure_by_group", {"measure": "headcount", "major": major})],
            [Step("continuing_registration_change", {})],
        ):
            text = _answer(steps, con, catalog)
            assert "withheld (fewer than 10 students) students" not in text, text
            assert not re.search(r"withheld \([^)]*\) \w+ were", text), text
            if "count is withheld" in text:
                seen_withheld_term = True
                assert re.search(
                    r"^(?:The \w+ \d{4}|Every term's) count is withheld: ", text
                ), text
    assert seen_withheld_term


def test_every_eval_expectation_is_a_valid_plan(catalog: Catalog) -> None:
    """Each expected step validates on its own (a value carried from an
    earlier step is the scorer's to check, so a stand-in takes its place)."""
    stand_in = {"course": "MEEN 3310", "major_required": "NURS"}
    for _question, plans in EVAL_SET:
        for plan in plans or []:
            for analysis_id, params in plan:
                fixed = {k: (stand_in[k] if v == REF else v) for k, v in params.items()}
                validate_plan(
                    {"steps": [{"analysis_id": analysis_id, "params": fixed}]}, catalog
                )
    assert len(EVAL_SET) >= 40


# --- the trace through the API ---------------------------------------------


def test_the_stream_says_what_was_understood_from_the_model_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import cabinet.explore.api as explore_api

    stub = StubProvider(_model_text(HEADCOUNT_CSCI, "SECRET-REASONING-TEXT"))
    monkeypatch.setattr(explore_api, "provider_from_env", lambda: stub)
    monkeypatch.setenv("CABINET_EXPLORE_WRITER", "template")
    client: TestClient = make_authenticated_client(create_app(), role="executive")
    response = client.post("/explore/stream", json={"question": OWNER_FIRST})
    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    understood_events = [e for e in events if e["type"] == "understood"]
    assert understood_events[0]["text"].startswith(
        "Computer Science students enrolled in "
    )
    plan = next(e for e in events if e["type"] == "plan")
    assert plan["planner"] == "model"
    assert "SECRET-REASONING-TEXT" not in response.text
    body = events[-1]["response"]
    assert body["planner"] == "model"
    assert body["steps"][0]["analysis_id"] == "measure_by_group"


def test_the_plan_is_read_from_the_one_json_object_in_the_answer(
    catalog: Catalog,
) -> None:
    body = _model_text(HEADCOUNT_CSCI)
    expected = [Step("measure_by_group", {"measure": "headcount", "major": "CSCI"})]
    for text in (
        body,
        f"Here is the plan:\n{body}",
        f"{body}\nDone.",
        f"```json\n{body}\n```",
    ):
        outcome = plan_question(OWNER_FIRST, catalog, StubProvider(text))
        assert outcome.planner == "model", text
        assert outcome.steps == expected


def test_a_conversational_lead_in_is_dropped(
    catalog: Catalog,
) -> None:
    for question in (
        "Actually, how many CS students are there?",
        "ok so how many cs students",
    ):
        assert rule_plan(question, catalog) == [
            Step("measure_by_group", {"measure": "headcount", "major": "CSCI"})
        ], question
