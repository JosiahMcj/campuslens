"""The demonstration student directory: name search, roles, and the log."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from cabinet.api import create_app
from cabinet.audit import EVENT_TYPES
from cabinet.roster import ENV_ROSTER, search_roster, student_body
from conftest import make_authenticated_client

ROWS: list[dict[str, Any]] = [
    {
        "studentId": "DEM1001",
        "name": "Josiah McKenna",
        "program": "Computer Science",
        "degreeProgress": 0.62,
        "currentGPA": 3.71,
        "previousGPA": 3.64,
        "holds": [],
        "advisor": "Dr. Fitzgerald",
    },
    {
        "studentId": "DEM1002",
        "name": "Obiora Amato",
        "program": "Computer Science",
        "degreeProgress": 0.74,
        "currentGPA": 3.68,
        "previousGPA": 3.59,
        "holds": [],
        "advisor": "Dr. Fitzgerald",
    },
    {
        "studentId": "DEM1005",
        "name": "Amara Obi",
        "program": "Nursing",
        "degreeProgress": 0.4,
        "currentGPA": 2.31,
        "previousGPA": 3.12,
        "holds": ["Financial Balance"],
        "advisor": "Dr. Smith",
    },
]


@pytest.fixture
def roster(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "students.json"
    path.write_text(json.dumps(ROWS), encoding="utf-8")
    monkeypatch.setenv(ENV_ROSTER, str(path))
    return path


def test_search_finds_by_name_words_and_returns_the_record(roster: Path) -> None:
    app = create_app()
    executive = make_authenticated_client(app, role="executive")
    body = executive.get("/students/search", params={"q": "obi am"}).json()
    # "obi am" also fits Amara Obi; the name in the typed order comes first.
    assert body["total"] == 2
    assert body["fictional"] is True
    assert body["students"][1]["name"] == "Amara Obi"
    assert body["students"][:1] == [
        {
            "student_id": "DEM1002",
            "name": "Obiora Amato",
            "program": "Computer Science",
            "degree_progress": 0.74,
            "degree_progress_display": "74%",
            "current_gpa_display": "3.68",
            "previous_gpa_display": "3.59",
            "gpa_change_display": "+0.09",
            "holds": [],
            "advisor": "Dr. Fitzgerald",
        }
    ]
    # Either name word, any case; the typed order comes first.
    both = executive.get("/students/search", params={"q": "OBI"}).json()
    assert [row["name"] for row in both["students"]] == ["Obiora Amato", "Amara Obi"]
    # An exact student id matches too; a made-up name matches nobody.
    by_id = executive.get("/students/search", params={"q": "dem1005"}).json()
    assert [row["name"] for row in by_id["students"]] == ["Amara Obi"]
    assert by_id["students"][0]["gpa_change_display"] == "−0.81"
    assert executive.get("/students/search", params={"q": "zzz"}).json()["total"] == 0


def test_search_is_for_the_executive_and_admin_only(roster: Path) -> None:
    app = create_app()
    for role in ("staff", "reviewer", "aid"):
        client = make_authenticated_client(app, role=role)
        assert client.get("/students/search", params={"q": "obi"}).status_code == 403
    admin = make_authenticated_client(app, role="admin")
    assert admin.get("/students/search", params={"q": "obi"}).status_code == 200
    assert admin.get("/students/search", params={"q": "o"}).status_code == 422


def test_every_search_is_logged_without_the_text_or_a_student(roster: Path) -> None:
    assert "student.searched" in EVENT_TYPES
    app = create_app()
    admin = make_authenticated_client(app, role="admin")
    admin.get("/students/search", params={"q": "McKenna"})
    events = admin.get("/events", params={"type": "student.searched"}).json()
    events = events["events"] if isinstance(events, dict) else events
    assert len(events) == 1
    assert events[0]["actor"] == "admin@test.example"
    assert events[0]["payload"] == {
        "matches": 1,
        "shown": 1,
        "directory": "demonstration",
    }
    logged = json.dumps(events[0])
    assert "McKenna" not in logged and "DEM1001" not in logged


def test_no_directory_file_is_a_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_ROSTER, str(tmp_path / "missing.json"))
    admin = make_authenticated_client(create_app(), role="admin")
    assert admin.get("/students/search", params={"q": "obi"}).status_code == 404


def test_pure_search_and_display_helpers() -> None:
    rows = [
        {
            **row,
            "_tokens": tuple(str(row["name"]).lower().split()),
            "_name": str(row["name"]).lower(),
        }
        for row in ROWS
    ]
    assert [r["name"] for r in search_roster(rows, "  josiah   mc ")] == [
        "Josiah McKenna"
    ]
    assert search_roster(rows, "siah") == []  # the start of a word, not its middle
    assert student_body(ROWS[0])["gpa_change_display"] == "+0.07"
    assert (
        student_body({**ROWS[0], "previousGPA": 3.71})["gpa_change_display"] == "0.00"
    )
