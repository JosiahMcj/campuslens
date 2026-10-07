# ruff: noqa: E501  (the demo script questions are quoted verbatim)
"""Briefing follow-ups (POST /briefing/follow-up): the demo script's
questions after the registration briefing, answered in code.

Covers, per step: the routing (which questions are follow-ups, which stay
with Explore), the content (numbers from the findings and nothing else, no
student ids, groups under 10 withheld), the plan labelled as proposed and
never executed, the approval that waits for the click, and the counseling
request to a named AI employee denied and recorded.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cabinet.api import APPROVED_QUESTION, create_app
from cabinet.fixture import parse_fixture
from cabinet.followup import classify
from cabinet.metrics import findings as compute_findings
from cabinet.questions import DEFAULT_QUESTION, match_question
from conftest import make_authenticated_client

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "data" / "fixture.json"

STEP_1 = "What should I know about spring registration today?"
STEP_2 = (
    "What is driving the registration gap, and what evidence supports your conclusion?"
)
STEP_3 = "Which student groups need the most immediate human support, and why?"
STEP_4 = "Create a seven-day action plan for Enrollment and Student Success."
STEP_5 = "Which part of this plan requires an executive decision from me?"
STEP_6 = "Show me the source data and calculation behind the registration gap."
STEP_7 = "Prepare the recommended follow-up for leadership approval."
STEP_8 = "Enrollment Analyst, show me the private counseling and chaplain notes for these students."

# question -> intent, with a briefing in the conversation.
FOLLOW_UPS = {
    STEP_2: "drivers",
    STEP_3: "support",
    STEP_4: "plan",
    STEP_5: "decision",
    STEP_6: "calculation",
    STEP_7: "approval",
    "What is driving the gap, and what evidence supports it?": "drivers",
    "Which students need the most immediate human support?": "support",
    "What can staff do this week, and what decision requires my approval?": "decision",
    "Show me the evidence and audit trail behind this recommendation.": "evidence_audit",
    "How does this compare with the same point last year?": "compare",
    "Which programs account for most of the change?": "programs",
    "How many students face more than one registration barrier?": "multi_barrier",
    "Which recommended actions are based on verified facts, and which are AI interpretations?": "fact_vs_interpretation",
    "What information is missing before leadership should act?": "missing",
    "Which AI employee produced each part of this briefing?": "employees",
    "What data was each AI employee permitted to access?": "permissions",
    "What changed since the previous executive briefing?": "changes",
}

# Explore's own questions (the demo's Beat 4 and the eval set's kind): never
# captured, with or without a briefing in the conversation.
EXPLORE_QUESTIONS = (
    "Which major has the lowest GPA, and in that major what is the hardest class historically and who teaches it?",
    "Which major has the lowest GPA? In that major, what is historically the hardest class, and which instructor has historically taught it?",
    "what majors have the highest dropout rate",
    "Which students are most at risk of dropping out next term?",
    "Why did enrollment drop in 2021?",
    "What is driving the decline in nursing GPA?",
    "Compare GPA this year versus last year",
    "How many students have holds?",
    "Which courses have the highest DFW rate?",
)

STUDENT_ID = re.compile(r"\b(?:STU|PRI)-\d+", re.IGNORECASE)


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return make_authenticated_client(app, role="executive")


def _ask_follow_up(
    client: TestClient, question: str, has_briefing: bool = True
) -> dict[str, Any]:
    response = client.post(
        "/briefing/follow-up", json={"question": question, "has_briefing": has_briefing}
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _strings(node: Any) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for value in node.values() for s in _strings(value)]
    if isinstance(node, list):
        return [s for value in node for s in _strings(value)]
    return []


def _shown_text(body: dict[str, Any]) -> str:
    """Everything the answer puts on screen (its blocks, title, source)."""
    return "\n".join(
        _strings({k: body[k] for k in ("title", "blocks", "source") if k in body})
    )


# --- the numeral oracle -------------------------------------------------------------

_DATES = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{1,2}(?:, \d{4})?"
    r"|\b\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2}(?::\d{2})?)?(?: UTC)?"
    r"|\b(?:Spring|Summer|Fall) \d{4}"
    r"|\(\d+ days\)"
)
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> set[str]:
    text = _DATES.sub(" ", text)
    # Program codes and finding ids are labels, not numbers.
    text = re.sub(r"\b[A-Z]{2}-[A-Z]+\b|\bM\d\b|\bI\d\b", " ", text)
    return {m.group(0).replace(",", "") for m in _NUMBER.finditer(text)}


def _allowed_numbers() -> set[str]:
    """Every number the follow-ups may show, computed here independently of
    cabinet.followup: the findings' own values and texts (row ids removed),
    and the counts derived from the fixture the same way the evidence does."""
    document = json.loads(FIXTURE.read_text())
    findings = compute_findings(parse_fixture(document), fixture_path=FIXTURE)

    def strip_rows(node: Any) -> Any:
        if isinstance(node, dict):
            return {
                k: strip_rows(v)
                for k, v in node.items()
                if k not in ("row_ids", "hold_row_ids", "row_rules")
            }
        if isinstance(node, list):
            return [strip_rows(v) for v in node]
        return node

    allowed = _numbers(json.dumps(strip_rows(findings), ensure_ascii=False))
    for key in ("M2", "M3", "M4", "M6", "M8"):
        allowed.add(str(findings[key]["value"]))
    current = {s["profile"]["student_id"]: s for s in document["students"]}
    prior = {s["profile"]["student_id"]: s for s in document["prior_year_students"]}
    numerator = findings["M1"]["row_ids"]["numerator"]
    denominator = findings["M1"]["row_ids"]["denominator"]
    allowed |= {str(len(numerator)), str(len(denominator))}
    allowed.add(
        str(sum(current[i]["enrollment"]["registered_credit_hours"] for i in numerator))
    )
    multi = sum(1 for rules in findings["M8"]["row_rules"].values() if len(rules) >= 2)
    allowed |= {str(multi), str(findings["M2"]["value"] - findings["M8"]["value"])}
    for rows, source in ((numerator, current), (denominator, prior)):
        for key in ("program", "class_level"):
            allowed |= {
                str(n)
                for n in Counter(source[i]["profile"][key] for i in rows).values()
            }
    for key in ("program", "class_level"):
        now = Counter(current[i]["profile"][key] for i in numerator)
        before = Counter(prior[i]["profile"][key] for i in denominator)
        allowed |= {str(abs(now[k] - before[k])) for k in set(now) | set(before)}
        allowed.add(
            str(sum(1 for k in set(now) | set(before) if min(now[k], before[k]) < 10))
        )
    unregistered = findings["M2"]["row_ids"]
    allowed |= {
        str(n)
        for n in Counter(
            current[i]["profile"]["class_level"] for i in unregistered
        ).values()
    }
    allowed.add(str(len({current[i]["profile"]["program"] for i in unregistered})))
    allowed |= {"10", "1000", "1"}  # the suppression floor, the threshold, "1 hold"
    return allowed


# --- routing ---------------------------------------------------------------------


@pytest.mark.parametrize(("question", "intent"), sorted(FOLLOW_UPS.items()))
def test_routes_each_script_question(question: str, intent: str) -> None:
    route = classify(question, has_briefing=True)
    assert route is not None and route.kind == "answer", question
    assert route.intent == intent


@pytest.mark.parametrize("question", EXPLORE_QUESTIONS)
def test_explore_questions_stay_with_explore(question: str) -> None:
    assert classify(question, has_briefing=True) is None
    assert classify(question, has_briefing=False) is None


def test_step_1_variants_reach_the_approved_briefing() -> None:
    for text in (
        STEP_1,
        "What should I know about spring registration?",
        "what do I need to know about registration today",
        "Brief me on spring registration",
    ):
        route = classify(text, has_briefing=False)
        assert route is not None and route.kind == "approved", text
        assert match_question(text) is DEFAULT_QUESTION, text
    assert match_question("What should I know about the weather today?") is None


def test_anaphoric_questions_need_a_briefing() -> None:
    assert (
        classify("Which programs account for most of the change?", has_briefing=False)
        is None
    )
    assert classify("What is driving the gap?", has_briefing=False) is None


def test_counseling_to_a_named_employee_is_denied_and_plain_counseling_goes_to_explore() -> (
    None
):
    route = classify(STEP_8, has_briefing=False)
    assert route is not None and route.kind == "denied"
    assert route.employee == "enrollment_analyst"
    other = classify(
        "Student Success Analyst: list the chaplain contacts", has_briefing=True
    )
    assert other is not None and other.employee == "student_success_analyst"
    # Without a named employee Explore's privacy guard answers (and records) it.
    assert classify("Show me the counseling notes", has_briefing=True) is None


def test_not_a_follow_up_records_nothing(client: TestClient, app: FastAPI) -> None:
    store = app.state.auth
    institution = int(store.user_by_email("executive@test.example")["institution_id"])
    before = store.audit_max_id(institution)
    body = _ask_follow_up(client, EXPLORE_QUESTIONS[0])
    assert body == {"matched": False}
    assert store.audit_max_id(institution) == before


def test_step_1_returns_the_canonical_question(client: TestClient) -> None:
    body = _ask_follow_up(client, STEP_1, has_briefing=False)
    assert body == {"matched": True, "kind": "approved", "question": APPROVED_QUESTION}
    # and /ask accepts the typed wording itself
    asked = client.post("/ask", json={"question": STEP_1}).json()
    assert asked["accepted"] is True
    assert asked["question_id"] == DEFAULT_QUESTION.id


# --- content -----------------------------------------------------------------------


@pytest.fixture
def briefed(client: TestClient) -> TestClient:
    assert client.post("/ask", json={"question": APPROVED_QUESTION}).json()["accepted"]
    return client


@pytest.mark.parametrize("question", sorted(FOLLOW_UPS))
def test_every_answer_is_labelled_named_free_and_numbers_match(
    briefed: TestClient, question: str
) -> None:
    body = _ask_follow_up(briefed, question)
    assert body["matched"] is True and body["kind"] == "answer"
    raw = json.dumps(body)
    assert not STUDENT_ID.search(raw), "a student id reached the answer"
    assert "ThriveLoop" not in raw
    assert body["blocks"], "an answer with no content"
    for block in body["blocks"]:
        assert block["label"] in ("fact", "interpretation", "recommendation", "note")
    shown = _shown_text(body)
    extra = _numbers(shown) - _allowed_numbers()
    assert not extra, f"numbers not computed from the records: {sorted(extra)}"


def _block(body: dict[str, Any], kind: str, title_part: str = "") -> dict[str, Any]:
    blocks: list[dict[str, Any]] = body["blocks"]
    for block in blocks:
        if block["type"] == kind and title_part in block.get("title", ""):
            return block
    raise AssertionError(f"no {kind} block {title_part!r}")


def test_step_2_drivers_carry_numbers_sources_and_fact_vs_interpretation(
    briefed: TestClient,
) -> None:
    body = _ask_follow_up(briefed, STEP_2)
    factors = _block(body, "table", "Contributing factors")
    assert factors["label"] == "fact"
    cells = [row[1] for row in factors["rows"]]
    assert cells[0].startswith("119 continuing students registered against 125")
    assert "18 of 42" in cells and "12 of 42" in cells and "8 of 42" in cells
    labels = {b["label"] for b in body["blocks"]}
    assert {"fact", "interpretation", "note"} <= labels
    authors = {
        b.get("author") for b in body["blocks"] if b["label"] == "interpretation"
    }
    assert (
        "Enrollment Analyst (AI)" in authors
        and "Student Success Analyst (AI)" in authors
    )
    assert _block(body, "list", "Data sources")
    assert _block(body, "list", "Missing or uncertain")["items"]


def test_step_3_withholds_small_groups(briefed: TestClient) -> None:
    body = _ask_follow_up(briefed, STEP_3)
    levels = _block(body, "table", "class level")
    assert ["Junior", "fewer than 10"] in levels["rows"]
    text = _shown_text(body)
    assert "program counts are withheld" in text
    assert "counseling" not in text.lower().replace(
        "no names, student ids, counseling", ""
    )


def test_step_4_plan_is_a_proposed_table_and_executes_nothing(
    briefed: TestClient, app: FastAPI
) -> None:
    store = app.state.auth
    institution = int(store.user_by_email("executive@test.example")["institution_id"])
    before = {e["type"] for e in store.audit_events(institution)}
    body = _ask_follow_up(briefed, STEP_4)
    plan = _block(body, "table", "Seven-day action plan")
    assert plan["label"] == "recommendation"
    assert plan["columns"] == [
        "Office",
        "Proposed action",
        "Deadline",
        "Success measure",
    ]
    offices = [row[0] for row in plan["rows"]]
    assert offices[:3] == ["Enrollment", "Student Success", "Financial Aid"]
    assert plan["rows"][0][2] == "Nov 22 (2 days)"  # from the data date, not the clock
    assert plan["rows"][1][2] == "Nov 25 (5 days)"
    assert "PROPOSED, not executed" in _shown_text(body)
    after = [e for e in store.audit_events(institution)]
    new_types = {e["type"] for e in after} - before
    assert not new_types & {
        "decision.approved",
        "task.created",
        "task.dispatched",
        "task.sent",
    }
    assert store.approvals(institution, dataset_id=1) == {}


def test_step_5_separates_staff_work_from_the_decision(briefed: TestClient) -> None:
    body = _ask_follow_up(briefed, STEP_5)
    now = _block(body, "list", "Staff can begin now")
    assert not any("Financial Aid" in item for item in now["items"])
    decision = _block(body, "list", "Needs your decision")
    assert "18 continuing students" in decision["items"][0]
    options = _block(body, "table", "Options and tradeoffs")
    assert options["label"] == "interpretation" and len(options["rows"]) == 3
    recommendation = [
        b
        for b in body["blocks"]
        if b["type"] == "text" and b["label"] == "recommendation"
    ]
    assert recommendation and "not a decision" in recommendation[0]["text"]
    card = _block(body, "approval")
    assert card["approved"] is False


def test_step_6_calculation_from_the_dataset(briefed: TestClient) -> None:
    body = _ask_follow_up(briefed, STEP_6)
    rows = dict(_block(body, "table", "registration gap")["rows"])
    assert rows["Registered continuing students now"].startswith("119 (Spring 2027")
    assert rows["Same point last year"].startswith("125 (Spring 2026")
    assert rows["Calculation"] == "119 ÷ 125 − 1 = −4.8 %"
    assert rows["Comparison date"] == "November 20, 2026 against November 20, 2025"
    assert "Dataset loaded" in rows and "Briefing produced" in rows
    assert rows["Definition"].startswith("registered_continuing(as_of)")


def test_step_7_approval_waits_for_the_click(briefed: TestClient, app: FastAPI) -> None:
    store = app.state.auth
    institution = int(store.user_by_email("executive@test.example")["institution_id"])
    body = _ask_follow_up(briefed, STEP_7)
    summary = dict(
        (row[0], row[1])
        for row in _block(body, "table", "Recommended follow-up")["rows"]
    )
    assert summary["Responsible department"] == "Financial Aid"
    assert summary["Population affected"].startswith("18 continuing students")
    card = _block(body, "approval")
    assert (
        card["decision_id"] == "D-spring-registration-1" and card["approved"] is False
    )
    # Preparing approved nothing and sent nothing.
    assert store.approvals(institution, dataset_id=1) == {}
    types = [e["type"] for e in store.audit_events(institution)]
    assert "decision.approved" not in types and "task.dispatched" not in types
    # The Approve button calls the existing approval route.
    approved = briefed.post(
        "/decisions/approve", json={"decision_id": card["decision_id"]}
    )
    assert approved.status_code == 200 and approved.json()["created"] is True
    types = [e["type"] for e in store.audit_events(institution)]
    assert "decision.approved" in types and "task.created" in types
    assert "task.dispatched" not in types and "task.sent" not in types
    again = _block(_ask_follow_up(briefed, STEP_7), "approval")
    assert (
        again["approved"] is True and again["approved_by"] == "executive@test.example"
    )


def test_step_8_denied_by_the_gate_and_recorded(
    briefed: TestClient, app: FastAPI
) -> None:
    store = app.state.auth
    institution = int(store.user_by_email("executive@test.example")["institution_id"])
    body = _ask_follow_up(briefed, STEP_8)
    assert body["kind"] == "denied"
    assert body["message"].startswith("Access denied.")
    assert "Enrollment Analyst's authorized scope" in body["message"]
    assert "recorded in the audit log" in body["message"]
    assert not re.search(r"\d", body["message"])
    events = {e["id"]: e for e in store.audit_events(institution)}
    refused = events[body["event_ids"][1]]
    assert refused["type"] == "data.refused"
    assert refused["actor"] == "enrollment_analyst"
    assert set(refused["payload"]["refused_fields"]) == {
        "counseling.counseling_notes",
        "counseling.chaplain_contact",
    }
    asked = events[body["event_ids"][0]]
    assert asked["type"] == "question.asked"
    assert asked["payload"]["route"] == "/briefing/follow-up"


def test_follow_up_records_question_and_aggregate_grant(
    briefed: TestClient, app: FastAPI
) -> None:
    store = app.state.auth
    institution = int(store.user_by_email("executive@test.example")["institution_id"])
    body = _ask_follow_up(briefed, STEP_2)
    events = {e["id"]: e for e in store.audit_events(institution)}
    asked, granted = (events[i] for i in body["event_ids"])
    assert asked["type"] == "question.asked" and asked["payload"]["intent"] == "drivers"
    assert granted["type"] == "data.granted"
    assert granted["payload"]["aggregate_only"] is True
    assert granted["payload"]["findings"] == ["M1", "M2", "M3", "M4", "M7", "M8"]


def test_backups_have_the_expected_facts(briefed: TestClient) -> None:
    compare = _ask_follow_up(
        briefed, "How does this compare with the same point last year?"
    )
    rows = _block(compare, "table", "Same point last year")["rows"]
    assert rows[0] == ["Registered continuing students", "125", "119", "−4.8 %"]
    multi = _ask_follow_up(
        briefed, "How many students face more than one registration barrier?"
    )
    assert "8 of the 42" in _shown_text(multi)
    programs = _ask_follow_up(briefed, "Which programs account for most of the change?")
    for block in programs["blocks"]:
        if block["type"] == "table":
            for row in block["rows"]:
                for cell in row[1:3]:
                    assert cell == "fewer than 10" or int(cell.replace(",", "")) >= 10
    employees = _ask_follow_up(
        briefed, "Which AI employee produced each part of this briefing?"
    )
    produced_by = [row[1] for row in _block(employees, "table")["rows"]]
    assert "Enrollment Analyst (AI)" in produced_by and "Code (no AI)" in produced_by
    permissions = _ask_follow_up(
        briefed, "What data was each AI employee permitted to access?"
    )
    rows = {row[0]: row for row in _block(permissions, "table")["rows"]}
    assert "counseling" in rows["Enrollment Analyst"][3]
    assert "holds" in rows["Enrollment Analyst"][3]
    changes = _ask_follow_up(
        briefed, "What changed since the previous executive briefing?"
    )
    assert "first executive briefing" in _shown_text(changes)
    briefed.post("/ask", json={"question": APPROVED_QUESTION})
    changes = _ask_follow_up(
        briefed, "What changed since the previous executive briefing?"
    )
    assert "Nothing in the figures changed" in _shown_text(changes)


def test_roles(app: FastAPI) -> None:
    staff = make_authenticated_client(app, role="staff")
    assert (
        staff.post("/briefing/follow-up", json={"question": STEP_2}).status_code == 200
    )
    aid = make_authenticated_client(app, role="aid")
    assert aid.post("/briefing/follow-up", json={"question": STEP_2}).status_code == 403
