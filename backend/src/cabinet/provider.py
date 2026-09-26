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

  - ``CABINET_LLM_BASE_URL`` — endpoint base, e.g. ``https://your-endpoint.example/v1``.
  - ``CABINET_LLM_MODEL`` — the model id sent to the endpoint. It is never
    logged, recorded, or returned to the UI; responses carry the label instead.
  - ``CABINET_LLM_REASONING_EFFORT`` — sent as ``reasoning_effort`` (default
    ``low``; set it empty to omit the field). Reasoning models otherwise spend
    the whole output budget thinking and answer nothing.
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

ENV_PROVIDER = "CABINET_PROVIDER"
ENV_RECORD = "CABINET_RECORD"
ENV_REPLAY_DIR = "CABINET_REPLAY_DIR"
ENV_GOLDEN_DIR = "CABINET_GOLDEN_DIR"
ENV_LLM_BASE_URL = "CABINET_LLM_BASE_URL"
ENV_LLM_MODEL = "CABINET_LLM_MODEL"
ENV_LLM_LABEL = "CABINET_LLM_LABEL"
ENV_LLM_REASONING_EFFORT = "CABINET_LLM_REASONING_EFFORT"
DEFAULT_REASONING_EFFORT = "low"
ENV_LLM_API_KEY = "CABINET_LLM_API_KEY"
ENV_LLM_API_KEY_FILE = "CABINET_LLM_API_KEY_FILE"
ENV_LLM_API_KEY_VAR = "CABINET_LLM_API_KEY_VAR"
ENV_LOCAL_ENV = "CABINET_LOCAL_ENV"

DEFAULT_LABEL = "live model"

_LOCAL_ENV_KEY_RE = re.compile(r"CABINET_[A-Z0-9_]+")


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

        system, user = build_prompt(findings, role)
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            **_reasoning_effort_field(),
            "max_tokens": 2048,
        }
        url = base_url.rstrip("/") + "/chat/completions"
        data = self._post_with_retry(url, key, payload)

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
        self, url: str, key: str, payload: dict[str, Any]
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
                with urllib.request.urlopen(
                    request, timeout=CHAT_TIMEOUT_SECONDS
                ) as response:
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
                    f"the chat endpoint did not answer within "
                    f"{CHAT_TIMEOUT_SECONDS} s",
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
