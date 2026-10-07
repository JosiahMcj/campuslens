"""Regression tests for the 2026-10-06 leak audit.

Each section pins one fix: student ids in GET /findings by role, the /ask
question redaction, counseling free text and file modes at rest, the
Ellucian resource allow list, the production model endpoint and redirects,
and the small operational counts in the live model prompt.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from cabinet.api import create_app
from conftest import make_authenticated_client

# Any pseudonymous student id the demonstration fixture carries.
STUDENT_ID_RE = re.compile(r"\b(?:STU|PRI)-\d{3,}\b")


# --- 1. GET /findings: student ids for the executive and admin only -------------


@pytest.mark.parametrize("role", ["staff", "reviewer", "aid"])
def test_findings_carry_no_student_ids_for_non_row_roles(role: str) -> None:
    client = make_authenticated_client(create_app(), role=role)
    response = client.get("/findings")
    assert response.status_code == 200
    body = response.json()
    assert STUDENT_ID_RE.search(response.text) is None
    # The figures and counts are all still there, and say what was withheld.
    m1 = body["M1"]
    assert m1["rows_withheld"] is True
    assert m1["row_ids"] == {"numerator": [], "denominator": []}
    assert m1["row_counts"]["numerator"] > 0
    assert m1["row_counts"]["denominator"] > 0
    m2 = body["M2"]
    assert m2["row_ids"] == []
    assert m2["row_counts"] == m2["value"]
    offices = body["M5"]["value"]
    assert offices and all(o["hold_row_ids"] == [] for o in offices)
    assert all(o["count"] > 0 for o in offices)
    m8 = body["M8"]
    assert m8["row_rules"] == {}
    assert all(rule["row_ids"] == [] for rule in m8["rules"])
    assert m8["row_counts"] == m8["value"]


@pytest.mark.parametrize("role", ["admin", "executive"])
def test_findings_carry_student_ids_for_row_roles(role: str) -> None:
    client = make_authenticated_client(create_app(), role=role)
    response = client.get("/findings")
    assert response.status_code == 200
    body = response.json()
    assert STUDENT_ID_RE.search(response.text) is not None
    assert "rows_withheld" not in body["M2"]
    assert len(body["M2"]["row_ids"]) == body["M2"]["value"]
    assert body["M8"]["row_rules"]


def test_stripping_for_one_role_leaves_the_cached_findings_whole() -> None:
    app = create_app()
    staff = make_authenticated_client(app, role="staff")
    assert STUDENT_ID_RE.search(staff.get("/findings").text) is None
    executive = make_authenticated_client(app, role="executive")
    body: dict[str, Any] = executive.get("/findings").json()
    assert body["M2"]["row_ids"]
    assert any(o["hold_row_ids"] for o in body["M5"]["value"])
    assert json.dumps(body).count("rows_withheld") == 0


# --- 2. /ask: the typed question is redacted before the audit log -------------


def test_ask_redacts_student_ids_before_the_audit_log() -> None:
    from cabinet.questions import QUESTIONS

    app = create_app()
    executive = make_authenticated_client(app, role="executive")
    typed = "Why has S-100023 not registered? Also student 4471 and 20261234."
    response = executive.post("/ask", json={"question": typed})
    assert response.status_code == 200
    assert response.json()["accepted"] is False

    reviewer = make_authenticated_client(app, role="reviewer")
    events = reviewer.get("/events").json()["events"]
    text = json.dumps(events)
    for token in ("S-100023", "20261234"):
        assert token not in text
    asked = [e for e in events if e["type"] == "question.asked"]
    refused = [
        e
        for e in events
        if e["type"] == "data.refused" and "question" in e["payload"]
    ]
    assert asked and refused
    assert "[number withheld]" in asked[-1]["payload"]["question"]
    assert "[number withheld]" in refused[-1]["payload"]["question"]

    # An approved question is unchanged by the redaction, so matching and the
    # recorded question text stay exactly as before.
    from cabinet.explore.privacy import redact_question

    for question in QUESTIONS:
        assert redact_question(question.text) == question.text


# --- 3. counseling free text and file modes at rest -----------------------------


def _fixture_document() -> dict[str, Any]:
    from cabinet.api import DEFAULT_FIXTURE_PATH

    document: dict[str, Any] = json.loads(
        DEFAULT_FIXTURE_PATH.read_text(encoding="utf-8")
    )
    return document


def _note_texts(document: dict[str, Any]) -> list[str]:
    return [
        record["counseling"]["counseling_notes"]
        for key in ("students", "prior_year_students")
        for record in document[key]
        if isinstance(record.get("counseling"), dict)
        and isinstance(record["counseling"].get("counseling_notes"), str)
        and record["counseling"]["counseling_notes"].strip()
    ]


def _mode(path: Any) -> int:
    import os
    import stat

    return stat.S_IMODE(os.stat(path).st_mode)


def test_upload_stores_no_counseling_note_text_and_keeps_the_m9_count() -> None:
    from cabinet.counseling import m9_count
    from cabinet.datasets import COUNSELING_NOTE_MARKER

    document = _fixture_document()
    notes = _note_texts(document)
    assert notes  # the fixture carries note text on purpose
    app = create_app()
    admin = make_authenticated_client(app, role="admin")
    response = admin.post(
        "/admin/datasets",
        content=json.dumps(document).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["validation"]["counseling"] == "present, will always be refused"

    store = app.state.auth
    dataset = store.dataset_row(
        int(store.user_by_email("admin@test.example")["institution_id"]),
        body["dataset"]["id"],
    )
    path = store.dataset_path(dataset)
    stored_bytes = path.read_bytes()
    stored_text = stored_bytes.decode("utf-8")
    for note in notes:
        assert note not in stored_text
    stored = json.loads(stored_text)
    assert _note_texts(stored) == [COUNSELING_NOTE_MARKER] * len(notes)
    # The sha256 the row carries is of the stored (stripped) bytes.
    import hashlib

    assert dataset["sha256"] == hashlib.sha256(stored_bytes).hexdigest()
    # M9's authorized count reads only the fact of contact: unchanged.
    m2_ids = admin.get("/findings").json()["M2"]["row_ids"]
    assert m9_count(document, m2_ids) > 0
    assert m9_count(stored, m2_ids) == m9_count(document, m2_ids)
    assert _mode(path) == 0o600


def test_strip_counseling_text_blanks_empty_and_non_text_notes() -> None:
    from cabinet.datasets import COUNSELING_NOTE_MARKER, strip_counseling_text

    document: dict[str, Any] = {
        "students": [
            {
                "counseling": {
                    "counseling_notes": "Grief support.",
                    "chaplain_contact": True,
                }
            },
            {"counseling": {"counseling_notes": "   "}},
            {"counseling": {"counseling_notes": {"text": "nested"}}},
            {"counseling": {"counseling_notes": None}},
            {"profile": {}},
        ],
        "prior_year_students": [{"counseling": {"counseling_notes": "Old note"}}],
    }
    assert strip_counseling_text(document) is True
    notes = [
        r.get("counseling", {}).get("counseling_notes") for r in document["students"]
    ]
    assert notes == [COUNSELING_NOTE_MARKER, None, None, None, None]
    assert document["students"][0]["counseling"]["chaplain_contact"] is True
    assert (
        document["prior_year_students"][0]["counseling"]["counseling_notes"]
        == COUNSELING_NOTE_MARKER
    )
    assert strip_counseling_text(document) is False  # idempotent


def test_database_is_created_and_tightened_to_0600(tmp_path: Any) -> None:
    import os

    from cabinet.store import CabinetStore

    db = tmp_path / "fresh" / "cabinet.db"
    store = CabinetStore(db)
    store.close()
    assert _mode(db) == 0o600
    os.chmod(db, 0o644)
    CabinetStore(db).close()
    assert _mode(db) == 0o600


def test_backup_and_restore_write_private_files(tmp_path: Any) -> None:
    from cabinet.backup import create_backup, restore_backup

    app = create_app()
    admin = make_authenticated_client(app, role="admin")
    assert admin.get("/findings").status_code == 200
    store = app.state.auth
    db_path, data_dir = store.path, store.data_dir
    store.close()

    backup = create_backup(db_path, data_dir, tmp_path / "backups" / "b1")
    assert _mode(backup) == 0o700
    files = [p for p in backup.rglob("*") if p.is_file()]
    assert files
    assert {_mode(p) for p in files} == {0o600}
    assert {_mode(p) for p in backup.rglob("*") if p.is_dir()} <= {0o700}

    restore_backup(backup, db_path, data_dir)
    assert _mode(db_path) == 0o600
    restored = [p for p in data_dir.rglob("*") if p.is_file()]
    assert restored and {_mode(p) for p in restored} == {0o600}


# --- 4. Ellucian: an exact allow list of resource paths -------------------------

REFUSED_ETHOS_PATHS = [
    "person-emails",
    "person-addresses",
    "health-records",
    "person-emergency-contacts",
    "persons",
    "person-holds-v2",
    "students-detail",
]


@pytest.mark.parametrize("path", REFUSED_ETHOS_PATHS)
@pytest.mark.parametrize("key", ["students", "person_holds", "student_appointments"])
def test_ellucian_refuses_any_path_off_the_allow_list(
    monkeypatch: pytest.MonkeyPatch, key: str, path: str
) -> None:
    from cabinet import ellucian

    monkeypatch.setenv("CABINET_ETHOS_RESOURCES", json.dumps({key: {"path": path}}))
    with pytest.raises(ellucian.EthosError, match="allow list"):
        ellucian.resources_from_env()


def test_ellucian_allow_list_accepts_its_own_paths_and_version_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cabinet import ellucian

    monkeypatch.setenv(
        "CABINET_ETHOS_RESOURCES",
        json.dumps({"person_holds": {"path": "person-holds", "version": 5}}),
    )
    resources = ellucian.resources_from_env()
    assert resources["person_holds"] == {"path": "person-holds", "version": 5}
    assert {key: spec["path"] for key, spec in resources.items()} == (
        ellucian.ALLOWED_RESOURCE_PATHS
    )
    # Swapping two allowed paths between resources is refused too.
    config = ellucian.default_resources()
    config["students"]["path"] = "person-holds"
    with pytest.raises(ellucian.EthosError, match="allow list"):
        ellucian.validate_resource_config(config)
