"""Answer cards (cabinet.explore.card) and approved decisions reaching the
owning department's inbox (cabinet.inbox.notify_decision)."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cabinet.api import create_app
from cabinet.counseling import SUPPRESSED_DISPLAY
from cabinet.explore import card as card_mod
from cabinet.explore.card import CHART_TEMPLATES, build_card, choose_template
from cabinet.explore.catalog import Catalog, catalog_for, connect_readonly
from cabinet.explore.execute import StepResult, execute
from cabinet.explore.planner import Step, rule_plan_detail
from cabinet.questions import DEMO_DECISION_ID, UNRESOLVED_HOLDS_DECISION_ID
from cabinet.store import CabinetStore
from conftest import make_authenticated_client

GENERATE = Path(__file__).resolve().parents[2] / "data" / "school" / "generate.py"


@pytest.fixture(scope="module")
def school_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("school-cards") / "school.db"
    proc = subprocess.run(
        [sys.executable, str(GENERATE), "--scale", "0.01", "--out", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return out


@pytest.fixture(autouse=True)
def _env(school_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CABINET_SCHOOL_DB", str(school_db))
    monkeypatch.setenv("CABINET_PROVIDER", "fake")


@pytest.fixture
def con(school_db: Path) -> Iterator[sqlite3.Connection]:
    connection = connect_readonly(school_db)
    yield connection
    connection.close()


@pytest.fixture
def catalog(con: sqlite3.Connection) -> Catalog:
    return catalog_for(con)


def _answer(
    question: str, con: sqlite3.Connection, catalog: Catalog, role: str = "executive"
) -> tuple[list[StepResult], card_mod.Card]:
    planned, _ = rule_plan_detail(question, catalog)
    assert planned, question
    steps = execute(planned, con, catalog, role)
    return steps, build_card(steps, question, con, catalog, role)


def _cell(
    steps: list[StepResult], extra: list[StepResult], claim: dict[str, Any]
) -> Any:
    tables = [*steps, *extra]
    return tables[claim["table"]].cell(claim["row"], claim["column"])


def _check_claims(steps: list[StepResult], card: card_mod.Card) -> None:
    """Every claim names a real cell, never a withheld one, and its value is
    written in the sentence."""
    for sentence in [*card.key_points, *card.plan]:
        for claim in sentence.claims:
            value = _cell(steps, card.extra_steps, claim)
            assert value is not None and value != SUPPRESSED_DISPLAY, claim
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                digits = f"{abs(value):,}".rstrip("0").rstrip(".")
                assert digits.split(".")[0] in sentence.text, (sentence.text, value)


# --- key points, chart templates -----------------------------------------------


def test_ranking_key_points_name_highest_lowest_and_gap(
    con: sqlite3.Connection, catalog: Catalog
) -> None:
    steps, card = _answer("What majors have the highest dropout rate?", con, catalog)
    texts = [s.text for s in card.key_points]
    assert texts[0].startswith("Highest: ")
    # A top-10 ranking: its last row is the lowest shown, not the lowest.
    assert texts[1].startswith("Lowest of the 10 shown: ")
    assert any("the whole" in t for t in texts)
    assert 2 <= len(card.key_points) <= 4
    _check_claims(steps, card)
    # The gap is a cell of the card's own Key figures table.
    key = card.extra_steps[-1]
    assert (
        key.columns[2].key == "change" and key.rows[0]["what"] == "Gap from the whole"
    )
    top = steps[0].rows[0]["value"]
    whole = next(r for r in steps[0].rows if r.get("major_name") == "All students")
    assert key.rows[0]["change"] == round(abs(top - whole["value"]), 1)
    assert card.chart is not None and card.chart["template"] == "ranking_bar"
    assert card.chart["reference_row"] == steps[0].rows.index(whole)


def test_trend_reads_the_same_term_a_year_earlier(
    con: sqlite3.Connection, catalog: Catalog
) -> None:
    steps, card = _answer("Headcount by gender", con, catalog)
    trend = card.extra_steps[0]
    assert trend.params["group_by"] == "term"
    assert trend.params["term_from"] == "202520" and trend.params["term_to"] == "202620"
    assert trend.index == len(steps)
    point = next(s for s in card.key_points if "All groups together" in s.text)
    assert "Spring 2026" in point.text and "Spring 2025" in point.text
    now = next(r for r in trend.rows if r.get("term") == "202620")["value"]
    then = next(r for r in trend.rows if r.get("term") == "202520")["value"]
    word = "up" if now > then else "down"
    assert f"{word} {abs(now - then):,}" in point.text
    _check_claims(steps, card)


def test_single_number_gets_a_kpi_with_its_change(
    con: sqlite3.Connection, catalog: Catalog
) -> None:
    steps, card = _answer("How many students are enrolled?", con, catalog)
    assert card.chart is not None and card.chart["template"] == "kpi_number"
    if any(s.text.startswith("A year earlier") for s in card.key_points):
        assert card.chart["trend"]["then_row"] is not None
    _check_claims(steps, card)


def test_a_withheld_term_is_a_gap_never_a_number(
    con: sqlite3.Connection, catalog: Catalog
) -> None:
    # A tiny group at this scale: the trend's terms are withheld.
    step = Step(
        "measure_by_group",
        {"measure": "headcount", "residency": "international", "major": "NURS"},
    )
    steps = execute([step], con, catalog, "executive")
    card = build_card(steps, "international in nursing", con, catalog, "executive")
    _check_claims(steps, card)
    trend = [e for e in card.extra_steps if e.params]
    if trend:
        shown = {r.get("term") for r in trend[0].rows}
        point = next(
            (
                s
                for s in card.key_points
                if "A year earlier" in s.text or "withheld" in s.text
            ),
            None,
        )
        if "202520" not in shown or "202620" not in shown:
            assert point is not None and "withheld" in point.text
            assert point.claims == []


def test_by_term_answer_is_a_trend_line_without_a_second_read(
    con: sqlite3.Connection, catalog: Catalog
) -> None:
    steps, card = _answer("Pell share by term", con, catalog)
    assert card.chart is not None and card.chart["template"] == "trend_line"
    assert all(not e.params for e in card.extra_steps)  # no trend read
    assert card.key_points[0].text.startswith("Latest: ")
    assert card.key_points[1].text.startswith("Since Spring 2025: ")
    _check_claims(steps, card)


def test_two_groupings_choose_grouped_bars_or_small_multiples(
    con: sqlite3.Connection, catalog: Catalog
) -> None:
    _, grouped = _answer("Hold rate by college and class level", con, catalog)
    assert grouped.chart is not None and grouped.chart["template"] == "grouped_bars"
    assert grouped.chart["series"] == "group"
    _, multiples = _answer("Hold rate by term and gender", con, catalog)
    assert multiples.chart is not None
    assert multiples.chart["template"] == "small_multiples"


def test_template_registry_validates_a_suggestion(
    con: sqlite3.Connection, catalog: Catalog
) -> None:
    assert {
        "ranking_bar",
        "trend_line",
        "grouped_bars",
        "kpi_number",
        "share_bar",
        "before_after",
        "funnel",
        "small_multiples",
    } == set(CHART_TEMPLATES)
    steps, _ = _answer("What majors have the highest dropout rate?", con, catalog)
    shape = card_mod._shape(steps[0])
    assert shape is not None
    assert choose_template(shape, "funnel") == "ranking_bar"  # does not fit
    assert choose_template(shape, "not-a-template") == "ranking_bar"
    steps, card = _answer(
        "How much did continuing spring registration change in Spring 2026?",
        con,
        catalog,
    )
    assert card.chart is not None and card.chart["template"] == "before_after"


def test_followups_round_trip_through_the_rule_planner(
    con: sqlite3.Connection, catalog: Catalog
) -> None:
    _, card = _answer("Stop-out rate by first-generation status", con, catalog)
    follow = card.followups
    assert follow["trend"] == "Stop-out rate by term"
    assert len(follow["breakdowns"]) == 3
    for offer in follow["breakdowns"]:
        planned, _ = rule_plan_detail(offer["question"], catalog)
        assert planned and planned[0].params["then_by"] == offer["grouping"]
    _, pell = _answer("Pell share by term", con, catalog)
    assert "pell" not in [b["grouping"] for b in pell.followups["breakdowns"]]
    _, reg = _answer(
        "How much did continuing spring registration change in Spring 2026?",
        con,
        catalog,
    )
    assert reg.followups["topic"] == "registration"


def test_plan_is_proposed_and_names_the_owner(
    con: sqlite3.Connection, catalog: Catalog
) -> None:
    steps, card = _answer("What is the hold rate by college?", con, catalog)
    assert card.plan and "Student Accounts (Bursar)" in card.plan[0].text
    _check_claims(steps, card)


def test_trend_over_budget_is_skipped(
    con: sqlite3.Connection, catalog: Catalog
) -> None:
    planned, _ = rule_plan_detail("Headcount by gender", catalog)
    assert planned is not None
    steps = execute(planned, con, catalog, "executive")
    card = build_card(steps, "q", con, catalog, "executive", trend_budget_s=0.0)
    assert all(not e.params for e in card.extra_steps)
    # The connection still works after the interrupted read.
    assert con.execute("SELECT 1").fetchone() == (1,)


# --- the API ---------------------------------------------------------------------


@pytest.fixture
def app() -> FastAPI:
    return create_app()


def _login(app: FastAPI, role: str, email: str | None = None) -> TestClient:
    return make_authenticated_client(app, role=role, email=email)


def _events(app: FastAPI, event_type: str) -> list[dict[str, Any]]:
    store: CabinetStore = app.state.auth
    institution = store.ensure_bootstrap_institution()
    return store.audit_events(institution, event_type)


def test_explore_returns_the_card_and_audits_the_trend_read(app: FastAPI) -> None:
    registrar = _login(app, "registrar")
    body = registrar.post("/explore", json={"question": "Headcount by gender"}).json()
    card = body["card"]
    assert (
        card["chart"]["template"] == "share_bar"
        or card["chart"]["template"] == "ranking_bar"
    )
    assert [e["analysis_id"] for e in card["extra_steps"]] == [
        "measure_by_group",
        "key_figures",
    ]
    assert card["extra_steps"][0]["purpose"] == "trend"
    tables = [*body["steps"], *card["extra_steps"]]
    for point in card["key_points"]:
        for claim in point["claims"]:
            assert claim["table"] < len(tables)
    granted = [
        e
        for e in _events(app, "data.granted")
        if e["payload"].get("purpose") == "trend"
    ]
    assert len(granted) == 1 and granted[0]["payload"]["step"] == len(body["steps"])
    answered = _events(app, "explore.answered")[-1]["payload"]
    assert answered["card_steps"] == ["measure_by_group"]
    catalog = registrar.get("/explore/catalog").json()
    assert len(catalog["chart_templates"]) == len(CHART_TEMPLATES)


# --- approved decisions reach the department ----------------------------------------


def test_approval_lands_in_the_aid_inbox_with_re_read_figures(app: FastAPI) -> None:
    president = _login(app, "executive")
    aid = _login(app, "aid")
    finance = _login(app, "finance")
    it = _login(app, "it")
    approved = president.post(
        "/decisions/approve", json={"decision_id": DEMO_DECISION_ID}
    )
    assert approved.status_code == 200, approved.text
    delivered = approved.json()["department_inbox"]
    assert [d["to"]["role"] for d in delivered] == ["aid"]

    received = aid.get("/inbox").json()["received"]
    assert len(received) == 1
    message = received[0]
    assert message["source_kind"] == "decision"
    snapshot = message["snapshot"]
    assert snapshot["decision_id"] == DEMO_DECISION_ID
    assert snapshot["office"] == "Financial Aid"
    assert snapshot["action"].startswith("Conduct the emergency-aid")
    assert [f["id"] for f in snapshot["figures"]] == ["M3"]
    assert "row_ids" not in repr(snapshot)
    assert snapshot["due"] == message["review_by"]
    # Only the department: not finance, never IT.
    assert finance.get("/inbox").json()["received"] == []
    assert it.get("/inbox").json()["received"] == []

    events = [e for e in _events(app, "inbox.sent") if e["payload"].get("reason")]
    assert len(events) == 1
    assert events[0]["payload"]["reason"] == "decision.approved"
    assert events[0]["payload"]["decision_id"] == DEMO_DECISION_ID
    assert events[0]["payload"]["recipient_role"] == "aid"

    # Acknowledge, and the president sees it on the decision.
    ack = aid.post(f"/inbox/{message['id']}/reviewed").json()
    assert ack["changed"] is True
    state = president.get(f"/decisions/{DEMO_DECISION_ID}/dispatch").json()
    assert state["department_roles"] == ["aid"]
    assert state["department_inbox"][0]["acknowledged_at"]
    # Approving again sends nothing more.
    again = president.post("/decisions/approve", json={"decision_id": DEMO_DECISION_ID})
    assert again.json()["created"] is False
    assert len(aid.get("/inbox").json()["received"]) == 1


def test_holds_decision_goes_to_finance(app: FastAPI) -> None:
    president = _login(app, "executive")
    finance = _login(app, "finance")
    president.post(
        "/decisions/approve", json={"decision_id": UNRESOLVED_HOLDS_DECISION_ID}
    )
    received = finance.get("/inbox").json()["received"]
    assert len(received) == 1 and received[0]["snapshot"]["office"] == "Bursar"
    assert [f["id"] for f in received[0]["snapshot"]["figures"]] == ["M5", "M3"]


def test_no_department_account_means_no_message_and_says_so(app: FastAPI) -> None:
    president = _login(app, "executive")
    approved = president.post(
        "/decisions/approve", json={"decision_id": DEMO_DECISION_ID}
    ).json()
    assert approved["department_inbox"] == []
    state = president.get(f"/decisions/{DEMO_DECISION_ID}/dispatch").json()
    assert state["department_roles"] == ["aid"] and state["department_inbox"] == []


def test_staff_cannot_approve_so_nothing_is_sent(app: FastAPI) -> None:
    staff = _login(app, "staff")
    aid = _login(app, "aid")
    assert (
        staff.post(
            "/decisions/approve", json={"decision_id": DEMO_DECISION_ID}
        ).status_code
        == 403
    )
    assert aid.get("/inbox").json()["received"] == []
