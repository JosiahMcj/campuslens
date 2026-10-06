"""Deterministic metric functions M1-M7 (CONTRACTS.md), the M8 support
indicators (cabinet.indicators), and the findings object.

One pure function per contract row. Every value and row-ID list follows
CONTRACTS.md exactly; the rules in brief:

- A zero denominator yields ``None`` (the UI renders ``--``), never ``0 %``.
- ``0`` is a real value and renders as ``0``; ``--`` is never a synonym for zero.
- Windows anchor to data timestamps (the derived as-of date, the prior-year
  equivalent date, ``terms.in_session.start_date``), never to "now".

Ratios (M1, M7) are computed as exact ``Fraction`` values; ``findings()``
converts them to floats for JSON and renders the one-decimal percent display
with a proper minus sign (U+2212).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

from cabinet.fixture import Fixture, Hold, StudentRecord
from cabinet.indicators import SMALL_BALANCE_LIMIT, evaluate_indicators, m8_finding

MINUS_SIGN = "\u2212"
# Alias of indicators.SMALL_BALANCE_LIMIT, so M3 and support rule I1 share one
# small-balance boundary and can never drift apart.
M3_AMOUNT_LIMIT = SMALL_BALANCE_LIMIT


@dataclass(frozen=True)
class RatioResult:
    """M1/M7: a ratio change plus the row IDs behind numerator and denominator."""

    value: Fraction | None
    reason: str | None
    numerator_ids: list[str]
    denominator_ids: list[str]
    numerator_sum: int | None = None
    denominator_sum: int | None = None


@dataclass(frozen=True)
class CountResult:
    """M2/M3/M4: a student count plus the row IDs behind it."""

    value: int | None
    reason: str | None
    row_ids: list[str]


@dataclass(frozen=True)
class OfficeHolds:
    """M5: unresolved hold records for one responsible office."""

    office: str
    count: int
    hold_row_ids: list[str]


@dataclass(frozen=True)
class DaysResult:
    """M6: whole calendar days until registration closes."""

    value: int | None
    reason: str | None
    closed: bool


def _sid(record: StudentRecord) -> str:
    return record.profile.student_id


def _is_registered(record: StudentRecord) -> bool:
    return record.enrollment.registration_status == "registered"


def _m1_populations(
    fixture: Fixture,
) -> tuple[list[StudentRecord], list[StudentRecord]]:
    """M1 populations (CONTRACTS.md M1), shared by M1 and M7 so they stay comparable.

    Numerator: ``students`` rows with ``profile.continuing`` and
    ``registration_status == "registered"`` and ``registration_date <= as_of``.
    Denominator: ``prior_year_students`` rows, same filters, with a non-null
    ``registration_date <= terms.prior_year.prior_year_equivalent_date``.
    """
    numerator: list[StudentRecord] = []
    if fixture.as_of is not None:
        as_of = fixture.as_of
        numerator = [
            s
            for s in fixture.students
            if s.profile.continuing
            and _is_registered(s)
            and s.enrollment.registration_date is not None
            and s.enrollment.registration_date <= as_of
        ]
    equivalent = fixture.terms.prior_year.prior_year_equivalent_date
    denominator = [
        s
        for s in fixture.prior_year_students
        if s.profile.continuing
        and _is_registered(s)
        and s.enrollment.registration_date is not None
        and s.enrollment.registration_date <= equivalent
    ]
    return numerator, denominator


def m1_registration_vs_prior_year(fixture: Fixture) -> RatioResult:
    """M1 — spring registration vs. same point last year.

    ``registered_continuing(as_of) / registered_continuing(equivalent date) − 1``.
    ``None`` exactly when the prior-year count is 0.
    """
    numerator, denominator = _m1_populations(fixture)
    numerator_ids = sorted(_sid(s) for s in numerator)
    denominator_ids = sorted(_sid(s) for s in denominator)
    if not denominator:
        return RatioResult(
            value=None,
            reason="prior-year registered-continuing count is 0; "
            "a zero denominator renders `--`, never `0 %`",
            numerator_ids=numerator_ids,
            denominator_ids=denominator_ids,
        )
    return RatioResult(
        value=Fraction(len(numerator), len(denominator)) - 1,
        reason=None,
        numerator_ids=numerator_ids,
        denominator_ids=denominator_ids,
    )


def m2_unregistered_continuing(fixture: Fixture) -> CountResult:
    """M2 — continuing students not yet registered.

    ``count(profile.continuing = true
    ∧ enrollment.registration_status ≠ "registered")``.
    ``None`` exactly when ``students`` is empty; an all-registered fixture is ``0``.
    """
    if not fixture.students:
        return CountResult(
            value=None,
            reason="the fixture has no current-term students",
            row_ids=[],
        )
    rows = [
        s for s in fixture.students if s.profile.continuing and not _is_registered(s)
    ]
    return CountResult(
        value=len(rows),
        reason=None,
        row_ids=sorted(_sid(s) for s in rows),
    )


def m3_financial_hold_under_1000(fixture: Fixture) -> CountResult:
    """M3 — of M2, unresolved financial hold under $1,000 (strict boundary).

    ``count(row ∈ M2 ∧ ∃ h ∈ holds: h.category = "financial"
    ∧ h.resolved = false ∧ h.amount < 1000)``. ``None`` exactly when the M2
    population is empty or the fixture is empty; no qualifying holds is ``0``.
    """
    m2 = m2_unregistered_continuing(fixture)
    if m2.value is None or not m2.row_ids:
        return CountResult(
            value=None,
            reason="the M2 population is empty",
            row_ids=[],
        )
    m2_ids = set(m2.row_ids)
    rows = [
        s for s in fixture.students if _sid(s) in m2_ids and m3_qualifying_holds(s)
    ]
    return CountResult(
        value=len(rows),
        reason=None,
        row_ids=sorted(_sid(s) for s in rows),
    )


def m3_qualifying_holds(record: StudentRecord) -> list[Hold]:
    """The holds that put a student in M3: financial, unresolved, and strictly
    under the M3 amount limit. One named predicate shared by M3 itself and the
    Financial Aid review queue (``cabinet.aidqueue``), so the queue's facts can
    never describe a different hold than the one the finding counted."""
    return [
        h
        for h in record.holds
        if h.category == "financial" and not h.resolved and h.amount < M3_AMOUNT_LIMIT
    ]


def m4_no_advising_this_term(fixture: Fixture) -> CountResult:
    """M4 — of M2, no advising appointment in the term in session.

    ``count(row ∈ M2 ∧ (advising.last_appointment_date is null
    ∨ last_appointment_date < terms.in_session.start_date))``. An appointment
    exactly on the in-session start counts as this term. ``None`` exactly when
    the M2 population is empty, the fixture is empty, or
    ``terms.in_session.start_date`` is missing.
    """
    m2 = m2_unregistered_continuing(fixture)
    if m2.value is None or not m2.row_ids:
        return CountResult(
            value=None,
            reason="the M2 population is empty",
            row_ids=[],
        )
    start = fixture.terms.in_session.start_date
    if start is None:
        return CountResult(
            value=None,
            reason="terms.in_session.start_date is missing",
            row_ids=[],
        )
    m2_ids = set(m2.row_ids)
    rows = [
        s
        for s in fixture.students
        if _sid(s) in m2_ids
        and (
            s.advising.last_appointment_date is None
            or s.advising.last_appointment_date < start
        )
    ]
    return CountResult(
        value=len(rows),
        reason=None,
        row_ids=sorted(_sid(s) for s in rows),
    )


def m5_unresolved_holds_by_office(fixture: Fixture) -> list[OfficeHolds]:
    """M5 — unresolved hold records by office, over ALL current-term students.

    The unit is unresolved hold records, not students: a student with two
    unresolved holds at one office contributes 2 (and their ID appears twice in
    ``hold_row_ids``). Sorted by office. No unresolved holds — including an
    empty fixture — returns an empty list (the UI renders an empty table with
    the line "No unresolved holds", never ``--``).
    """
    by_office: dict[str, list[str]] = {}
    for student in fixture.students:
        for hold in student.holds:
            if not hold.resolved:
                by_office.setdefault(hold.responsible_office, []).append(_sid(student))
    return [
        OfficeHolds(office=office, count=len(ids), hold_row_ids=sorted(ids))
        for office, ids in sorted(by_office.items())
    ]


def m6_days_until_registration_closes(fixture: Fixture) -> DaysResult:
    """M6 — days until registration closes.

    ``terms.current.registration_close_date − as_of`` in whole calendar days.
    ``None`` exactly when the close date is missing (or the as-of date cannot be
    derived). A past close date clamps to ``0`` with ``closed=True``, never
    negative.
    """
    close = fixture.terms.current.registration_close_date
    if close is None:
        return DaysResult(
            value=None,
            reason="terms.current.registration_close_date is missing",
            closed=False,
        )
    if fixture.as_of is None:
        return DaysResult(
            value=None,
            reason="as-of date could not be derived: "
            "no enrollment.registration_date in students",
            closed=False,
        )
    delta = (close - fixture.as_of).days
    if delta < 0:
        return DaysResult(value=0, reason=None, closed=True)
    return DaysResult(value=delta, reason=None, closed=False)


def m7_credit_hours_vs_prior_year(fixture: Fixture) -> RatioResult:
    """M7 — registered credit hours vs. prior year, over the M1 populations.

    ``sum(registered_credit_hours over the M1 numerator population)
    / sum(over the M1 denominator population) − 1``. ``None`` exactly when the
    prior-year sum is 0.
    """
    numerator, denominator = _m1_populations(fixture)
    numerator_ids = sorted(_sid(s) for s in numerator)
    denominator_ids = sorted(_sid(s) for s in denominator)
    numerator_sum = sum(s.enrollment.registered_credit_hours for s in numerator)
    denominator_sum = sum(s.enrollment.registered_credit_hours for s in denominator)
    if denominator_sum == 0:
        return RatioResult(
            value=None,
            reason="prior-year registered credit-hour sum is 0; "
            "a zero denominator renders `--`, never `0 %`",
            numerator_ids=numerator_ids,
            denominator_ids=denominator_ids,
            numerator_sum=numerator_sum,
            denominator_sum=denominator_sum,
        )
    return RatioResult(
        value=Fraction(numerator_sum, denominator_sum) - 1,
        reason=None,
        numerator_ids=numerator_ids,
        denominator_ids=denominator_ids,
        numerator_sum=numerator_sum,
        denominator_sum=denominator_sum,
    )


def _percent_display(value: Fraction) -> str:
    """One-decimal percent display, rounded half-up from the exact ratio.

    Computed with ``Decimal`` from the ``Fraction`` (never through a float),
    so halfway values are well-defined: ``Fraction(-1, 80)`` renders
    ``−1.3 %``, and the validator's allowed percent is read back from this
    string — a single source of truth.
    """
    scaled = Decimal(value.numerator) * 100 / Decimal(value.denominator)
    rounded = scaled.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{rounded}".replace("-", MINUS_SIGN) + " %"


def _count_display(value: int) -> str:
    return str(value)


def _m5_display(offices: list[OfficeHolds]) -> str:
    total = sum(o.count for o in offices)
    if total == 0:
        return "No unresolved holds"
    return f"{total} unresolved hold" + ("" if total == 1 else "s")


def _ratio_finding(
    finding_id: str,
    title: str,
    result: RatioResult,
    comparison: dict[str, Any] | None,
    source_fields: list[str],
    definition: str,
) -> dict[str, Any]:
    return {
        "id": finding_id,
        "title": title,
        "value": float(result.value) if result.value is not None else None,
        "display": _percent_display(result.value) if result.value is not None else "--",
        "reason": result.reason,
        "comparison": comparison,
        "source_fields": source_fields,
        "row_ids": {
            "numerator": result.numerator_ids,
            "denominator": result.denominator_ids,
        },
        "definition": definition,
    }


def _count_finding(
    finding_id: str,
    title: str,
    result: CountResult,
    source_fields: list[str],
    definition: str,
    comparison: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": finding_id,
        "title": title,
        "value": result.value,
        "display": _count_display(result.value) if result.value is not None else "--",
        "reason": result.reason,
        "comparison": comparison,
        "source_fields": source_fields,
        "row_ids": result.row_ids,
        "definition": definition,
    }


def findings(fixture: Fixture, *, fixture_path: str | Path) -> dict[str, Any]:
    """The ROADMAP §3 findings object: one entry per metric M1-M8, plus meta.

    The full object (row IDs included) backs the executive's evidence drawer;
    an AI employee receives the same object minus every row-ID list, scoped to
    its role (``permissions.findings_for_role``).
    """
    m1 = m1_registration_vs_prior_year(fixture)
    m2 = m2_unregistered_continuing(fixture)
    m3 = m3_financial_hold_under_1000(fixture)
    m4 = m4_no_advising_this_term(fixture)
    m5 = m5_unresolved_holds_by_office(fixture)
    m6 = m6_days_until_registration_closes(fixture)
    m7 = m7_credit_hours_vs_prior_year(fixture)
    m8 = evaluate_indicators(fixture)
    terms = fixture.terms
    equivalent_date = terms.prior_year.prior_year_equivalent_date.isoformat()

    out: dict[str, Any] = {
        "M1": _ratio_finding(
            "M1",
            "Spring registration vs. same point last year",
            m1,
            comparison=(
                {
                    "prior_year_registered_continuing": len(m1.denominator_ids),
                    "prior_year_equivalent_date": equivalent_date,
                }
                if m1.value is not None
                else None
            ),
            source_fields=[
                "enrollment.term",
                "enrollment.registration_status",
                "enrollment.registration_date",
                "terms.prior_year.prior_year_equivalent_date",
                "profile.continuing",
            ],
            definition=(
                "registered_continuing(as_of) / "
                "registered_continuing(prior_year_equivalent_date) − 1"
            ),
        ),
        "M2": _count_finding(
            "M2",
            "Continuing students not yet registered",
            m2,
            source_fields=["profile.continuing", "enrollment.registration_status"],
            definition=(
                "count(profile.continuing = true "
                '∧ enrollment.registration_status ≠ "registered")'
            ),
        ),
        "M3": _count_finding(
            "M3",
            "Of M2, financial hold under $1,000",
            m3,
            source_fields=["holds[].category", "holds[].amount", "holds[].resolved"],
            definition=(
                'count(row ∈ M2 ∧ ∃ h ∈ holds: h.category = "financial" '
                "∧ h.resolved = false ∧ h.amount < 1000)"
            ),
            # The threshold as data, so the UI never hardcodes $1,000.
            comparison={"threshold_usd": int(M3_AMOUNT_LIMIT)},
        ),
        "M4": _count_finding(
            "M4",
            "Of M2, no advising appointment this term",
            m4,
            source_fields=[
                "advising.last_appointment_date",
                "advising.appointment_status",
                "terms.in_session.start_date",
            ],
            definition=(
                "count(row ∈ M2 ∧ (advising.last_appointment_date is null "
                "∨ last_appointment_date < terms.in_session.start_date))"
            ),
        ),
        "M5": {
            "id": "M5",
            "title": "Unresolved holds by responsible office",
            "value": [
                {"office": o.office, "count": o.count, "hold_row_ids": o.hold_row_ids}
                for o in m5
            ],
            "display": _m5_display(m5),
            "reason": None,
            "comparison": None,
            "source_fields": [
                "holds[].category",
                "holds[].responsible_office",
                "holds[].resolved",
            ],
            "row_ids": sorted({sid for o in m5 for sid in o.hold_row_ids}),
            "definition": (
                "for each responsible_office: count(h ∈ holds over all current-term "
                "students where h.resolved = false), grouped by office"
            ),
        },
        "M6": {
            "id": "M6",
            "title": "Days until registration closes",
            "value": m6.value,
            "display": _count_display(m6.value) if m6.value is not None else "--",
            "reason": m6.reason,
            "closed": m6.closed,
            "comparison": None,
            "source_fields": ["terms.current.registration_close_date"],
            "row_ids": [],
            "definition": (
                "terms.current.registration_close_date − as_of, in whole calendar days"
            ),
        },
        "M7": _ratio_finding(
            "M7",
            "Registered credit hours vs. prior year",
            m7,
            comparison=(
                {
                    "prior_year_registered_credit_hours": m7.denominator_sum,
                    "prior_year_equivalent_date": equivalent_date,
                }
                if m7.value is not None
                else None
            ),
            source_fields=["enrollment.registered_credit_hours"],
            definition=(
                "sum(enrollment.registered_credit_hours over the M1 numerator "
                "population) / sum(registered_credit_hours over the M1 "
                "denominator population) − 1"
            ),
        ),
        "M8": m8_finding(m8),
        "meta": {
            "as_of": fixture.as_of.isoformat() if fixture.as_of is not None else None,
            "fixture": str(fixture_path),
            "terms": {
                "current": terms.current.term,
                "prior_year": terms.prior_year.term,
                "in_session": terms.in_session.term,
                "prior_year_equivalent_date": equivalent_date,
                "in_session_start_date": (
                    terms.in_session.start_date.isoformat()
                    if terms.in_session.start_date is not None
                    else None
                ),
                "registration_close_date": (
                    terms.current.registration_close_date.isoformat()
                    if terms.current.registration_close_date is not None
                    else None
                ),
            },
        },
    }
    return out
