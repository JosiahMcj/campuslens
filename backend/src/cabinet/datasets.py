"""Dataset upload validation: a stricter gate than the fixture loader.

``POST /admin/datasets`` accepts a JSON body in the SCHEMA.md shape (up to
20 MB; the route is exempted from the default body cap in
``cabinet.security``). ``validate_upload`` runs the fixture loader's type
checks (``cabinet.fixture.parse_fixture``) plus the upload-only rules:

- **no field outside the schema** — every key at every level must be one
  SCHEMA.md names (extra keys are rejected, not ignored, so a real export's
  surprises surface at upload time instead of silently dropping data);
- **no obvious PII columns** — a key named ``email``, ``phone``, ``ssn``,
  ``dob``, or ``name`` anywhere inside a student record is rejected with a
  message naming the field (pseudonymous records only);
- **pseudonymous student ids** — ``profile.student_id`` must be a
  ``STU-``/``PRI-``-style id or another opaque token (no spaces, no ``@``);
- **counseling fields** — allowed (the fixture carries them on purpose) but
  flagged in the report as "present, will always be refused"; no metric,
  finding, or analyst ever reads them (ROADMAP §5, §9);
- **row counts** — reported for both student lists.

All problems are collected and reported together; nothing is stored until
the document is clean.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from cabinet.fixture import Fixture, FixtureError, parse_fixture
from cabinet.store import PURGE_AFTER_DAYS

MAX_UPLOAD_BYTES = 20 * 1024 * 1024

ALLOWED_TOP_LEVEL = frozenset({"meta", "terms", "students", "prior_year_students"})
ALLOWED_META = frozenset(
    {
        "title",
        "description",
        "fictional",
        "seed",
        "generator",
        "timezone",
        "date_format",
        "planted_metrics",
    }
)
ALLOWED_TERM = frozenset(
    {
        "term",
        "name",
        "start_date",
        "registration_open_date",
        "registration_close_date",
        "prior_year_equivalent_date",
        "end_date",
        "as_of_rule",
        "timezone",
    }
)
ALLOWED_STUDENT = frozenset(
    {"profile", "enrollment", "holds", "advising", "comparison", "counseling"}
)
ALLOWED_PROFILE = frozenset({"student_id", "program", "class_level", "continuing"})
ALLOWED_ENROLLMENT = frozenset(
    {"term", "registration_status", "registered_credit_hours", "registration_date"}
)
ALLOWED_HOLD = frozenset(
    {"category", "amount", "responsible_office", "hold_date", "resolved"}
)
ALLOWED_ADVISING = frozenset(
    {"advisor_id", "last_appointment_date", "appointment_status"}
)
ALLOWED_COMPARISON = frozenset(
    {"prior_year_equivalent_date", "prior_term_status", "baseline"}
)
ALLOWED_COUNSELING = frozenset({"counseling_notes", "chaplain_contact"})
_GROUP_KEYS = {
    "profile": ALLOWED_PROFILE,
    "enrollment": ALLOWED_ENROLLMENT,
    "advising": ALLOWED_ADVISING,
    "comparison": ALLOWED_COMPARISON,
    "counseling": ALLOWED_COUNSELING,
}

# Keys that mark an obvious PII column anywhere inside a student record.
# Rejected, never flagged-and-stored: the schema has no place for them.
PII_KEYS = frozenset({"email", "phone", "ssn", "dob", "name"})

# STU-/PRI-style pseudonymous ids, or any opaque token: no whitespace, no
# '@', so a name or an email address cannot pass as an id.
STRUCTURED_ID_RE = re.compile(r"^[A-Z]{2,6}-\d{3,}$")
OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,63}$")

COUNSELING_FLAG = "present, will always be refused"


class UploadError(ValueError):
    """The uploaded document failed validation; ``errors`` lists every
    problem found, each naming its JSON path."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class UploadReport:
    """The result of a successful validation. ``document`` is the parsed
    upload, so callers never decode the body a second time."""

    fixture: Fixture
    document: dict[str, Any]
    row_counts: dict[str, int]
    counseling_present: bool
    fictional: bool

    @property
    def counseling_note(self) -> str:
        return COUNSELING_FLAG if self.counseling_present else "absent"


def _check_allowed(
    obj: Any, allowed: frozenset[str], path: str, errors: list[str]
) -> None:
    if not isinstance(obj, dict):
        return  # type errors are the fixture loader's job
    for key in obj:
        if key not in allowed:
            errors.append(f"{path}: field {key!r} is not in the SCHEMA.md shape")


def _scan_pii(value: Any, path: str, errors: list[str]) -> None:
    """Reject obvious PII column names at any depth inside a student record."""
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in PII_KEYS:
                errors.append(
                    f"{path}.{key}: looks like a PII column "
                    f"({str(key).lower()}); student records must be "
                    "pseudonymous — remove it before uploading"
                )
            else:
                _scan_pii(item, f"{path}.{key}", errors)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _scan_pii(item, f"{path}[{index}]", errors)


def _check_student_record(
    record: Any, path: str, errors: list[str]
) -> bool:
    """Upload-only checks for one student record.

    Returns True when the record carries counseling content — counseling
    fields are allowed but reported, because they exist only to be refused.
    """
    if not isinstance(record, dict):
        return False
    _check_allowed(record, ALLOWED_STUDENT, path, errors)
    _scan_pii(record, path, errors)
    counseling_present = False
    counseling = record.get("counseling")
    if isinstance(counseling, dict):
        _check_allowed(counseling, ALLOWED_COUNSELING, f"{path}.counseling", errors)
        if counseling.get("counseling_notes") is not None or counseling.get(
            "chaplain_contact"
        ):
            counseling_present = True
    profile = record.get("profile")
    if isinstance(profile, dict):
        _check_allowed(profile, ALLOWED_PROFILE, f"{path}.profile", errors)
        student_id = profile.get("student_id")
        if isinstance(student_id, str) and not (
            STRUCTURED_ID_RE.fullmatch(student_id)
            or OPAQUE_ID_RE.fullmatch(student_id)
        ):
            errors.append(
                f"{path}.profile.student_id: {student_id!r} is not a "
                "pseudonymous id (expected a STU-/PRI- style id or another "
                "opaque token without spaces)"
            )
    for group, allowed in _GROUP_KEYS.items():
        value = record.get(group)
        if group == "profile" or not isinstance(value, dict):
            continue
        _check_allowed(value, allowed, f"{path}.{group}", errors)
    holds = record.get("holds")
    if isinstance(holds, list):
        for index, hold in enumerate(holds):
            if isinstance(hold, dict):
                _check_allowed(hold, ALLOWED_HOLD, f"{path}.holds[{index}]", errors)
    return counseling_present


def validate_upload(raw: bytes) -> UploadReport:
    """Validate an uploaded dataset document; raise :class:`UploadError`
    listing every problem when it is not clean."""
    errors: list[str] = []
    try:
        document: Any = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UploadError([f"$: not a JSON document ({exc})"]) from None
    if not isinstance(document, dict):
        raise UploadError(["$: expected a JSON object in the SCHEMA.md shape"])

    _check_allowed(document, ALLOWED_TOP_LEVEL, "$", errors)
    meta = document.get("meta")
    if isinstance(meta, dict):
        _check_allowed(meta, ALLOWED_META, "$.meta", errors)
    terms = document.get("terms")
    if isinstance(terms, dict):
        allowed_terms = {"current", "prior_year", "in_session"}
        for key in terms:
            if key not in allowed_terms:
                errors.append(f"$.terms: term object {key!r} is not in the schema")
        for key, term_obj in terms.items():
            if isinstance(term_obj, dict):
                _check_allowed(term_obj, ALLOWED_TERM, f"$.terms.{key}", errors)

    counseling_present = False
    for list_key in ("students", "prior_year_students"):
        records = document.get(list_key)
        if not isinstance(records, list):
            continue
        for index, record in enumerate(records):
            present = _check_student_record(
                record, f"$.{list_key}[{index}]", errors
            )
            counseling_present = counseling_present or present

    fixture: Fixture | None = None
    if not errors:
        try:
            fixture = parse_fixture(document)
        except FixtureError as exc:
            errors.append(str(exc))
    if errors or fixture is None:
        raise UploadError(errors)

    fictional = False
    if isinstance(meta, dict):
        fictional = meta.get("fictional") is True
    return UploadReport(
        fixture=fixture,
        document=document,
        row_counts={
            "students": len(fixture.students),
            "prior_year_students": len(fixture.prior_year_students),
        },
        counseling_present=counseling_present,
        fictional=fictional,
    )


def main(argv: list[str] | None = None) -> int:
    """Dataset housekeeping CLI.

    ``python -m cabinet.datasets purge-deleted [--days N]`` hard-deletes
    datasets soft-deleted more than N days ago (default
    ``PURGE_AFTER_DAYS`` = 30): the row and the dataset file. This is the
    retention rule's enforcement point — see RUNBOOK.md.
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(prog="python -m cabinet.datasets")
    sub = parser.add_subparsers(dest="command", required=True)
    purge = sub.add_parser(
        "purge-deleted", help="hard-delete datasets past the retention window"
    )
    purge.add_argument("--days", type=int, default=PURGE_AFTER_DAYS)
    args = parser.parse_args(argv)

    from cabinet.auth import db_path_from_env
    from cabinet.provider import load_local_env
    from cabinet.store import CabinetStore

    load_local_env()
    try:
        db_path = db_path_from_env()
    except RuntimeError as exc:
        # A set-but-empty CABINET_DB: one line, never a traceback.
        print(f"datasets: {exc}", file=sys.stderr)
        return 1
    store = CabinetStore(db_path)
    if args.command == "purge-deleted":
        purged = store.purge_deleted_datasets(older_than_days=args.days)
        for row in purged:
            print(
                f"purged dataset {row['id']} ({row['name']!r}, institution "
                f"{row['institution_id']}, deleted at {row['deleted_at']})"
            )
        if not purged:
            print(f"nothing to purge (retention window: {args.days} days)")
        return 0
    print(parser.format_usage(), file=sys.stderr)  # pragma: no cover
    return 2  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
