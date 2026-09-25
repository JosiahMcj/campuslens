"""Tests for the Enrollment Analyst (ROADMAP §3 layer 4).

Covers: the analyst receives only its permitted findings (M1, M2, M7) and
every call logs ``data.granted`` listing exactly those fields; output
validation (claim IDs, the numeral test); unavailable outcomes (provider down,
invented numbers) are never shown; the API endpoint's 200 and 503 shapes; and
the standing numeral test over every recording in ``var/replay/``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cabinet.analysts import (
    ENROLLMENT_ANALYST,
    OutputRejected,
    check_numerals,
    parse_claims,
    run_enrollment_analyst,
    validate_explanation,
)
from cabinet.api import create_app
from cabinet.audit import AuditLog
from cabinet.fixture import load_fixture
from cabinet.metrics import findings as compute_findings
from cabinet.permissions import ROLE_TASK_FIELDS, findings_for_role
from cabinet.provider import (
    DEFAULT_REPLAY_DIR,
    GOLDEN_REPLAY_DIR,
    Explanation,
    FakeProvider,
    ProviderUnavailable,
    RecordingProvider,
    ReplayProvider,
    replay_filename,
)
from conftest import make_authenticated_client

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO_ROOT / "data" / "fixture.json"
ENROLLMENT_FIELDS = list(ROLE_TASK_FIELDS[ENROLLMENT_ANALYST])


@pytest.fixture(scope="module")
def findings_obj() -> dict[str, Any]:
    return compute_findings(load_fixture(FIXTURE_PATH), fixture_path=FIXTURE_PATH)


@pytest.fixture
def log(tmp_path: Path) -> AuditLog:
    return AuditLog(tmp_path / "events.jsonl")


class StubProvider:
    """Returns a fixed text and remembers what findings it received."""

    name = "stub"
    model_label = "stub-1"

    def __init__(self, text: str) -> None:
        self.text = text
        self.received: dict[str, Any] | None = None

    def explain(self, findings: dict[str, Any], role: str) -> Explanation:
        self.received = findings
        return Explanation(
            text=self.text, provider=self.name, model_label=self.model_label
        )


class DownProvider:
    name = "down"
    model_label = "down-1"

    def explain(self, findings: dict[str, Any], role: str) -> Explanation:
        raise ProviderUnavailable("provider is down", provider=self.name)


# --- scoping and audit --------------------------------------------------------


def test_analyst_receives_only_m1_m2_m7(
    findings_obj: dict[str, Any], log: AuditLog
) -> None:
    provider = StubProvider("Registration is down 4.8 % [M1].")
    result = run_enrollment_analyst(findings_obj, provider, log)
    assert result.available is True
    assert provider.received is not None
    assert sorted(provider.received) == ["M1", "M2", "M7"]


def test_every_call_logs_data_granted_with_exact_fields(
    findings_obj: dict[str, Any], log: AuditLog
) -> None:
    provider = StubProvider("42 continuing students are not yet registered [M2].")
    run_enrollment_analyst(findings_obj, provider, log, task_id="task-a")
    run_enrollment_analyst(findings_obj, provider, log, task_id="task-b")

    granted = log.events("data.granted")
    assert len(granted) == 2
    for event in granted:
        assert event["actor"] == ENROLLMENT_ANALYST
        assert event["payload"]["granted_fields"] == ENROLLMENT_FIELDS
        # The enrollment role's grant contains no holds/advising/counseling.
        assert not any(
            f.startswith(("holds.", "advising.", "counseling."))
            for f in event["payload"]["granted_fields"]
        )

    produced = log.events("finding.produced")
    assert len(produced) == 2
    assert produced[0]["payload"]["findings"] == ["M1", "M2", "M7"]


def test_data_granted_written_once_per_role_task_findings(
    findings_obj: dict[str, Any], log: AuditLog, caplog: pytest.LogCaptureFixture
) -> None:
    """Audit fix: a page-load loop against a down model must not fill the log
    with grants that led nowhere — at most one data.granted per (role,
    task_id, findings hash) in a process run. Failures are still logged with
    the task id via Python logging."""
    import logging as std_logging

    provider = DownProvider()
    with caplog.at_level(std_logging.WARNING, logger="cabinet.analysts"):
        for _ in range(3):
            result = run_enrollment_analyst(
                findings_obj, provider, log, task_id="task-stuck"
            )
            assert result.available is False

    granted = log.events("data.granted")
    assert len(granted) == 1
    assert granted[0]["payload"]["task_id"] == "task-stuck"
    assert log.events("finding.produced") == []
    failure_logs = [
        r for r in caplog.records if "enrollment analyst unavailable" in r.getMessage()
    ]
    assert len(failure_logs) == 3
    assert all("task-stuck" in r.getMessage() for r in failure_logs)

    # A different task (or different findings) is a new grant.
    run_enrollment_analyst(findings_obj, provider, log, task_id="task-other")
    assert len(log.events("data.granted")) == 2


# --- the numeral test ----------------------------------------------------------


def test_numeral_test_passes_on_fake_provider_output(
    findings_obj: dict[str, Any], log: AuditLog
) -> None:
    """(a) FakeProvider output must pass validation against the real fixture."""
    result = run_enrollment_analyst(findings_obj, FakeProvider(), log)
    assert result.available is True, result.reason
    assert result.text is not None
    claims = validate_explanation(result.text, result_claims_findings(findings_obj))
    assert {fid for c in claims for fid in c.finding_ids} <= {"M1", "M2", "M7"}


def result_claims_findings(findings_obj: dict[str, Any]) -> dict[str, Any]:
    return findings_for_role(ENROLLMENT_ANALYST, findings_obj)


def test_numeral_test_rejects_an_invented_number(
    findings_obj: dict[str, Any], log: AuditLog
) -> None:
    """(b) "43 students" is not in the findings; the output is rejected,
    logged, and returned as unavailable — never shown."""
    provider = StubProvider("43 students have not registered yet [M2].")
    result = run_enrollment_analyst(findings_obj, provider, log)
    assert result.available is False
    assert result.reason is not None and "43" in result.reason
    assert result.text is None  # unvalidated text never leaves the analyst
    assert log.events("finding.produced") == []

    with pytest.raises(OutputRejected, match="43"):
        validate_explanation(
            "43 students have not registered yet [M2].",
            result_claims_findings(findings_obj),
        )


def test_numeral_test_accepts_signed_and_unsigned_forms(
    findings_obj: dict[str, Any],
) -> None:
    received = result_claims_findings(findings_obj)
    # Unicode minus, as the findings display it.
    check_numerals("Registration is −4.8 % below last year [M1].", received)
    # Narrative framing drops the sign; the magnitude must still exist.
    check_numerals("Registration is down 4.8 % [M1].", received)
    check_numerals("42 continuing students [M2].", received)
    check_numerals("Credit hours are down 2.7 % [M7].", received)


def test_numeral_test_ignores_finding_ids_in_prose(
    findings_obj: dict[str, Any],
) -> None:
    check_numerals(
        "Per M2, 42 continuing students are not yet registered [M2].",
        result_claims_findings(findings_obj),
    )


# --- review fixes: row IDs and glued digits ------------------------------------


def test_analyst_receives_no_row_ids(
    findings_obj: dict[str, Any], log: AuditLog
) -> None:
    """The model never sees a row ID; the evidence drawer reads them from
    /findings, not from the analyst's scoped findings."""
    provider = StubProvider("42 continuing students are not yet registered [M2].")
    result = run_enrollment_analyst(findings_obj, provider, log)
    assert result.available is True, result.reason
    received = json.dumps(provider.received)
    assert "row_ids" not in received
    assert "STU-" not in received
    assert "PRI-" not in received


def test_rejects_a_student_id_in_the_output(findings_obj: dict[str, Any]) -> None:
    """A student-ID-shaped token can never come from the findings (the analyst
    receives none), so it is rejected outright — not scanned as a numeral."""
    received = result_claims_findings(findings_obj)
    with pytest.raises(OutputRejected, match="STU-0999"):
        validate_explanation("Students STU-0999 need help [M2].", received)
    with pytest.raises(OutputRejected, match="PRI-0007"):
        validate_explanation("PRI-0007 registered last year [M1].", received)


def test_rejects_digits_glued_to_letters(findings_obj: dict[str, Any]) -> None:
    """The numeral scan examines every digit run, including one glued to
    letters: FY2031's 2031 is a numeral and is not in the findings."""
    received = result_claims_findings(findings_obj)
    with pytest.raises(OutputRejected, match="'2031'"):
        validate_explanation("In FY2031 registration fell [M1].", received)


def test_allowed_percent_comes_from_the_display_string_only() -> None:
    """The display is the single source of truth for percent prose. At the
    halfway value -0.0125 the display is −1.3 % and only 1.3 is allowed; a
    (stale) display of −1.3's neighbour would not license it."""
    finding = {"M1": {"value": -0.0125, "display": "−1.3 %", "comparison": None}}
    claims = validate_explanation("Registration is down 1.3 % [M1].", finding)
    assert [c.finding_ids for c in claims] == [["M1"]]
    with pytest.raises(OutputRejected, match="'1.2 %'"):
        validate_explanation("Registration is down 1.2 % [M1].", finding)
    # Same raw value, a display that rounded the other way: only the
    # display's number is allowed — value × 100 is never consulted.
    stale = {"M1": {"value": -0.0125, "display": "−1.2 %", "comparison": None}}
    with pytest.raises(OutputRejected, match="'1.3 %'"):
        validate_explanation("Registration is down 1.3 % [M1].", stale)


# --- review cases: attribution, dates, direction ------------------------------


def test_rejects_wrong_direction_on_a_negative_finding(
    findings_obj: dict[str, Any],
) -> None:
    """Failing case (1): sign is structural. M1 is −4.8 %; "up 4.8 %" lies."""
    received = result_claims_findings(findings_obj)
    with pytest.raises(OutputRejected, match="direction"):
        validate_explanation("Registration is up 4.8 % over last year [M1].", received)


def test_accepts_decline_word_for_a_negative_finding(
    findings_obj: dict[str, Any],
) -> None:
    received = result_claims_findings(findings_obj)
    claims = validate_explanation(
        "Registration is down 4.8 % from the same point last year [M1].", received
    )
    assert [c.finding_ids for c in claims] == [["M1"]]


def test_comma_after_a_number_is_punctuation_not_a_separator(
    findings_obj: dict[str, Any],
) -> None:
    received = result_claims_findings(findings_obj)
    validate_explanation(
        "Continuing students not yet registered: 42, while last year 125 had "
        "registered by this point [M1, M2].",
        received,
    )
    validate_explanation(
        "Last year's registered credit hours were 1,872, and hours are down "
        "2.7 % [M7].",
        received,
    )
    with pytest.raises(OutputRejected):
        validate_explanation("About 1,250 students registered [M1].", received)


def test_rejects_date_fragments_as_numbers(findings_obj: dict[str, Any]) -> None:
    """Failing case (2): the ISO date 2025-11-20 never justifies a bare 20
    or 2025; those must match a real number in the cited findings."""
    received = result_claims_findings(findings_obj)
    with pytest.raises(OutputRejected, match="'20'"):
        validate_explanation(
            "About 20 continuing students are not yet registered [M2].", received
        )
    with pytest.raises(OutputRejected, match="'2025'"):
        validate_explanation("In 2025 registration looked different [M1].", received)


def test_accepts_the_comparison_date_in_iso_or_written_form(
    findings_obj: dict[str, Any],
) -> None:
    received = result_claims_findings(findings_obj)
    claims = validate_explanation(
        "The prior-year comparison date is November 20, 2025 [M1].", received
    )
    assert [c.finding_ids for c in claims] == [["M1"]]
    validate_explanation("The prior-year comparison date is 2025-11-20 [M1].", received)
    # A real date form naming the wrong date is still rejected.
    with pytest.raises(OutputRejected, match="date"):
        validate_explanation(
            "The prior-year comparison date is November 21, 2025 [M1].", received
        )


def test_per_claim_attribution(findings_obj: dict[str, Any]) -> None:
    """Failing case (3): 42 is M2's number; a claim citing M1 cannot use it."""
    received = result_claims_findings(findings_obj)
    with pytest.raises(OutputRejected, match="'42'"):
        validate_explanation(
            "42 students fewer registered than last year [M1].", received
        )
    claims = validate_explanation(
        "42 continuing students are not yet registered [M2].", received
    )
    assert [c.finding_ids for c in claims] == [["M2"]]


# --- number words: word forms validate exactly like digits --------------------


def test_number_words_validate_like_digits(findings_obj: dict[str, Any]) -> None:
    """A live model run wrote "Forty-two continuing students…" and passed
    unchecked while the scan read digits only. Word forms now convert and
    validate against the same per-claim allowed set."""
    received = result_claims_findings(findings_obj)
    claims = validate_explanation(
        "Forty-two continuing students have not registered [M2].", received
    )
    assert [c.finding_ids for c in claims] == [["M2"]]
    check_numerals("Forty two continuing students [M2].", received)
    check_numerals(
        "One hundred twenty-five continuing students had registered by the "
        "equivalent date last year [M1].",
        received,
    )
    with pytest.raises(OutputRejected, match="Forty-three"):
        validate_explanation(
            "Forty-three continuing students have not registered [M2].", received
        )
    # Per-claim attribution: 18 is M3's number; a claim citing M2 cannot use it.
    with pytest.raises(OutputRejected, match="eighteen"):
        validate_explanation(
            "eighteen continuing students have not registered [M2].", received
        )


def test_number_words_skip_ordinals_articles_and_lone_one(
    findings_obj: dict[str, Any],
) -> None:
    received = result_claims_findings(findings_obj)
    # "first" is an ordinal, "a"/"one of" ordinary prose, and a scale word
    # with no unit or tens word ("hundreds of") says nothing checkable.
    check_numerals(
        "The first step is outreach to one of these students [M2].", received
    )
    check_numerals("A hundred students here, hundreds more there [M2].", received)


def test_point_decimal_number_words_validate_like_digits(
    findings_obj: dict[str, Any],
) -> None:
    """"four point eight percent" is 4.8 % and follows the same rules: the
    value must exist and the direction must match."""
    received = result_claims_findings(findings_obj)
    claims = validate_explanation(
        "Registration is down four point eight percent [M1].", received
    )
    assert [c.finding_ids for c in claims] == [["M1"]]
    # Direction still binds: "higher" against a negative finding is a lie.
    with pytest.raises(OutputRejected, match="direction"):
        validate_explanation(
            "Registration is four point eight percent higher [M1].", received
        )
    # A value that does not exist is rejected, word form or not.
    with pytest.raises(OutputRejected, match="four point nine"):
        validate_explanation(
            "Registration is down four point nine percent [M1].", received
        )


# --- audit fixes: dates without years, per-number direction ------------------


def test_accepts_month_and_day_without_a_year(
    findings_obj: dict[str, Any],
) -> None:
    """"as of November 20" is accepted when an allowed date's month and day
    match (2025-11-20 in M1/M7's comparison); the year stays the findings'
    business."""
    received = result_claims_findings(findings_obj)
    claims = validate_explanation(
        "As of November 20, registration is down 4.8 % [M1].", received
    )
    assert [c.finding_ids for c in claims] == [["M1"]]
    validate_explanation("As of Nov 20, hours are down 2.7 % [M7].", received)
    # The with-year forms still work, and a wrong month-day is still rejected.
    validate_explanation(
        "The prior-year comparison date is November 20, 2025 [M1].", received
    )
    with pytest.raises(OutputRejected, match="date"):
        validate_explanation(
            "As of November 19, registration is down 4.8 % [M1].", received
        )


def test_direction_is_checked_per_number_not_per_claim(
    findings_obj: dict[str, Any],
) -> None:
    """Old bug: direction was a claim-level flag, so one wrong direction
    poisoned every number in the claim (and one right word laundered a wrong
    one). Now each direction word attaches to its nearest numeral."""
    received = result_claims_findings(findings_obj)
    claims = validate_explanation(
        "Registration is down 4.8 % and credit hours are down 2.7 % [M1, M7].",
        received,
    )
    assert [c.finding_ids for c in claims] == [["M1", "M7"]]
    # Only the 2.7 misstates its direction; the 4.8 is fine.
    with pytest.raises(OutputRejected, match=r"'2.7 %'"):
        validate_explanation(
            "Registration is down 4.8 % and credit hours are up 2.7 % [M1, M7].",
            received,
        )


# --- audit fixes: risk/score language and negated direction ------------------


def test_rejects_risk_and_score_language_about_students(
    findings_obj: dict[str, Any],
) -> None:
    """Students are people who may need support, never risk scores."""
    received = result_claims_findings(findings_obj)
    for text in (
        "42 students are at risk of missing registration [M2].",
        "42 students are at-risk [M2].",
        "42 students are at high risk [M2].",
        "The risk score for these 42 students [M2].",
        "The score for these 42 students [M2].",
        "There is a risk these 42 students miss registration [M2].",
    ):
        with pytest.raises(OutputRejected, match="never risk scores"):
            validate_explanation(text, received)
    # No student reference, no rejection.
    validate_explanation(
        "Continuing students not yet registered: 42 [M2].", received
    )


def test_rejects_negated_direction_next_to_a_change_number(
    findings_obj: dict[str, Any],
) -> None:
    """"not down 4.8 %" inverts the sign instead of honoring it — rejected.
    Contractions and other negation forms are the same dodge and must not
    satisfy the direction check either."""
    received = result_claims_findings(findings_obj)
    for text in (
        "Registration is not down 4.8 % [M1].",
        "Credit hours are not up 2.7 % [M7].",
        "Registration isn't down 4.8 % [M1].",
        "Registration never fell 4.8 % [M1].",
        "There was no decline of 4.8 % [M1].",
        "Credit hours didn't increase 2.7 % [M7].",
    ):
        with pytest.raises(OutputRejected, match="negated direction"):
            validate_explanation(text, received)
    # A plain negation away from any direction word is prose, not a dodge.
    validate_explanation(
        "42 continuing students have not registered yet [M2].", received
    )


def test_rejects_raw_fraction_for_a_percent_finding(
    findings_obj: dict[str, Any],
) -> None:
    """Rule (d): 0.048 is the raw value; prose must use the display form."""
    received = result_claims_findings(findings_obj)
    with pytest.raises(OutputRejected, match="'0.048'"):
        validate_explanation("Registration changed by 0.048 [M1].", received)


def test_claim_must_carry_a_received_finding_id(
    findings_obj: dict[str, Any],
) -> None:
    received = result_claims_findings(findings_obj)
    # No bracketed ID at all.
    with pytest.raises(OutputRejected, match="without a finding ID"):
        parse_claims("Registration is down.")
    # Trailing sentence without an ID.
    with pytest.raises(OutputRejected, match="without a finding ID"):
        parse_claims("Down 4.8 % [M1]. And it will get worse.")
    # An ID the enrollment analyst did not receive.
    with pytest.raises(OutputRejected, match="M3"):
        validate_explanation("18 students have small holds [M3].", received)


def test_parse_claims_trims_leading_conjunction_for_rendering() -> None:
    """A claim continuing the previous sentence (", and 12 have had …")
    renders without the punctuation and conjunction; validation still sees
    the original body in ``source_text``."""
    claims = parse_claims(
        "Among continuing students who have not registered, 18 have an "
        "unresolved financial hold under $1,000 [M3], and 12 have had no "
        "advising appointment this term [M4]."
    )
    assert len(claims) == 2
    assert claims[1].text.startswith("12 have had")
    assert claims[1].source_text.startswith(", and 12 have had")
    # The first claim's text is unchanged apart from capitalisation, and a
    # lowercase sentence start is capitalised for rendering.
    assert claims[0].text.startswith("Among continuing students")
    assert parse_claims("down 4.8 % [M1].")[0].text == "Down 4.8 %"


def test_provider_down_is_unavailable_and_not_shown(
    findings_obj: dict[str, Any], log: AuditLog
) -> None:
    result = run_enrollment_analyst(findings_obj, DownProvider(), log)
    assert result.available is False
    assert result.reason == "provider is down"
    assert result.text is None
    # The grant is still logged; nothing is produced.
    assert len(log.events("data.granted")) == 1
    assert log.events("finding.produced") == []


# --- audit fix: only validated output is ever recorded ------------------------


def test_recording_happens_only_after_validation(
    findings_obj: dict[str, Any], log: AuditLog, tmp_path: Path
) -> None:
    """Old bug: RecordingProvider saved the response BEFORE validation, so an
    invalid live answer overwrote a good recording and replay then 503'd.
    Now the runner records only after validation succeeds."""
    replay_dir = tmp_path / "replay"

    bad = RecordingProvider(
        StubProvider("43 students have not registered yet [M2]."),
        replay_dir=replay_dir,
    )
    result = run_enrollment_analyst(findings_obj, bad, log)
    assert result.available is False
    assert not replay_dir.exists() or list(replay_dir.glob("*.json")) == []

    good = RecordingProvider(
        StubProvider("42 continuing students are not yet registered [M2]."),
        replay_dir=replay_dir,
    )
    result = run_enrollment_analyst(findings_obj, good, log)
    assert result.available is True
    received = result_claims_findings(findings_obj)
    path = replay_dir / replay_filename(received, ENROLLMENT_ANALYST)
    assert path.exists()
    recorded = json.loads(path.read_text(encoding="utf-8"))
    assert recorded["text"] == "42 continuing students are not yet registered [M2]."
    assert recorded["provider"] == "stub"
    assert recorded["model_label"] == "stub-1"


def test_invalid_live_answer_cannot_overwrite_a_good_recording(
    findings_obj: dict[str, Any],
    log: AuditLog,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact reported failure: a good recording exists, the next live
    answer is invalid — the recording must survive, and CABINET_RECORD=
    overwrite replaces only with validated output."""
    replay_dir = tmp_path / "replay"
    replay_dir.mkdir()
    received = result_claims_findings(findings_obj)
    path = replay_dir / replay_filename(received, ENROLLMENT_ANALYST)
    good_text = "42 continuing students are not yet registered [M2]."
    path.write_text(
        json.dumps(
            {
                "provider": "chat",
                "model_label": "live model",
                "role": ENROLLMENT_ANALYST,
                "text": good_text,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("CABINET_RECORD", "overwrite")  # even overwrite...
    bad = RecordingProvider(
        StubProvider("99 students invented [M2]."), replay_dir=replay_dir
    )
    result = run_enrollment_analyst(findings_obj, bad, log)
    assert result.available is False
    assert json.loads(path.read_text(encoding="utf-8"))["text"] == good_text

    # ...and replay of the surviving recording still passes validation.
    replayed = ReplayProvider(replay_dirs=[replay_dir]).explain(
        received, ENROLLMENT_ANALYST
    )
    assert replayed.text == good_text
    validate_explanation(replayed.text, received)

    # CABINET_RECORD=1 (not overwrite) keeps the existing file on success too.
    monkeypatch.setenv("CABINET_RECORD", "1")
    ok = RecordingProvider(
        StubProvider("Registration is down 4.8 % [M1]."), replay_dir=replay_dir
    )
    assert run_enrollment_analyst(findings_obj, ok, log).available is True
    assert json.loads(path.read_text(encoding="utf-8"))["text"] == good_text

    # Only overwrite + validated output replaces it.
    monkeypatch.setenv("CABINET_RECORD", "overwrite")
    assert run_enrollment_analyst(findings_obj, ok, log).available is True
    assert (
        json.loads(path.read_text(encoding="utf-8"))["text"]
        == "Registration is down 4.8 % [M1]."
    )


def test_every_replay_cache_file_passes_the_numeral_test(
    findings_obj: dict[str, Any],
) -> None:
    """(c) Every recording in var/replay/ and the golden run in data/golden/
    must validate against the findings its role received. Roles come from
    ROLE_FINDINGS; an unknown role in a recording is a failure, not a skip.
    A Chief of Staff recording holds the two-section JSON and is checked by
    the chief validator — the same claim/numeral rules, plus the JSON-only
    and sentence-cap rules."""
    from cabinet.analysts import CHIEF_OF_STAFF, validate_chief_output

    files: list[Path] = []
    for directory in (DEFAULT_REPLAY_DIR, GOLDEN_REPLAY_DIR):
        if directory.exists():
            files.extend(sorted(directory.glob("*.json")))
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        role = data["role"]
        received = findings_for_role(role, findings_obj)
        if role == CHIEF_OF_STAFF:
            validate_chief_output(data["text"], received)
        else:
            validate_explanation(data["text"], received)


# --- the API --------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return make_authenticated_client(create_app())


def test_briefing_enrollment_with_fake_provider(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    response = client.get("/briefing/enrollment")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["provider"] == "fake"
    assert body["model_label"]
    assert "model" not in body  # the model id is never exposed
    assert isinstance(body["text"], str) and body["text"]
    assert body["claims"], "expected at least one claim"
    for claim in body["claims"]:
        assert claim["text"]
        assert claim["finding_ids"]
        assert set(claim["finding_ids"]) <= {"M1", "M2", "M7"}

    granted = client.get("/events", params={"type": "data.granted"}).json()["events"]
    assert granted[-1]["payload"]["granted_fields"] == ENROLLMENT_FIELDS
    produced = client.get("/events", params={"type": "finding.produced"}).json()[
        "events"
    ]
    assert produced[-1]["payload"]["findings"] == ["M1", "M2", "M7"]


def test_briefing_enrollment_without_config_is_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The live path fails cleanly: no CABINET_LLM_* configuration -> 503
    {available: false, reason}; the reason names only CABINET_* variables."""
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
    monkeypatch.setenv("CABINET_PROVIDER", "chat")
    response = client.get("/briefing/enrollment")
    assert response.status_code == 503
    body = response.json()
    assert body["available"] is False
    assert "CABINET_LLM_BASE_URL" in body["reason"]
    assert "CABINET_LLM_API_KEY" in body["reason"]
    # Metrics and evidence still work while the model is unavailable.
    assert client.get("/findings").status_code == 200
    assert client.get("/events").status_code == 200


def test_briefing_enrollment_replay_roundtrip(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Record through the API with the fake provider, then replay it."""
    replay_dir = tmp_path / "replay"
    monkeypatch.setenv("CABINET_REPLAY_DIR", str(replay_dir))
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    monkeypatch.setenv("CABINET_RECORD", "1")
    recorded = client.get("/briefing/enrollment")
    assert recorded.status_code == 200
    assert recorded.json()["recorded"] is False

    monkeypatch.setenv("CABINET_PROVIDER", "replay")
    monkeypatch.delenv("CABINET_RECORD", raising=False)
    replayed = client.get("/briefing/enrollment")
    assert replayed.status_code == 200
    assert replayed.json()["text"] == recorded.json()["text"]
    assert replayed.json()["recorded"] is True
    assert replayed.json()["provider"] == "fake"


def test_briefing_enrollment_replay_miss_is_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CABINET_PROVIDER", "replay")
    monkeypatch.setenv("CABINET_REPLAY_DIR", str(tmp_path / "empty"))
    # Hermetic against a populated local var/replay (e.g. after a demo run).
    monkeypatch.setattr("cabinet.provider.DEFAULT_REPLAY_DIR", tmp_path / "no-var")
    monkeypatch.setattr(
        "cabinet.provider.GOLDEN_REPLAY_DIR", tmp_path / "no-golden"
    )
    response = client.get("/briefing/enrollment")
    assert response.status_code == 503
    assert response.json()["available"] is False
    assert "no recorded response" in response.json()["reason"]


def test_briefing_enrollment_falls_back_to_the_golden_dir(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    findings_obj: dict[str, Any],
) -> None:
    """REPLAY mode reads the committed golden run in data/golden/ when
    var/replay (and CABINET_REPLAY_DIR) have no match."""
    received = result_claims_findings(findings_obj)
    golden = tmp_path / "golden"
    golden.mkdir(exist_ok=True)
    (golden / replay_filename(received, ENROLLMENT_ANALYST)).write_text(
        json.dumps(
            {
                "provider": "chat",
                "model_label": "live model",
                "role": ENROLLMENT_ANALYST,
                "text": "Registration is down 4.8 % [M1].",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("cabinet.provider.GOLDEN_REPLAY_DIR", golden)
    monkeypatch.setenv("CABINET_REPLAY_DIR", str(tmp_path / "empty"))
    # Hermetic against a populated local var/replay (e.g. after a demo run).
    monkeypatch.setattr("cabinet.provider.DEFAULT_REPLAY_DIR", tmp_path / "no-var")
    monkeypatch.setenv("CABINET_PROVIDER", "replay")

    response = client.get("/briefing/enrollment")
    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "Registration is down 4.8 % [M1]."
    assert body["recorded"] is True
    assert body["provider"] == "chat"
    assert body["model_label"] == "live model"
