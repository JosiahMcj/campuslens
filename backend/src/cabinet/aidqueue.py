"""The Financial Aid review queue: facts for the aid office, never a determination.

When leadership authorizes the emergency-aid review (Q1's decision
``D-spring-registration-1``), a person can ask the cabinet to prepare a queue
for the Financial Aid office: one row per student in the M3 population
(continuing, not registered, an unresolved financial hold under the M3 amount
limit), carrying the facts the office needs to START its own review.

What this module deliberately does not do: it computes no outcome, no rank,
no order of priority, and no label for any student. The rows come out in
pseudonymous student id order, the same order the evidence drawer uses, and
the only fields a person changes later are a status from a fixed list and a
free-text note typed by someone in the aid role.

The facts are read from the same parsed record the M3 finding reads, through
the same hold predicate (``metrics.m3_qualifying_holds``), and limited to:

- the qualifying hold(s): amount, hold date, responsible office;
- the registration status;
- the advising status: appointment status and last appointment date (the
  advising group M4 reads).

The parsed record type carries no counseling group at all, so a counseling
field cannot reach the queue even by mistake. Nothing here is ever sent to a
model: the analysts and the Chief of Staff receive findings through
``permissions.findings_for_role`` only, and this module is not imported by
``cabinet.analysts`` or ``cabinet.provider`` (a test checks both).
"""

from __future__ import annotations

from typing import Any

from cabinet.fixture import Fixture
from cabinet.metrics import m3_financial_hold_under_1000, m3_qualifying_holds
from cabinet.questions import DEMO_DECISION_ID

# The one decision that opens a Financial Aid review queue today: Q1's
# emergency-aid review, whose follow-up office is Financial Aid.
AID_QUEUE_DECISION_IDS: tuple[str, ...] = (DEMO_DECISION_ID,)

# The statuses a person in the aid role may set. "open" is where every row
# starts; the system never moves a row on its own.
AID_STATUSES: tuple[str, ...] = ("open", "in_review", "closed")

# The note is free text typed by a person; the cap keeps a row readable and
# bounds what one PATCH can store.
AID_NOTE_MAX_CHARS = 1000

# The keys a row's facts may carry. Tests compare against this set, so a new
# key has to be added here on purpose.
FACT_KEYS: frozenset[str] = frozenset(
    {
        "registration_status",
        "holds",
        "advising_appointment_status",
        "advising_last_appointment_date",
    }
)
HOLD_FACT_KEYS: frozenset[str] = frozenset(
    {"amount", "hold_date", "responsible_office"}
)


def queue_rows(fixture: Fixture) -> list[tuple[str, dict[str, Any]]]:
    """``(student_id, facts)`` for every student in M3, in student id order.

    The population is M3's own row ids, so the queue always has exactly the
    M3 count. Amounts become floats rounded to cents and dates ISO strings,
    so the facts are plain JSON.
    """
    population = m3_financial_hold_under_1000(fixture).row_ids
    by_id = {record.profile.student_id: record for record in fixture.students}
    rows: list[tuple[str, dict[str, Any]]] = []
    for student_id in population:
        record = by_id[student_id]
        holds = [
            {
                "amount": round(float(hold.amount), 2),
                "hold_date": hold.hold_date.isoformat(),
                "responsible_office": hold.responsible_office,
            }
            for hold in m3_qualifying_holds(record)
        ]
        last = record.advising.last_appointment_date
        rows.append(
            (
                student_id,
                {
                    "registration_status": record.enrollment.registration_status,
                    "holds": holds,
                    "advising_appointment_status": record.advising.appointment_status,
                    "advising_last_appointment_date": (
                        last.isoformat() if last is not None else None
                    ),
                },
            )
        )
    return rows


__all__ = [
    "AID_NOTE_MAX_CHARS",
    "AID_QUEUE_DECISION_IDS",
    "AID_STATUSES",
    "FACT_KEYS",
    "HOLD_FACT_KEYS",
    "queue_rows",
]
