"""The approved executive questions and their registry (the Q2 slice).

Q1 — "What should I know about spring registration?" — is the prototype's
original question (PROPOSAL.md, DEMO-SCRIPT.md). Its dispatch is the standing
``permissions.ROLE_FINDINGS`` split, and the registry adds nothing to any
payload, prompt, or hash Q1 produces: its replay/golden files stay valid
byte-for-byte.

Q2 — "Where are unresolved holds affecting continued enrollment?" — is the
second approved question (PROPOSAL.md, "Executive Questions the Platform Can
Eventually Support"). Dispatch: the Student Success Analyst receives M3 and
M5 (holds), the Enrollment Analyst receives M2, and M6 is aggregate-only for
the Chief of Staff, who receives the M2, M3, M5, M6 aggregates.

Each approved question carries:

- ``id`` and ``text`` — what ``GET /questions`` lists;
- the matching rule (``match_question``): normalized, case-insensitive,
  trailing "?" optional — the rule the API has always applied;
- ``dispatch`` — role -> the finding ids the role receives for this
  question, a subset of ``permissions.ROLE_FINDINGS[role]`` (checked at
  import);
- ``chief_brief`` — one sentence telling the Chief of Staff what the
  president asked. It joins the user prompt — and the received payload,
  hence the replay hash — for every question other than the default, so
  Q1's prompt bytes and golden hashes never change;
- ``build_decisions`` and ``build_actions`` — sections 6 and 5, functions
  over the findings object; every number comes from the findings.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cabinet.metrics import M3_AMOUNT_LIMIT
from cabinet.permissions import ROLE_FINDINGS, findings_for_role

# The key under which a non-default question rides in the payload a role
# receives (and therefore in the replay hash). The default question never
# adds it, which is what keeps Q1's hashes byte-identical.
QUESTION_KEY = "question"


@dataclass(frozen=True)
class Question:
    """One approved executive question."""

    id: str
    text: str
    dispatch: dict[str, tuple[str, ...]]
    chief_brief: str
    build_decisions: Callable[[dict[str, Any]], list[dict[str, Any]]]
    build_actions: Callable[[dict[str, Any]], list[dict[str, Any]]]


# --- Q1: spring registration (the original question, verbatim behavior) --------

DEMO_DECISION_ID = "D-spring-registration-1"


def _q1_decisions(findings_obj: dict[str, Any]) -> list[dict[str, Any]]:
    """The leadership decision(s), text built from the findings at request time.

    The wording is PROPOSAL.md's ("Illustrative Briefing"): leadership decides
    whether to authorize a focused emergency-aid eligibility REVIEW for
    students below a defined balance threshold. Financial-aid eligibility
    decisions are explicitly out of scope — approval authorizes the review,
    nothing else. The threshold is the M3 contract's amount limit
    (``metrics.M3_AMOUNT_LIMIT``) and the count is M3's current value, so the
    wording can never drift from the data or the contract. When M3 has no
    value the prose says so — a bare ``--`` is never interpolated.
    """
    holds = findings_obj["M3"].get("value")
    limit = f"${M3_AMOUNT_LIMIT:,.0f}"
    if isinstance(holds, int):
        text = (
            "Decide whether to authorize a focused emergency-aid "
            f"eligibility review for the {holds} continuing students "
            "whose barrier to spring registration is an unresolved "
            f"financial hold below the defined balance threshold of "
            f"{limit}. Approving authorizes the review only. No "
            "financial-aid eligibility decision is made, and nothing is "
            "sent."
        )
        follow_up = (
            "Conduct the emergency-aid eligibility review for the "
            f"{holds} students with unresolved financial holds below "
            f"{limit}."
        )
    else:
        text = (
            "Decide whether to authorize a focused emergency-aid "
            "eligibility review for continuing students whose barrier to "
            "spring registration is an unresolved financial hold below the "
            f"defined balance threshold of {limit}; the count of those "
            "students is not available in the current findings (M3). "
            "Approving authorizes the review only. No financial-aid "
            "eligibility decision is made, and nothing is sent."
        )
        follow_up = (
            "Conduct the emergency-aid eligibility review for the students "
            f"with unresolved financial holds below {limit} (the M3 count "
            "is not available)."
        )
    return [
        {
            "id": DEMO_DECISION_ID,
            "title": (
                "Emergency-aid eligibility review below the "
                f"{limit} balance threshold"
            ),
            "text": text,
            "follow_up": {"office": "Financial Aid", "description": follow_up},
        }
    ]


def _q1_actions(findings_obj: dict[str, Any]) -> list[dict[str, Any]]:
    """Section 5, the operational actions, built from the findings in code
    (ROADMAP §3 layer 5: actions are code, never model text). Each action
    names its responsible office and the findings behind its numbers; the
    leadership decision lives separately in section 6 (``_q1_decisions``).
    """
    limit = f"${M3_AMOUNT_LIMIT:,.0f}"
    # When a finding has no value the sentence says so; a bare ``--`` (the
    # contracted display for the evidence list and tiles) never lands in
    # prose.
    advising = findings_obj["M4"].get("value")
    advising_text = (
        f"Review the {advising} students with no advising contact this term."
        if isinstance(advising, int)
        else "No students to review; the M4 value is not available."
    )
    balances = findings_obj["M3"].get("value")
    balances_text = (
        f"Review the {balances} small-balance cases (unresolved financial "
        f"holds below {limit})."
        if isinstance(balances, int)
        else "No small-balance cases to review; the M3 value is not available."
    )
    actions: list[dict[str, Any]] = [
        {
            "office": "Student Success",
            "text": advising_text,
            "finding_ids": ["M4"],
        },
        {
            "office": "Financial Aid",
            "text": balances_text,
            "finding_ids": ["M3"],
        },
    ]
    rows = findings_obj["M5"].get("value")
    if isinstance(rows, list):
        for row in rows:
            count = row["count"]
            actions.append(
                {
                    "office": row["office"],
                    "text": (
                        f"Resolve the {count} unresolved "
                        f"hold{'s' if count != 1 else ''} recorded for that "
                        "office."
                    ),
                    "finding_ids": ["M5"],
                }
            )
    return actions


# --- Q2: unresolved holds affecting continued enrollment ------------------------

UNRESOLVED_HOLDS_DECISION_ID = "D-unresolved-holds-1"


def _m3_threshold_text(findings_obj: dict[str, Any]) -> str:
    """The M3 balance threshold as prose ("$1,000"), read from M3's
    comparison data so the wording can never drift from the contract."""
    comparison = findings_obj["M3"].get("comparison")
    threshold = (
        comparison.get("threshold_usd") if isinstance(comparison, dict) else None
    )
    if isinstance(threshold, (int, float)):
        return f"${threshold:,.0f}"
    return f"${M3_AMOUNT_LIMIT:,.0f}"


def _q2_decisions(findings_obj: dict[str, Any]) -> list[dict[str, Any]]:
    """Q2's leadership decision: authorize a coordinated hold-resolution
    review led by the Bursar. The count comes from M5's table (unresolved
    hold records by office), the threshold text from M3's comparison, and
    approval authorizes the review only. Nothing is cleared and nothing is
    sent. When M5 has no table the prose says so — a bare ``--`` is never
    interpolated.
    """
    limit = _m3_threshold_text(findings_obj)
    rows = findings_obj["M5"].get("value")
    total = (
        sum(row["count"] for row in rows)
        if isinstance(rows, list) and rows
        else None
    )
    if total is not None:
        text = (
            "Decide whether to authorize a coordinated hold-resolution "
            f"review, led by the Bursar, for the {total} unresolved "
            "holds affecting continued enrollment, with priority for the "
            f"small-balance cases below the defined balance threshold of "
            f"{limit}. Approving authorizes the review only. No hold is "
            "cleared, and nothing is sent."
        )
        follow_up = (
            f"Lead the coordinated hold-resolution review for the {total} "
            "unresolved holds, working with each responsible office."
        )
    else:
        text = (
            "Decide whether to authorize a coordinated hold-resolution "
            "review, led by the Bursar, for the unresolved holds affecting "
            "continued enrollment, with priority for the small-balance "
            f"cases below the defined balance threshold of {limit}; the "
            "count of unresolved holds is not available in the current "
            "findings (M5). Approving authorizes the review only. No hold "
            "is cleared, and nothing is sent."
        )
        follow_up = (
            "Lead the coordinated hold-resolution review for the unresolved "
            "holds, working with each responsible office (the M5 count is "
            "not available)."
        )
    return [
        {
            "id": UNRESOLVED_HOLDS_DECISION_ID,
            "title": "Coordinated hold-resolution review led by the Bursar",
            "text": text,
            "follow_up": {"office": "Bursar", "description": follow_up},
        }
    ]


def _q2_actions(findings_obj: dict[str, Any]) -> list[dict[str, Any]]:
    """Q2's operational actions: one per office from M5's rows, plus
    Financial Aid for the M3 small-balance count. Built in code from the
    findings, never model text; the leadership decision lives separately in
    section 6 (``_q2_decisions``).
    """
    limit = _m3_threshold_text(findings_obj)
    actions: list[dict[str, Any]] = []
    rows = findings_obj["M5"].get("value")
    if isinstance(rows, list):
        for row in rows:
            count = row["count"]
            actions.append(
                {
                    "office": row["office"],
                    "text": (
                        f"Resolve the {count} unresolved "
                        f"hold{'s' if count != 1 else ''} recorded for that "
                        "office."
                    ),
                    "finding_ids": ["M5"],
                }
            )
    balances = findings_obj["M3"].get("value")
    balances_text = (
        f"Review the {balances} small-balance cases (unresolved financial "
        f"holds below {limit})."
        if isinstance(balances, int)
        else "No small-balance cases to review; the M3 value is not available."
    )
    actions.append(
        {
            "office": "Financial Aid",
            "text": balances_text,
            "finding_ids": ["M3"],
        }
    )
    return actions


# --- the registry ---------------------------------------------------------------

SPRING_REGISTRATION = Question(
    id="spring-registration",
    text="What should I know about spring registration?",
    dispatch={
        "enrollment_analyst": ROLE_FINDINGS["enrollment_analyst"],
        "student_success_analyst": ROLE_FINDINGS["student_success_analyst"],
        "chief_of_staff": ROLE_FINDINGS["chief_of_staff"],
    },
    chief_brief="The president asked: What should I know about spring registration?",
    build_decisions=_q1_decisions,
    build_actions=_q1_actions,
)

UNRESOLVED_HOLDS = Question(
    id="unresolved-holds",
    text="Where are unresolved holds affecting continued enrollment?",
    dispatch={
        "enrollment_analyst": ("M2",),
        "student_success_analyst": ("M3", "M5"),
        "chief_of_staff": ("M2", "M3", "M5", "M6"),
    },
    chief_brief=(
        "The president asked: Where are unresolved holds affecting "
        "continued enrollment?"
    ),
    build_decisions=_q2_decisions,
    build_actions=_q2_actions,
)


def _check_dispatch(question: Question) -> Question:
    """Every dispatched finding must be a subset of the role's standing
    findings — a question can narrow a role's lane, never widen it."""
    for role, finding_ids in question.dispatch.items():
        permitted = ROLE_FINDINGS.get(role)
        if permitted is None:
            raise ValueError(
                f"question {question.id!r} dispatches to unknown role {role!r}"
            )
        unknown = [f for f in finding_ids if f not in permitted]
        if unknown:
            raise ValueError(
                f"question {question.id!r} dispatches {unknown} to {role!r}, "
                f"outside its findings {list(permitted)}"
            )
    return question


QUESTIONS: tuple[Question, ...] = (
    _check_dispatch(SPRING_REGISTRATION),
    _check_dispatch(UNRESOLVED_HOLDS),
)

# The default question: what the standalone analyst briefing routes answer,
# what GET /decisions falls back to before anything is asked, and the only
# question whose payloads carry no QUESTION_KEY (keeping its hashes stable).
DEFAULT_QUESTION = SPRING_REGISTRATION

OUT_OF_SCOPE_REFUSAL = (
    "I'm sorry, but the Cabinet only answers the approved questions — "
    + " and ".join(f'"{question.text}"' for question in QUESTIONS)
    + " — and it never discloses counseling or spiritual-care information."
)


def _normalize(text: str) -> str:
    """Trim, case-insensitive, trailing '?' optional."""
    return text.strip().lower().rstrip("?").strip()


def match_question(text: str) -> Question | None:
    """The approved question ``text`` asks, or None (a refusal)."""
    normalized = _normalize(text)
    for question in QUESTIONS:
        if _normalize(question.text) == normalized:
            return question
    return None


def question_payload(question: Question) -> dict[str, str]:
    """How a non-default question rides in a role's received payload (and
    therefore in its replay hash)."""
    return {"id": question.id, "text": question.text, "brief": question.chief_brief}


def received_for(
    question: Question, role: str, findings_obj: dict[str, Any]
) -> dict[str, Any]:
    """The findings ``role`` receives for ``question``: the dispatch's
    findings, row IDs stripped like every role. A non-default question also
    carries itself under ``QUESTION_KEY`` — the one difference that keys its
    prompts and recordings apart from Q1's."""
    finding_ids = question.dispatch.get(role)
    if finding_ids is None:
        raise ValueError(f"question {question.id!r} gives role {role!r} no findings")
    received = findings_for_role(role, findings_obj, finding_ids)
    if question.id == DEFAULT_QUESTION.id:
        return received
    return {**received, QUESTION_KEY: question_payload(question)}
