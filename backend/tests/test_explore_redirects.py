"""Explore answers instead of refusing (owner direction, 2026-10-07).

Covers: forward-looking questions about groups answered from the records
with one lead line; the hold-status grouping ("how many students have
holds", and the owner's "how many students have holds and will drop");
single-student and counseling questions redirected without the protected
data and still audited; the "Not something CampusLens answers" card kept
for off-topic requests only, never reaching an analysis.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cabinet.api import create_app
from cabinet.auth import AuthStore
from cabinet.explore.planner import (
    FORWARD_HINT,
    GREETING_MESSAGE,
    UNANSWERABLE_MESSAGE,
)
from cabinet.explore.privacy import (
    COUNSELING_MESSAGE,
    FORWARD_LEAD,
    INDIVIDUAL_LEAD,
    INDIVIDUAL_MESSAGE,
    OFF_TOPIC_MESSAGE,
    PREDICTION_LEAD,
    is_forward_looking,
    is_off_topic,
    refusal_for,
)
from cabinet.explore.prompts import build_explore_prompt
from cabinet.provider import Explanation
from conftest import make_authenticated_client

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATE = REPO_ROOT / "data" / "school" / "generate.py"
STUDENT_ID = re.compile(r"\bS-\d+")


@pytest.fixture(scope="session")
def school_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("school_redirects") / "school.db"
    proc = subprocess.run(
        [sys.executable, str(GENERATE), "--scale", "0.05", "--out", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return out


@pytest.fixture(autouse=True)
def _school_env(school_db: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("CABINET_SCHOOL_DB", str(school_db))
    monkeypatch.setenv("CABINET_PROVIDER", "replay")
    monkeypatch.delenv("CABINET_RECORD", raising=False)
    yield


@pytest.fixture
def app() -> FastAPI:
    return create_app()


def _client(app: FastAPI, role: str = "executive") -> TestClient:
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


def _measures(body: dict[str, Any]) -> list[tuple[str, list[str]]]:
    return [(s["analysis_id"], s["params_plain"]) for s in body["steps"]]


def _never(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("the planner or a provider ran")


def _no_planning(monkeypatch: pytest.MonkeyPatch) -> None:
    import cabinet.explore.api as explore_api

    monkeypatch.setattr(explore_api, "plan_question", _never)
    monkeypatch.setattr(explore_api, "provider_from_env", _never)


def _no_student_rows(body: dict[str, Any]) -> None:
    """Totals only: no step reads or returns anything per student."""
    for step in body["steps"]:
        keys = {c["key"] for c in step["table"]["columns"]}
        assert not keys & {"student_id", "name", "email", "risk", "risk_score"}
        assert step["aggregate_only"] is True
    assert "risk score" not in json.dumps(body).lower()


class StubProvider:
    """A live-looking provider that answers the planner with fixed text and
    records what it received."""

    name = "chat"
    model_label = "stub"

    def __init__(self, plan: str) -> None:
        self.plan = plan
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def explain(self, findings: dict[str, Any], role: str) -> Explanation:
        self.calls.append((role, findings))
        if role != "explore_planner":
            raise AssertionError("only the planner is asked in these tests")
        return Explanation(self.plan, self.name, self.model_label)


# --- holds ---------------------------------------------------------------------


def test_how_many_students_have_holds_is_a_headcount(app: FastAPI) -> None:
    body = _ask(_client(app), "how many students have holds")
    assert body["refused"] is False
    [(analysis, lines)] = _measures(body)
    assert analysis == "measure_by_group"
    assert "Measure: headcount" in lines
    assert "Only: Students with a hold" in lines
    assert re.search(r"\d", body["answer"][0]["text"])


def test_the_owners_holds_question_is_answered_from_the_records(app: FastAPI) -> None:
    body = _ask(_client(app), "how many students have holds and will drop")
    assert body["refused"] is False and "redirect" not in body
    # One plain line first, then the computed sentences.
    assert body["answer"][0] == {"text": FORWARD_LEAD, "claims": []}
    assert len(body["answer"]) > 1
    steps = _measures(body)
    assert [lines[0] for _, lines in steps] == [
        "Measure: headcount",
        "Measure: dropout rate",
        "Measure: stop-out rate",
    ]
    assert "Only: Students with a hold" in steps[0][1]
    assert "Grouped by: hold status" in steps[1][1]
    groups = [row[0] for row in body["steps"][1]["table"]["rows"]]
    assert "Students with a hold" in groups and "Students without a hold" in groups
    assert any(
        "hold was placed" in note for note in body["steps"][1]["notes"]
    ), body["steps"][1]["notes"]
    _no_student_rows(body)
    answered = _events(app, "explore.answered")[-1]["payload"]
    assert answered["answered"] is True and answered["lead"] == "forward"


def test_hold_groups_keep_small_cell_withholding(app: FastAPI) -> None:
    # Hold status inside each major: many cells are small at test scale.
    body = _ask(_client(app), "dropout rate by major and hold status")
    step = body["steps"][0]
    assert "Then by: hold status" in step["params_plain"] or (
        "Grouped by: hold status" in step["params_plain"]
    )
    keys = [c["key"] for c in step["table"]["columns"]]
    students = keys.index("students")
    for row in step["table"]["rows"]:
        count = row[students]
        assert not isinstance(count, int) or count >= 10, row


def test_offices_question_still_ranks_offices(app: FastAPI) -> None:
    body = _ask(_client(app), "Which offices hold the most active holds?")
    assert body["steps"][0]["analysis_id"] == "holds_by_office"


# --- forward-looking questions about groups -------------------------------------

FORWARD = [
    ("will enrollment fall next year", "enrollment_by_term", None),
    ("what % will graduate", "measure_by_group", "Measure: 6-year graduation rate"),
    ("at-risk students in nursing", "measure_by_group", "Measure: dropout rate"),
    (
        "how many nursing students are likely to drop out next year",
        "measure_by_group",
        "Measure: headcount",
    ),
    (
        "predict the dropout rate for first-gen students",
        "measure_by_group",
        "Measure: dropout rate",
    ),
    (
        "Will retention go down for Pell students?",
        "measure_by_group",
        "Measure: first-year retention rate",
    ),
]


@pytest.mark.parametrize(("question", "analysis", "line"), FORWARD)
def test_forward_questions_are_answered_from_history(
    app: FastAPI, question: str, analysis: str, line: str | None
) -> None:
    assert refusal_for(question) is None
    assert is_forward_looking(question)
    body = _ask(_client(app), question)
    assert body["refused"] is False
    assert body["answer"][0]["text"] == FORWARD_LEAD
    assert body["answer"][0]["claims"] == []
    assert body["steps"][0]["analysis_id"] == analysis
    if line is not None:
        assert line in body["steps"][0]["params_plain"]
    _no_student_rows(body)
    assert not _events(app, "data.refused")


def test_the_model_planner_gets_the_forward_instruction(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cabinet.explore.api as explore_api

    stub = StubProvider(
        json.dumps(
            {
                "reasoning": "Past dropout rate in Nursing.",
                "steps": [
                    {
                        "analysis_id": "measure_by_group",
                        "params": {"measure": "dropout_rate", "major": "Nursing"},
                    }
                ],
            }
        )
    )
    monkeypatch.setattr(explore_api, "provider_from_env", lambda: stub)
    monkeypatch.setenv("CABINET_EXPLORE_WRITER", "template")
    # A forward question the rules map is planned by the rules first.
    body = _ask(_client(app), "how many nursing students will drop out?")
    assert body["planner"] == "rule" and not stub.calls
    assert body["answer"][0]["text"] == FORWARD_LEAD
    # One they cannot map goes to the model, with the instruction.
    body = _ask(_client(app), "what will next year look like for nursing?")
    assert body["planner"] == "model"
    assert body["answer"][0]["text"] == FORWARD_LEAD
    role, payload = stub.calls[0]
    assert payload["hint"] == FORWARD_HINT
    system, user = build_explore_prompt(payload, role)
    assert FORWARD_HINT in user and FORWARD_HINT not in system
    # An ordinary question carries no instruction.
    _ask(_client(app), "what is the dropout rate in nursing")
    assert "hint" not in stub.calls[-1][1]


# --- single students and counseling ---------------------------------------------


def test_a_student_by_id_gets_examples_without_planning(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_planning(monkeypatch)
    body = _ask(_client(app), "What is S-1234's GPA?")
    assert body["refused"] is False and body["redirect"] == "individual_student"
    assert body["message"] == INDIVIDUAL_MESSAGE
    assert body["steps"] == [] and body["answer"] == []
    assert 2 <= len(body["suggestions"]) <= 3
    refused = _events(app, "data.refused")[-1]["payload"]
    assert refused["category"] == "individual_student"
    assert "1234" not in json.dumps(_events(app))


@pytest.mark.parametrize(
    ("question", "analysis", "lead"),
    [
        ("Which students are on probation in Nursing?", "standing_by_major", None),
        ("Did Jane Doe pass MEEN 3310?", "course_dfw_trend", None),
        ("Emails of students with a hold", "measure_by_group", None),
        ("Which first-year students are at risk of leaving", "measure_by_group", None),
        ("Who will be suspended next term?", "standing_by_major", PREDICTION_LEAD),
    ],
)
def test_student_questions_get_totals_for_students_like_that(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    question: str,
    analysis: str,
    lead: str | None,
) -> None:
    _no_planning(monkeypatch)  # rules only: a name or id never reaches a model
    body = _ask(_client(app), question)
    assert body["refused"] is False
    assert body["redirect"] in ("individual_student", "prediction")
    first = body["answer"][0]
    assert first["claims"] == []
    assert first["text"] in (lead,) if lead else first["text"] in (
        INDIVIDUAL_LEAD,
        PREDICTION_LEAD,
    )
    assert body["steps"][0]["analysis_id"] == analysis
    _no_student_rows(body)
    text = json.dumps(body)
    assert "Jane" not in text and "Doe" not in text
    assert "@" not in text  # no e-mail address
    events = _events(app)
    refused = [e for e in events if e["type"] == "data.refused"][-1]["payload"]
    assert refused["category"] == body["redirect"]
    answered = _events(app, "explore.answered")[-1]["payload"]
    assert answered["redirect"] == body["redirect"] and answered["planner"] == "rule"


def test_counseling_never_gives_a_figure(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_planning(monkeypatch)
    for question in (
        "How many students saw a counselor?",
        "What share of students in Nursing used CAPS services?",
        "Do students who pray have higher GPAs?",
    ):
        body = _ask(_client(app), question)
        assert body["refused"] is False and body["redirect"] == "counseling"
        assert body["message"] == COUNSELING_MESSAGE
        assert body["answer"] == [] and body["steps"] == []
        assert not re.search(r"\d", body["message"])
        # Related questions, none of them about counseling.
        assert body["suggestions"]
        for suggestion in body["suggestions"]:
            assert refusal_for(suggestion) is None, suggestion
        payload = _events(app, "data.refused")[-1]["payload"]
        assert payload["category"] == "counseling"


# --- off-topic: the only card ---------------------------------------------------

OFF_TOPIC = [
    "code me a website",
    "Write a poem about spring",
    "What's the weather like today?",
    "Tell me a joke",
    "Translate this paragraph into Spanish",
]


@pytest.mark.parametrize("question", OFF_TOPIC)
def test_off_topic_requests_get_the_card_and_no_analysis(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, question: str
) -> None:
    _no_planning(monkeypatch)
    body = _ask(_client(app), question)
    assert body["refused"] is True
    assert body["message"] == OFF_TOPIC_MESSAGE
    assert body["steps"] == [] and body["answer"] == []
    assert not _events(app, "data.granted")
    assert _events(app, "data.refused")[-1]["payload"]["category"] == "off_topic"


def test_off_topic_rules_spare_real_questions(app: FastAPI) -> None:
    from cabinet.explore.catalog import catalog_for, connect_readonly

    con = connect_readonly()
    try:
        names = catalog_for(con).title_names
    finally:
        con.close()
    for question in (
        "How many computer science students are enrolled?",
        "What is the DFW rate in Programming I?",
        "Which majors have the highest dropout rate?",
        "Should we develop a new nursing program?",
        "Build a stronger advising program for freshmen",
        # Student life and words that are off-topic only on their own.
        "how many students live on campus",
        "do athletes have higher GPAs",
        "how many students are in clubs",
        "how many students had a conduct hold",
        "how many students study film",
        "how many students take Python",
        "what's the weather impact on enrollment",
        "how many students applied through the website",
        "hi",
        "What can you do?",
    ):
        assert not is_off_topic(question, names), question
    # Course titles are masked first ("Web Development", "Poetry Writing").
    for title in ("Web Development", "Poetry Writing", "Web Application Development"):
        if title in names:
            question = f"What is the DFW rate in {title}?"
            assert not is_off_topic(question, names), question
            body = _ask(_client(app), question)
            assert body["refused"] is False and body["steps"], question
    assert not is_forward_looking("Will you show me enrollment by term?")


def test_a_model_empty_plan_is_off_topic_only_without_campus_words(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cabinet.explore.api as explore_api

    stub = StubProvider(json.dumps({"reasoning": "Nothing fits.", "steps": []}))
    monkeypatch.setattr(explore_api, "provider_from_env", lambda: stub)
    body = _ask(_client(app), "Who is the best coach in the conference?")
    assert body["refused"] is True and body["message"] == OFF_TOPIC_MESSAGE
    assert _events(app, "data.refused")[-1]["payload"]["category"] == "off_topic"
    body = _ask(_client(app), "What is the parking situation for students?")
    assert body["refused"] is False and body["message"] == UNANSWERABLE_MESSAGE


def test_greetings_keep_their_instant_reply(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_planning(monkeypatch)
    for text in ("hi", "Hello!", "thanks"):
        body = _ask(_client(app), text)
        assert body["refused"] is False and body["message"] == GREETING_MESSAGE


def test_the_hold_rate_is_never_split_by_hold_status() -> None:
    from cabinet.explore.general import GeneralError, check_request

    with pytest.raises(GeneralError):
        check_request("hold_rate", [], {"hold": "hold"})
    with pytest.raises(GeneralError):
        check_request("hold_rate", ["hold"], {})
    check_request("dropout_rate", ["hold"], {})
