"""Tests for CABINET_FIXTURE.

The fixture path comes from the CABINET_FIXTURE environment variable (default
data/fixture.json). A malformed fixture fails startup with one clear line on
stderr, never a traceback. An empty-but-valid fixture drives the contracted
empty state: every metric renders ``--`` (M5 renders its empty-table line).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cabinet.api import DEFAULT_FIXTURE_PATH, create_app, fixture_path_from_env
from conftest import make_authenticated_client

EMPTY_FIXTURE = {
    "terms": {
        "current": {
            "term": "202720",
            "name": "Spring 2027",
            "start_date": "2027-01-11",
            "registration_open_date": "2026-11-02",
            "registration_close_date": "2026-12-18",
        },
        "prior_year": {
            "term": "202620",
            "name": "Spring 2026",
            "start_date": "2026-01-12",
            "registration_open_date": "2025-11-03",
            "registration_close_date": "2025-12-19",
            "prior_year_equivalent_date": "2025-11-20",
        },
        "in_session": {
            "term": "202710",
            "name": "Fall 2026",
            "start_date": "2026-08-24",
            "end_date": "2026-12-11",
        },
    },
    "students": [],
    "prior_year_students": [],
}


def test_fixture_path_default_and_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CABINET_FIXTURE", raising=False)
    assert fixture_path_from_env() == DEFAULT_FIXTURE_PATH
    monkeypatch.setenv("CABINET_FIXTURE", "/tmp/elsewhere.json")
    assert fixture_path_from_env() == Path("/tmp/elsewhere.json")


def test_empty_fixture_renders_dash_dash_per_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture_file = tmp_path / "empty.json"
    fixture_file.write_text(json.dumps(EMPTY_FIXTURE), encoding="utf-8")
    monkeypatch.setenv("CABINET_FIXTURE", str(fixture_file))

    client = make_authenticated_client(create_app())
    response = client.get("/findings")
    assert response.status_code == 200
    body = response.json()
    for finding_id in ("M1", "M2", "M3", "M4", "M6", "M7"):
        assert body[finding_id]["value"] is None
        assert body[finding_id]["display"] == "--"
        assert body[finding_id]["reason"], f"{finding_id} should say why"
    # M5's empty state is the empty-table line, never "--".
    assert body["M5"]["value"] == []
    assert body["M5"]["display"] == "No unresolved holds"
    assert body["meta"]["as_of"] is None


def test_empty_fixture_prose_never_interpolates_a_bare_dash_dash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Section 5 actions and the section 6 decision say "not available" when
    a finding has no value; the contracted ``--`` stays in the evidence list
    and tiles, never in prose."""
    fixture_file = tmp_path / "empty.json"
    fixture_file.write_text(json.dumps(EMPTY_FIXTURE), encoding="utf-8")
    monkeypatch.setenv("CABINET_FIXTURE", str(fixture_file))
    monkeypatch.setenv("CABINET_PROVIDER", "fake")

    client = make_authenticated_client(create_app())
    asked = client.post(
        "/ask", json={"question": "What should I know about spring registration?"}
    )
    assert asked.status_code == 200
    briefing = asked.json()["briefing"]
    prose = [action["text"] for action in briefing["sections"]["5"]["actions"]]
    decisions = client.get("/decisions").json()["decisions"]
    prose += [d["text"] for d in decisions]
    prose += [d["follow_up"]["description"] for d in decisions]
    assert prose
    for sentence in prose:
        assert "--" not in sentence, sentence
    assert any("not available" in sentence for sentence in prose)


def test_malformed_fixture_fails_startup_with_one_clear_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    monkeypatch.setenv("CABINET_FIXTURE", str(bad))

    with pytest.raises(SystemExit) as excinfo:
        create_app()
    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    lines = [line for line in err.splitlines() if line.strip()]
    assert len(lines) == 1
    assert lines[0].startswith("cabinet: cannot start: fixture")
    assert "Traceback" not in err


def test_invalid_shape_fixture_fails_startup_with_one_clear_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "wrong.json"
    bad.write_text('{"terms": {}, "students": []}', encoding="utf-8")
    monkeypatch.setenv("CABINET_FIXTURE", str(bad))

    with pytest.raises(SystemExit) as excinfo:
        create_app()
    assert excinfo.value.code == 1
    lines = [
        line for line in capsys.readouterr().err.splitlines() if line.strip()
    ]
    assert len(lines) == 1
    assert "cabinet: cannot start: fixture" in lines[0]
