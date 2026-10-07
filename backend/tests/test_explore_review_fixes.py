"""Fixes from the independent review of the forward/redirect change.

A named or numbered student, in any case, script or number of words, and
lists of at-risk students go to the guarded path: group totals or
suggestions, a ``data.refused`` audit event, the name never stored in the
audit log and never sent to a model. Course codes and catalog names count as
campus words, so a model's empty plan for them is not the off-topic card.
Hold phrasings plan the right measure.
"""

from __future__ import annotations

import json
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
from cabinet.explore.catalog import Catalog, catalog_for, connect_readonly
from cabinet.explore.planner import UNANSWERABLE_MESSAGE, rule_plan
from cabinet.explore.privacy import (
    OFF_TOPIC_MESSAGE,
    aggregate_form,
    is_off_topic,
    mentions_campus_data,
    refusal_for,
    strip_names,
)
from cabinet.provider import Explanation
from conftest import make_authenticated_client

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATE = REPO_ROOT / "data" / "school" / "generate.py"

# (question, the name or id it must never pass on)
NAMED = [
    ("Will Jane drop out?", "Jane"),
    ("Will Bob withdraw?", "Bob"),
    ("Is Maria going to fail MEEN 3310?", "Maria"),
    ("Is Jane likely to fail MEEN 3310?", "Jane"),
    ("Will Jane change her major next year?", "Jane"),
    ("did jane doe pass MEEN 3310?", "jane"),
    ("Did Ana pass MEEN 3310?", "Ana"),
    ("Does Jane Doe have a hold?", "Jane"),
    ("Is Jane Doe on probation?", "Doe"),
    ("What GPA did Jean-Luc Picard get?", "Picard"),
    ("Is José Núñez on probation?", "Núñez"),
    ("Did Jane Doe and John Smith pass MEEN 3310?", "Smith"),
    ("Did Siobhan O'Shaughnessy-Ngata pass MEEN 3310?", "Ngata"),
    ("Will S12345 graduate?", "12345"),
    ("GPA of Jane Doe", "Doe"),
]
LISTS = [
    "List at-risk students",
    "Students most likely to drop out",
    "Which first-gen students will drop out?",
]


@pytest.fixture(scope="session")
def school_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("school_review") / "school.db"
    proc = subprocess.run(
        [sys.executable, str(GENERATE), "--scale", "0.02", "--out", str(out)],
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
    monkeypatch.delenv("CABINET_EXPLORE_PLANNER", raising=False)
    yield


@pytest.fixture(scope="session")
def catalog(school_db: Path) -> Catalog:
    con = connect_readonly(school_db)
    try:
        return catalog_for(con, school_db)
    finally:
        con.close()


@pytest.fixture
def app() -> FastAPI:
    return create_app()


class RecordingModel:
    """A live-looking model that records everything it receives and plans
    nothing (an empty plan)."""

    name = "chat"
    model_label = "stub"

    def __init__(self, plan: str = '{"reasoning": "Nothing fits.", "steps": []}'):
        self.plan = plan
        self.received: list[str] = []

    def explain(self, findings: dict[str, Any], role: str) -> Explanation:
        self.received.append(json.dumps(findings, ensure_ascii=False))
        return Explanation(self.plan, self.name, self.model_label)


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> RecordingModel:
    import cabinet.explore.api as explore_api

    stub = RecordingModel()
    monkeypatch.setattr(explore_api, "provider_from_env", lambda: stub)
    return stub


def _client(app: FastAPI) -> TestClient:
    return make_authenticated_client(app, role="executive")


def _ask(client: TestClient, question: str) -> dict[str, Any]:
    response = client.post("/explore", json={"question": question})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _events(app: FastAPI, event_type: str | None = None) -> list[dict[str, Any]]:
    store: AuthStore = app.state.auth
    institution = store.institution_by_slug("bootstrap")
    assert institution is not None
    return store.audit_events(int(institution["id"]), event_type)


# --- 1 and 3: named students and lists never reach a model; names never logged --


@pytest.mark.parametrize(("question", "secret"), NAMED)
def test_a_named_student_is_guarded_audited_and_never_sent_to_a_model(
    app: FastAPI, model: RecordingModel, question: str, secret: str
) -> None:
    body = _ask(_client(app), question)
    assert body["refused"] is False
    assert body["redirect"] in ("individual_student", "prediction"), body
    assert secret not in json.dumps(body, ensure_ascii=False)
    # The model received nothing with the name (here: nothing at all).
    assert not any(secret in text for text in model.received)
    events = _events(app)
    assert any(
        e["type"] == "data.refused" and e["payload"]["category"] == body["redirect"]
        for e in events
    )
    logged = json.dumps([e["payload"] for e in events], ensure_ascii=False)
    assert secret not in logged, logged


@pytest.mark.parametrize("question", LISTS)
def test_lists_of_at_risk_students_are_guarded(
    app: FastAPI, model: RecordingModel, question: str
) -> None:
    body = _ask(_client(app), question)
    assert body["refused"] is False and body["redirect"] == "prediction"
    assert not model.received
    for step in body["steps"]:
        assert step["aggregate_only"] is True
    assert _events(app, "data.refused")[-1]["payload"]["category"] == "prediction"


def test_group_questions_are_not_mistaken_for_names(catalog: Catalog) -> None:
    for question in (
        "Will enrollment fall next year?",
        "Will nursing students drop out?",
        "Is retention going to fall?",
        "Did students pass MEEN 3310?",
        "How did Nursing do last year?",
        "Will Pell students graduate?",
        "Did Hispanic students graduate faster?",
        "Which course has the highest withdrawal rate online?",
        "How much did continuing spring registration change in Spring 2026?",
        "What has Alicia Shelby taught?",
        "what share of at-risk students are first-gen",
        "which majors have the most at-risk students",
        "at-risk students in nursing",
    ):
        assert refusal_for(question, catalog.title_names) is None, question


# --- 4 and 5: ids as typed, several names, hyphenated names ----------------------


def test_an_id_is_read_before_folding_and_every_name_is_removed() -> None:
    text, named = aggregate_form("Will S12345 graduate?")
    assert named and "12345" not in text and "students" in text
    text, named = aggregate_form("Did Jane Doe and John Smith pass MEEN 3310?")
    assert named
    for word in ("Jane", "Doe", "John", "Smith"):
        assert word not in text
    text, _ = aggregate_form("Did Siobhan O'Shaughnessy-Ngata pass MEEN 3310?")
    assert "Shaughnessy" not in text and "Ngata" not in text and "MEEN 3310" in text
    assert strip_names("Did Jane Doe and John Smith pass MEEN 3310?") == (
        "Did [name withheld] and [name withheld] pass MEEN 3310?"
    )


# --- 2 and 6: campus words, and the off-topic card only off topic ----------------


def test_course_codes_and_catalog_names_are_campus_words(catalog: Catalog) -> None:
    names = catalog.title_names
    for question in (
        "What is the pass rate in MEEN 3310?",
        "How many people are in nursing?",
        "Which department has the most applicants?",
        "Thermodynamics I",
    ):
        assert mentions_campus_data(question, names), question
        assert not is_off_topic(question, names), question
    assert not mentions_campus_data("Who is the best coach in the conference?", names)


def test_a_model_decline_with_campus_words_is_not_the_card(
    app: FastAPI, model: RecordingModel
) -> None:
    client = _client(app)
    for question in ("What is the pass rate in MEEN 3310 for transfers?",):
        body = _ask(client, question)
        assert body["refused"] is False, question
    body = _ask(client, "How many people are in the marching band?")
    assert body["refused"] is False and body["message"] == UNANSWERABLE_MESSAGE
    assert body["suggestions"]


def test_an_off_topic_model_decline_is_audited_as_after_the_model(
    app: FastAPI, model: RecordingModel
) -> None:
    body = _ask(_client(app), "Who is the best coach in the conference?")
    assert body["refused"] is True and body["message"] == OFF_TOPIC_MESSAGE
    assert model.received  # the model was asked
    payload = _events(app, "data.refused")[-1]["payload"]
    assert payload["category"] == "off_topic"
    assert "model call" not in payload["before"]
    # A rule-caught off-topic request never reaches the model.
    before = len(model.received)
    body = _ask(_client(app), "code me a website")
    assert body["refused"] is True and len(model.received) == before
    assert _events(app, "data.refused")[-1]["payload"]["before"] == (
        "planning and any model call"
    )


# --- 7: hold phrasings ------------------------------------------------------------


def test_hold_phrasings_plan_the_right_measure(catalog: Catalog) -> None:
    cases = {
        "How many nursing students have no hold?": {
            "measure": "headcount",
            "hold": "no_hold",
            "major": "NURS",
        },
        "Headcount by major and hold status": {
            "measure": "headcount",
            "group_by": "major",
            "then_by": "hold",
        },
        "GPA by hold status for first-gen students": {
            "measure": "avg_gpa",
            "group_by": "hold",
            "first_generation": "first_generation",
        },
        "Dropout rate by hold status and major": {
            "measure": "dropout_rate",
            "group_by": "hold",
            "then_by": "major",
        },
    }
    for question, params in cases.items():
        steps = rule_plan(question, catalog)
        assert steps is not None and len(steps) == 1, question
        assert steps[0].analysis_id == "measure_by_group", question
        assert steps[0].params == params, (question, steps[0].params)
