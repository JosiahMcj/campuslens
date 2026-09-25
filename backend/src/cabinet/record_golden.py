"""Record the golden run: one live pass per AI employee per approved
question into data/golden/.

    CABINET_RECORD=1 .venv/bin/python -m cabinet.record_golden

For each approved question in the registry (``cabinet.questions.QUESTIONS``),
runs each analyst (Enrollment, Student Success) once through the normal path
— gate, provider, validation — then the Chief of Staff on the analysts'
validated texts. Each golden file is written from the validated run's own
output, in the same JSON shape the recorder writes — never copied from
``var/replay/``. The replay cache is irrelevant here: the recorder keeps an
existing ``var/replay/`` file, so whatever sits there may predate this run,
and a stale cache file whose text differs from the validated text is ignored
by construction. The Chief of Staff's recording is keyed by the hash of
everything it received — the question's aggregate findings plus the analysts'
validated texts (and, for a non-default question, the question itself) — so
the key is stable when those texts are replayed from the golden recordings.
Requires the live model to be configured (see RUNBOOK.md); exits non-zero
with the reason otherwise, touching nothing in ``data/golden/``. An existing
golden file is never overwritten unless CABINET_RECORD=overwrite.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from cabinet.analysts import (
    ANALYST_RECORD_ROLES,
    CHIEF_OF_STAFF,
    chief_received,
    run_analyst,
    run_chief_of_staff,
)
from cabinet.api import fixture_path_from_env
from cabinet.auth import db_path_from_env
from cabinet.fixture import load_fixture
from cabinet.metrics import findings as compute_findings
from cabinet.provider import (
    Explanation,
    golden_dir_from_env,
    provider_from_env,
    recording_payload,
    replay_filename,
)
from cabinet.questions import QUESTIONS, received_for
from cabinet.store import CabinetStore


def write_golden(
    filename: str,
    findings: dict[str, Any],
    role: str,
    *,
    text: str,
    provider: str,
    model_label: str,
) -> bool:
    """Write one validated response into the golden directory.

    The file is built from the validated run's own output (same JSON shape as
    the recorder writes), so it can never pick up a stale ``var/replay/``
    file. Keeps an existing golden file unless CABINET_RECORD=overwrite (the
    standing guard against clobbering the golden run). Returns True when the
    golden file is in place (written now or kept), False on a write failure.
    """
    golden_dir = golden_dir_from_env()
    golden_dir.mkdir(parents=True, exist_ok=True)
    destination = golden_dir / filename
    if destination.exists() and os.environ.get("CABINET_RECORD") != "overwrite":
        print(
            f"record-golden: {destination} already exists and is kept "
            "(run with CABINET_RECORD=overwrite to replace it)"
        )
        return True
    payload = recording_payload(
        findings,
        role,
        Explanation(text=text, provider=provider, model_label=model_label),
    )
    destination.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    try:
        written = json.loads(destination.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        written = None
    if not isinstance(written, dict) or written.get("text") != text:
        print(
            f"record-golden: write verification failed for {destination}",
            file=sys.stderr,
        )
        return False
    print(f"record-golden: golden recording written to {destination}")
    return True


def main() -> int:
    fixture_path = fixture_path_from_env()
    fixture = load_fixture(fixture_path)
    findings_obj = compute_findings(fixture, fixture_path=fixture_path)

    provider = provider_from_env()
    # Audit events land on the bootstrap institution's chain in the
    # store (the golden run is recorded against the fictional fixture,
    # which is the bootstrap institution's seeded dataset).
    try:
        db_path = db_path_from_env()
    except RuntimeError as exc:
        # A set-but-empty CABINET_DB: one line, never a traceback.
        print(f"record-golden: {exc}", file=sys.stderr)
        return 1
    store = CabinetStore(db_path, seed_fixture=fixture_path)
    log = store.audit_for(store.ensure_bootstrap_institution())
    overwrite = os.environ.get("CABINET_RECORD") == "overwrite"
    for question in QUESTIONS:
        analyst_texts: dict[str, str] = {}
        for role in ANALYST_RECORD_ROLES:
            received = received_for(question, role, findings_obj)
            filename = replay_filename(received, role)
            kept = golden_dir_from_env() / filename
            if kept.exists() and not overwrite:
                # The Chief of Staff must be keyed to the text that will
                # replay, which is this golden file's, so use it and skip the
                # live call.
                analyst_texts[role] = json.loads(kept.read_text())["text"]
                print(
                    f"record-golden: {kept} already exists and is kept; "
                    "its text feeds the chief"
                )
                continue
            result = run_analyst(
                role,
                findings_obj,
                provider,
                log,
                task_id=f"record-golden-{question.id}-{role}",
                question=question,
            )
            if not result.available or result.text is None:
                print(
                    f"record-golden: the live run for {role} "
                    f"({question.id}) failed: {result.reason}",
                    file=sys.stderr,
                )
                return 1
            analyst_texts[role] = result.text
            if not write_golden(
                filename,
                received,
                role,
                text=result.text,
                provider=str(result.provider),
                model_label=str(result.model_label),
            ):
                return 1

        received = chief_received(findings_obj, analyst_texts, question)
        chief_file = golden_dir_from_env() / replay_filename(received, CHIEF_OF_STAFF)
        if chief_file.exists() and not overwrite:
            print(f"record-golden: {chief_file} already exists and is kept")
            continue
        chief_result = run_chief_of_staff(
            findings_obj,
            analyst_texts,
            provider,
            log,
            task_id=f"record-golden-{question.id}-{CHIEF_OF_STAFF}",
            question=question,
        )
        if (
            not chief_result.available
            or chief_result.text is None
            or chief_result.provider is None
            or chief_result.model_label is None
        ):
            print(
                f"record-golden: the live run for {CHIEF_OF_STAFF} "
                f"({question.id}) failed: {chief_result.reason}",
                file=sys.stderr,
            )
            return 1
        if not write_golden(
            replay_filename(received, CHIEF_OF_STAFF),
            received,
            CHIEF_OF_STAFF,
            text=chief_result.text,
            provider=chief_result.provider,
            model_label=chief_result.model_label,
        ):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
