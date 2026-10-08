"""The staff action worklist: briefing section 5 as tracked work.

The operational actions are the things staff can do now, each with a
responsible office and no leadership approval: Student Success reviews the
students with no advising contact this term (M4), Financial Aid reviews the
small-balance cases (M3), and every office with unresolved holds resolves
them (one action per M5 row). This module builds that list from the
findings in code, the same way ``cabinet.questions`` builds section 5: every
count comes from a finding, nothing is model text, and no action carries a
student id (an action is an office, a count and the finding it comes from).

A person tracks each action: a status from a fixed list, an owner (a user
of the institution, or the office itself), a due date, notes, and a history
of changes (``cabinet.store`` keeps them, per institution and per dataset).
"Send to office" composes a plain message for the office mailbox
(``compose_message``) with the counts and a sign-in link, never a student
name or id, and the outbound seam (``cabinet.outbound``) delivers it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from cabinet.metrics import M3_AMOUNT_LIMIT

# The statuses a person may set. "todo" is where every action starts; the
# system never moves an action on its own.
ACTION_STATUSES: tuple[str, ...] = ("todo", "in_progress", "done")

STATUS_WORDS: dict[str, str] = {
    "todo": "To do",
    "in_progress": "In progress",
    "done": "Done",
}

# A note is free text typed by a person; the cap keeps a thread readable and
# bounds what one request can store (the aid queue's limit).
ACTION_NOTE_MAX_CHARS = 1000

# The office that reviews the small-balance cases. The aid role sees only
# this office's actions.
FINANCIAL_AID_OFFICE = "Financial Aid"

# The dispatches table's task id for an action's message: one message per
# action per dataset (the table's UNIQUE constraint), so a double-clicked
# Send composes once and a sent message is never sent twice.
TASK_PREFIX = "ACTION-"

# The page the message links to.
STAFF_ACTIONS_PATH = "/view/staff-actions"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class ActionSpec:
    """One action as the findings define it."""

    key: str
    office: str
    finding_id: str
    count: int | None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "office"


def action_specs(findings_obj: dict[str, Any]) -> list[ActionSpec]:
    """The actions for one dataset's findings, in the briefing's order:
    Student Success, Financial Aid, then one per office with unresolved
    holds. A finding with no value still yields its action, with no count,
    so the page can say the figure is not available instead of hiding it."""
    advising = findings_obj.get("M4", {}).get("value")
    balances = findings_obj.get("M3", {}).get("value")
    specs = [
        ActionSpec(
            key="advising-outreach",
            office="Student Success",
            finding_id="M4",
            count=advising if isinstance(advising, int) else None,
        ),
        ActionSpec(
            key="small-balance-review",
            office=FINANCIAL_AID_OFFICE,
            finding_id="M3",
            count=balances if isinstance(balances, int) else None,
        ),
    ]
    rows = findings_obj.get("M5", {}).get("value")
    if isinstance(rows, list):
        for row in rows:
            office = str(row.get("office", "")).strip()
            count = row.get("count")
            if not office:
                continue
            specs.append(
                ActionSpec(
                    key=f"holds-{_slug(office)}",
                    office=office,
                    finding_id="M5",
                    count=count if isinstance(count, int) else None,
                )
            )
    return specs


def _plural(count: int, one: str, many: str) -> str:
    return one if count == 1 else many


def action_words(finding_id: str, office: str, count: int | None) -> dict[str, str]:
    """The title, the "what to do" sentence and the counted noun of one
    action, in plain words (no finding ids, no field names)."""
    limit = f"${M3_AMOUNT_LIMIT:,.0f}"
    if finding_id == "M4":
        noun = (
            "students with no advising contact this term"
            if count is None
            else f"{count} {_plural(count, 'student', 'students')} with no "
            "advising contact this term"
        )
        return {
            "title": "Reach students with no advising contact",
            "what": (
                "Review the continuing students who have not registered for "
                "spring and have not met an advisor this term, and offer each "
                "one an advising appointment."
            ),
            "noun": noun,
        }
    if finding_id == "M3":
        noun = (
            f"small-balance cases (holds under {limit})"
            if count is None
            else f"{count} small-balance {_plural(count, 'case', 'cases')} "
            f"(holds under {limit})"
        )
        return {
            "title": f"Review small-balance holds under {limit}",
            "what": (
                "Review the continuing students who have not registered and "
                f"carry an unresolved financial hold under {limit}: a payment "
                "plan or a focused aid review often clears these."
            ),
            "noun": noun,
        }
    noun = (
        "unresolved holds"
        if count is None
        else f"{count} unresolved {_plural(count, 'hold', 'holds')}"
    )
    return {
        "title": f"Resolve the {office} office's holds",
        "what": (
            f"Work through the unresolved holds recorded for the {office} "
            "office, so they no longer stand between students and "
            "registration."
        ),
        "noun": noun,
    }


def valid_due_date(value: str) -> bool:
    """A due date is a calendar date written YYYY-MM-DD."""
    if not _DATE_RE.match(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def readable_date(value: str) -> str:
    """2026-10-20 -> "October 20, 2026" (the message's wording)."""
    parsed = date.fromisoformat(value)
    return f"{parsed.strftime('%B')} {parsed.day}, {parsed.year}"


def compose_message(
    item: dict[str, Any],
    *,
    sender: str,
    institution_name: str,
    sign_in_url: str | None,
) -> dict[str, str]:
    """The message to the office mailbox for one action: the office, what to
    do, the count, the status, the owner and due date, the sender, and a
    sign-in link. Built in code from the stored action only; the action
    has no student column, so no student name or id can reach it."""
    office = str(item["office"])
    count = item["count"] if isinstance(item["count"], int) else None
    words = action_words(str(item["finding_id"]), office, count)
    owner = item.get("owner")
    due = item.get("due_date")
    count_line = (
        f"How many: {words['noun']}."
        if count is not None
        else "How many: the figure is not available in the current data."
    )
    link_lines = (
        [
            "See the figure, the records behind it and the latest status in "
            "CampusLens (sign in with your own account):",
            f"{sign_in_url.rstrip('/')}{STAFF_ACTIONS_PATH}",
        ]
        if sign_in_url
        else [
            "See the figure, the records behind it and the latest status on "
            "the Staff actions page in CampusLens (sign in with your own "
            "account)."
        ]
    )
    lines = [
        f"To the {office} office,",
        "",
        f"{sender} sent this staff action to your office from CampusLens "
        f"at {institution_name}. It needs no leadership approval.",
        "",
        f"Action: {words['title']}",
        f"What to do: {words['what']}",
        count_line,
        f"Status: {STATUS_WORDS.get(str(item['status']), str(item['status']))}",
        f"Owner: {owner if owner else f'the {office} office'}",
        f"Due: {readable_date(str(due)) if due else 'no due date set'}",
        "",
        *link_lines,
        "",
        "This message was composed by CampusLens from the verified figures "
        "of the current data and sent by a named member of staff. It "
        "contains no student records and no student identifiers.",
    ]
    return {
        "subject": f"Staff action for {office}: {words['title']}",
        "body": "\n".join(lines),
    }


__all__ = [
    "ACTION_NOTE_MAX_CHARS",
    "ACTION_STATUSES",
    "ActionSpec",
    "FINANCIAL_AID_OFFICE",
    "STAFF_ACTIONS_PATH",
    "STATUS_WORDS",
    "TASK_PREFIX",
    "action_specs",
    "action_words",
    "compose_message",
    "readable_date",
    "valid_due_date",
]
