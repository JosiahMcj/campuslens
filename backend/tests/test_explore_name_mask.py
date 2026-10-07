"""Allow-list name masking (second review).

Every capitalized word that is not a catalog name, a campus phrase, an
acronym or a common word is treated as a person's name, as are lowercase
words in a name position. A masked question takes the guarded path (group
totals or suggestions, audited as a refusal), the audit log stores it with
"[name withheld]", and the stub model below records every input it receives:
no name ever appears in it. Questions about groups, colleges and offices are
never masked.
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
from cabinet.explore.catalog import Catalog, catalog_for, connect_readonly
from cabinet.explore.evalset import EVAL_SET, FINANCE_SET, FORWARD_SET, HELD_OUT
from cabinet.explore.planner import (
    EXAMPLE_QUESTIONS,
    RULE_PHRASINGS,
    UNANSWERABLE_MESSAGE,
    planner_vocabulary,
)
from cabinet.explore.privacy import (
    _allowed_words,
    _is_allowed,
    _protected_spans,
    mask_names,
    refusal_for,
    safe_text,
)
from cabinet.provider import Explanation
from conftest import make_authenticated_client
from test_explore_general import GENERAL_PHRASINGS
from test_explore_model_planner import HEADCOUNT_PHRASINGS

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATE = REPO_ROOT / "data" / "school" / "generate.py"

# (question, words of the name that must never be stored or sent)
ADVERSARIAL = [
    ("Tell me about Kenji Watanabe in Nursing", ("Kenji", "Watanabe")),
    ("Show the transcript of student Liam O'Brien", ("Liam", "Brien")),
    ("Can you check if Fatima al-Sayed passed Calculus I?", ("Fatima", "Sayed")),
    ("mary jane watson's gpa please", ("mary", "watson")),
    ("Has Zoë Ångström been suspended?", ("Zoë", "Ångström")),
    ("Is it true that Carlos Mendez failed MEEN 3310?", ("Carlos", "Mendez")),
    ("Compare Jane Doe with other nursing students", ("Jane",)),
    ("How many classes is Aaliyah Brooks taking this term?", ("Aaliyah", "Brooks")),
    (
        "I'm advising Hannah Lee; what is the DFW rate in her courses?",
        ("Hannah", " Lee"),
    ),
    ("Did Jane Doe's roommate drop out?", ("Jane",)),
    ("Did the student named Ravi pass MEEN 3310?", ("Ravi",)),
    # Third review: common words used as names, sentence-initial names,
    # capitals, lowercase, titles, ids written as emails or handles.
    ("Did May pass MEEN 3310?", ("May",)),
    ("How did Christian do in Calculus I?", ("Christian",)),
    ("Ravi failed MEEN 3310, right?", ("Ravi",)),
    ("Jane withdrew from Calculus I last fall; how common is that?", ("Jane",)),
    ("Jordan failed MEEN 3310.", ("Jordan",)),
    ("Hunter and Brooklyn both dropped out of nursing", ("Hunter", "Brooklyn")),
    ("JOHN SMITH failed MEEN 3310 - what is the DFW rate?", ("JOHN", "SMITH")),
    ("Did KIM pass MEEN 3310?", ("KIM",)),
    ("Has jose garcia registered for spring?", ("jose", "garcia")),
    ("dr. patel's advisee sam failed calc, is that common?", ("patel", " sam ")),
    ('Is "jdoe" on academic probation?', ("jdoe",)),
    ("Did jdoe@demo.test pass MEEN 3310?", ("jdoe", "demo.test")),
    ("Will Faith graduate this spring?", ("Faith",)),
    ("What's Ricky's GPA?", ("Ricky",)),
]
NOT_PEOPLE = [
    "Does Engineering have a high DFW rate?",
    "Does Student Accounts have the most holds?",
    "Does Main Campus have more holds?",
    "How many Texas residents are enrolled?",
    "How many students did Dr. Alicia Shelby teach?",
    "What is the DFW rate in Intro to Python?",
]


@pytest.fixture(scope="session")
def school_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("school_mask") / "school.db"
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

    def __init__(self) -> None:
        self.received: list[str] = []

    def explain(self, findings: dict[str, Any], role: str) -> Explanation:
        self.received.append(json.dumps(findings, ensure_ascii=False))
        return Explanation('{"reasoning": "x", "steps": []}', self.name, "stub")


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


def _assert_only_allowed(text: str, catalog: Catalog) -> None:
    """Every word outside a placeholder or a catalog name is allow-listed."""
    allowed = _allowed_words(catalog.known_names) | planner_vocabulary(catalog)
    spans = _protected_spans(text, catalog.known_names)
    spans += [m.span() for m in re.finditer(r"\[[^\]]*\](?:'s)?", text)]
    for m in re.finditer(r"[^\W\d_][\w'’-]*", text):
        if any(a <= m.start() and m.end() <= b for a, b in spans):
            continue
        token = m.group(0)
        assert _is_allowed(token, allowed) or token.lower() in allowed, (token, text)


@pytest.mark.parametrize(("question", "secrets"), ADVERSARIAL)
def test_names_are_masked_before_the_model_and_the_audit_log(
    app: FastAPI,
    model: RecordingModel,
    catalog: Catalog,
    question: str,
    secrets: tuple[str, ...],
) -> None:
    masked, hidden = mask_names(question, catalog.known_names)
    assert hidden, question
    for secret in secrets:
        assert secret not in masked
    # What a model would receive keeps allow-listed words only.
    _assert_only_allowed(
        safe_text(question, catalog.known_names, planner_vocabulary(catalog)),
        catalog,
    )
    body = _ask(_client(app), question)
    assert body["refused"] is False
    assert body["redirect"] in ("individual_student", "prediction")
    response = json.dumps(body, ensure_ascii=False)
    logged = json.dumps([e["payload"] for e in _events(app)], ensure_ascii=False)
    for secret in secrets:
        assert secret not in response, secret
        assert secret not in logged, (secret, logged)
        assert not any(secret in text for text in model.received)
    assert any(
        e["payload"].get("category") == body["redirect"]
        for e in _events(app, "data.refused")
    )
    asked = _events(app, "question.asked")[-1]["payload"]["question"]
    assert "[name withheld]" in asked or "[id]" in asked
    _assert_only_allowed(asked, catalog)


@pytest.mark.parametrize("question", NOT_PEOPLE)
def test_colleges_and_offices_are_not_people(
    app: FastAPI, model: RecordingModel, catalog: Catalog, question: str
) -> None:
    assert mask_names(question, catalog.known_names)[1] == []
    assert refusal_for(question, catalog.known_names) is None
    body = _ask(_client(app), question)
    assert "redirect" not in body
    assert not _events(app, "data.refused")


def test_no_ordinary_question_is_masked(catalog: Catalog) -> None:
    """Every evaluation, example and phrasing question keeps every word."""
    protected = {
        "can you list every student on probation",
        "what is S-1234's gpa",
    }
    questions = [
        *(q for q, _ in EVAL_SET),
        *(q for q, _ in HELD_OUT),
        *(q for q, _ in FORWARD_SET),
        *(q for q, _ in FINANCE_SET),
        *(q for q, _ in RULE_PHRASINGS),
        *EXAMPLE_QUESTIONS,
        *(q for q, _ in GENERAL_PHRASINGS),
        *(q for q, _ in HEADCOUNT_PHRASINGS),
        "Will Pell students graduate?",
        "What has Alicia Shelby taught?",
        "Did Hispanic students graduate faster?",
        "How did Nursing do last year?",
        "Show the teaching history of I-0003.",
        "What is our yield?",
    ]
    assert len(questions) > 150
    for question in questions:
        masked, hidden = mask_names(question, catalog.known_names)
        assert hidden == [] or question in protected, (question, hidden)
        if question not in protected:
            safe = safe_text(question, catalog.known_names, planner_vocabulary(catalog))
            assert "[name]" not in safe, (question, safe)
        if question not in protected:
            assert refusal_for(question, catalog.known_names) is None, question


def test_group_measures_for_forward_and_list_questions(
    app: FastAPI, model: RecordingModel
) -> None:
    client = _client(app)
    body = _ask(client, "Will Pell students graduate?")
    lines = body["steps"][0]["params_plain"]
    assert "Measure: 6-year graduation rate" in lines
    assert any("Pell" in line for line in lines), lines
    for question in (
        "Which first-gen students will drop out?",
        "List at-risk students",
    ):
        body = _ask(client, question)
        first = body["steps"][0]["params_plain"]
        assert first[0] == "Measure: dropout rate", (question, first)
    body = _ask(client, "What is our yield?")
    assert body["refused"] is False and body["message"] == UNANSWERABLE_MESSAGE


def test_the_model_receives_allow_listed_words_only(
    app: FastAPI, model: RecordingModel, catalog: Catalog
) -> None:
    """An unknown lowercase word that is not in a name position is not
    guarded, but the model still receives "[name]" for it."""
    body = _ask(_client(app), "what is the dropout rate for blorft students")
    assert "redirect" not in body
    assert model.received, "the model planner was asked"
    sent = json.loads(model.received[-1])["question"]
    assert "blorft" not in sent and "[name]" in sent
    _assert_only_allowed(sent, catalog)
    asked = _events(app, "question.asked")[-1]["payload"]["question"]
    assert "blorft" not in asked
