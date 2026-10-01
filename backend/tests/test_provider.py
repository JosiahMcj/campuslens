"""Tests for the provider interface (ROADMAP §3 layer 5, D5).

Covers: the deterministic FakeProvider, the replay cache (miss = unavailable,
never a network call; search order CABINET_REPLAY_DIR → var/replay →
data/golden), record-after-validation semantics (only validated output is
recorded; an existing recording is never overwritten unless
CABINET_RECORD=overwrite), record-then-replay byte-identical with networking
blocked, the cabinet.local.env loader, and the CABINET_PROVIDER factory.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
from pathlib import Path
from typing import Any

import pytest

from cabinet.analysts import ANALYST_RECORD_ROLES
from cabinet.permissions import findings_for_role
from cabinet.provider import (
    Explanation,
    FakeProvider,
    ProviderUnavailable,
    RecordingProvider,
    ReplayProvider,
    canonical_findings_json,
    golden_dir_from_env,
    load_local_env,
    provider_from_env,
    replay_dirs_from_env,
    replay_filename,
)

FINDINGS: dict[str, Any] = {
    "M2": {
        "id": "M2",
        "title": "Continuing students not yet registered",
        "value": 42,
        "display": "42",
        "reason": None,
        "comparison": None,
        "source_fields": ["profile.continuing"],
        "row_ids": [],
        "definition": "count(...)",
    }
}


def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_network(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("network is blocked in this test")

    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)


def test_fake_provider_is_deterministic_and_cites_each_finding() -> None:
    provider = FakeProvider()
    first = provider.explain(FINDINGS, "enrollment_analyst")
    second = provider.explain(FINDINGS, "enrollment_analyst")
    assert first == second
    assert first.provider == "fake"
    assert first.model_label == "test stub"
    assert "42" in first.text
    assert "[M2]" in first.text
    assert first.recorded is False


def test_replay_miss_is_unavailable_and_never_touches_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _block_network(monkeypatch)
    provider = ReplayProvider(replay_dirs=[tmp_path])
    with pytest.raises(ProviderUnavailable) as excinfo:
        provider.explain(FINDINGS, "enrollment_analyst")
    assert "no recorded response" in excinfo.value.reason
    assert excinfo.value.provider == "replay"


def test_replay_search_order_env_var_replay_golden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Search order: CABINET_REPLAY_DIR if set, then var/replay, then the
    committed golden run in data/golden/; the first match wins."""
    monkeypatch.setenv("CABINET_REPLAY_DIR", str(tmp_path / "custom"))
    dirs = replay_dirs_from_env()
    assert dirs[0] == tmp_path / "custom"
    assert dirs[1].name == "replay" and dirs[1].parent.name == "var"
    assert dirs[2] == golden_dir_from_env()
    assert dirs[2].name == "golden"

    # A recording present only in the LAST directory is still found.
    first, second = tmp_path / "first", tmp_path / "second"
    second.mkdir()
    filename = replay_filename(FINDINGS, "enrollment_analyst")
    (second / filename).write_text(
        json.dumps(
            {
                "provider": "chat",
                "model_label": "live model",
                "role": "enrollment_analyst",
                "text": "from the golden dir [M2].",
            }
        ),
        encoding="utf-8",
    )
    replayed = ReplayProvider(replay_dirs=[first, second]).explain(
        FINDINGS, "enrollment_analyst"
    )
    assert replayed.text == "from the golden dir [M2]."
    assert replayed.recorded is True

    # The first directory in the search path wins over the later ones.
    first.mkdir()
    (first / filename).write_text(
        json.dumps(
            {
                "provider": "fake",
                "model_label": "test stub",
                "role": "enrollment_analyst",
                "text": "from the first dir [M2].",
            }
        ),
        encoding="utf-8",
    )
    assert (
        ReplayProvider(replay_dirs=[first, second]).explain(
            FINDINGS, "enrollment_analyst"
        ).text
        == "from the first dir [M2]."
    )


def test_explain_does_not_record_save_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recording is separate from explain: the analyst runner calls save()
    only after validation, so an invalid live answer is never written."""
    monkeypatch.setenv("CABINET_RECORD", "1")
    recorder = RecordingProvider(FakeProvider(), replay_dir=tmp_path)
    explanation = recorder.explain(FINDINGS, "enrollment_analyst")
    assert not (tmp_path / replay_filename(FINDINGS, "enrollment_analyst")).exists()

    path = recorder.save(FINDINGS, "enrollment_analyst", explanation)
    assert path is not None and path.exists()
    recorded = json.loads(path.read_text(encoding="utf-8"))
    assert recorded["provider"] == "fake"
    assert recorded["model_label"] == "test stub"
    assert recorded["role"] == "enrollment_analyst"
    assert recorded["text"] == explanation.text


def test_existing_recording_is_never_overwritten_unless_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / replay_filename(FINDINGS, "enrollment_analyst")
    original = json.dumps(
        {
            "provider": "chat",
            "model_label": "live model",
            "role": "enrollment_analyst",
            "text": "the good recording [M2].",
        }
    )
    path.write_text(original, encoding="utf-8")

    monkeypatch.setenv("CABINET_RECORD", "1")
    recorder = RecordingProvider(FakeProvider(), replay_dir=tmp_path)
    new = recorder.explain(FINDINGS, "enrollment_analyst")
    assert recorder.save(FINDINGS, "enrollment_analyst", new) is None
    assert path.read_text(encoding="utf-8") == original  # untouched

    monkeypatch.setenv("CABINET_RECORD", "overwrite")
    assert recorder.save(FINDINGS, "enrollment_analyst", new) == path
    recorded = json.loads(path.read_text(encoding="utf-8"))
    assert recorded["text"] == new.text  # replaced only under `overwrite`


def test_record_then_replay_byte_identical_with_network_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Save a FakeProvider run through the recorder, then prove ReplayProvider
    reproduces it byte-identically with networking blocked. The recording is
    labelled provider "fake" — it never claims to be live."""
    replay_dir = tmp_path / "replay"
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    monkeypatch.setenv("CABINET_RECORD", "1")
    monkeypatch.setenv("CABINET_REPLAY_DIR", str(replay_dir))

    recorder = provider_from_env()
    assert isinstance(recorder, RecordingProvider)
    live = recorder.explain(FINDINGS, "enrollment_analyst")
    recorder.save(FINDINGS, "enrollment_analyst", live)

    _block_network(monkeypatch)
    monkeypatch.setenv("CABINET_PROVIDER", "replay")
    monkeypatch.delenv("CABINET_RECORD", raising=False)

    replayer = provider_from_env()
    assert isinstance(replayer, ReplayProvider)
    replayed = replayer.explain(FINDINGS, "enrollment_analyst")
    assert isinstance(replayed, Explanation)
    assert replayed.text == live.text  # byte-identical
    assert replayed.recorded is True
    assert replayed.provider == "fake"  # honest label, never "live"
    assert replayed.model_label == "test stub"


def test_factory_selects_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CABINET_RECORD", raising=False)
    monkeypatch.setenv("CABINET_PROVIDER", "chat")
    from cabinet.provider import ChatProvider

    assert isinstance(provider_from_env(), ChatProvider)
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    assert isinstance(provider_from_env(), FakeProvider)
    monkeypatch.setenv("CABINET_PROVIDER", "replay")
    assert isinstance(provider_from_env(), ReplayProvider)
    monkeypatch.setenv("CABINET_PROVIDER", "other")
    with pytest.raises(ValueError, match="unknown CABINET_PROVIDER"):
        provider_from_env()


def test_factory_default_is_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    from cabinet.provider import ChatProvider

    monkeypatch.delenv("CABINET_PROVIDER", raising=False)
    monkeypatch.delenv("CABINET_RECORD", raising=False)
    assert isinstance(provider_from_env(), ChatProvider)


def test_record_wraps_on_1_and_overwrite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    monkeypatch.setenv("CABINET_RECORD", "1")
    assert isinstance(provider_from_env(), RecordingProvider)
    monkeypatch.setenv("CABINET_RECORD", "overwrite")
    assert isinstance(provider_from_env(), RecordingProvider)
    # Replay output is never recorded.
    monkeypatch.setenv("CABINET_PROVIDER", "replay")
    assert isinstance(provider_from_env(), ReplayProvider)


def test_record_golden_copies_only_a_validated_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`make record-golden` path: a validated run is written into
    the golden directory for EACH analyst role (Enrollment, Student Success)
    and for the Chief of Staff on their validated texts; a failed run writes
    nothing and exits non-zero."""
    import cabinet.record_golden as record_golden
    from cabinet.analysts import CHIEF_OF_STAFF, chief_received

    golden = tmp_path / "golden"
    monkeypatch.setenv("CABINET_GOLDEN_DIR", str(golden))
    monkeypatch.setenv("CABINET_REPLAY_DIR", str(tmp_path / "replay"))
    monkeypatch.setenv("CABINET_RECORD", "1")

    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    assert record_golden.main() == 0
    golden_files = list(golden.glob("*.json"))
    # Both analysts plus the Chief of Staff, per approved question.
    from cabinet.questions import DEFAULT_QUESTION, UNRESOLVED_HOLDS, received_for

    assert len(golden_files) == 2 * (len(ANALYST_RECORD_ROLES) + 1)
    findings_obj = _real_findings()
    analyst_texts: dict[str, str] = {}
    for role in ANALYST_RECORD_ROLES:
        received = findings_for_role(role, findings_obj)
        # Q1's received payload is byte-identical to the pre-registry one, so
        # its recording lands under the same name as always.
        assert received == received_for(DEFAULT_QUESTION, role, findings_obj)
        recorded_path = golden / replay_filename(received, role)
        assert recorded_path.exists()
        recorded = json.loads(recorded_path.read_text(encoding="utf-8"))
        assert recorded["provider"] == "fake"
        assert recorded["model_label"] == "test stub"
        assert recorded["role"] == role
        analyst_texts[role] = recorded["text"]

        # And the golden file replays.
        replayed = ReplayProvider(replay_dirs=[golden]).explain(received, role)
        assert replayed.text == recorded["text"]

    # The third golden file: the Chief of Staff, keyed by everything it
    # received (aggregates plus the analysts' validated texts).
    chief_path = golden / replay_filename(
        chief_received(findings_obj, analyst_texts), CHIEF_OF_STAFF
    )
    assert chief_path.exists()
    chief_recorded = json.loads(chief_path.read_text(encoding="utf-8"))
    assert chief_recorded["role"] == CHIEF_OF_STAFF
    chief_replayed = ReplayProvider(replay_dirs=[golden]).explain(
        chief_received(findings_obj, analyst_texts), CHIEF_OF_STAFF
    )
    assert chief_replayed.text == chief_recorded["text"]

    # Q2's recordings key apart from Q1's: the received payload carries the
    # question, and the chief's hash includes the question id.
    for role in ANALYST_RECORD_ROLES:
        q2_received = received_for(UNRESOLVED_HOLDS, role, findings_obj)
        assert replay_filename(q2_received, role) != replay_filename(
            findings_for_role(role, findings_obj), role
        )
        q2_path = golden / replay_filename(q2_received, role)
        assert q2_path.exists()
        replayed = ReplayProvider(replay_dirs=[golden]).explain(q2_received, role)
        assert json.loads(q2_path.read_text(encoding="utf-8"))["text"] == (
            replayed.text
        )


def test_record_golden_failed_run_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cabinet.record_golden as record_golden

    golden = tmp_path / "golden"
    monkeypatch.setenv("CABINET_GOLDEN_DIR", str(golden))
    monkeypatch.setenv("CABINET_REPLAY_DIR", str(tmp_path / "replay"))
    monkeypatch.setenv("CABINET_RECORD", "1")
    for var in (
        "CABINET_LLM_BASE_URL",
        "CABINET_LLM_MODEL",
        "CABINET_LLM_LABEL",
        "CABINET_LLM_API_KEY",
        "CABINET_LLM_API_KEY_FILE",
        "CABINET_LLM_API_KEY_VAR",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(
        "cabinet.provider.LOCAL_ENV_PATH", tmp_path / "no-cabinet-local-env"
    )
    monkeypatch.setenv("CABINET_PROVIDER", "chat")  # unconfigured live model

    assert record_golden.main() == 1
    assert not golden.exists() or list(golden.glob("*.json")) == []


def _real_findings() -> dict[str, Any]:
    from cabinet.fixture import load_fixture
    from cabinet.metrics import findings as compute_findings

    fixture_path = Path(__file__).resolve().parents[2] / "data" / "fixture.json"
    return compute_findings(load_fixture(fixture_path), fixture_path=fixture_path)


def test_local_env_loaded_env_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cabinet.local.env supplies CABINET_* keys; the real environment wins;
    non-CABINET keys are never read."""
    env_file = tmp_path / "cabinet.local.env"
    env_file.write_text(
        "# comment\n"
        "CABINET_LLM_BASE_URL=https://llm.example.invalid/v1\n"
        'CABINET_LLM_LABEL="from the file"\n'
        "CABINET_LLM_MODEL=file-model\n"
        "OTHER_SECRET=never-read\n",
        encoding="utf-8",
    )
    for var in ("CABINET_LLM_BASE_URL", "CABINET_LLM_LABEL", "CABINET_LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CABINET_LLM_MODEL", "env-model")

    load_local_env(env_file)
    assert os.environ.get("CABINET_LLM_BASE_URL") == "https://llm.example.invalid/v1"
    assert os.environ.get("CABINET_LLM_LABEL") == "from the file"
    assert os.environ.get("CABINET_LLM_MODEL") == "env-model"  # env wins
    assert os.environ.get("OTHER_SECRET") is None

    # A missing file is fine and never raises.
    load_local_env(tmp_path / "no-such-file")


def test_record_golden_keeps_an_existing_golden_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A committed golden run is never replaced by a re-run unless
    CABINET_RECORD=overwrite is set; the demo depends on that file."""
    import cabinet.record_golden as record_golden

    golden = tmp_path / "golden"
    monkeypatch.setenv("CABINET_GOLDEN_DIR", str(golden))
    monkeypatch.setenv("CABINET_REPLAY_DIR", str(tmp_path / "replay"))
    monkeypatch.setenv("CABINET_PROVIDER", "fake")

    monkeypatch.setenv("CABINET_RECORD", "1")
    assert record_golden.main() == 0
    files = sorted(golden.glob("*.json"))
    assert files
    before = {f.name: f.read_bytes() for f in files}
    planted = "Continuing students not yet registered: 42 [M2]. keep me"
    for f in files:
        data = json.loads(f.read_text())
        data["text"] = planted
        f.write_text(json.dumps(data))

    assert record_golden.main() == 0
    kept = {
        f.name: json.loads(f.read_text())["text"]
        for f in golden.glob("*.json")
        if f.name in before
    }
    assert kept == {name: planted for name in before}

    monkeypatch.setenv("CABINET_RECORD", "overwrite")
    assert record_golden.main() == 0
    after = {
        f.name: json.loads(f.read_text())["text"]
        for f in golden.glob("*.json")
        if f.name in before
    }
    assert after == {name: json.loads(raw)["text"] for name, raw in before.items()}


def test_record_golden_keys_the_chief_to_the_kept_analyst_texts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the analysts' golden files already exist, record-golden skips their
    live runs and feeds the Chief of Staff the golden texts, so the chief's
    recording is keyed to what actually replays."""
    import cabinet.record_golden as record_golden
    from cabinet.analysts import (
        ANALYST_RECORD_ROLES,
        CHIEF_OF_STAFF,
        chief_received,
    )
    from cabinet.fixture import load_fixture
    from cabinet.metrics import findings as compute_findings
    from cabinet.permissions import findings_for_role

    golden = tmp_path / "golden"
    monkeypatch.setenv("CABINET_GOLDEN_DIR", str(golden))
    monkeypatch.setenv("CABINET_REPLAY_DIR", str(tmp_path / "replay"))
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    monkeypatch.setenv("CABINET_RECORD", "1")
    assert record_golden.main() == 0

    from cabinet.api import fixture_path_from_env

    fixture_path = fixture_path_from_env()
    findings_obj = compute_findings(
        load_fixture(fixture_path), fixture_path=fixture_path
    )
    planted: dict[str, str] = {}
    for role in ANALYST_RECORD_ROLES:
        f = golden / replay_filename(findings_for_role(role, findings_obj), role)
        data = json.loads(f.read_text())
        data["text"] = (
            f"Continuing students not yet registered: 42 [M2]. planted {role}"
        )
        planted[role] = data["text"]
        f.write_text(json.dumps(data))
    for f in golden.glob(f"{CHIEF_OF_STAFF}-*.json"):
        f.unlink()

    assert record_golden.main() == 0
    expected = golden / replay_filename(
        chief_received(findings_obj, planted), CHIEF_OF_STAFF
    )
    assert expected.exists(), "chief recording must be keyed to the kept golden texts"


def test_record_golden_writes_from_the_validated_run_not_the_replay_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale var/replay file under the very name this run uses is ignored:
    the golden file holds the text this run validated, in the recorder's JSON
    shape — never whatever sat in the replay cache."""
    import cabinet.record_golden as record_golden

    replay_dir = tmp_path / "replay"
    golden = tmp_path / "golden"
    monkeypatch.setenv("CABINET_GOLDEN_DIR", str(golden))
    monkeypatch.setenv("CABINET_REPLAY_DIR", str(replay_dir))
    monkeypatch.setenv("CABINET_RECORD", "1")
    monkeypatch.setenv("CABINET_PROVIDER", "fake")

    findings_obj = _real_findings()
    received = findings_for_role("enrollment_analyst", findings_obj)
    filename = replay_filename(received, "enrollment_analyst")
    replay_dir.mkdir(parents=True)
    (replay_dir / filename).write_text(
        json.dumps(
            {
                "provider": "chat",
                "model_label": "live model",
                "role": "enrollment_analyst",
                "text": "STALE — not this run's validated text",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert record_golden.main() == 0

    recorded = json.loads((golden / filename).read_text(encoding="utf-8"))
    expected = FakeProvider().explain(received, "enrollment_analyst")
    assert recorded["text"] == expected.text
    assert recorded["text"] != "STALE — not this run's validated text"
    assert recorded["provider"] == expected.provider
    assert recorded["model_label"] == expected.model_label
    assert recorded["role"] == "enrollment_analyst"
    assert recorded["findings_sha256"] == hashlib.sha256(
        canonical_findings_json(received).encode("utf-8")
    ).hexdigest()
    # The stale cache file was left alone, not propagated into data/golden.
    assert "STALE" in (replay_dir / filename).read_text(encoding="utf-8")


def test_rekeyed_golden_files_carry_their_provenance_and_the_same_text(
    tmp_path: Path,
) -> None:
    """A golden file may be re-keyed (same validated text under a new
    findings key) only with the full marker, and the replay provider
    surfaces the marker so a briefing can say where the text was first
    recorded."""
    golden = Path(__file__).resolve().parents[2] / "data" / "golden"
    files = {
        p.name: json.loads(p.read_text(encoding="utf-8")) for p in golden.glob("*.json")
    }
    rekeyed = {name: d for name, d in files.items() if "rekeyed_from" in d}
    assert rekeyed, "the M8 re-key must be marked"
    for name, data in rekeyed.items():
        assert data["rekeyed_at"] and data["rekey_reason"], name
        assert data["text"].strip(), name
        # The marker names a real key (sha256 hex), and the superseded file
        # is gone so replay can never find two texts for one payload.
        assert len(data["rekeyed_from"]) == 64, name
        assert set(data["rekeyed_from"]) <= set("0123456789abcdef"), name
        role = name.split("-", 1)[0]
        assert f"{role}-{data['rekeyed_from']}.json" not in files, name
    name, data = next(iter(rekeyed.items()))
    role = name.split("-", 1)[0]
    findings = {"M1": {"id": "M1", "value": 1}}
    (tmp_path / replay_filename(findings, role)).write_text(
        json.dumps(data), encoding="utf-8"
    )
    explanation = ReplayProvider(replay_dirs=[tmp_path]).explain(findings, role)
    assert explanation.recorded is True
    assert explanation.rekeyed_from == data["rekeyed_from"]
