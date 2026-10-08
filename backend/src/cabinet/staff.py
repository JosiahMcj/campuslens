"""The AI staff: one AI employee for each university department.

Each employee has a job title, a one-line job, the human office it serves,
and a field-level scope in ``cabinet.permissions`` (``ROLE_PERMISSIONS`` for
the briefing's fields, ``ROLE_SCHOOL_AREAS`` for the school records Explore
reads). Every scope is least privilege: aggregates only, no names, no
free-text notes, never counseling.

The Chief of Staff coordinates. An Explore question's plan is delegated
step by step to the employee whose department owns what the step measures
(``MEASURE_OWNER``, ``ANALYSIS_OWNER``); a grouping or filter outside that
employee's areas brings in the department that owns it (``GROUPING_OWNER``,
``AREA_OWNER``), so a step can be worked by two employees, each reading only
its own fields. ``delegate_step`` returns who reads which fields, and the
Explore route logs one ``data.granted`` per employee through
``permissions.grant_school_fields``, which refuses anything outside the
employee's areas before a row is read.

``GET /staff`` lists the employees for the "AI employees and data access"
panel: title, job, office, what each may and may never read in plain words,
and how many requests each handled today (from the audit log). Counts only;
no field values and no student data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from cabinet.auth import (
    ROLE_ACADEMIC_AFFAIRS,
    ROLE_ADMISSIONS,
    ROLE_ADVANCEMENT,
    ROLE_ADVISING,
    ROLE_AID,
    ROLE_ATHLETICS,
    ROLE_CAREERS,
    ROLE_EXECUTIVE,
    ROLE_FINANCE,
    ROLE_INSTITUTIONAL_RESEARCH,
    ROLE_INTERNATIONAL,
    ROLE_IT,
    ROLE_REGISTRAR,
    ROLE_STUDENT_ACCOUNTS,
    ROLE_STUDENT_LIFE,
)
from cabinet.explore import general
from cabinet.explore.catalog import ANALYSES
from cabinet.explore.privacy import EMPLOYEE_NAMES, counseling_message
from cabinet.permissions import (
    INSTRUCTOR_AREA,
    REFUSED,
    ROLE_FINDINGS,
    ROLE_PERMISSIONS,
    ROLE_SCHOOL_AREAS,
    ROLES,
    SCHOOL_AREAS,
    SCHOOL_FIELD_AREA,
)

COORDINATOR = "chief_of_staff"


@dataclass(frozen=True)
class Employee:
    role: str
    title: str
    job: str
    office: str
    # True when no data is connected for this department yet: the employee
    # exists, with an empty scope, and the panel says so.
    no_data: bool = False


EMPLOYEES: dict[str, Employee] = {
    e.role: e
    for e in (
        Employee(
            "chief_of_staff",
            "Chief of Staff",
            "Hands each question to the right department's analyst, then writes "
            "the summary and its limits from their checked totals.",
            "President's Office",
        ),
        Employee(
            "enrollment_analyst",
            "Enrollment Analyst",
            "Explains registration and headcount: who has registered and how that "
            "compares with last year.",
            "Enrollment Management",
        ),
        Employee(
            "student_success_analyst",
            "Student Success Analyst",
            "Explains what stands in students' way: holds, missing advising "
            "appointments, stop-outs and support indicators.",
            "Student Success",
        ),
        Employee(
            "registrar_analyst",
            "Registrar Analyst",
            "Reports registration, academic standing, credit hours and course "
            "sections.",
            "Office of the Registrar",
        ),
        Employee(
            "student_accounts_analyst",
            "Student Accounts Analyst",
            "Reports account holds and balances owed, by office.",
            "Finance — Student Accounts (Bursar)",
        ),
        Employee(
            "financial_aid_analyst",
            "Financial Aid Analyst",
            "Reports Pell and aid-eligibility figures and the size of the aid "
            "review queue.",
            "Financial Aid",
        ),
        Employee(
            "advising_analyst",
            "Advising Analyst",
            "Reports advising coverage, appointments and major changes.",
            "Academic Advising",
        ),
        Employee(
            "student_life_analyst",
            "Student Life Analyst",
            "Reports housing, athletics and conduct holds.",
            "Student Life",
        ),
        Employee(
            "academic_affairs_analyst",
            "Academic Affairs Analyst",
            "Reports courses, grades, D, F and withdrawal rates and teaching "
            "mode; instructor rows only for the president and the admin.",
            "Academic Affairs (Provost)",
        ),
        Employee(
            "institutional_research_analyst",
            "Institutional Research Analyst",
            "Reports cohorts, retention, graduation and trends over time, "
            "including gaps between student groups.",
            "Institutional Research",
        ),
        Employee(
            "admissions_analyst",
            "Admissions Analyst",
            "Reports entering classes: entry cohorts and admit type "
            "(first-time or transfer).",
            "Admissions",
        ),
        Employee(
            "career_outcomes_analyst",
            "Career & Alumni Outcomes Analyst",
            "Reports graduates' first destinations: employment, starting "
            "salary, graduate and medical school.",
            "Career Services & Alumni Relations",
        ),
        Employee(
            "advancement_analyst",
            "Advancement Analyst",
            "Reports alumni giving: participation, average gift and totals.",
            "Advancement",
        ),
        Employee(
            "finance_budget_analyst",
            "Finance & Budget Analyst",
            "Reports the university's budget against actual, revenue by source "
            "and the tuition discount; for the president and finance only.",
            "Finance — CFO",
        ),
        Employee(
            "it_data_steward",
            "IT & Data Steward",
            "Looks after the outside connections and the data-access audit. "
            "Reads no student data.",
            "IT",
        ),
    )
}

assert tuple(EMPLOYEES) == ROLES, "every permissions role has a staff entry"

# Each signed-in department's own AI employee(s), shown first in the panel
# and named on the department's home screen. The president's is the Chief
# of Staff; roles not listed (admin, staff, reviewer) see the whole staff.
LOGIN_EMPLOYEES: dict[str, tuple[str, ...]] = {
    ROLE_EXECUTIVE: ("chief_of_staff",),
    ROLE_FINANCE: ("finance_budget_analyst",),
    ROLE_STUDENT_ACCOUNTS: ("student_accounts_analyst",),
    ROLE_REGISTRAR: ("registrar_analyst",),
    ROLE_STUDENT_LIFE: ("student_life_analyst",),
    ROLE_AID: ("financial_aid_analyst",),
    ROLE_ADMISSIONS: ("admissions_analyst",),
    ROLE_ADVISING: ("advising_analyst",),
    ROLE_ACADEMIC_AFFAIRS: ("academic_affairs_analyst",),
    ROLE_INSTITUTIONAL_RESEARCH: ("institutional_research_analyst",),
    ROLE_CAREERS: ("career_outcomes_analyst",),
    ROLE_ADVANCEMENT: ("advancement_analyst",),
    # Athletics data (athlete flag, standing, retention) is read through the
    # Student Life analyst (campus_life area).
    ROLE_ATHLETICS: ("student_life_analyst",),
    # International students: the Institutional Research analyst, whose areas
    # cover residency (demographics) plus retention and enrollment; the
    # Admissions analyst has demographics but not outcomes.
    ROLE_INTERNATIONAL: ("institutional_research_analyst",),
    ROLE_IT: ("it_data_steward",),
}

# --- plain words for the scopes ---------------------------------------------

AREA_LABELS: dict[str, str] = {
    "structure": "Majors, colleges, class levels and terms",
    "course_sections": "Courses and sections",
    "registration": "Registration, credit hours and full- or part-time load",
    "standing": "Academic standing (probation and suspension)",
    "grades": "Grades, GPA, honors and teaching mode",
    INSTRUCTOR_AREA: "Instructor-level results (for the president and the admin only)",
    "programs": "Program status, major changes and degrees",
    "outcomes": "Retention, stop-out, dropout and graduation outcomes",
    "entry": "Entry term and admit type",
    "demographics": "Student groups (gender, race and ethnicity, age, residency, "
    "first generation)",
    "aid": "Pell status",
    "holds": "Holds and balances owed, by office",
    "advising": "Advising appointments",
    "support_programs": "Support programs: their rules, take-up and follow-up "
    "outcomes (totals)",
    "campus_life": "Housing and athletics",
    "career_outcomes": "Graduates' first destinations, salaries, graduate and "
    "medical school",
    "giving": "Alumni giving",
    "billing": "Student charges, payments and payment plans",
    "budget": "The university's budget, revenue and tuition discount "
    "(institutional figures, no student data)",
}

# What no AI employee ever reads, whatever its job.
NEVER_READS: tuple[str, ...] = (
    "Student names",
    "Counseling and chaplain notes",
    "Free-text notes",
    "One student's record",
)

_FIXTURE_GROUP_LABELS = {
    "profile": "student profile",
    "enrollment": "registration",
    "holds": "holds",
    "advising": "advising",
    "comparison": "last year's comparison",
}


def may_read(role: str) -> list[str]:
    """What the employee may receive, in plain words (always as totals)."""
    if role == COORDINATOR:
        # The coordinator's areas are everyone's but the instructor rows:
        # said once rather than listed.
        return [
            "Every department's totals, as its analyst reports them "
            "(never instructor-level rows)",
            "The briefing's figures, and the counseling count only when the "
            "institution has authorized it",
        ]
    words = [AREA_LABELS[a] for a in ROLE_SCHOOL_AREAS.get(role, ())]
    briefing = sorted(
        {
            _FIXTURE_GROUP_LABELS[path.split(".")[0]]
            for path, access in ROLE_PERMISSIONS.get(role, {}).items()
            if access != REFUSED and path.split(".")[0] in _FIXTURE_GROUP_LABELS
        }
    )
    if briefing and role != COORDINATOR:
        words.append("Briefing figures on " + ", ".join(briefing))
    return words


def never_reads(role: str) -> list[str]:
    """What the employee may never read, whatever it is asked."""
    if not ROLE_SCHOOL_AREAS.get(role):
        return ["Any student record", *NEVER_READS[1:]]
    return list(NEVER_READS)


def outside_scope(role: str) -> list[str]:
    """The data areas outside the employee's job, in plain words: refused if
    a request ever asks it for them."""
    if not ROLE_SCHOOL_AREAS.get(role):
        return []
    return [
        label
        for area, label in AREA_LABELS.items()
        if area not in ROLE_SCHOOL_AREAS.get(role, ())
    ]


# --- the routing map ----------------------------------------------------------

# The department that owns each data area: brought in when a step's grouping
# or filter reads an area the leading employee may not read.
AREA_OWNER: dict[str, str] = {
    "structure": "registrar_analyst",
    "course_sections": "registrar_analyst",
    "registration": "registrar_analyst",
    "standing": "registrar_analyst",
    "grades": "academic_affairs_analyst",
    INSTRUCTOR_AREA: "academic_affairs_analyst",
    "programs": "registrar_analyst",
    "outcomes": "institutional_research_analyst",
    "entry": "admissions_analyst",
    "demographics": "institutional_research_analyst",
    "aid": "financial_aid_analyst",
    "holds": "student_accounts_analyst",
    "advising": "advising_analyst",
    "support_programs": "student_success_analyst",
    "campus_life": "student_life_analyst",
    "career_outcomes": "career_outcomes_analyst",
    "giving": "advancement_analyst",
    "billing": "student_accounts_analyst",
    "budget": "finance_budget_analyst",
}

# Each reviewed measure of the general analysis, by owning department.
MEASURE_OWNER: dict[str, str] = {
    "headcount": "enrollment_analyst",
    "avg_gpa": "academic_affairs_analyst",
    "avg_credits_earned": "registrar_analyst",
    "dropout_rate": "student_success_analyst",
    "transfer_out_rate": "institutional_research_analyst",
    "major_change_rate": "advising_analyst",
    "major_change_out_rate": "advising_analyst",
    "major_attrition_rate": "institutional_research_analyst",
    "fit_flag_rate": "advising_analyst",
    "first_year_major_dfw_rate": "academic_affairs_analyst",
    "pell_share": "financial_aid_analyst",
    "first_gen_share": "admissions_analyst",
    "international_share": "admissions_analyst",
    "part_time_share": "registrar_analyst",
    "on_campus_share": "student_life_analyst",
    "probation_rate": "registrar_analyst",
    "suspension_rate": "registrar_analyst",
    "stop_out_rate": "student_success_analyst",
    "credit_completion_rate": "registrar_analyst",
    "avg_credits_attempted": "registrar_analyst",
    "advising_rate": "advising_analyst",
    "hold_rate": "student_accounts_analyst",
    "retention_rate": "institutional_research_analyst",
    "grad_rate_4yr": "institutional_research_analyst",
    "grad_rate_6yr": "institutional_research_analyst",
    "time_to_degree": "institutional_research_analyst",
    "graduates": "institutional_research_analyst",
    "dfw_rate": "academic_affairs_analyst",
    "withdrawal_rate": "academic_affairs_analyst",
    "knowledge_rate": "career_outcomes_analyst",
    "employment_rate": "career_outcomes_analyst",
    "median_salary": "career_outcomes_analyst",
    "grad_school_rate": "career_outcomes_analyst",
    "med_acceptance_rate": "career_outcomes_analyst",
    "giving_rate": "advancement_analyst",
    "avg_gift": "advancement_analyst",
    "total_giving": "advancement_analyst",
    "past_due_balance": "student_accounts_analyst",
    "past_due_students": "student_accounts_analyst",
    "past_due_90_students": "student_accounts_analyst",
    "on_time_payment_rate": "student_accounts_analyst",
    "payment_plan_share": "student_accounts_analyst",
    "collection_rate": "student_accounts_analyst",
    "avg_balance_owed": "student_accounts_analyst",
}

# Each grouping (and filter) of the general analysis, by owning department.
# The leading employee keeps a grouping it may read itself; otherwise this
# department's employee joins the step for it.
GROUPING_OWNER: dict[str, str] = {
    "major": "registrar_analyst",
    "college": "registrar_analyst",
    "class_level": "registrar_analyst",
    "term": "registrar_analyst",
    "entry_cohort": "admissions_analyst",
    "admit_type": "admissions_analyst",
    "residency": "institutional_research_analyst",
    "first_generation": "institutional_research_analyst",
    "gender": "institutional_research_analyst",
    "race_ethnicity": "institutional_research_analyst",
    "age_band": "institutional_research_analyst",
    "pell": "financial_aid_analyst",
    "load": "registrar_analyst",
    "housing": "student_life_analyst",
    "athlete": "student_life_analyst",
    "honors": "academic_affairs_analyst",
    "modality": "academic_affairs_analyst",
    "hold": "student_accounts_analyst",
    "gpa_band": "academic_affairs_analyst",
    "aging": "student_accounts_analyst",
}

# Each approved analysis, by owning department (the general analysis is
# routed by its measure).
ANALYSIS_OWNER: dict[str, str] = {
    "gpa_by_major": "academic_affairs_analyst",
    "gpa_by_college": "academic_affairs_analyst",
    "dfw_by_course": "academic_affairs_analyst",
    "course_dfw_trend": "academic_affairs_analyst",
    "course_instructors": "academic_affairs_analyst",
    "instructor_history": "academic_affairs_analyst",
    "equity_gap": "academic_affairs_analyst",
    "headcount_growth": "enrollment_analyst",
    "enrollment_by_term": "enrollment_analyst",
    "continuing_registration_change": "enrollment_analyst",
    "withdrawal_by_modality": "academic_affairs_analyst",
    "withdrawal_by_course_modality": "academic_affairs_analyst",
    "standing_by_major": "registrar_analyst",
    "graduations": "institutional_research_analyst",
    "holds_by_office": "student_accounts_analyst",
    "advising_coverage": "advising_analyst",
    "credit_hours_by_term": "registrar_analyst",
    # The university's own budget: only reachable when the person asking
    # is in BUDGET_ROLES (the catalog and the gate both check).
    "budget_vs_actual": "finance_budget_analyst",
    "revenue_by_source": "finance_budget_analyst",
    "tuition_discount": "finance_budget_analyst",
    # Support programs (cabinet.interventions): aggregates only.
    "program_impact": "student_success_analyst",
    "program_reach": "student_success_analyst",
}

_ANALYSES_BY_ID = {a.id: a for a in ANALYSES}

# The equity gap reads one student-group field: the one the step compares.
_EQUITY_GROUP_FIELDS = {
    "first_generation": "students.first_generation",
    "pell": "students.pell_recipient",
    "residency": "students.residency",
    "entry_cohort": "students.entry_term",
    "entry_type": "students.entry_type",
}


def lead_for(analysis_id: str, params: dict[str, Any]) -> str:
    """The employee who leads one step: the measure's owner for the general
    analysis, else the analysis's owner (the Chief of Staff when unmapped,
    which the routing tests forbid)."""
    if analysis_id == general.ANALYSIS_ID:
        return MEASURE_OWNER.get(str(params.get("measure")), COORDINATOR)
    return ANALYSIS_OWNER.get(analysis_id, COORDINATOR)


def step_fields(
    analysis_id: str, params: dict[str, Any], fields: tuple[str, ...]
) -> tuple[str, ...]:
    """The fields one step's table is built from: the general analysis's
    measure, groupings and filters (``general.fields_used``); the equity gap's
    chosen student group only; every other analysis's ``fields`` as given."""
    if analysis_id == general.ANALYSIS_ID:
        return general.fields_used(params)
    if analysis_id == "equity_gap" and fields:
        chosen = _EQUITY_GROUP_FIELDS.get(
            str(params.get("group") or "first_generation")
        )
        others = set(_EQUITY_GROUP_FIELDS.values()) - {chosen}
        return tuple(f for f in fields if f not in others)
    return fields


def delegate_step(
    analysis_id: str,
    params: dict[str, Any],
    fields: tuple[str, ...],
    *,
    instructor_rows: bool = False,
    budget_rows: bool = False,
) -> list[tuple[str, tuple[str, ...]]]:
    """Who reads which fields for one step, leading employee first. A field in
    the leader's areas stays with the leader; any other goes to the
    department that owns its area. An employee always appears with at least
    the leader, even when the step reads nothing (a withheld step)."""
    lead = lead_for(analysis_id, params)
    assigned: dict[str, list[str]] = {lead: []}
    for field in step_fields(analysis_id, params, fields):
        area = SCHOOL_FIELD_AREA.get(field)
        reader = lead
        if area is not None and area not in ROLE_SCHOOL_AREAS.get(lead, ()):
            reader = AREA_OWNER.get(area, lead)
        if area == INSTRUCTOR_AREA and not instructor_rows:
            reader = lead  # refused by the gate either way; keep it with the lead
        assigned.setdefault(reader, []).append(field)
    return [(role, tuple(dict.fromkeys(f))) for role, f in assigned.items()]


def title(role: str) -> str:
    employee = EMPLOYEES.get(role)
    return employee.title if employee is not None else role


def join_titles(roles: list[str] | tuple[str, ...]) -> str:
    """"A", "A and B", "A, B and C"."""
    names = [title(r) for r in roles]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def delegation_line(roles: list[str] | tuple[str, ...], what: str) -> str:
    """"Chief of Staff → Student Accounts Analyst: Holds by office"."""
    return f"{title(COORDINATOR)} → {join_titles(roles)}: {what}"


# --- who a refused question was asked of --------------------------------------

# Topic words -> the employee whose department the question is about. Plain
# keyword matching only (a refused question never reaches a planner or a
# model); the first match wins.
_TOPICS: tuple[tuple[str, str], ...] = (
    (r"\bregist", "enrollment_analyst"),
    (r"\benrol", "enrollment_analyst"),
    (r"\bheadcount", "enrollment_analyst"),
    (r"\bprobation|\bsuspen|\bstanding\b|\btranscript", "registrar_analyst"),
    (r"\bholds?\b|\bbalance|\bbursar|\btuition|\bowe", "student_accounts_analyst"),
    (r"\bpell\b|\bfinancial aid\b|\baid\b|\bscholarship", "financial_aid_analyst"),
    (r"\badvis", "advising_analyst"),
    (r"\bhousing|\bdorm|\bresiden(?:ce|tial) hall|\bathlet|\bconduct",
     "student_life_analyst"),
    (r"\bgpa\b|\bgrades?\b|\bcourses?\b|\bdfw\b|\binstructor|\bfaculty",
     "academic_affairs_analyst"),
    (r"\bretention|\bretain|\bgraduat|\bcohort|\bdrop ?out|\bstop ?out",
     "institutional_research_analyst"),
    (r"\badmi(?:t|ssion)|\bapplicant|\bfreshm[ae]n|\btransfer", "admissions_analyst"),
)


def topic_employee(question: str) -> str | None:
    """The employee a question is about, by its topic words, or None."""
    text = question.lower()
    for pattern, role in _TOPICS:
        if re.search(pattern, text):
            return role
    return None


def asked_of(question: str, login_role: str) -> str:
    """The employee a refused question was put to: its topic's employee, else
    the signed-in department's own, else the Chief of Staff."""
    topic = topic_employee(question)
    if topic is not None:
        return topic
    own = LOGIN_EMPLOYEES.get(login_role, ())
    return own[0] if own else COORDINATOR


def denial_message(role: str) -> str:
    """The counseling denial (``privacy.counseling_message``), naming the
    employee it was asked of. The privacy module names the briefing's three
    employees itself; for the others its generic sentence is given the
    employee's name in place of "CampusLens's"."""
    if role in EMPLOYEE_NAMES:
        return counseling_message(role)
    generic = counseling_message()
    return generic.replace("CampusLens's", f"the {title(role)}'s", 1)


# --- GET /staff ---------------------------------------------------------------

COUNTED_EVENTS = ("data.granted", "data.refused", "explore.answered")

router = APIRouter()


def _local_day(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts).astimezone().date().isoformat()
    except ValueError:
        return ""


def requests_today(events: list[dict[str, Any]]) -> dict[str, int]:
    """Requests each employee handled today (server local date): distinct
    task ids across its ``data.granted`` and ``data.refused`` events, and for
    the Chief of Staff also the Explore questions it delegated
    (``explore.answered``)."""
    today = datetime.now().astimezone().date().isoformat()
    tasks: dict[str, set[str]] = {}
    for event in events:
        if event.get("type") not in COUNTED_EVENTS:
            continue
        actor = str(event.get("actor", ""))
        if actor not in EMPLOYEES or _local_day(str(event.get("ts", ""))) != today:
            continue
        payload = event.get("payload") or {}
        task = str(payload.get("task_id") or f"event-{event.get('id')}")
        tasks.setdefault(actor, set()).add(task)
    return {role: len(tasks.get(role, set())) for role in EMPLOYEES}


def staff_order(login_role: str) -> list[str]:
    """The signed-in department's own employee(s) first, then everyone in
    the directory's order."""
    own = list(LOGIN_EMPLOYEES.get(login_role, ()))
    return [*own, *(r for r in EMPLOYEES if r not in own)]


def staff_directory(login_role: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    counts = requests_today(events)
    own = LOGIN_EMPLOYEES.get(login_role, ())
    employees = []
    for role in staff_order(login_role):
        e = EMPLOYEES[role]
        employees.append(
            {
                "role": role,
                "title": e.title,
                "job": e.job,
                "office": e.office,
                "may_read": may_read(role),
                "never_reads": never_reads(role),
                "outside_scope": outside_scope(role),
                "findings": list(ROLE_FINDINGS.get(role, ())),
                "no_data": e.no_data,
                "yours": role in own,
                "requests_today": counts[role],
            }
        )
    return {"employees": employees, "yours": list(own)}


@router.get("/staff")
def get_staff(request: Request) -> JSONResponse:
    user = request.scope["cabinet_user"]
    store = request.app.state.auth
    institution_id = int(user["institution_id"])
    events = [
        event
        for event_type in COUNTED_EVENTS
        for event in store.audit_events(institution_id, event_type)
    ]
    return JSONResponse(content=staff_directory(str(user["role"]), events))


# Every data area has a label and an owner; every area in a scope exists.
assert set(AREA_LABELS) == set(SCHOOL_AREAS) == set(AREA_OWNER)
