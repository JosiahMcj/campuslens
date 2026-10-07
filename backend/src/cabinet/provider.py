"""Model provider interface (ROADMAP §3 layers 4-5, D5).

One interface — ``explain(findings, role) -> Explanation`` — with the provider
behind it. The code names no vendor: any OpenAI-compatible chat-completions
endpoint works. Implementations:

- :class:`ChatProvider` — the default. POSTs ``{base_url}/chat/completions``
  with a 55 s timeout, at most one retry after a 2 s pause (429, 5xx, and
  connection errors only — a timeout is never retried, so one call stays
  within ~60 s of wall time), accepts only ``finish_reason == "stop"``,
  treats a 200 whose body is not a JSON object as unavailability (never a
  500), strips a ``<think>...</think>`` block, and redacts the key from
  every error. Configuration comes only from
  environment variables or a gitignored ``cabinet.local.env`` at the repo root
  (``KEY=VALUE`` lines, loaded at startup, the real environment wins):

  - ``CABINET_LLM_BASE_URL`` — endpoint base, e.g. ``https://<provider host>/v1``.
    In production it must be ``https`` or a loopback address (127.0.0.1,
    localhost, ::1, for a self-hosted model), or startup is refused
    (:func:`check_production_llm_base_url`). Redirects are never followed, so
    the key is never forwarded to another host.
  - ``CABINET_LLM_MODEL`` — the model id sent to the endpoint. It is never
    logged, recorded, or returned to the UI; responses carry the label instead.
  - ``CABINET_LLM_REASONING_EFFORT`` — sent as ``reasoning_effort`` (default
    ``low``; set it empty to omit the field). Reasoning models otherwise spend
    the whole output budget thinking and answer nothing. Some endpoints
    ignore ``low`` and honour only ``none``, which turns hidden reasoning
    off. Nothing beyond the setting's value is ever sent.
  - ``CABINET_LLM_MAX_TOKENS`` — the output budget per call, sent as
    ``max_tokens`` (default 2048, a whole number from 256 to 32768). A
    thinking model's hidden reasoning counts against it, and running out is
    ``finish_reason 'length'``, which is unavailability, never a half answer.
  - ``CABINET_EXPLORE_PLANNER_TIMEOUT`` — seconds the explore planner waits
    for its plan (default 20, a number from 3 to 55). Explore falls back to
    its rule planner when the model does not answer in time, so this call
    gets a shorter budget than the 55 s every other call has.
  - ``CABINET_LLM_LABEL`` — what the UI shows as the source (default
    ``"live model"``).
  - ``CABINET_LLM_API_KEY`` — the key; or ``CABINET_LLM_API_KEY_FILE`` +
    ``CABINET_LLM_API_KEY_VAR`` to read that one variable's line from another
    env file (no other line of that file is ever read).

  Missing configuration is typed unavailability whose reason names only the
  ``CABINET_*`` variables; the API answers 503.
- :class:`FakeProvider` — deterministic, builds its text only from the
  findings it receives. Used by tests and offline development.
- :class:`ReplayProvider` — serves recorded responses so the demo runs
  identically with the network down. Search order: ``CABINET_REPLAY_DIR`` if
  set, then ``var/replay/``, then the committed golden run in
  ``data/golden/``. A miss everywhere is :class:`ProviderUnavailable`; it
  never touches the network.
- :class:`RecordingProvider` — wraps the selected provider when
  ``CABINET_RECORD=1``. Recording is deliberately separate from ``explain``:
  the analyst runner calls :meth:`RecordingProvider.save` only after the
  response has passed validation, so an invalid live answer can never
  overwrite a good recording. An existing recording is never overwritten
  unless ``CABINET_RECORD=overwrite``.

Other environment configuration:

- ``CABINET_PROVIDER`` — ``chat`` (default) | ``replay`` | ``fake``.
- ``CABINET_RECORD`` — ``1`` wraps the selected provider in the recorder;
  ``overwrite`` also replaces existing recordings.
- ``CABINET_REPLAY_DIR`` — extra replay cache directory, searched first
  (default write target for recordings: ``var/replay/``).

Validation retry: a provider may also offer ``correct(findings, role,
correction)``, the same call plus one extra user message stating why its
previous answer failed validation. The analyst and Chief of Staff runners use
it for at most ``CABINET_VALIDATION_RETRIES`` corrective tries (see
``cabinet.analysts``). :class:`ChatProvider` and :class:`FakeProvider` offer it.
:class:`ReplayProvider` does not, by design: a recording was validated when it
was made, so replay never takes the retry path.

Adding a provider (the D5 seam): write a class that satisfies
:class:`Provider` — a ``name``/``model_label`` pair plus
``explain(findings, role)`` that raises :class:`ProviderUnavailable` instead of
faking a response — and register it in :data:`_PROVIDERS` below. Select it
with ``CABINET_PROVIDER=<name>``. Nothing else changes: the analysts, the
validator, the API, and the replay/record path are provider-agnostic.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REPLAY_DIR = REPO_ROOT / "var" / "replay"
GOLDEN_REPLAY_DIR = REPO_ROOT / "data" / "golden"
LOCAL_ENV_PATH = REPO_ROOT / "cabinet.local.env"

# The endpoint gateway may cut long requests server-side; stay well under a
# minute per attempt. A retry happens only on fast failures (429/5xx/refused
# connection) after a short pause — a timeout already spent the whole budget,
# so it is never retried and one explain stays within ~60 s of wall time.
CHAT_TIMEOUT_SECONDS = 55
RETRY_DELAY_SECONDS = 2
# The explore planner's own budget: a plan the model has not written in this
# time is abandoned for the rule planner, so the person is not kept waiting.
ENV_EXPLORE_PLANNER_TIMEOUT = "CABINET_EXPLORE_PLANNER_TIMEOUT"
DEFAULT_PLANNER_TIMEOUT_SECONDS = 20.0
MIN_PLANNER_TIMEOUT_SECONDS = 3.0

ENV_PROVIDER = "CABINET_PROVIDER"
ENV_RECORD = "CABINET_RECORD"
ENV_REPLAY_DIR = "CABINET_REPLAY_DIR"
ENV_GOLDEN_DIR = "CABINET_GOLDEN_DIR"
ENV_LLM_BASE_URL = "CABINET_LLM_BASE_URL"
ENV_LLM_MODEL = "CABINET_LLM_MODEL"
ENV_LLM_LABEL = "CABINET_LLM_LABEL"
ENV_LLM_REASONING_EFFORT = "CABINET_LLM_REASONING_EFFORT"
DEFAULT_REASONING_EFFORT = "low"
ENV_LLM_MAX_TOKENS = "CABINET_LLM_MAX_TOKENS"
DEFAULT_MAX_TOKENS = 2048
MIN_MAX_TOKENS = 256
MAX_MAX_TOKENS = 32768
ENV_LLM_API_KEY = "CABINET_LLM_API_KEY"
ENV_LLM_API_KEY_FILE = "CABINET_LLM_API_KEY_FILE"
ENV_LLM_API_KEY_VAR = "CABINET_LLM_API_KEY_VAR"
ENV_LOCAL_ENV = "CABINET_LOCAL_ENV"

DEFAULT_LABEL = "live model"

_LOCAL_ENV_KEY_RE = re.compile(r"CABINET_[A-Z0-9_]+")


# Plain http is allowed only to these hosts (a self-hosted model on the same
# machine); anything else must be https.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def check_production_llm_base_url() -> None:
    """Fail closed on the model endpoint in production.

    ``CABINET_LLM_BASE_URL``, when set, must be ``https`` or plain ``http``
    to a loopback host, so the key and the findings never cross a network in
    the clear. Unset is allowed (replay, or the model is not configured yet;
    asks then answer unavailable). Raises ``RuntimeError`` with one line.
    """
    raw = os.environ.get(ENV_LLM_BASE_URL, "").strip()
    if not raw:
        return
    try:
        parts = urllib.parse.urlsplit(raw)
        host = parts.hostname
    except ValueError:
        host = None
        parts = None
    if parts is not None and host:
        if parts.scheme == "https":
            return
        if parts.scheme == "http" and host.lower() in LOOPBACK_HOSTS:
            return
    raise RuntimeError(
        f"{ENV_LLM_BASE_URL} must be an https URL or a loopback address "
        "(127.0.0.1, localhost, ::1) when CABINET_ENV=production, so the key "
        "and the findings never travel in the clear; refusing to start"
    )


class _RedirectRefused(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect from the model endpoint: following one would
    send the Authorization header (the key) and the findings to whatever
    host the redirect names."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        raise ProviderUnavailable(
            f"the chat endpoint answered a redirect (HTTP {code}); redirects "
            "are never followed, so the key is never sent to another host. "
            f"Set {ENV_LLM_BASE_URL} to the endpoint's final address",
            provider="chat",
        )


_NO_REDIRECT_OPENER = urllib.request.build_opener(_RedirectRefused())


def _urlopen(request: urllib.request.Request, timeout: float) -> Any:
    """The model client's only network call: urllib with redirects refused."""
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


class ProviderUnavailable(Exception):
    """Typed unavailability: the API turns this into a 503, never a fake."""

    def __init__(self, reason: str, *, provider: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.provider = provider


@dataclass(frozen=True)
class Explanation:
    """One provider response. ``recorded=True`` means it came from the replay
    cache; ``provider``/``model_label`` then name what made the original
    recording. The model id itself is never carried here."""

    text: str
    provider: str
    model_label: str
    recorded: bool = False
    # Set only by the replay provider when the file carries a re-key marker:
    # the sha256 key of the recording this text was first validated under.
    rekeyed_from: str | None = None


class Provider(Protocol):
    """The one interface (ROADMAP §3): explain the findings a role received."""

    @property
    def name(self) -> str: ...

    @property
    def model_label(self) -> str: ...

    def explain(self, findings: dict[str, Any], role: str) -> Explanation:
        """Return an explanation, or raise :class:`ProviderUnavailable`."""
        ...


def _reasoning_effort_field() -> dict[str, str]:
    """``{"reasoning_effort": ...}`` for the request body, or ``{}`` when the
    setting is empty. Default ``low``: on reasoning models the default effort
    can consume the entire output budget as hidden reasoning."""
    value = os.environ.get(ENV_LLM_REASONING_EFFORT, DEFAULT_REASONING_EFFORT).strip()
    return {"reasoning_effort": value} if value else {}


def timeout_for_role(role: str) -> float:
    """Seconds one attempt may take: ``CABINET_EXPLORE_PLANNER_TIMEOUT``
    (default 20) for the explore planner, 55 for every other role. A value
    that is not a number from 3 to 55 is logged and read as the default."""
    if role != "explore_planner":
        return float(CHAT_TIMEOUT_SECONDS)
    raw = os.environ.get(ENV_EXPLORE_PLANNER_TIMEOUT, "").strip()
    if not raw:
        return DEFAULT_PLANNER_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        value = -1.0
    if not MIN_PLANNER_TIMEOUT_SECONDS <= value <= CHAT_TIMEOUT_SECONDS:
        logger.warning(
            "%s=%r is not a number from %g to %d; using %g",
            ENV_EXPLORE_PLANNER_TIMEOUT,
            raw,
            MIN_PLANNER_TIMEOUT_SECONDS,
            CHAT_TIMEOUT_SECONDS,
            DEFAULT_PLANNER_TIMEOUT_SECONDS,
        )
        return DEFAULT_PLANNER_TIMEOUT_SECONDS
    return value


def max_tokens_from_env() -> int:
    """``CABINET_LLM_MAX_TOKENS`` read at call time, the one place the output
    budget is decided. Unset or empty gives the default; a value that is not a
    whole number in range is logged and the default is used."""
    raw = os.environ.get(ENV_LLM_MAX_TOKENS, "").strip()
    if not raw:
        return DEFAULT_MAX_TOKENS
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if not MIN_MAX_TOKENS <= value <= MAX_MAX_TOKENS:
        logger.warning(
            "%s=%r is not a whole number from %d to %d; using %d",
            ENV_LLM_MAX_TOKENS,
            raw,
            MIN_MAX_TOKENS,
            MAX_MAX_TOKENS,
            DEFAULT_MAX_TOKENS,
        )
        return DEFAULT_MAX_TOKENS
    return value


def load_local_env(path: Path | None = None) -> None:
    """Load ``KEY=VALUE`` lines from the gitignored local env file.

    The file is ``path`` when given, else ``CABINET_LOCAL_ENV`` when set
    (service managers that cannot source a file, like launchd, point at it
    that way), else ``cabinet.local.env`` at the repo root. Only
    ``CABINET_*`` keys are honored, and variables already present in the
    real environment win (``setdefault``). Missing file is fine. Never raises.
    """
    if path is not None:
        env_path = path
    else:
        override = os.environ.get(ENV_LOCAL_ENV, "").strip()
        env_path = Path(override) if override else LOCAL_ENV_PATH
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not _LOCAL_ENV_KEY_RE.fullmatch(key):
            continue
        os.environ.setdefault(key, value.strip().strip("\"'").strip())


def replay_dir_from_env() -> Path:
    """The write target for recordings: CABINET_REPLAY_DIR, else var/replay/."""
    override = os.environ.get(ENV_REPLAY_DIR)
    return Path(override) if override else DEFAULT_REPLAY_DIR


def golden_dir_from_env() -> Path:
    """The committed golden-run directory: CABINET_GOLDEN_DIR (tests point it
    at an empty directory), else data/golden/."""
    override = os.environ.get(ENV_GOLDEN_DIR)
    return Path(override) if override else GOLDEN_REPLAY_DIR


def replay_dirs_from_env() -> list[Path]:
    """The replay search path, in order: CABINET_REPLAY_DIR if set, then
    var/replay/, then the committed golden run in data/golden/."""
    candidates: list[Path] = []
    override = os.environ.get(ENV_REPLAY_DIR)
    if override:
        candidates.append(Path(override))
    candidates.append(DEFAULT_REPLAY_DIR)
    candidates.append(golden_dir_from_env())
    dirs: list[Path] = []
    for candidate in candidates:
        if candidate not in dirs:
            dirs.append(candidate)
    return dirs


def canonical_findings_json(findings: dict[str, Any]) -> str:
    """Canonical serialization of the received findings; the replay key hash."""
    return json.dumps(findings, sort_keys=True, separators=(",", ":"))


def replay_filename(findings: dict[str, Any], role: str) -> str:
    canonical = canonical_findings_json(findings)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{role}-{digest}.json"


def recording_payload(
    findings: dict[str, Any], role: str, explanation: Explanation
) -> dict[str, Any]:
    """The recording file's JSON shape, shared by :meth:`RecordingProvider.save`
    and ``record_golden`` so a golden file is byte-for-byte the same shape as a
    replay-cache recording, whoever writes it."""
    return {
        "provider": explanation.provider,
        "model_label": explanation.model_label,
        "role": role,
        "findings_sha256": hashlib.sha256(
            canonical_findings_json(findings).encode("utf-8")
        ).hexdigest(),
        "recorded_at": datetime.now(UTC).isoformat(),
        "text": explanation.text,
    }


class ChatProvider:
    """Any OpenAI-compatible chat-completions endpoint, stdlib urllib only.

    Configuration comes from the CABINET_LLM_* variables (see the module
    docstring). The key is never logged, returned, or written anywhere, and
    error text is redacted against it. A ``finish_reason`` other than
    ``stop`` (truncation, content filter) is typed unavailability, never a
    half answer. A ``<think>...</think>`` block (reasoning models sometimes
    emit one) is stripped before returning; the text is otherwise untouched —
    the validator decides.
    """

    name = "chat"

    @property
    def model_label(self) -> str:
        return os.environ.get(ENV_LLM_LABEL, DEFAULT_LABEL)

    def explain(self, findings: dict[str, Any], role: str) -> Explanation:
        return self._complete(findings, role, correction=None)

    def correct(
        self, findings: dict[str, Any], role: str, correction: str
    ) -> Explanation:
        """The corrective second try: the same system and user messages as
        :meth:`explain`, plus one more user message carrying ``correction``
        (the validator's reason in plain words). The rejected answer is not
        sent back, and nothing but the correction is added."""
        return self._complete(findings, role, correction=correction)

    def _complete(
        self, findings: dict[str, Any], role: str, *, correction: str | None
    ) -> Explanation:
        base_url = os.environ.get(ENV_LLM_BASE_URL, "").strip()
        model = os.environ.get(ENV_LLM_MODEL, "").strip()
        key = self._resolve_key()
        missing: list[str] = []
        if not base_url:
            missing.append(ENV_LLM_BASE_URL)
        if not model:
            missing.append(ENV_LLM_MODEL)
        if not key:
            missing.append(
                f"{ENV_LLM_API_KEY} (or {ENV_LLM_API_KEY_FILE} + {ENV_LLM_API_KEY_VAR})"
            )
        if missing:
            raise ProviderUnavailable(
                "the live model is not configured: set "
                + ", ".join(missing)
                + " (environment or cabinet.local.env). "
                "Run with CABINET_PROVIDER=replay (recorded responses) or "
                "CABINET_PROVIDER=fake (deterministic stub) instead.",
                provider=self.name,
            )
        assert base_url and model and key  # narrowing for the type checker

        from cabinet.analysts import build_prompt
        from cabinet.permissions import coarsen_small_counts

        # A live call never carries an M5 office count or an M8 indicator
        # count under 10: those read "fewer than 10" in the prompt. Only the
        # prompt changes; the received findings (the replay key and what the
        # validator checks against) are untouched.
        # Explore's payloads are its own (aggregate tables, cabinet.explore)
        # and pass through unchanged.
        prompt_findings = (
            findings if role.startswith("explore_") else coarsen_small_counts(findings)
        )
        system, user = build_prompt(prompt_findings, role)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        if correction is not None:
            messages.append({"role": "user", "content": correction})
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.2,
            **_reasoning_effort_field(),
            "max_tokens": max_tokens_from_env(),
        }
        url = base_url.rstrip("/") + "/chat/completions"
        data = self._post_with_retry(url, key, payload, timeout_for_role(role))

        choices = data.get("choices")
        if not choices:
            raise ProviderUnavailable(
                "the chat endpoint's response had no choices", provider=self.name
            )
        choice = choices[0]
        finish_reason = choice.get("finish_reason")
        if finish_reason != "stop":
            raise ProviderUnavailable(
                f"the model stopped with finish_reason {finish_reason!r} "
                "instead of 'stop'; the answer is incomplete and was discarded",
                provider=self.name,
            )
        content = choice.get("message", {}).get("content")
        if not isinstance(content, str):
            raise ProviderUnavailable(
                "the chat endpoint's response had no message content",
                provider=self.name,
            )
        return Explanation(
            text=self._strip_think(content),
            provider=self.name,
            model_label=self.model_label,
        )

    @staticmethod
    def _resolve_key() -> str | None:
        key = os.environ.get(ENV_LLM_API_KEY)
        if key:
            return key
        file_var = os.environ.get(ENV_LLM_API_KEY_FILE)
        var_name = os.environ.get(ENV_LLM_API_KEY_VAR)
        if not (file_var and var_name):
            return None
        prefix = f"{var_name}="
        try:
            # Read line by line and stop at the first match, so no line after
            # the key's own line is ever read into memory.
            with Path(file_var).expanduser().open(encoding="utf-8") as handle:
                for raw in handle:
                    line = raw.strip()
                    if line.startswith(prefix):
                        value = line.split("=", 1)[1].strip().strip("\"'").strip()
                        return value or None
        except OSError:
            return None
        return None

    @staticmethod
    def _redact(text: str, key: str) -> str:
        """Guarantee the key never appears in an error message."""
        return text.replace(key, "***") if key else text

    @staticmethod
    def _strip_think(text: str) -> str:
        if "<think>" not in text:
            return text
        return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    def _post_with_retry(
        self,
        url: str,
        key: str,
        payload: dict[str, Any],
        timeout: float = CHAT_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """One POST plus at most one retry, after a 2 s pause.

        Retried: HTTP 429/5xx and connection errors (refused, dropped before
        a response). Never retried: timeouts — one 55 s attempt already
        spends the wall-time budget — and a 200 whose body is not a JSON
        object (a gateway's HTML error page, a connection dropped mid-body),
        which is typed unavailability with a plain reason, never a 500.
        """
        body = json.dumps(payload).encode("utf-8")
        for attempt in (0, 1):
            retryable = False
            try:
                request = urllib.request.Request(
                    url,
                    data=body,
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with _urlopen(request, timeout=timeout) as response:
                    try:
                        parsed: Any = json.loads(response.read().decode("utf-8"))
                    # ValueError covers JSONDecodeError and UnicodeDecodeError;
                    # ConnectionError/OSError/HTTPException cover a connection
                    # dropped while the body is read.
                    except (
                        ValueError,
                        ConnectionError,
                        OSError,
                        http.client.HTTPException,
                    ) as exc:
                        raise ProviderUnavailable(
                            self._redact(
                                "the chat endpoint returned an unreadable "
                                f"response: {exc}",
                                key,
                            ),
                            provider=self.name,
                        ) from exc
                    if not isinstance(parsed, dict):
                        raise ProviderUnavailable(
                            "the chat endpoint's response was not a JSON object",
                            provider=self.name,
                        )
                    return parsed
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                reason = self._redact(f"chat endpoint HTTP {exc.code}: {detail}", key)
                retryable = exc.code == 429 or exc.code >= 500
            except TimeoutError as exc:
                raise ProviderUnavailable(
                    f"the chat endpoint did not answer within {timeout:g} s",
                    provider=self.name,
                ) from exc
            except urllib.error.URLError as exc:
                reason = self._redact(
                    f"chat endpoint connection error: {exc.reason}", key
                )
                # A connect-phase timeout surfaces as URLError(reason=
                # TimeoutError) and, like a read timeout, is never retried.
                retryable = not isinstance(exc.reason, TimeoutError)
            if not retryable or attempt == 1:
                raise ProviderUnavailable(reason, provider=self.name)
            time.sleep(RETRY_DELAY_SECONDS)
        raise AssertionError("unreachable")  # pragma: no cover


class FakeProvider:
    """Deterministic stub: text built only from the findings it receives.

    Each finding becomes one claim of the form ``<title>: <display> [<id>]``
    using only numerals already present in the finding's value/display, so the
    numeral validator passes by construction. A table finding (M5: a list of
    per-office rows) becomes one claim from its per-office counts instead of
    its display string. For the Chief of Staff the received payload is the
    ``chief_received`` object, and the stub answers with the two-section JSON
    object, again built only from the findings' displays.
    """

    name = "fake"

    def __init__(self, model_label: str = "test stub") -> None:
        self.model_label = model_label

    def explain(self, findings: dict[str, Any], role: str) -> Explanation:
        import re

        if role == "chief_of_staff":
            return Explanation(
                text=self._chief_text(findings), provider=self.name,
                model_label=self.model_label,
            )

        sentences: list[str] = []
        # Only finding keys (M1 … M7) become claims; a non-default question's
        # QUESTION_KEY entry in the received payload is context, not a
        # finding, and never gets a claim.
        for finding_id in sorted(k for k in findings if re.fullmatch(r"M\d+", k)):
            finding = findings[finding_id]
            title = str(finding.get("title", finding_id))
            # Titles may embed numbers from other findings' contracts (e.g.
            # "Of M2, financial hold under $1,000"); drop the cross-references
            # and digits so this stub only ever emits numerals from
            # value/display.
            title = re.sub(r"\bM\d+\b", "", title)
            title = re.sub(r"\$?\d[\d,]*", "", title)
            title = re.sub(r"\bOf\s*,", "In the group,", title)
            title = re.sub(r"\bunder\s*$", "", title)
            title = " ".join(title.split()).strip(" ,;:–—-") or str(
                finding.get("title", finding_id)
            )
            rows = finding.get("value")
            if (
                isinstance(rows, list)
                and rows
                and all(
                    isinstance(row, dict) and "office" in row and "count" in row
                    for row in rows
                )
            ):
                per_office = ", ".join(
                    f"{row['office']} {row['count']}" for row in rows
                )
                sentences.append(f"{title}: {per_office} [{finding_id}].")
                continue
            display = str(finding.get("display", "--"))
            if display == "--":
                sentences.append(
                    f"{title}: no value available [{finding_id}]."
                )
            else:
                sentences.append(f"{title}: {display} [{finding_id}].")
        return Explanation(
            text=" ".join(sentences), provider=self.name, model_label=self.model_label
        )

    def correct(
        self, findings: dict[str, Any], role: str, correction: str
    ) -> Explanation:
        """The corrective second try. The stub's answers are valid by
        construction, so it answers exactly as :meth:`explain` does."""
        return self.explain(findings, role)

    @staticmethod
    def _chief_text(received: dict[str, Any]) -> str:
        """The Chief of Staff's two-section JSON, built from the aggregate
        findings' displays only (never from the analyst explanations)."""
        findings = received["findings"]

        def display(finding_id: str) -> str:
            finding = findings.get(finding_id)
            if not isinstance(finding, dict):
                return "--"
            return str(finding.get("display", "--"))

        summary: list[str] = []
        if display("M1") != "--":
            summary.append(
                f"Spring registration is {display('M1')} versus the same "
                "point last year [M1]."
            )
        if display("M2") != "--":
            summary.append(
                f"{display('M2')} continuing students have not yet "
                "registered — people who may need support [M2]."
            )
        m6 = findings.get("M6")
        if isinstance(m6, dict) and m6.get("closed") is True:
            summary.append("Registration for the spring term has closed [M6].")
        elif display("M6") != "--":
            summary.append(f"Registration closes in {display('M6')} days [M6].")
        limitations_parts: list[str] = []
        if "M2" in findings:
            limitations_parts.append(
                "All records are fictional demonstration data, and the as-of "
                "date comes from the data, not today's date [M2]."
            )
        if "M7" in findings:
            limitations_parts.append(
                "Registered credit hours versus the prior year is an "
                "optional measure [M7]."
            )
        # A question whose chief dispatch has neither M2 nor M7 still gets a
        # true limitation, citing a finding the chief actually received.
        limitations = " ".join(limitations_parts) or (
            "All records are fictional demonstration data "
            f"[{sorted(findings)[0]}]."
        )
        return json.dumps(
            {
                "executive_summary": " ".join(summary)
                or "No findings are available for this briefing [M2].",
                "limitations": limitations,
            },
            ensure_ascii=False,
        )


class ReplayProvider:
    """Serves recorded responses from the replay search path. Never the network.

    Search order: ``CABINET_REPLAY_DIR`` if set, then ``var/replay/``, then the
    committed golden run in ``data/golden/``; the first match wins.
    """

    name = "replay"

    def __init__(self, replay_dirs: Sequence[Path] | None = None) -> None:
        self.replay_dirs = (
            list(replay_dirs) if replay_dirs is not None else replay_dirs_from_env()
        )
        self.model_label = "replay"

    def explain(self, findings: dict[str, Any], role: str) -> Explanation:
        filename = replay_filename(findings, role)
        for directory in self.replay_dirs:
            path = directory / filename
            if path.exists():
                break
        else:
            searched = ", ".join(str(d) for d in self.replay_dirs)
            raise ProviderUnavailable(
                f"no recorded response for role {role!r} (searched {searched}); "
                "record one with CABINET_RECORD=1 against a live or fake "
                "provider, or add the golden run with `make record-golden`",
                provider=self.name,
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        return Explanation(
            text=data["text"],
            provider=str(data.get("provider", "unknown")),
            model_label=str(data.get("model_label", data.get("model", "unknown"))),
            recorded=True,
            rekeyed_from=(
                str(data["rekeyed_from"]) if data.get("rekeyed_from") else None
            ),
        )


class RecordingProvider:
    """Wraps a provider; :meth:`save` writes a *validated* response.

    ``explain`` deliberately does NOT record: the analyst runner calls
    :meth:`save` only after the output has passed validation, so an invalid
    live answer can never overwrite a good recording. An existing recording is
    kept unless ``CABINET_RECORD=overwrite``.
    """

    def __init__(self, inner: Provider, replay_dir: Path | None = None) -> None:
        self.inner = inner
        self.replay_dir = replay_dir or replay_dir_from_env()

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def model_label(self) -> str:
        return self.inner.model_label

    def explain(self, findings: dict[str, Any], role: str) -> Explanation:
        return self.inner.explain(findings, role)

    @property
    def can_correct(self) -> bool:
        """Whether the wrapped provider takes a corrective second try."""
        return callable(getattr(self.inner, "correct", None))

    def correct(
        self, findings: dict[str, Any], role: str, correction: str
    ) -> Explanation:
        """Forward the corrective try. Like :meth:`explain`, it never
        records: the runner saves only the answer that passed validation,
        keyed to ``findings`` without the correction."""
        inner_correct = getattr(self.inner, "correct", None)
        if not callable(inner_correct):
            raise ProviderUnavailable(
                f"provider {self.inner.name!r} takes no corrective try",
                provider=self.inner.name,
            )
        result: Explanation = inner_correct(findings, role, correction)
        return result

    def save(
        self, findings: dict[str, Any], role: str, explanation: Explanation
    ) -> Path | None:
        """Write one validated response to the replay cache.

        Returns the path written, or ``None`` when an existing recording was
        kept (``CABINET_RECORD=overwrite`` replaces it).
        """
        self.replay_dir.mkdir(parents=True, exist_ok=True)
        path = self.replay_dir / replay_filename(findings, role)
        if path.exists() and os.environ.get(ENV_RECORD) != "overwrite":
            logger.info(
                "keeping existing recording %s "
                "(set CABINET_RECORD=overwrite to replace it)",
                path,
            )
            return None
        payload = recording_payload(findings, role, explanation)
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        logger.info("recorded %s response for role %s to %s", self.name, role, path)
        return path


# The D5 seam: provider name -> class. To add a provider, implement the
# Provider protocol and add one entry here.
_PROVIDERS: dict[str, type] = {
    "chat": ChatProvider,
    "fake": FakeProvider,
    "replay": ReplayProvider,
}


def provider_from_env() -> Provider:
    """Build the configured provider: CABINET_PROVIDER (default ``chat``),
    wrapped by the recorder when CABINET_RECORD=1|overwrite (recording replay
    output would be pointless). Loads ``cabinet.local.env`` first."""
    load_local_env()
    name = os.environ.get(ENV_PROVIDER, "chat")
    provider_cls = _PROVIDERS.get(name)
    if provider_cls is None:
        raise ValueError(
            f"unknown {ENV_PROVIDER} {name!r}; expected one of "
            f"{', '.join(sorted(_PROVIDERS))}"
        )
    provider: Provider = provider_cls()
    if name != "replay" and os.environ.get(ENV_RECORD) in ("1", "overwrite"):
        return RecordingProvider(provider)
    return provider
