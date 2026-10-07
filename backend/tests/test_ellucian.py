"""The Ellucian Ethos connector (cabinet.ellucian) against the mock server.

Covers the round trip (mock -> connector -> validate_upload -> the fixture's
planted metrics), the planted-PII guarantee, pseudonym stability, the
resource allow-list and substring deny list, redirect refusal, the https
rule, 429 backoff observation, mid-run token expiry, the page cap, date
normalisation, named refusals for missing fields, prior-year comparison
resolution, export-file retention, same-second filename uniqueness, key-file
permissions, the missing-key refusal, and the CLI's dry-run vs. real import
behavior.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from cabinet import ellucian
from cabinet.api import DEFAULT_FIXTURE_PATH
from cabinet.datasets import validate_upload
from cabinet.metrics import (
    m1_registration_vs_prior_year,
    m2_unregistered_continuing,
    m3_financial_hold_under_1000,
    m4_no_advising_this_term,
)
from cabinet.store import CabinetStore
from ethos_mock import (
    MOCK_API_KEY,
    PLANTED_EMAIL,
    PLANTED_NAME,
    PLANTED_PHONE,
    PLANTED_SSN,
    EthosMockServer,
    build_ethos_resources,
    load_fixture_document,
    person_guid,
)

PSEUDONYM_KEY = b"test-pseudonym-key-not-a-real-secret"
OTHER_KEY = b"a-different-test-key"


@pytest.fixture
def mock_server() -> Iterator[EthosMockServer]:
    """The fixture, reversed into Ethos resources, served with one 429 on
    the students resource (Retry-After: 2)."""
    resources = build_ethos_resources(load_fixture_document())
    with EthosMockServer(resources, rate_limit_once=("students",)) as server:
        yield server


def _client(
    server: EthosMockServer,
    *,
    api_key: str = MOCK_API_KEY,
    page_size: int = 500,
    max_pages: int = ellucian.MAX_PAGES,
    resources: dict[str, dict[str, Any]] | None = None,
    sleeps: list[float] | None = None,
) -> ellucian.EthosClient:
    return ellucian.EthosClient(
        server.base_url,
        api_key,
        resources,
        page_size=page_size,
        max_pages=max_pages,
        sleeper=(lambda s: None) if sleeps is None else sleeps.append,
    )


def _export(server: EthosMockServer, key: bytes = PSEUDONYM_KEY) -> bytes:
    client = _client(server, page_size=50)
    result = ellucian.build_export_document(
        client,
        pseudonym_key=key,
        term="202720",
        institution_name="Test College",
    )
    return ellucian.render_export(result)


# -- round trip ---------------------------------------------------------------


def test_round_trip_metrics_match_the_fixture(mock_server: EthosMockServer) -> None:
    raw = _export(mock_server)
    report = validate_upload(raw)  # raises UploadError on any problem
    fixture = report.fixture
    m1 = m1_registration_vs_prior_year(fixture)
    assert m1.value == Fraction(-6, 125)  # −4.8 % exactly
    assert m2_unregistered_continuing(fixture).value == 42
    assert m3_financial_hold_under_1000(fixture).value == 18
    assert m4_no_advising_this_term(fixture).value == 12
    assert report.row_counts == {"students": 185, "prior_year_students": 135}
    assert report.fictional is False
    assert report.counseling_present is False
    meta = report.document["meta"]
    assert meta["source"]["host"] == mock_server.base_url.split("//")[1]
    assert meta["source"]["resource_versions"]["students"] == 6


def test_persons_resource_is_never_requested(mock_server: EthosMockServer) -> None:
    # students[].person.id carries every id the connector needs; pulling the
    # tenant-wide persons resource would move names, emails, and credentials
    # across the wire for nothing.
    _export(mock_server)
    assert "persons" not in mock_server.state.get_counts
    meta = json.loads(_export(mock_server))["meta"]
    assert "persons" not in meta["source"]["resource_versions"]


def test_planted_pii_never_enters_the_export(mock_server: EthosMockServer) -> None:
    text = _export(mock_server).decode("utf-8")
    for planted in (PLANTED_NAME, PLANTED_EMAIL, PLANTED_SSN, PLANTED_PHONE):
        assert planted not in text
    for part in PLANTED_NAME.split():
        assert part not in text
    # The Ethos person id itself is never written either — only its HMAC.
    assert person_guid(0) not in text


def test_unknown_hold_category_maps_to_other_with_a_warning(
    mock_server: EthosMockServer,
) -> None:
    client = _client(mock_server)
    result = ellucian.build_export_document(
        client,
        pseudonym_key=PSEUDONYM_KEY,
        term="202720",
        institution_name="Test College",
    )
    assert any("PARKING" in warning for warning in result.warnings)
    holds = [
        hold
        for record in result.document["students"]
        for hold in record["holds"]
        if hold["category"] == "other"
    ]
    assert len(holds) == 1
    assert holds[0]["responsible_office"] == "Campus Safety"


def test_pseudonym_stable_with_same_key_different_with_another(
    mock_server: EthosMockServer,
) -> None:
    first = json.loads(_export(mock_server))
    second = json.loads(_export(mock_server))
    other = json.loads(_export(mock_server, key=OTHER_KEY))

    def ids(document: dict[str, Any]) -> list[str]:
        return sorted(
            record["profile"]["student_id"]
            for record in document["students"] + document["prior_year_students"]
        )

    assert ids(first) == ids(second)
    assert ids(first) != ids(other)
    assert all(sid.startswith("S-") for sid in ids(first))


# -- resource allow list ------------------------------------------------------

BYPASS_PATHS = [
    "person-counseling-notes/",
    "Person-Counseling-Notes",
    "person-counseling-notes?x=1",
    "student-counseling-notes",
    "../admin/keys",
]


@pytest.mark.parametrize("path", BYPASS_PATHS)
def test_override_paths_are_refused(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    monkeypatch.setenv(
        "CABINET_ETHOS_RESOURCES",
        json.dumps({"person_holds": {"path": path}}),
    )
    with pytest.raises(ellucian.EthosError):
        ellucian.resources_from_env()


def test_override_version_must_be_an_int(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "CABINET_ETHOS_RESOURCES",
        json.dumps({"person_holds": {"version": "v5"}}),
    )
    with pytest.raises(ellucian.EthosError, match="version"):
        ellucian.resources_from_env()


@pytest.mark.parametrize("key", ["students", "person_holds"])
def test_persons_override_is_refused(
    monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    # `persons` is never requested, however the configuration is edited:
    # an exact-name deny (a substring would catch person-holds).
    monkeypatch.setenv(
        "CABINET_ETHOS_RESOURCES",
        json.dumps({key: {"path": "persons"}}),
    )
    with pytest.raises(ellucian.EthosError, match="persons"):
        ellucian.resources_from_env()


def test_persons_override_refused_before_any_request() -> None:
    resources = build_ethos_resources(load_fixture_document())
    with EthosMockServer(resources) as server:
        config = ellucian.default_resources()
        config["students"]["path"] = "persons"
        client = _client(server, resources=config)
        with pytest.raises(ellucian.EthosError):
            ellucian.build_export_document(
                client,
                pseudonym_key=PSEUDONYM_KEY,
                term="202720",
                institution_name="Test College",
            )
        # The refusal precedes the first request: /api/persons was not hit.
        assert server.state.get_counts == {}
        assert server.state.auth_count == 0


def test_off_allow_list_path_fires_before_any_request() -> None:
    resources = build_ethos_resources(load_fixture_document())
    with EthosMockServer(resources) as server:
        config = ellucian.default_resources()
        config["person_holds"]["path"] = "person-counseling-notes/"
        client = _client(server, resources=config)
        with pytest.raises(ellucian.EthosError):
            ellucian.build_export_document(
                client,
                pseudonym_key=PSEUDONYM_KEY,
                term="202720",
                institution_name="Test College",
            )
        assert server.state.get_counts == {}
        assert server.state.auth_count == 0


# -- transport hygiene ----------------------------------------------------------


def test_redirect_is_refused_and_nothing_reaches_the_target() -> None:
    resources = build_ethos_resources(load_fixture_document())
    with EthosMockServer(resources) as target:
        redirect = {"students": target.base_url + "/api/students"}
        with EthosMockServer(resources, redirect=redirect) as front:
            client = _client(front)
            with pytest.raises(ellucian.EthosError, match="redirect refused"):
                client.fetch("students")
            # The bearer credential was never forwarded: the second server
            # saw no request at all.
            assert target.state.get_counts == {}
            assert target.state.auth_count == 0


def test_plain_http_base_url_is_refused() -> None:
    with pytest.raises(ellucian.EthosError, match="https"):
        ellucian.EthosClient("http://example.edu", MOCK_API_KEY)


def test_429_then_success_honors_retry_after(mock_server: EthosMockServer) -> None:
    sleeps: list[float] = []
    client = _client(mock_server, sleeps=sleeps)
    students = client.fetch("students")
    assert len(students) == 320  # 185 current-term + 135 prior-year records
    # The mock's 429 carried Retry-After: 2; the backoff honored it exactly.
    assert sleeps == [2.0]
    assert mock_server.state.get_counts["students"] == 2


def test_401_mid_run_is_a_one_line_refusal_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    resources = build_ethos_resources(load_fixture_document())
    with EthosMockServer(resources, expire_token_after=2) as server:
        export_dir = _cli_env(monkeypatch, tmp_path, server)
        rc = ellucian.main(
            ["import", "--institution", "bootstrap", "--term", "202720", "--dry-run"]
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "HTTP 401" in err
        assert len(err.strip().splitlines()) == 1  # one line, never a traceback
        assert not export_dir.exists()


def test_page_cap_stops_loudly(mock_server: EthosMockServer) -> None:
    client = _client(mock_server, page_size=50, max_pages=1)
    with pytest.raises(ellucian.EthosError, match="page cap"):
        client.fetch("students")


def test_wrong_api_key_is_a_clean_error() -> None:
    resources = build_ethos_resources(load_fixture_document())
    with EthosMockServer(resources) as server:
        client = _client(server, api_key="wrong-key")
        with pytest.raises(ellucian.EthosError, match="HTTP 401"):
            client.fetch("students")


# -- mapping robustness ---------------------------------------------------------


def test_datetime_shaped_dates_are_normalised() -> None:
    resources = build_ethos_resources(load_fixture_document())
    # A tenant that sends datetime-with-offset values instead of bare dates.
    resources["student-academic-periods"][0]["registeredOn"] = "2026-11-20T10:00:00Z"
    with EthosMockServer(resources) as server:
        report = validate_upload(_export(server))
    first = report.document["students"][0]
    assert first["enrollment"]["registration_date"] == "2026-11-20"


def test_missing_period_field_is_a_named_refusal() -> None:
    resources = build_ethos_resources(load_fixture_document())
    current = next(
        p for p in resources["academic-periods"] if p["code"] == "202720"
    )
    del current["startOn"]
    with EthosMockServer(resources) as server:
        client = _client(server)
        with pytest.raises(ellucian.EthosError, match="academic-periods.*startOn"):
            ellucian.build_export_document(
                client,
                pseudonym_key=PSEUDONYM_KEY,
                term="202720",
                institution_name="Test College",
            )


def test_prior_year_comparison_status_resolves(mock_server: EthosMockServer) -> None:
    document = json.loads(_export(mock_server))
    statuses = {
        record["comparison"]["prior_term_status"]
        for record in document["prior_year_students"]
    }
    assert "registered" in statuses
    assert statuses <= {"registered", "not_registered", "not_enrolled"}


# -- CLI ------------------------------------------------------------------------


def _write_key_files(tmp_path: Path) -> tuple[Path, Path]:
    api_key_file = tmp_path / "ethos-api-key"
    api_key_file.write_text(MOCK_API_KEY + "\n", encoding="utf-8")
    pseudonym_file = tmp_path / "pseudonym-key"
    pseudonym_file.write_bytes(PSEUDONYM_KEY + b"\n")
    os.chmod(api_key_file, 0o600)
    os.chmod(pseudonym_file, 0o600)
    return api_key_file, pseudonym_file


def _cli_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    server: EthosMockServer,
    *,
    with_pseudonym_key: bool = True,
) -> Path:
    api_key_file, pseudonym_file = _write_key_files(tmp_path)
    monkeypatch.setenv("CABINET_ETHOS_BASE_URL", server.base_url)
    monkeypatch.setenv("CABINET_ETHOS_API_KEY_FILE", str(api_key_file))
    if with_pseudonym_key:
        monkeypatch.setenv("CABINET_PSEUDONYM_KEY_FILE", str(pseudonym_file))
    else:
        monkeypatch.setenv(
            "CABINET_PSEUDONYM_KEY_FILE", str(tmp_path / "missing-key")
        )
    export_dir = tmp_path / "exports"
    monkeypatch.setenv("CABINET_EXPORTS_DIR", str(export_dir))
    return export_dir


def test_missing_pseudonym_key_file_refuses_before_anything(
    mock_server: EthosMockServer,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    export_dir = _cli_env(monkeypatch, tmp_path, mock_server, with_pseudonym_key=False)
    rc = ellucian.main(["import", "--institution", "bootstrap", "--term", "202720"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "CABINET_PSEUDONYM_KEY_FILE" in err
    assert PSEUDONYM_KEY.decode() not in err
    # Nothing written, nothing requested.
    assert not export_dir.exists()
    assert mock_server.state.get_counts == {}
    assert mock_server.state.auth_count == 0


def test_group_or_world_readable_key_file_is_refused(
    mock_server: EthosMockServer,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    export_dir = _cli_env(monkeypatch, tmp_path, mock_server)
    os.chmod(tmp_path / "pseudonym-key", 0o644)  # docs say 0600
    rc = ellucian.main(["import", "--institution", "bootstrap", "--term", "202720"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "0600" in err
    assert not export_dir.exists()
    assert mock_server.state.get_counts == {}


def test_dry_run_writes_the_export_and_uploads_nothing(
    mock_server: EthosMockServer,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    export_dir = _cli_env(monkeypatch, tmp_path, mock_server)
    rc = ellucian.main(
        ["import", "--institution", "bootstrap", "--term", "202720", "--dry-run"]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "dry run: nothing uploaded" in out
    written = list(export_dir.glob("bootstrap-202720-*.json"))
    assert len(written) == 1  # the export copy is kept only on a dry run
    report = validate_upload(written[0].read_bytes())
    assert report.row_counts == {"students": 185, "prior_year_students": 135}
    # The store was never opened: no database file appeared.
    assert not (tmp_path / "cabinet.db").exists()


def test_two_exports_in_the_same_clock_second_both_succeed(
    mock_server: EthosMockServer,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
            return datetime(2026, 9, 26, 12, 0, 0, tzinfo=UTC)

    monkeypatch.setattr(ellucian, "datetime", _FrozenDatetime)
    export_dir = _cli_env(monkeypatch, tmp_path, mock_server)
    args = ["import", "--institution", "bootstrap", "--term", "202720", "--dry-run"]
    assert ellucian.main(args) == 0
    assert ellucian.main(args) == 0
    assert len(list(export_dir.glob("bootstrap-202720-*.json"))) == 2


def test_audit_failure_stores_nothing_and_keeps_the_export(
    mock_server: EthosMockServer,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The dataset and its dataset.uploaded audit event commit as one unit:
    # an audit failure rolls the dataset back, the message says so (and is
    # true), and the export copy is kept with its path named.
    db_path = tmp_path / "cabinet.db"
    store = CabinetStore(db_path, seed_fixture=DEFAULT_FIXTURE_PATH)
    institution_id = store.create_institution("Test College", "test-college")
    store.close()

    def _boom(self: Any, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("simulated audit failure")

    monkeypatch.setattr(CabinetStore, "_audit_append_locked", _boom)
    export_dir = _cli_env(monkeypatch, tmp_path, mock_server)
    rc = ellucian.main(["import", "--institution", "test-college", "--term", "202720"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "nothing was stored" in err
    assert "export copy is kept at" in err

    store = CabinetStore(db_path)
    imported = [
        d
        for d in store.datasets_for(institution_id)
        if d["uploaded_by"] == "ethos-import"
    ]
    assert imported == []
    assert store.audit_events(institution_id, "dataset.uploaded") == []
    store.close()
    kept = list(export_dir.glob("*.json"))
    assert len(kept) == 1
    assert str(kept[0]) in err


def test_import_uploads_an_inactive_dataset_and_removes_the_export_copy(
    mock_server: EthosMockServer,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "cabinet.db"  # the conftest CABINET_DB location
    store = CabinetStore(db_path, seed_fixture=DEFAULT_FIXTURE_PATH)
    institution_id = store.create_institution("Test College", "test-college")
    store.close()
    export_dir = _cli_env(monkeypatch, tmp_path, mock_server)
    rc = ellucian.main(["import", "--institution", "test-college", "--term", "202720"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "inactive until an admin activates it" in out
    # Retention: the second pseudonymised copy in var/exports is removed once
    # the dataset is stored; exports are kept only on a dry run.
    assert list(export_dir.glob("*.json")) == []

    store = CabinetStore(db_path)
    datasets = store.datasets_for(institution_id)
    imported = [d for d in datasets if d["uploaded_by"] == "ethos-import"]
    assert len(imported) == 1
    assert imported[0]["is_active"] == 0
    assert json.loads(str(imported[0]["row_counts"])) == {
        "students": 185,
        "prior_year_students": 135,
    }
    events = store.audit_events(institution_id, "dataset.uploaded")
    assert any(e["payload"].get("importer") == "ethos" for e in events)
    store.close()
