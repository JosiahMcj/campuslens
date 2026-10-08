"""Explainable support indicators (CONTRACTS.md M8): named rules, never a sum.

Each rule in :data:`RULES` is one named, deterministic test over a single
student record, with a plain-language reason that names the exact fields it
reads. A student "has indicators" when at least one rule fires. There is no
weighting, no sum across rules, no threshold beyond each rule's own
definition, and no ordering by severity: the M8 finding is a count of people
who may need support, plus per-rule counts. The per-student detail (which
rules fired for which pseudonymous id) exists only for the evidence drawer
through ``GET /findings``; AI employees receive the aggregate count with all
row-level detail stripped (``permissions.findings_for_role``).

The registry is data, a tuple of rule objects a reviewer can read in one
screen. Rules read only fields the frozen schema carries (SCHEMA.md); a rule
that needs a field the schema lacks cannot exist here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from cabinet.fixture import Fixture, StudentRecord

# The small-balance boundary, shared with M3: metrics.M3_AMOUNT_LIMIT aliases
# this constant so the metric and rule I1 can never drift apart.
SMALL_BALANCE_LIMIT = Decimal("1000")

# I4's window: registration closing this many days out (or fewer) counts.
CLOSING_SOON_DAYS = 14


@dataclass(frozen=True)
class RuleContext:
    """The term anchors the date-based rules read.

    Windows anchor to data timestamps (CONTRACTS.md §0), never to "now": the
    derived as-of date, the in-session term's start, and the current term's
    registration close date.
    """

    as_of: date | None
    in_session_start: date | None
    registration_close_date: date | None


@dataclass(frozen=True)
class IndicatorRule:
    """One named support rule: its id, title, the fields it reads, the
    plain-language reason shown in the evidence drawer, and the predicate
    itself."""

    rule_id: str
    title: str
    fields_read: tuple[str, ...]
    reason: str
    predicate: Callable[[StudentRecord, RuleContext], bool]

    def fires(self, record: StudentRecord, context: RuleContext) -> bool:
        return self.predicate(record, context)


def _continuing_unregistered(record: StudentRecord) -> bool:
    """The shared gate for I1, I2, and I4 (the M2 population test)."""
    return (
        record.profile.continuing
        and record.enrollment.registration_status != "registered"
    )


def _i1_small_financial_hold(record: StudentRecord, _context: RuleContext) -> bool:
    return _continuing_unregistered(record) and any(
        hold.category == "financial"
        and not hold.resolved
        and hold.amount < SMALL_BALANCE_LIMIT
        for hold in record.holds
    )


def _i2_no_advising_this_term(record: StudentRecord, context: RuleContext) -> bool:
    if not _continuing_unregistered(record):
        return False
    last = record.advising.last_appointment_date
    if last is None:
        # Never seen an advisor: true regardless of the term dates.
        return True
    if context.in_session_start is None:
        # "This term" cannot be established without the in-session start.
        return False
    return last < context.in_session_start


def _i3_holds_across_offices(record: StudentRecord, _context: RuleContext) -> bool:
    offices = {hold.responsible_office for hold in record.holds if not hold.resolved}
    return len(offices) >= 2


def _i4_registration_closes_soon(record: StudentRecord, context: RuleContext) -> bool:
    if not _continuing_unregistered(record):
        return False
    if context.as_of is None or context.registration_close_date is None:
        return False
    days = (context.registration_close_date - context.as_of).days
    # Registration already closed is not "closes within 14 days".
    return 0 <= days <= CLOSING_SOON_DAYS


# The rule registry, as data. Order is display order (I1 first), nothing more:
# no rule outranks another.
RULES: tuple[IndicatorRule, ...] = (
    IndicatorRule(
        rule_id="I1",
        title="Unresolved financial hold under $1,000",
        fields_read=(
            "profile.continuing",
            "enrollment.registration_status",
            "holds[].category",
            "holds[].resolved",
            "holds[].amount",
        ),
        reason=(
            "Continuing and not registered (profile.continuing, "
            "enrollment.registration_status) with an unresolved financial "
            "hold under $1,000 (holds[].category, holds[].resolved, "
            "holds[].amount)."
        ),
        predicate=_i1_small_financial_hold,
    ),
    IndicatorRule(
        rule_id="I2",
        title="No advising appointment this term",
        fields_read=(
            "profile.continuing",
            "enrollment.registration_status",
            "advising.last_appointment_date",
            "terms.in_session.start_date",
        ),
        reason=(
            "Continuing and not registered (profile.continuing, "
            "enrollment.registration_status) with no advising appointment "
            "in the term in session (advising.last_appointment_date, "
            "terms.in_session.start_date)."
        ),
        predicate=_i2_no_advising_this_term,
    ),
    IndicatorRule(
        rule_id="I3",
        title="Unresolved holds at more than one office",
        fields_read=("holds[].resolved", "holds[].responsible_office"),
        reason=(
            "Two or more unresolved holds at different offices "
            "(holds[].resolved, holds[].responsible_office)."
        ),
        predicate=_i3_holds_across_offices,
    ),
    IndicatorRule(
        rule_id="I4",
        title="Registration closes within 14 days",
        fields_read=(
            "profile.continuing",
            "enrollment.registration_status",
            "terms.current.registration_close_date",
        ),
        reason=(
            "Continuing and not registered (profile.continuing, "
            "enrollment.registration_status) while registration closes "
            "within 14 days of the as-of date "
            "(terms.current.registration_close_date)."
        ),
        predicate=_i4_registration_closes_soon,
    ),
)


@dataclass(frozen=True)
class RuleOutcome:
    """One rule's result over the fixture: the rule and the ids it fired for."""

    rule: IndicatorRule
    row_ids: list[str]


@dataclass(frozen=True)
class IndicatorReport:
    """M8's computation: the union count, the per-rule outcomes, and the
    per-student map of fired rule ids (evidence-drawer detail only)."""

    value: int | None
    reason: str | None
    row_ids: list[str]
    row_rules: dict[str, list[str]]
    outcomes: tuple[RuleOutcome, ...]


def evaluate_indicators(fixture: Fixture) -> IndicatorReport:
    """Fire every rule over every current-term student and union the results.

    ``value`` is ``None`` exactly when the fixture has no current-term
    students (the UI renders ``--``); a fixture with students and no fires is
    a real ``0``. The rules are evaluated even then, so the finding always
    carries the per-rule table.
    """
    context = RuleContext(
        as_of=fixture.as_of,
        in_session_start=fixture.terms.in_session.start_date,
        registration_close_date=fixture.terms.current.registration_close_date,
    )
    outcomes: list[RuleOutcome] = []
    row_rules: dict[str, list[str]] = {}
    for rule in RULES:
        fired = sorted(
            record.profile.student_id
            for record in fixture.students
            if rule.fires(record, context)
        )
        outcomes.append(RuleOutcome(rule=rule, row_ids=fired))
        for student_id in fired:
            row_rules.setdefault(student_id, []).append(rule.rule_id)
    union_ids = sorted(row_rules)
    if not fixture.students:
        return IndicatorReport(
            value=None,
            reason="the fixture has no current-term students",
            row_ids=[],
            row_rules={},
            outcomes=tuple(outcomes),
        )
    return IndicatorReport(
        value=len(union_ids),
        reason=None,
        row_ids=union_ids,
        row_rules=row_rules,
        outcomes=tuple(outcomes),
    )


def m8_source_fields(report: IndicatorReport) -> list[str]:
    """The union of the fields the rules read, in registry order, deduplicated."""
    fields: list[str] = []
    for outcome in report.outcomes:
        for field in outcome.rule.fields_read:
            if field not in fields:
                fields.append(field)
    return fields


def m8_finding(report: IndicatorReport) -> dict[str, Any]:
    """The M8 findings entry: the count, the per-rule table with the fields
    read, and the per-student map of fired rules for the evidence drawer.

    ``permissions.findings_for_role`` strips ``row_ids`` and ``row_rules``
    before any AI employee receives this object, so the per-student detail
    never leaves the evidence layer.
    """
    return {
        "id": "M8",
        "title": "Students with one or more support indicators",
        "value": report.value,
        "display": str(report.value) if report.value is not None else "--",
        "reason": report.reason,
        "comparison": None,
        "source_fields": m8_source_fields(report),
        "row_ids": report.row_ids,
        "definition": (
            "count(students for whom at least one support indicator rule "
            "(I1-I4) fires); the rules never combine into a weighting, a "
            "sum, or an ordering"
        ),
        "rules": [
            {
                "id": outcome.rule.rule_id,
                "title": outcome.rule.title,
                "reason": outcome.rule.reason,
                "fields_read": list(outcome.rule.fields_read),
                "count": len(outcome.row_ids),
                "row_ids": outcome.row_ids,
            }
            for outcome in report.outcomes
        ],
        "row_rules": {
            student_id: rule_ids
            for student_id, rule_ids in sorted(report.row_rules.items())
        },
    }
