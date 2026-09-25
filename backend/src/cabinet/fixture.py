"""Load and validate data/fixture.json into typed records (CONTRACTS.md §0, VERIFY.md).

The as-of date is derived, never read from the wall clock: it is the latest
``enrollment.registration_date`` over ``students`` (``terms.current.as_of_rule``).

The ``comparison`` and ``counseling`` groups of a fixture record are deliberately
not modeled: no metric reads them (``comparison.baseline`` only names the
baseline term; ``counseling.*`` exists only to be refused, ROADMAP §5).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, NoReturn


class FixtureError(ValueError):
    """Raised when the fixture does not match the expected shape."""


@dataclass(frozen=True)
class Profile:
    student_id: str
    program: str
    class_level: str
    continuing: bool


@dataclass(frozen=True)
class Enrollment:
    term: str
    registration_status: str
    registered_credit_hours: int
    registration_date: date | None


@dataclass(frozen=True)
class Hold:
    category: str
    amount: Decimal
    responsible_office: str
    hold_date: date
    resolved: bool


@dataclass(frozen=True)
class Advising:
    advisor_id: str
    last_appointment_date: date | None
    appointment_status: str


@dataclass(frozen=True)
class StudentRecord:
    profile: Profile
    enrollment: Enrollment
    holds: tuple[Hold, ...]
    advising: Advising


@dataclass(frozen=True)
class CurrentTerm:
    term: str
    name: str
    start_date: date
    registration_open_date: date
    registration_close_date: date | None


@dataclass(frozen=True)
class PriorYearTerm:
    term: str
    name: str
    start_date: date
    registration_open_date: date
    registration_close_date: date | None
    prior_year_equivalent_date: date


@dataclass(frozen=True)
class InSessionTerm:
    term: str
    name: str
    start_date: date | None
    end_date: date


@dataclass(frozen=True)
class Terms:
    current: CurrentTerm
    prior_year: PriorYearTerm
    in_session: InSessionTerm


@dataclass(frozen=True)
class Fixture:
    students: tuple[StudentRecord, ...]
    prior_year_students: tuple[StudentRecord, ...]
    terms: Terms
    as_of: date | None


def _fail(path: str, message: str) -> NoReturn:
    raise FixtureError(f"{path}: {message}")


def _mapping(value: object, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(path, f"expected an object, got {type(value).__name__}")
    return value


def _key(obj: dict[str, Any], key: str, path: str) -> Any:
    if key not in obj:
        _fail(path, f"missing key {key!r}")
    return obj[key]


def _str(obj: dict[str, Any], key: str, path: str) -> str:
    value = _key(obj, key, path)
    if not isinstance(value, str):
        _fail(f"{path}.{key}", f"expected a string, got {type(value).__name__}")
    return value


def _bool(obj: dict[str, Any], key: str, path: str) -> bool:
    value = _key(obj, key, path)
    if not isinstance(value, bool):
        _fail(f"{path}.{key}", f"expected a boolean, got {type(value).__name__}")
    return value


def _int(obj: dict[str, Any], key: str, path: str) -> int:
    value = _key(obj, key, path)
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{path}.{key}", f"expected an integer, got {type(value).__name__}")
    return value


def _decimal(obj: dict[str, Any], key: str, path: str) -> Decimal:
    value = _key(obj, key, path)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"{path}.{key}", f"expected a number, got {type(value).__name__}")
    return Decimal(str(value))


def _iso_date(value: str, path: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        _fail(path, f"expected an ISO YYYY-MM-DD date, got {value!r}")


def _date(obj: dict[str, Any], key: str, path: str) -> date:
    return _iso_date(_str(obj, key, path), f"{path}.{key}")


def _opt_date(obj: dict[str, Any], key: str, path: str) -> date | None:
    if key not in obj or obj[key] is None:
        return None
    value = obj[key]
    if not isinstance(value, str):
        _fail(f"{path}.{key}", f"expected a string or null, got {type(value).__name__}")
    return _iso_date(value, f"{path}.{key}")


def _hold(value: object, path: str) -> Hold:
    obj = _mapping(value, path)
    return Hold(
        category=_str(obj, "category", path),
        amount=_decimal(obj, "amount", path),
        responsible_office=_str(obj, "responsible_office", path),
        hold_date=_date(obj, "hold_date", path),
        resolved=_bool(obj, "resolved", path),
    )


def _student(value: object, path: str) -> StudentRecord:
    obj = _mapping(value, path)
    profile = _mapping(_key(obj, "profile", path), f"{path}.profile")
    enrollment = _mapping(_key(obj, "enrollment", path), f"{path}.enrollment")
    advising = _mapping(_key(obj, "advising", path), f"{path}.advising")
    holds_raw = _key(obj, "holds", path)
    if not isinstance(holds_raw, list):
        _fail(f"{path}.holds", f"expected a list, got {type(holds_raw).__name__}")
    return StudentRecord(
        profile=Profile(
            student_id=_str(profile, "student_id", f"{path}.profile"),
            program=_str(profile, "program", f"{path}.profile"),
            class_level=_str(profile, "class_level", f"{path}.profile"),
            continuing=_bool(profile, "continuing", f"{path}.profile"),
        ),
        enrollment=Enrollment(
            term=_str(enrollment, "term", f"{path}.enrollment"),
            registration_status=_str(
                enrollment, "registration_status", f"{path}.enrollment"
            ),
            registered_credit_hours=_int(
                enrollment, "registered_credit_hours", f"{path}.enrollment"
            ),
            registration_date=_opt_date(
                enrollment, "registration_date", f"{path}.enrollment"
            ),
        ),
        holds=tuple(_hold(h, f"{path}.holds[{i}]") for i, h in enumerate(holds_raw)),
        advising=Advising(
            advisor_id=_str(advising, "advisor_id", f"{path}.advising"),
            last_appointment_date=_opt_date(
                advising, "last_appointment_date", f"{path}.advising"
            ),
            appointment_status=_str(advising, "appointment_status", f"{path}.advising"),
        ),
    )


def _student_list(value: object, path: str) -> tuple[StudentRecord, ...]:
    if not isinstance(value, list):
        _fail(path, f"expected a list, got {type(value).__name__}")
    return tuple(_student(s, f"{path}[{i}]") for i, s in enumerate(value))


def _terms(value: object, path: str) -> Terms:
    obj = _mapping(value, path)
    current = _mapping(_key(obj, "current", path), f"{path}.current")
    prior_year = _mapping(_key(obj, "prior_year", path), f"{path}.prior_year")
    in_session = _mapping(_key(obj, "in_session", path), f"{path}.in_session")
    return Terms(
        current=CurrentTerm(
            term=_str(current, "term", f"{path}.current"),
            name=_str(current, "name", f"{path}.current"),
            start_date=_date(current, "start_date", f"{path}.current"),
            registration_open_date=_date(
                current, "registration_open_date", f"{path}.current"
            ),
            registration_close_date=_opt_date(
                current, "registration_close_date", f"{path}.current"
            ),
        ),
        prior_year=PriorYearTerm(
            term=_str(prior_year, "term", f"{path}.prior_year"),
            name=_str(prior_year, "name", f"{path}.prior_year"),
            start_date=_date(prior_year, "start_date", f"{path}.prior_year"),
            registration_open_date=_date(
                prior_year, "registration_open_date", f"{path}.prior_year"
            ),
            registration_close_date=_opt_date(
                prior_year, "registration_close_date", f"{path}.prior_year"
            ),
            prior_year_equivalent_date=_date(
                prior_year, "prior_year_equivalent_date", f"{path}.prior_year"
            ),
        ),
        in_session=InSessionTerm(
            term=_str(in_session, "term", f"{path}.in_session"),
            name=_str(in_session, "name", f"{path}.in_session"),
            start_date=_opt_date(in_session, "start_date", f"{path}.in_session"),
            end_date=_date(in_session, "end_date", f"{path}.in_session"),
        ),
    )


def parse_fixture(data: object) -> Fixture:
    """Validate a decoded fixture document and derive the as-of date."""
    root = _mapping(data, "$")
    terms = _terms(_key(root, "terms", "$"), "$.terms")
    students = _student_list(_key(root, "students", "$"), "$.students")
    prior_year_students = _student_list(
        _key(root, "prior_year_students", "$"), "$.prior_year_students"
    )
    registration_dates = [
        s.enrollment.registration_date
        for s in students
        if s.enrollment.registration_date is not None
    ]
    as_of = max(registration_dates) if registration_dates else None
    return Fixture(
        students=students,
        prior_year_students=prior_year_students,
        terms=terms,
        as_of=as_of,
    )


def load_fixture(path: str | Path) -> Fixture:
    """Read fixture.json from disk and validate it."""
    fixture_path = Path(path)
    return parse_fixture(json.loads(fixture_path.read_text(encoding="utf-8")))
