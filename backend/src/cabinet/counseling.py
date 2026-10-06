"""M9, the one counseling figure, and only in aggregate (CONTRACTS.md M9).

Per-student counseling and spiritual-care data stays refused to every role
and every AI employee (``permissions``: the ``counseling`` group is granted
to no role, and ``/governance/request`` refuses it whatever is recorded
here). What this module adds is narrower: when an institution's counseling
director has authorized it in writing, and an admin has recorded that
authorization on the institution row (migration 7), the findings gain ONE
figure, the count of students in M2 with any counseling contact this term.

The rules this module keeps:

- No authorization, no M9. The finding is absent, not zero and not
  "refused", so an institution that never opted in sees exactly the
  findings it saw before.
- A count, never rows. ``row_ids`` is always empty and there is no
  drill-down: the evidence drawer shows the authorization and the fields
  read, never a student.
- A minimum cell size. A count under ``MINIMUM_CELL_SIZE`` (zero included)
  is suppressed: ``value`` is ``None``, the display says "fewer than 10",
  and the model receives a marker with no number in it.
- The typed record stays blind. ``fixture.StudentRecord`` deliberately does
  not model the counseling group, so ``metrics.findings`` can never read it
  by accident. M9 reads the raw dataset document here instead, in the one
  place that is allowed to, and only when the authorization is on.
"""

from __future__ import annotations

from typing import Any

M9_ID = "M9"

# Below this many students the count is withheld: in a small group a count
# can identify a person, and counseling contact is the most sensitive fact
# the dataset carries.
MINIMUM_CELL_SIZE = 10

M9_TITLE = "Students in M2 with any counseling contact this term"

# What the briefing and the evidence drawer show in place of a small count.
SUPPRESSED_DISPLAY = f"fewer than {MINIMUM_CELL_SIZE}"

# What the model receives in place of a small count: words only, so the
# withheld number (and the threshold, which brackets it) never reaches a
# prompt.
SUPPRESSED_MODEL_DISPLAY = "withheld because the group is below the minimum size"

# Every field M9 reads, named in the finding so the drawer and the audit
# event can say exactly what code looked at.
M9_SOURCE_FIELDS: tuple[str, ...] = (
    "profile.continuing",
    "enrollment.registration_status",
    "counseling.counseling_notes",
    "counseling.chaplain_contact",
)

M9_DEFINITION = (
    "count(row ∈ M2 ∧ (counseling.counseling_notes is not null "
    "∨ counseling.chaplain_contact = true)), shown only when the count is "
    f"at least {MINIMUM_CELL_SIZE}"
)

# The keys of the authorization block a finding carries (and the admin
# route returns): who authorized it, the document, and who recorded it when.
AUTHORIZATION_KEYS: tuple[str, ...] = (
    "authorized_by",
    "document_reference",
    "recorded_by",
    "recorded_at",
)


def _has_counseling_contact(record: dict[str, Any]) -> bool:
    """True when the record shows any counseling or chaplaincy contact.

    A record without the group (a real upload may omit it) has no contact.
    Notes count only when they carry text, so an empty string from an
    export is not mistaken for a visit.
    """
    counseling = record.get("counseling")
    if not isinstance(counseling, dict):
        return False
    notes = counseling.get("counseling_notes")
    if isinstance(notes, str) and notes.strip() != "":
        return True
    return counseling.get("chaplain_contact") is True


def m9_count(document: dict[str, Any], m2_row_ids: list[str]) -> int:
    """How many M2 students have any counseling contact this term.

    The population is M2's own row list (computed by ``metrics``), so M9 can
    never describe a different group than the one M2 counts. Each id is
    matched to its raw record by ``profile.student_id``.
    """
    in_m2 = set(m2_row_ids)
    students = document.get("students")
    if not isinstance(students, list):
        return 0
    count = 0
    for record in students:
        if not isinstance(record, dict):
            continue
        profile = record.get("profile")
        student_id = profile.get("student_id") if isinstance(profile, dict) else None
        if student_id in in_m2 and _has_counseling_contact(record):
            count += 1
    return count


def authorization_block(authorization: dict[str, Any]) -> dict[str, Any]:
    """The authorization reference a finding and its audit event carry: the
    typed name and document reference, and who recorded it when."""
    return {key: authorization.get(key) for key in AUTHORIZATION_KEYS}


def m9_finding(
    document: dict[str, Any],
    m2: dict[str, Any],
    authorization: dict[str, Any],
) -> dict[str, Any] | None:
    """The M9 finding, or ``None`` when the institution has not authorized it.

    ``m2`` is the computed M2 finding. When M2 has no value (an empty
    dataset) M9 has none either, and says why, exactly like M3 and M4.
    """
    if authorization.get("authorized") is not True:
        return None
    m2_value = m2.get("value")
    m2_rows = m2.get("row_ids")
    value: int | None
    reason: str | None
    suppressed = False
    if not isinstance(m2_value, int) or not isinstance(m2_rows, list):
        value, reason, display = None, "the M2 population is empty", "--"
    else:
        count = m9_count(document, [str(row) for row in m2_rows])
        if count < MINIMUM_CELL_SIZE:
            suppressed = True
            value = None
            display = SUPPRESSED_DISPLAY
            reason = (
                "the count is withheld below the minimum group size of "
                f"{MINIMUM_CELL_SIZE} so that no student can be identified"
            )
        else:
            value, reason, display = count, None, str(count)
    return {
        "id": M9_ID,
        "title": M9_TITLE,
        "value": value,
        "display": display,
        "reason": reason,
        "suppressed": suppressed,
        "minimum_cell_size": MINIMUM_CELL_SIZE,
        "aggregate_only": True,
        "comparison": None,
        "source_fields": list(M9_SOURCE_FIELDS),
        # Always empty: M9 has no drill-down, in the API or the drawer.
        "row_ids": [],
        "definition": M9_DEFINITION,
        "authorization": authorization_block(authorization),
    }


def m9_model_view(finding: dict[str, Any]) -> dict[str, Any]:
    """What the Chief of Staff receives for M9: the count, or the suppressed
    marker, and nothing else.

    No ``source_fields``: the Chief of Staff's aggregate grant is derived
    from source fields, and the counseling fields are refused to every role,
    so naming them here would turn an authorized aggregate into a refusal.
    The fields read are recorded instead in M9's own ``data.granted`` event.
    No authorization block, definition, or minimum size either: none of it
    helps the model explain, and the definition names the threshold.
    """
    suppressed = finding.get("suppressed") is True
    value = finding.get("value")
    if suppressed or not isinstance(value, int):
        return {
            "id": M9_ID,
            "title": M9_TITLE,
            "value": None,
            "display": (
                SUPPRESSED_MODEL_DISPLAY if suppressed else "not available"
            ),
            "suppressed": suppressed,
        }
    return {
        "id": M9_ID,
        "title": M9_TITLE,
        "value": value,
        "display": str(value),
        "suppressed": False,
    }
