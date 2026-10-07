"""The AI staff (cabinet.staff): one employee per department.

Covers the permission table for every employee (fields, findings, task
fields, school-data areas), the routing map (every measure, grouping and
analysis owned, every routed step inside the reading employee's areas), the
audit attribution of Explore answers (one ``data.granted`` per employee),
the live delegation trace, the counseling denial naming the employee it was
asked of, and ``GET /staff``.
"""

from __future__ import annotations

import itertools
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cabinet import staff
from cabinet.api import create_app
from cabinet.audit import AuditLog
from cabinet.explore import general
from cabinet.explore.catalog import ANALYSES
from cabinet.explore.execute import COURSE_SUMMARY_FIELDS
from cabinet.explore.privacy import counseling_message
from cabinet.fixture import load_fixture
from cabinet.metrics import findings as compute_findings
from cabinet.permissions import (
    AGGREGATE_ONLY,
    ALL_FIELDS,
    AUTHORIZED_AGGREGATE_FINDINGS,
    BRIEFING_ROLES,
    DEPARTMENT_EMPLOYEES,
    INSTRUCTOR_AREA,
    READ,
    REFUSED,
    ROLE_FINDINGS,
    ROLE_PERMISSIONS,
    ROLE_SCHOOL_AREAS,
    ROLE_TASK_FIELDS,
    ROLES,
    SCHOOL_FIELD_AREA,
    FieldRequestRefused,
    findings_for_role,
    grant_aggregates,
    grant_authorized_aggregate,
    grant_school_fields,
    request_fields,
    school_access_for,
)
from cabinet.store import CabinetStore
from conftest import make_authenticated_client

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO_ROOT / "data" / "fixture.json"
GENERATE = REPO_ROOT / "data" / "school" / "generate.py"

NO_DATA = ("career_outcomes_analyst", "advancement_analyst")
NO_STUDENT_DATA = (*NO_DATA, "it_data_steward")


@pytest.fixture(scope="module")
def findings_obj() -> dict[str, Any]:
    return compute_findings(load_fixture(FIXTURE_PATH), fixture_path=FIXTURE_PATH)


# --- the permission table, per employee ---------------------------------------


def test_every_department_has_an_employee_with_a_title_job_and_office() -> None:
    assert (*BRIEFING_ROLES, *DEPARTMENT_EMPLOYEES) == ROLES
    assert len(ROLES) == 14
    titles = [staff.EMPLOYEES[r].title for r in ROLES]
    assert len(set(titles)) == len(titles)
    for role in ROLES:
        e = staff.EMPLOYEES[role]
        assert e.title and e.office
        # One line: a single sentence or two, no line breaks.
        assert e.job.endswith(".") and "\n" not in e.job and len(e.job) <= 160
    for name in (
        "Registrar Analyst",
        "Student Accounts Analyst",
        "Financial Aid Analyst",
        "Advising Analyst",
        "Student Life Analyst",
        "Academic Affairs Analyst",
        "Institutional Research Analyst",
        "Admissions Analyst",
        "Career & Alumni Outcomes Analyst",
        "Advancement Analyst",
        "IT & Data Steward",
    ):
        assert name in titles


@pytest.mark.parametrize("role", ROLES)
def test_each_employee_has_a_complete_permission_row(role: str) -> None:
    assert set(ROLE_PERMISSIONS[role]) == set(ALL_FIELDS)
    assert role in ROLE_FINDINGS
    # The Chief of Staff has no task of its own: it receives the analysts'
    # aggregates (analysts.chief_aggregate_fields).
    assert (role in ROLE_TASK_FIELDS) == (role != "chief_of_staff")
    assert role in ROLE_SCHOOL_AREAS
    # Counseling is refused to every employee, always.
    assert ROLE_PERMISSIONS[role]["counseling.counseling_notes"] == REFUSED
    assert ROLE_PERMISSIONS[role]["counseling.chaplain_contact"] == REFUSED
    # Task fields are inside the employee's own row.
    for field in ROLE_TASK_FIELDS.get(role, ()):
        assert ROLE_PERMISSIONS[role][field] != REFUSED, (role, field)


@pytest.mark.parametrize("role", DEPARTMENT_EMPLOYEES)
def test_department_employees_get_aggregates_only_and_no_student_id(
    role: str, tmp_path: Path
) -> None:
    row = ROLE_PERMISSIONS[role]
    assert set(row.values()) <= {AGGREGATE_ONLY, REFUSED}
    assert row["profile.student_id"] == REFUSED
    allowed = [f for f, access in row.items() if access == AGGREGATE_ONLY]
    if role in NO_STUDENT_DATA:
        assert allowed == [] and ROLE_FINDINGS[role] == ()
        assert ROLE_SCHOOL_AREAS[role] == ()
    else:
        # A real allow-list: something, never everything.
        assert 0 < len(allowed) < len(row) - 2
        assert ROLE_SCHOOL_AREAS[role]
    log = AuditLog(tmp_path / "events.jsonl")
    if ROLE_TASK_FIELDS[role]:
        # Granted as aggregates, never as raw values.
        assert grant_aggregates(role, ROLE_TASK_FIELDS[role], "t", log)
        with pytest.raises(FieldRequestRefused):
            request_fields(role, ROLE_TASK_FIELDS[role], "t2", log)


@pytest.mark.parametrize("role", ROLES)
def test_each_employees_findings_rest_on_fields_it_may_receive(
    role: str, findings_obj: dict[str, Any]
) -> None:
    for finding_id in ROLE_FINDINGS[role]:
        for source in findings_obj[finding_id]["source_fields"]:
            field = source.replace("[]", "")
            if field.startswith("terms."):
                continue  # term anchors, not student fields
            assert ROLE_PERMISSIONS[role][field] != REFUSED, (role, finding_id, field)
    received = findings_for_role(role, findings_obj)
    assert set(received) == set(ROLE_FINDINGS[role])
    assert "row_ids" not in json.dumps(received)


@pytest.mark.parametrize("role", ROLES)
def test_counseling_m9_is_refused_to_every_employee_but_the_chief(
    role: str, findings_obj: dict[str, Any], tmp_path: Path
) -> None:
    assert "M9" not in ROLE_FINDINGS[role]
    if role == "chief_of_staff":
        assert AUTHORIZED_AGGREGATE_FINDINGS[role] == ("M9",)
        return
    assert role not in AUTHORIZED_AGGREGATE_FINDINGS
    with pytest.raises(ValueError):
        findings_for_role(role, findings_obj, ["M9"])
    with pytest.raises(ValueError):
        grant_authorized_aggregate(
            role, "M9", [], {}, "t", AuditLog(tmp_path / "e.jsonl")
        )


def test_no_employee_can_read_a_student_name() -> None:
    # The briefing's fields carry no names at all.
    assert not [f for f in ALL_FIELDS if "name" in f]
    named = [f for f in SCHOOL_FIELD_AREA if "name" in f]
    # The only names in the school areas are programs, colleges and the
    # instructors of record, never a student's.
    assert set(named) == {
        "academic_programs.name",
        "colleges.name",
        "instructors.first_name",
        "instructors.last_name",
    }
    for role in ROLES:
        for field in ("instructors.first_name", "instructors.last_name"):
            # Instructor names only with instructor-level rows allowed, and
            # only for the Academic Affairs Analyst.
            assert school_access_for(role, field) == REFUSED
            expected = AGGREGATE_ONLY if role == "academic_affairs_analyst" else REFUSED
            assert school_access_for(role, field, instructor_rows=True) == expected
        assert school_access_for(role, "students.first_name") == REFUSED
        assert school_access_for(role, "student_profiles.notes") == REFUSED


def test_the_school_gate_logs_a_grant_and_refuses_outside_the_areas(
    tmp_path: Path,
) -> None:
    log = AuditLog(tmp_path / "events.jsonl")
    granted = grant_school_fields(
        "student_accounts_analyst",
        ["person_holds.amount", "person_holds.responsible_office"],
        "t1",
        log,
        extra={"step": 0},
    )
    assert granted == ["person_holds.amount", "person_holds.responsible_office"]
    event = log.events("data.granted")[-1]
    assert event["actor"] == "student_accounts_analyst"
    assert event["payload"]["aggregate_only"] is True
    assert event["payload"]["step"] == 0
    with pytest.raises(FieldRequestRefused):
        grant_school_fields(
            "student_accounts_analyst", ["students.pell_recipient"], "t2", log
        )
    refused = log.events("data.refused")[-1]
    assert refused["actor"] == "student_accounts_analyst"
    assert refused["payload"]["refused_fields"] == ["students.pell_recipient"]


# --- the routing map ------------------------------------------------------------


def _every_school_field() -> set[str]:
    fields = set(COURSE_SUMMARY_FIELDS)
    for analysis in ANALYSES:
        if analysis.id != general.ANALYSIS_ID:
            fields |= set(analysis.fields_read)
    for measure in general.MEASURES:
        fields |= set(general.fields_used({"measure": measure}))
        for key in general.GROUPINGS:
            fields |= set(general.fields_used({"measure": measure, "group_by": key}))
    return fields


def test_every_measure_grouping_and_analysis_has_an_owning_department() -> None:
    assert set(staff.MEASURE_OWNER) == set(general.MEASURES)
    assert set(staff.GROUPING_OWNER) == set(general.GROUPINGS)
    assert set(staff.ANALYSIS_OWNER) == {
        a.id for a in ANALYSES if a.id != general.ANALYSIS_ID
    }
    owners = {
        *staff.MEASURE_OWNER.values(),
        *staff.GROUPING_OWNER.values(),
        *staff.ANALYSIS_OWNER.values(),
        *staff.AREA_OWNER.values(),
    }
    assert owners <= set(ROLES)
    # The coordinator delegates; the no-data employees own nothing.
    assert not owners & {"chief_of_staff", *NO_STUDENT_DATA}
    # Every field Explore can read sits in a data area.
    assert _every_school_field() <= set(SCHOOL_FIELD_AREA)


def test_owners_may_read_what_they_own() -> None:
    for measure, owner in staff.MEASURE_OWNER.items():
        for field in general.fields_used({"measure": measure}):
            assert school_access_for(owner, field) == AGGREGATE_ONLY, (measure, field)
    for key, owner in staff.GROUPING_OWNER.items():
        for field in general.fields_used({"measure": "headcount", "group_by": key}):
            if field == "student_term_records.term_code":
                continue  # the headcount's own unit
            assert school_access_for(owner, field) == AGGREGATE_ONLY, (key, field)
            # The grouping's owner is its data area's owner.
            assert staff.AREA_OWNER[SCHOOL_FIELD_AREA[field]] == owner
    for analysis in ANALYSES:
        if analysis.id == general.ANALYSIS_ID:
            continue
        lead = staff.ANALYSIS_OWNER[analysis.id]
        assert lead == staff.lead_for(analysis.id, {})


def _assert_routed_within_scope(
    analysis_id: str,
    params: dict[str, Any],
    fields: tuple[str, ...] = (),
    instructor_rows: bool = False,
) -> list[tuple[str, tuple[str, ...]]]:
    workers = staff.delegate_step(
        analysis_id, params, fields, instructor_rows=instructor_rows
    )
    seen: list[str] = []
    for role, role_fields in workers:
        for field in role_fields:
            access = school_access_for(role, field, instructor_rows=instructor_rows)
            assert access == AGGREGATE_ONLY, (analysis_id, params, role, field)
        seen.extend(role_fields)
    # Every field the step uses is read by exactly one employee.
    used = staff.step_fields(analysis_id, params, fields)
    assert sorted(seen) == sorted(set(used))
    return workers


def test_every_routed_step_stays_inside_each_employees_areas() -> None:
    for measure_key, measure in general.MEASURES.items():
        groupings = general.allowed_groupings(measure)
        for pair in itertools.chain(
            [()], ((g,) for g in groupings), itertools.combinations(groupings, 2)
        ):
            params: dict[str, Any] = {"measure": measure_key}
            if pair:
                params["group_by"] = pair[0]
            if len(pair) == 2:
                params["then_by"] = pair[1]
            workers = _assert_routed_within_scope(general.ANALYSIS_ID, params)
            assert workers[0][0] == staff.MEASURE_OWNER[measure_key]
    for analysis in ANALYSES:
        if analysis.id == general.ANALYSIS_ID:
            continue
        for rows in (True, False):
            fields = (
                analysis.fields_read
                if rows or not analysis.instructor_level
                else (
                    COURSE_SUMMARY_FIELDS if analysis.id == "course_instructors" else ()
                )
            )
            _assert_routed_within_scope(analysis.id, {}, fields, instructor_rows=rows)
    for group in (
        "first_generation",
        "pell",
        "residency",
        "entry_cohort",
        "entry_type",
    ):
        equity = next(a for a in ANALYSES if a.id == "equity_gap")
        _assert_routed_within_scope("equity_gap", {"group": group}, equity.fields_read)


def test_a_multi_department_step_lists_each_employee() -> None:
    workers = staff.delegate_step(
        general.ANALYSIS_ID, {"measure": "dfw_rate", "group_by": "pell"}, ()
    )
    assert [r for r, _ in workers] == [
        "academic_affairs_analyst",
        "financial_aid_analyst",
    ]
    assert dict(workers)["financial_aid_analyst"] == ("students.pell_recipient",)
    assert "students.pell_recipient" not in dict(workers)["academic_affairs_analyst"]
    line = staff.delegation_line([r for r, _ in workers], "D, F or withdrawal rate")
    assert line == (
        "Chief of Staff → Academic Affairs Analyst and Financial Aid Analyst: "
        "D, F or withdrawal rate"
    )
    # A grouping the lead may read stays with the lead.
    single = staff.delegate_step(
        general.ANALYSIS_ID, {"measure": "hold_rate", "group_by": "major"}, ()
    )
    assert [r for r, _ in single] == ["student_accounts_analyst"]


def test_instructor_fields_never_leave_academic_affairs() -> None:
    for analysis in ANALYSES:
        if not analysis.instructor_level:
            continue
        workers = staff.delegate_step(
            analysis.id, {}, analysis.fields_read, instructor_rows=True
        )
        for role, fields in workers:
            if any(SCHOOL_FIELD_AREA.get(f) == INSTRUCTOR_AREA for f in fields):
                assert role == "academic_affairs_analyst"


# --- the counseling denial names the employee -------------------------------------


def test_the_denial_names_the_employee_it_was_asked_of() -> None:
    # The privacy module's own sentence for the briefing's employees.
    assert staff.denial_message("enrollment_analyst") == counseling_message(
        "enrollment_analyst"
    )
    for role in DEPARTMENT_EMPLOYEES:
        message = staff.denial_message(role)
        assert message.startswith("Access denied.")
        # Fails loudly if the privacy module's generic sentence changes.
        assert f"outside the {staff.title(role)}'s authorized scope" in message
        assert "CampusLens's" not in message
    assert staff.asked_of("How many registered students saw a counselor?", "staff") == (
        "enrollment_analyst"
    )
    assert staff.asked_of("How many students saw a counselor?", "registrar") == (
        "registrar_analyst"
    )
    assert staff.asked_of("How many students saw a counselor?", "executive") == (
        "chief_of_staff"
    )
    assert staff.asked_of("Counseling notes for students with holds", "admin") == (
        "student_accounts_analyst"
    )


# --- the routes -------------------------------------------------------------------


@pytest.fixture(scope="module")
def school_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("school-staff") / "school.db"
    proc = subprocess.run(
        [sys.executable, str(GENERATE), "--scale", "0.01", "--out", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return out


@pytest.fixture
def app(school_db: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("CABINET_SCHOOL_DB", str(school_db))
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    return create_app()


def _events(app: FastAPI, event_type: str) -> list[dict[str, Any]]:
    store: CabinetStore = app.state.auth
    return store.audit_events(store.ensure_bootstrap_institution(), event_type)


def _stream(client: TestClient, question: str) -> list[dict[str, Any]]:
    response = client.post("/explore/stream", json={"question": question})
    assert response.status_code == 200, response.text
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def test_explore_is_delegated_audited_and_attributed(app: FastAPI) -> None:
    client = make_authenticated_client(app, role="finance")
    before = len(_events(app, "data.granted"))
    lines = _stream(client, "Which offices hold the most active holds?")
    delegation = [line for line in lines if line.get("type") == "delegation"]
    assert delegation and delegation[0]["text"].startswith(
        "Chief of Staff → Student Accounts Analyst: "
    )
    assert delegation[0]["employees"] == ["Student Accounts Analyst"]
    final = lines[-1]
    body = final.get("response", final)
    assert body["answered_by"] == ["Student Accounts Analyst"]
    assert body["delegated_by"] == "Chief of Staff"
    grants = _events(app, "data.granted")[before:]
    assert grants and all(g["actor"] == "student_accounts_analyst" for g in grants)
    payload = grants[0]["payload"]
    assert payload["delegated_by"] == "chief_of_staff"
    assert payload["analysis_id"] == "holds_by_office"
    assert "person_holds.amount" in payload["fields_read"]
    answered = _events(app, "explore.answered")[-1]
    assert answered["actor"] == "chief_of_staff"
    assert answered["payload"]["employees"] == ["student_accounts_analyst"]


def test_a_multi_department_answer_records_each_employees_fields(app: FastAPI) -> None:
    client = make_authenticated_client(app, role="executive")
    before = len(_events(app, "data.granted"))
    response = client.post(
        "/explore",
        json={
            "question": (
                "What is the 6-year graduation rate for Pell students by college?"
            )
        },
    )
    body = response.json()
    assert body["answered_by"][0] == "Institutional Research Analyst"
    assert "Financial Aid Analyst" in body["answered_by"]
    grants = _events(app, "data.granted")[before:]
    by_actor = {g["actor"]: g["payload"]["fields_read"] for g in grants}
    assert by_actor["financial_aid_analyst"] == ["students.pell_recipient"]
    assert "students.pell_recipient" not in by_actor["institutional_research_analyst"]
    # The lead's event also keeps everything the step's query touched.
    lead = next(g for g in grants if g["actor"] == "institutional_research_analyst")
    assert "student_profiles.gender" in lead["payload"]["query_fields"]


def test_counseling_asked_of_a_department_names_its_employee(app: FastAPI) -> None:
    client = make_authenticated_client(app, role="registrar")
    body = client.post(
        "/explore", json={"question": "How many students saw a counselor?"}
    ).json()
    assert body["redirect"] == "counseling"
    assert "outside the Registrar Analyst's authorized scope" in body["message"]
    refused = _events(app, "data.refused")[-1]
    assert refused["actor"] == "registrar_analyst"
    assert refused["payload"]["category"] == "counseling"
    body = client.post(
        "/explore",
        json={"question": "How many students who registered late saw a counselor?"},
    ).json()
    assert "outside the Enrollment Analyst's authorized scope" in body["message"]


def test_staff_route_lists_everyone_own_department_first_with_todays_counts(
    app: FastAPI,
) -> None:
    finance = make_authenticated_client(app, role="finance")
    finance.post(
        "/explore", json={"question": "Which offices hold the most active holds?"}
    )
    body = finance.get("/staff").json()
    employees = body["employees"]
    assert len(employees) == 14
    assert employees[0]["title"] == "Student Accounts Analyst"
    assert employees[0]["yours"] is True and body["yours"] == [
        "student_accounts_analyst"
    ]
    by_role = {e["role"]: e for e in employees}
    assert by_role["student_accounts_analyst"]["requests_today"] == 1
    assert by_role["registrar_analyst"]["requests_today"] == 0
    # The Chief of Staff delegated it.
    assert by_role["chief_of_staff"]["requests_today"] == 1
    for e in employees:
        assert e["title"] and e["job"] and e["office"]
        assert "Counseling and chaplain notes" in e["never_reads"]
        assert "Free-text notes" in e["never_reads"]
        assert ("Student names" in e["never_reads"]) or (
            "Any student record" in e["never_reads"]
        )
    assert "Pell status" in by_role["student_accounts_analyst"]["outside_scope"]
    assert by_role["it_data_steward"]["never_reads"][0] == "Any student record"
    for role in NO_DATA:
        assert by_role[role]["no_data"] is True and by_role[role]["may_read"] == []
    assert by_role["student_accounts_analyst"]["may_read"][1] == (
        "Holds and balances owed, by office"
    )
    # The president sees the Chief of Staff first; student life its two.
    president = make_authenticated_client(app, role="executive").get("/staff").json()
    assert president["employees"][0]["title"] == "Chief of Staff"
    life = make_authenticated_client(app, role="studentlife").get("/staff").json()
    assert [e["title"] for e in life["employees"][:2]] == [
        "Student Life Analyst",
        "Advising Analyst",
    ]
    it = make_authenticated_client(app, role="it").get("/staff")
    assert it.status_code == 200
    assert it.json()["employees"][0]["title"] == "IT & Data Steward"
    # Counts and words only: no field values, no student ids.
    assert "S-" not in json.dumps(body)


def test_staff_route_needs_a_session(app: FastAPI) -> None:
    assert TestClient(app).get("/staff").status_code == 401


def test_existing_briefing_employees_keep_their_read_grants() -> None:
    # The two briefing analysts are unchanged by the new staff.
    assert ROLE_PERMISSIONS["enrollment_analyst"]["profile.continuing"] == READ
    assert ROLE_PERMISSIONS["student_success_analyst"]["holds.amount"] == READ


def test_the_home_screens_name_the_same_employees_as_the_api() -> None:
    """ui/src/personas.ts names each office's AI employee on its home screen;
    it must match LOGIN_EMPLOYEES here."""
    personas = (REPO_ROOT / "ui" / "src" / "personas.ts").read_text(encoding="utf-8")
    for login, roles in staff.LOGIN_EMPLOYEES.items():
        titles = ", ".join(f"'{staff.title(r)}'" for r in roles)
        block = personas.split(f"  {login}: {{", 1)[1].split("\n  },", 1)[0]
        assert f"employees: [{titles}]" in block, (login, titles)


def test_the_ui_names_every_employee_as_the_api_does() -> None:
    """ui/src/staff.ts EMPLOYEE_TITLES (the audit log's names) matches
    EMPLOYEES here, entry for entry."""
    source = (REPO_ROOT / "ui" / "src" / "staff.ts").read_text(encoding="utf-8")
    block = source.split("export const EMPLOYEE_TITLES", 1)[1].split("\n}", 1)[0]
    entries = dict(
        line.strip().rstrip(",").split(": ", 1)
        for line in block.splitlines()[1:]
        if ": " in line
    )
    assert {k: v.strip("'") for k, v in entries.items()} == {
        role: e.title for role, e in staff.EMPLOYEES.items()
    }
