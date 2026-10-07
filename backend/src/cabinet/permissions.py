"""ROADMAP §5 data boundary: per-role field permissions and the request gate.

Fields are granted by role. A request outside the role is refused *before any
model call* and logged as ``data.refused``; a granted request is logged as
``data.granted``. Access levels:

- ``read`` — the role may receive the field's values.
- ``status_only`` — the role may receive the field's status, not its detail
  (granted by the gate, flagged so downstream code can limit what it sends).
- ``aggregate_only`` — the role receives aggregates only; raw field requests
  are refused.
- ``refused`` — never granted. ``counseling.*`` is refused to every role: it
  exists in the fixture only so the refusal is real.

The counseling fields stay refused even when an institution has recorded a
counseling aggregate authorization (``cabinet.counseling``). That
authorization never grants a field. It lets code compute one count (M9),
which the Chief of Staff alone may receive as a number or a suppressed
marker, with its own ``data.granted`` event marked ``aggregate_only``.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from cabinet.audit import AuditSink
from cabinet.counseling import MINIMUM_CELL_SIZE, m9_model_view

READ = "read"
STATUS_ONLY = "status_only"
AGGREGATE_ONLY = "aggregate_only"
REFUSED = "refused"

ACCESS_LEVELS = (READ, STATUS_ONLY, AGGREGATE_ONLY, REFUSED)
GRANTABLE = (READ, STATUS_ONLY)

ROLES = ("chief_of_staff", "enrollment_analyst", "student_success_analyst")

# Field groups per SCHEMA.md. ROADMAP §4/§5 writes `hold.x`; the fixture group
# is the list `holds`, so `hold.x` is normalized to `holds.x`.
_FIELD_GROUPS: dict[str, tuple[str, ...]] = {
    "profile": ("student_id", "program", "class_level", "continuing"),
    "enrollment": (
        "term",
        "registration_status",
        "registered_credit_hours",
        "registration_date",
    ),
    "holds": ("category", "amount", "responsible_office", "hold_date", "resolved"),
    "advising": ("advisor_id", "last_appointment_date", "appointment_status"),
    "comparison": ("prior_year_equivalent_date", "prior_term_status", "baseline"),
    "counseling": ("counseling_notes", "chaplain_contact"),
}

# The ROADMAP §5 table as data: group-level access per role.
_GROUP_ACCESS: dict[str, dict[str, str]] = {
    "chief_of_staff": {
        "profile": AGGREGATE_ONLY,
        "enrollment": AGGREGATE_ONLY,
        "holds": AGGREGATE_ONLY,
        "advising": AGGREGATE_ONLY,
        "comparison": AGGREGATE_ONLY,
        "counseling": REFUSED,
    },
    "enrollment_analyst": {
        "profile": READ,
        "enrollment": READ,
        "holds": REFUSED,
        "advising": REFUSED,
        "comparison": READ,
        "counseling": REFUSED,
    },
    "student_success_analyst": {
        "profile": READ,
        "enrollment": STATUS_ONLY,
        "holds": READ,
        "advising": READ,
        "comparison": READ,
        "counseling": REFUSED,
    },
}

# Field-level refinements on top of the group access: ROADMAP §5 grants the
# Student Success Analyst enrollment "read (status only)" — made literal here
# as the registration status and the term, nothing else: credit hours and the
# registration date are refused.
_FIELD_OVERRIDES: dict[tuple[str, str], str] = {
    ("student_success_analyst", "enrollment.term"): READ,
    ("student_success_analyst", "enrollment.registration_status"): READ,
    ("student_success_analyst", "enrollment.registered_credit_hours"): REFUSED,
    ("student_success_analyst", "enrollment.registration_date"): REFUSED,
}


def _build_table() -> dict[str, dict[str, str]]:
    table: dict[str, dict[str, str]] = {}
    for role, groups in _GROUP_ACCESS.items():
        fields: dict[str, str] = {}
        for group, access in groups.items():
            for name in _FIELD_GROUPS[group]:
                path = f"{group}.{name}"
                fields[path] = _FIELD_OVERRIDES.get((role, path), access)
        table[role] = fields
    return table


# The full §5 permission table: ROLE_PERMISSIONS[role][field_path] -> access.
ROLE_PERMISSIONS: dict[str, dict[str, str]] = _build_table()

ALL_FIELDS: tuple[str, ...] = tuple(sorted(ROLE_PERMISSIONS["chief_of_staff"]))

# Which findings each role may receive (ROADMAP §3 layer 4). Every role
# receives aggregates only: findings_for_role strips all row-ID lists (and
# M8's per-student rule map).
ROLE_FINDINGS: dict[str, tuple[str, ...]] = {
    "enrollment_analyst": ("M1", "M2", "M7"),
    "student_success_analyst": ("M3", "M4", "M5", "M8"),
    "chief_of_staff": ("M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8"),
}

# Findings a role may receive only when the institution has recorded the
# authorization that makes them exist (M9, the counseling aggregate). They
# sit outside ROLE_FINDINGS on purpose: a question's standing dispatch is
# ROLE_FINDINGS itself, and an absent M9 must never be dispatched. No
# analyst appears here, so findings_for_role refuses M9 to every analyst.
AUTHORIZED_AGGREGATE_FINDINGS: dict[str, tuple[str, ...]] = {
    "chief_of_staff": ("M9",),
}

# The fields each analyst's task requests when the Chief of Staff dispatches
# it (the source fields behind that role's findings, minus term-level anchors,
# which are not student fields).
ROLE_TASK_FIELDS: dict[str, tuple[str, ...]] = {
    "enrollment_analyst": (
        "profile.continuing",
        "enrollment.term",
        "enrollment.registration_status",
        "enrollment.registered_credit_hours",
        "enrollment.registration_date",
        "comparison.prior_year_equivalent_date",
        "comparison.baseline",
    ),
    "student_success_analyst": (
        "profile.continuing",
        "enrollment.registration_status",
        "holds.category",
        "holds.amount",
        "holds.responsible_office",
        "holds.hold_date",
        "holds.resolved",
        "advising.advisor_id",
        "advising.last_appointment_date",
        "advising.appointment_status",
        "comparison.prior_year_equivalent_date",
    ),
}


class FieldRequestRefused(PermissionError):
    """Raised by the gate when any requested field is outside the role."""

    def __init__(
        self,
        role: str,
        requested_fields: list[str],
        refused_fields: list[str],
        reason: str,
        event: dict[str, Any],
    ) -> None:
        super().__init__(reason)
        self.role = role
        self.requested_fields = requested_fields
        self.refused_fields = refused_fields
        self.reason = reason
        self.event = event


def normalize_field(field: str) -> str:
    """Trim and map the ROADMAP spelling `hold.x` to the fixture's `holds.x`."""
    path = field.strip()
    if path.startswith("hold."):
        path = "holds." + path[len("hold.") :]
    return path


def access_for(role: str, field: str) -> str:
    """The role's access level for one field; REFUSED for unknown role/field."""
    return ROLE_PERMISSIONS.get(role, {}).get(normalize_field(field), REFUSED)


def request_fields(
    role: str,
    fields: list[str] | tuple[str, ...],
    task_id: str,
    log: AuditSink,
) -> list[str]:
    """The field-request gate (ROADMAP §5).

    Grants every requested field (logging one ``data.granted`` event listing
    them) and returns the granted field paths, or refuses the *whole* request:
    logs one ``data.refused`` event with the role, the requested fields, the
    refused ones, and a one-sentence reason, then raises
    :class:`FieldRequestRefused` before anything downstream can run.
    """
    requested = [normalize_field(f) for f in fields]
    refused = [f for f in requested if access_for(role, f) not in GRANTABLE]
    if refused:
        reason = (
            f"Role '{role}' is not permitted to access "
            f"{', '.join(sorted(set(refused)))}; the request was refused "
            "before any model call."
        )
        event = log.append(
            "data.refused",
            actor=role,
            payload={
                "task_id": task_id,
                "requested_fields": requested,
                "refused_fields": sorted(set(refused)),
                "reason": reason,
            },
        )
        raise FieldRequestRefused(role, requested, sorted(set(refused)), reason, event)
    log.append(
        "data.granted",
        actor=role,
        payload={"task_id": task_id, "granted_fields": requested},
    )
    return requested


def grant_aggregates(
    role: str,
    fields: list[str] | tuple[str, ...],
    task_id: str,
    log: AuditSink,
    *,
    analyst_explanations: list[str] | tuple[str, ...] = (),
) -> list[str]:
    """The aggregate grant (Chief of Staff): the role receives aggregates only,
    never raw field values, so the raw-field gate does not apply. Logs one
    ``data.granted`` event at level ``aggregate`` listing the source fields
    behind the aggregates it received, plus the analyst roles whose validated
    explanations accompanied them. A field the role is flat-out REFUSED
    (counseling) can never appear here: no metric reads it, and if one ever
    did, this gate refuses the whole request before any model call.
    """
    requested = [normalize_field(f.replace("[]", "")) for f in fields]
    # Only known §5 fields can be refused; term-level anchors (terms.*) are
    # not student fields and sit outside the §5 table.
    refused = [
        f for f in requested if f in ALL_FIELDS and access_for(role, f) == REFUSED
    ]
    if refused:
        reason = (
            f"Role '{role}' is not permitted to access "
            f"{', '.join(sorted(set(refused)))}; the request was refused "
            "before any model call."
        )
        event = log.append(
            "data.refused",
            actor=role,
            payload={
                "task_id": task_id,
                "requested_fields": requested,
                "refused_fields": sorted(set(refused)),
                "reason": reason,
            },
        )
        raise FieldRequestRefused(role, requested, sorted(set(refused)), reason, event)
    log.append(
        "data.granted",
        actor=role,
        payload={
            "task_id": task_id,
            "granted_fields": requested,
            "level": "aggregate",
            "analyst_explanations": list(analyst_explanations),
        },
    )
    return requested


def grant_authorized_aggregate(
    role: str,
    finding_id: str,
    fields_read: list[str] | tuple[str, ...],
    authorization: dict[str, Any],
    task_id: str,
    log: AuditSink,
) -> None:
    """Record that ``role`` received an authorized aggregate (M9).

    A separate ``data.granted`` event from the role's aggregate grant, so a
    reader of the chain sees the counseling count apart from everything
    else: ``aggregate_only: true``, the fields code read to compute it (never
    their values, and never granted to the role), and the authorization
    reference it rests on. Only the roles listed in
    ``AUTHORIZED_AGGREGATE_FINDINGS`` for that finding may receive it.
    """
    if finding_id not in AUTHORIZED_AGGREGATE_FINDINGS.get(role, ()):
        raise ValueError(f"role {role!r} may not receive {finding_id!r}")
    log.append(
        "data.granted",
        actor=role,
        payload={
            "task_id": task_id,
            "finding_id": finding_id,
            "aggregate_only": True,
            "fields_read": list(fields_read),
            "authorization": dict(authorization),
        },
    )


def run_task_with_model(
    role: str,
    fields: list[str] | tuple[str, ...],
    task_id: str,
    log: AuditSink,
    model: Callable[[list[str]], str],
) -> str:
    """Gate-then-model: the model callable is only invoked after the gate
    grants the fields. On refusal the gate raises and ``model`` never runs."""
    granted = request_fields(role, fields, task_id, log)
    return model(granted)


def _strip_row_ids(value: Any) -> Any:
    """Recursively remove row-level detail: ``row_ids``, ``hold_row_ids``,
    and M8's per-student rule map ``row_rules``."""
    if isinstance(value, dict):
        return {
            key: _strip_row_ids(item)
            for key, item in value.items()
            if key not in ("row_ids", "hold_row_ids", "row_rules")
        }
    if isinstance(value, list):
        return [_strip_row_ids(item) for item in value]
    return value


def findings_for_role(
    role: str,
    findings_obj: dict[str, Any],
    finding_ids: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """The findings a role may receive (ROADMAP §3 layer 4).

    Enrollment Analyst: M1, M2, M7. Student Success Analyst: M3, M4, M5, M8.
    Chief of Staff: M1-M8, plus M9 when the institution has authorized it.
    ``finding_ids`` narrows the role's standing set for one approved
    question's dispatch (``questions.received_for``); it must be a subset of
    ``ROLE_FINDINGS[role]`` plus the role's ``AUTHORIZED_AGGREGATE_FINDINGS``
    — a question can narrow a role's lane, never widen it. M9 arrives as
    ``counseling.m9_model_view``: the count or the suppressed marker only.
    Every role receives aggregates only: all row-ID lists (and any other
    student-level lists) are stripped — row IDs reach the evidence drawer
    through ``GET /findings``, never through a model. Unknown roles raise
    ``ValueError``.
    """
    if role not in ROLE_FINDINGS:
        raise ValueError(f"unknown role: {role!r}")
    standing = ROLE_FINDINGS[role]
    permitted = (*standing, *AUTHORIZED_AGGREGATE_FINDINGS.get(role, ()))
    ids = standing if finding_ids is None else finding_ids
    outside = [f for f in ids if f not in permitted]
    if outside:
        raise ValueError(
            f"findings {outside} are outside role {role!r}'s permitted "
            f"{list(permitted)}"
        )
    out: dict[str, Any] = {}
    for finding_id in ids:
        if finding_id in AUTHORIZED_AGGREGATE_FINDINGS.get(role, ()):
            # The count or the suppressed marker only, never the fields read
            # or the authorization (see counseling.m9_model_view).
            out[finding_id] = m9_model_view(findings_obj[finding_id])
        else:
            out[finding_id] = _strip_row_ids(findings_obj[finding_id])
    return out


# --- what a signed-in person's GET /findings carries --------------------------


def _row_count(row_ids: Any) -> Any:
    """The size of a row-id list, in the same shape: an int for a list, and
    ``{"numerator": n, "denominator": m}`` for a ratio finding's pair."""
    if isinstance(row_ids, dict):
        return {
            key: len(value) if isinstance(value, list) else 0
            for key, value in row_ids.items()
        }
    return len(row_ids) if isinstance(row_ids, list) else 0


def _empty_like(row_ids: Any) -> Any:
    """An empty row-id value in the same shape (so the UI's shape checks hold)."""
    if isinstance(row_ids, dict):
        return {key: [] for key in row_ids}
    return []


def findings_without_rows(findings_obj: dict[str, Any]) -> dict[str, Any]:
    """The findings with every student id removed, for a role whose job does
    not need the records (staff, reviewer, aid).

    A new object (the cached findings are shared by every request and stay
    whole for the executive and admin). Each finding keeps its figures,
    definition, fields, and per-office and per-indicator counts; its
    ``row_ids`` (and M5's ``hold_row_ids``, M8's per-rule ``row_ids``) become
    empty lists of the same shape, M8's per-student ``row_rules`` map becomes
    empty, and two keys say what was withheld: ``rows_withheld: true`` and
    ``row_counts``, the number of records behind the figure in the shape
    ``row_ids`` had.
    """
    out: dict[str, Any] = {}
    for finding_id, finding in findings_obj.items():
        if not isinstance(finding, dict) or "row_ids" not in finding:
            out[finding_id] = copy.deepcopy(finding)
            continue
        view = copy.deepcopy(finding)
        view["row_counts"] = _row_count(finding["row_ids"])
        view["row_ids"] = _empty_like(finding["row_ids"])
        view["rows_withheld"] = True
        value = view.get("value")
        if isinstance(value, list):
            for entry in value:
                if isinstance(entry, dict) and "hold_row_ids" in entry:
                    entry["hold_row_ids"] = []
        rules = view.get("rules")
        if isinstance(rules, list):
            for rule in rules:
                if isinstance(rule, dict) and "row_ids" in rule:
                    rule["row_ids"] = []
        if "row_rules" in view:
            view["row_rules"] = {}
        out[finding_id] = view
    return out


# --- small operational counts in the live model prompt -------------------------

# What the live model reads in place of an office's or an indicator's count
# under MINIMUM_CELL_SIZE (zero included).
SMALL_COUNT_MODEL_TEXT = f"fewer than {MINIMUM_CELL_SIZE}"

# The findings whose per-office (M5) or per-indicator (M8) counts are
# operational work counts: shown exactly to the people who work them, sent to
# the live model only at MINIMUM_CELL_SIZE or above.
SMALL_COUNT_FINDINGS = ("M5", "M8")


def _small_count_entries(finding: dict[str, Any]) -> list[dict[str, Any]]:
    """M5's office rows or M8's indicator rows: the entries carrying a count."""
    entries: list[dict[str, Any]] = []
    for key in ("value", "rules"):
        rows = finding.get(key)
        if isinstance(rows, list):
            entries.extend(
                row for row in rows if isinstance(row, dict) and "count" in row
            )
    return entries


def has_small_count(finding: Any) -> bool:
    """True when an M5 office or an M8 indicator in ``finding`` counts fewer
    than MINIMUM_CELL_SIZE (the validator then accepts "fewer than 10")."""
    if not isinstance(finding, dict):
        return False
    return any(
        isinstance(entry["count"], int)
        and not isinstance(entry["count"], bool)
        and entry["count"] < MINIMUM_CELL_SIZE
        for entry in _small_count_entries(finding)
    )


def _coarsen(findings: dict[str, Any]) -> dict[str, Any]:
    out = dict(findings)
    for finding_id in SMALL_COUNT_FINDINGS:
        finding = out.get(finding_id)
        if not isinstance(finding, dict) or not has_small_count(finding):
            continue
        view = copy.deepcopy(finding)
        for entry in _small_count_entries(view):
            count = entry["count"]
            if isinstance(count, int) and count < MINIMUM_CELL_SIZE:
                entry["count"] = SMALL_COUNT_MODEL_TEXT
        out[finding_id] = view
    return out


def coarsen_small_counts(payload: dict[str, Any]) -> dict[str, Any]:
    """The payload a LIVE model call sends: every M5 office count and M8
    indicator count under MINIMUM_CELL_SIZE replaced by "fewer than 10".

    Applied only in ``ChatProvider`` just before the prompt is built, so the
    received findings (which key the replay recordings and which the
    validator checks against) are unchanged. Handles the analyst shape
    (``{"M5": ...}``) and the Chief of Staff's (``{"findings": {"M5": ...}}``).
    A new object; the input is never mutated.
    """
    out = _coarsen(payload)
    nested = payload.get("findings")
    if isinstance(nested, dict):
        out["findings"] = _coarsen(nested)
    return out
