"""Tests for the chat provider (any OpenAI-compatible chat-completions endpoint).

All tests run against a fake ``urllib.request.urlopen`` — no network, no real
key, no real endpoint (``https://llm.example.invalid`` is reserved-invalid per
RFC 2606). Covers: request shape (URL, auth header, model, messages, timeout),
success, ``finish_reason`` handling, retry policy, configuration resolution
(env, cabinet.local.env, key file + variable, missing), key redaction,
``<think>`` stripping, and the label (never the model id) in outputs.
"""

from __future__ import annotations

import http.client
import io
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from cabinet.provider import (
    ChatProvider,
    ProviderUnavailable,
    RecordingProvider,
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

TEST_KEY = "test-key-12345"
TEST_BASE_URL = "https://llm.example.invalid/v1"
TEST_URL = "https://llm.example.invalid/v1/chat/completions"
TEST_MODEL = "test-model"
SUCCESS_BODY = {
    "choices": [
        {
            "message": {"role": "assistant", "content": "42 students [M2]."},
            "finish_reason": "stop",
        }
    ]
}

_LLM_VARS = (
    "CABINET_LLM_BASE_URL",
    "CABINET_LLM_MODEL",
    "CABINET_LLM_LABEL",
    "CABINET_LLM_API_KEY",
    "CABINET_LLM_API_KEY_FILE",
    "CABINET_LLM_API_KEY_VAR",
)


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: Any) -> None:
        return None


def _http_error(code: int, body: bytes = b"server error") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        TEST_URL,
        code,
        "error",
        {},  # type: ignore[arg-type]
        io.BytesIO(body),
    )


def _install_urlopen(
    monkeypatch: pytest.MonkeyPatch,
    handler: Any,
    calls: list[dict[str, Any]],
) -> None:
    """Replace urllib.request.urlopen with a recording fake (no network)."""

    def fake_urlopen(request: Any, timeout: float | None = None) -> Any:
        calls.append(
            {
                "url": request.full_url,
                "authorization": request.get_header("Authorization"),
                "timeout": timeout,
                "body": json.loads(request.data.decode("utf-8")),
            }
        )
        return handler(request)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolate every test from the real environment and any local env file."""
    for var in _LLM_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("CABINET_RECORD", raising=False)
    monkeypatch.setattr(
        "cabinet.provider.LOCAL_ENV_PATH", tmp_path / "no-cabinet-local-env"
    )
    monkeypatch.setenv("CABINET_LLM_BASE_URL", TEST_BASE_URL)
    monkeypatch.setenv("CABINET_LLM_MODEL", TEST_MODEL)


def test_request_shape_and_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CABINET_LLM_API_KEY", TEST_KEY)
    calls: list[dict[str, Any]] = []
    _install_urlopen(monkeypatch, lambda request: _FakeResponse(SUCCESS_BODY), calls)

    provider = ChatProvider()
    explanation = provider.explain(FINDINGS, "enrollment_analyst")

    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == TEST_URL
    assert call["authorization"] == f"Bearer {TEST_KEY}"
    assert call["timeout"] == 55
    body = call["body"]
    assert body["model"] == TEST_MODEL
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 2048
    assert body["reasoning_effort"] == "low"
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert "M2" in body["messages"][1]["content"]

    assert explanation.text == "42 students [M2]."
    assert explanation.provider == "chat"
    assert explanation.model_label == "live model"  # the label, never the id
    assert explanation.recorded is False


def test_base_url_trailing_slash_and_label_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CABINET_LLM_API_KEY", TEST_KEY)
    monkeypatch.setenv("CABINET_LLM_BASE_URL", TEST_BASE_URL + "/")
    monkeypatch.setenv("CABINET_LLM_LABEL", "campus model")
    calls: list[dict[str, Any]] = []
    _install_urlopen(monkeypatch, lambda request: _FakeResponse(SUCCESS_BODY), calls)

    explanation = ChatProvider().explain(FINDINGS, "enrollment_analyst")
    assert calls[0]["url"] == TEST_URL  # no double slash
    assert explanation.model_label == "campus model"


def test_finish_reason_length_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CABINET_LLM_API_KEY", TEST_KEY)
    body = {
        "choices": [
            {"message": {"content": "Registration is"}, "finish_reason": "length"}
        ]
    }
    _install_urlopen(monkeypatch, lambda request: _FakeResponse(body), [])
    with pytest.raises(ProviderUnavailable, match="length") as excinfo:
        ChatProvider().explain(FINDINGS, "enrollment_analyst")
    assert excinfo.value.provider == "chat"


def test_http_500_retried_once_then_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CABINET_LLM_API_KEY", TEST_KEY)
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("cabinet.provider.time.sleep", fake_sleep)
    calls: list[dict[str, Any]] = []

    def fail(request: Any) -> Any:
        raise _http_error(500)

    _install_urlopen(monkeypatch, fail, calls)
    with pytest.raises(ProviderUnavailable, match="HTTP 500"):
        ChatProvider().explain(FINDINGS, "enrollment_analyst")
    assert len(calls) == 2  # one retry, then give up
    assert sleeps == [2]  # the retry waits out a short pause


def test_http_401_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CABINET_LLM_API_KEY", TEST_KEY)
    calls: list[dict[str, Any]] = []

    def fail(request: Any) -> Any:
        raise _http_error(401, b"unauthorized")

    _install_urlopen(monkeypatch, fail, calls)
    with pytest.raises(ProviderUnavailable, match="HTTP 401"):
        ChatProvider().explain(FINDINGS, "enrollment_analyst")
    assert len(calls) == 1


def test_timeout_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CABINET_LLM_API_KEY", TEST_KEY)
    calls: list[dict[str, Any]] = []

    def hang(request: Any) -> Any:
        raise TimeoutError("timed out")

    _install_urlopen(monkeypatch, hang, calls)
    with pytest.raises(ProviderUnavailable, match="55"):
        ChatProvider().explain(FINDINGS, "enrollment_analyst")
    assert len(calls) == 1


def test_connect_phase_timeout_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connect-phase timeout surfaces as URLError(reason=TimeoutError); one
    55 s attempt already spends the wall-time budget, so it is never retried."""
    monkeypatch.setenv("CABINET_LLM_API_KEY", TEST_KEY)

    def no_sleep(seconds: float) -> None:
        raise AssertionError(f"a timeout must not be retried (slept {seconds})")

    monkeypatch.setattr("cabinet.provider.time.sleep", no_sleep)
    calls: list[dict[str, Any]] = []

    def hang(request: Any) -> Any:
        raise urllib.error.URLError(TimeoutError("timed out"))

    _install_urlopen(monkeypatch, hang, calls)
    with pytest.raises(ProviderUnavailable, match="connection error"):
        ChatProvider().explain(FINDINGS, "enrollment_analyst")
    assert len(calls) == 1


def test_http_429_retried_once_after_a_two_second_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CABINET_LLM_API_KEY", TEST_KEY)
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("cabinet.provider.time.sleep", fake_sleep)
    calls: list[dict[str, Any]] = []

    def limited(request: Any) -> Any:
        raise _http_error(429, b"rate limited")

    _install_urlopen(monkeypatch, limited, calls)
    with pytest.raises(ProviderUnavailable, match="HTTP 429"):
        ChatProvider().explain(FINDINGS, "enrollment_analyst")
    assert len(calls) == 2  # exactly one retry
    assert sleeps == [2]  # after a 2 s pause, not immediately


class _RawResponse:
    """A response whose body is arbitrary bytes (a gateway's error page)."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _RawResponse:
        return self

    def __exit__(self, *args: Any) -> None:
        return None


def test_200_with_a_non_json_body_is_unavailable_never_a_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 200 whose body is HTML (a gateway error page) is typed
    unavailability with a plain reason — not a JSONDecodeError escaping to
    the API as a 500."""
    monkeypatch.setenv("CABINET_LLM_API_KEY", TEST_KEY)
    calls: list[dict[str, Any]] = []
    _install_urlopen(
        monkeypatch,
        lambda request: _RawResponse(b"<html><body>Bad Gateway</body></html>"),
        calls,
    )
    with pytest.raises(ProviderUnavailable, match="unreadable response"):
        ChatProvider().explain(FINDINGS, "enrollment_analyst")
    assert len(calls) == 1  # an unreadable body is not retried


def test_connection_dropped_mid_body_is_unavailable_never_a_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """http.client.RemoteDisconnected while the body is read (a 200 whose
    connection drops mid-body) is typed unavailability, not a ConnectionError
    escaping to the API as a 500."""

    class _DroppingResponse:
        def read(self) -> bytes:
            raise http.client.RemoteDisconnected("dropped")

        def __enter__(self) -> _DroppingResponse:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

    monkeypatch.setenv("CABINET_LLM_API_KEY", TEST_KEY)
    calls: list[dict[str, Any]] = []
    _install_urlopen(monkeypatch, lambda request: _DroppingResponse(), calls)
    with pytest.raises(ProviderUnavailable, match="unreadable response"):
        ChatProvider().explain(FINDINGS, "enrollment_analyst")


def test_key_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CABINET_LLM_API_KEY", TEST_KEY)
    calls: list[dict[str, Any]] = []
    _install_urlopen(monkeypatch, lambda request: _FakeResponse(SUCCESS_BODY), calls)
    ChatProvider().explain(FINDINGS, "enrollment_analyst")
    assert calls[0]["authorization"] == f"Bearer {TEST_KEY}"


def test_key_from_key_file_reads_only_the_named_variable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    key_file = tmp_path / "other.env"
    key_file.write_text(
        f'OTHER=value\nMY_KEY="{TEST_KEY}"\nANOTHER_KEY=wrong\n', encoding="utf-8"
    )
    monkeypatch.setenv("CABINET_LLM_API_KEY_FILE", str(key_file))
    monkeypatch.setenv("CABINET_LLM_API_KEY_VAR", "MY_KEY")
    calls: list[dict[str, Any]] = []
    _install_urlopen(monkeypatch, lambda request: _FakeResponse(SUCCESS_BODY), calls)
    ChatProvider().explain(FINDINGS, "enrollment_analyst")
    assert calls[0]["authorization"] == f"Bearer {TEST_KEY}"


def test_missing_config_is_unavailable_and_names_only_cabinet_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for var in _LLM_VARS:
        monkeypatch.delenv(var, raising=False)
    calls: list[dict[str, Any]] = []
    _install_urlopen(monkeypatch, lambda request: _FakeResponse(SUCCESS_BODY), calls)
    with pytest.raises(ProviderUnavailable) as excinfo:
        ChatProvider().explain(FINDINGS, "enrollment_analyst")
    reason = excinfo.value.reason
    assert excinfo.value.provider == "chat"
    assert "CABINET_LLM_BASE_URL" in reason
    assert "CABINET_LLM_MODEL" in reason
    assert "CABINET_LLM_API_KEY" in reason
    assert calls == []  # no HTTP attempt without configuration


def test_missing_key_alone_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ProviderUnavailable) as excinfo:
        ChatProvider().explain(FINDINGS, "enrollment_analyst")
    assert "CABINET_LLM_API_KEY" in excinfo.value.reason
    assert "CABINET_LLM_BASE_URL" not in excinfo.value.reason  # that one is set


def test_key_never_appears_in_exception_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An HTTP error whose body echoes the key must be redacted."""
    monkeypatch.setenv("CABINET_LLM_API_KEY", TEST_KEY)

    def fail(request: Any) -> Any:
        raise _http_error(400, f"bad key {TEST_KEY}".encode())

    _install_urlopen(monkeypatch, fail, [])
    with pytest.raises(ProviderUnavailable) as excinfo:
        ChatProvider().explain(FINDINGS, "enrollment_analyst")
    assert TEST_KEY not in str(excinfo.value)
    assert TEST_KEY not in excinfo.value.reason
    assert "***" in excinfo.value.reason


def test_key_and_model_id_never_written_to_recording(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The replay file stores provider "chat" and the label — never the key,
    never the model id."""
    monkeypatch.setenv("CABINET_LLM_API_KEY", TEST_KEY)
    _install_urlopen(monkeypatch, lambda request: _FakeResponse(SUCCESS_BODY), [])

    recorder = RecordingProvider(ChatProvider(), replay_dir=tmp_path)
    explanation = recorder.explain(FINDINGS, "enrollment_analyst")
    recorder.save(FINDINGS, "enrollment_analyst", explanation)

    path = tmp_path / replay_filename(FINDINGS, "enrollment_analyst")
    raw = path.read_text(encoding="utf-8")
    assert TEST_KEY not in raw
    assert TEST_MODEL not in raw
    recorded = json.loads(raw)
    assert recorded["provider"] == "chat"
    assert recorded["model_label"] == "live model"


def test_think_block_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CABINET_LLM_API_KEY", TEST_KEY)
    body = {
        "choices": [
            {
                "message": {
                    "content": "<think>draft reasoning\nover lines</think>\n"
                    "42 students [M2]."
                },
                "finish_reason": "stop",
            }
        ]
    }
    _install_urlopen(monkeypatch, lambda request: _FakeResponse(body), [])
    explanation = ChatProvider().explain(FINDINGS, "enrollment_analyst")
    assert explanation.text == "42 students [M2]."
    assert "think" not in explanation.text


def test_text_without_think_block_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CABINET_LLM_API_KEY", TEST_KEY)
    text = "  42 students [M2].  "
    body = {
        "choices": [
            {"message": {"content": text}, "finish_reason": "stop"},
        ]
    }
    _install_urlopen(monkeypatch, lambda request: _FakeResponse(body), [])
    explanation = ChatProvider().explain(FINDINGS, "enrollment_analyst")
    assert explanation.text == text


def test_config_from_local_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """provider_from_env loads cabinet.local.env first; the chat provider then
    finds its configuration there."""
    env_file = tmp_path / "cabinet.local.env"
    env_file.write_text(
        f"CABINET_LLM_BASE_URL={TEST_BASE_URL}\n"
        f"CABINET_LLM_MODEL={TEST_MODEL}\n"
        f"CABINET_LLM_API_KEY={TEST_KEY}\n",
        encoding="utf-8",
    )
    for var in _LLM_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("cabinet.provider.LOCAL_ENV_PATH", env_file)
    monkeypatch.setenv("CABINET_PROVIDER", "chat")

    calls: list[dict[str, Any]] = []
    _install_urlopen(monkeypatch, lambda request: _FakeResponse(SUCCESS_BODY), calls)
    from cabinet.provider import provider_from_env

    provider = provider_from_env()
    assert isinstance(provider, ChatProvider)
    explanation = provider.explain(FINDINGS, "enrollment_analyst")
    assert calls[0]["url"] == TEST_URL
    assert calls[0]["authorization"] == f"Bearer {TEST_KEY}"
    assert explanation.model_label == "live model"


def test_reasoning_effort_is_configurable_and_omittable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CABINET_LLM_API_KEY", TEST_KEY)
    calls: list[dict[str, Any]] = []
    _install_urlopen(monkeypatch, lambda request: _FakeResponse(SUCCESS_BODY), calls)

    monkeypatch.setenv("CABINET_LLM_REASONING_EFFORT", "medium")
    ChatProvider().explain(FINDINGS, "enrollment_analyst")
    assert calls[-1]["body"]["reasoning_effort"] == "medium"

    monkeypatch.setenv("CABINET_LLM_REASONING_EFFORT", "")
    ChatProvider().explain(FINDINGS, "enrollment_analyst")
    assert "reasoning_effort" not in calls[-1]["body"]


def test_key_file_reader_stops_at_the_first_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The documented promise: no line after the key's own line is read."""
    key_file = tmp_path / "other.env"
    key_file.write_text("FIRST=1\nTHE_KEY=secret-value\nNEVER_READ=2\n")
    monkeypatch.delenv("CABINET_LLM_API_KEY", raising=False)
    monkeypatch.setenv("CABINET_LLM_API_KEY_FILE", str(key_file))
    monkeypatch.setenv("CABINET_LLM_API_KEY_VAR", "THE_KEY")

    seen: list[str] = []
    real_open = Path.open

    class SpyFile:
        def __init__(self, handle: Any) -> None:
            self._handle = handle

        def __enter__(self) -> SpyFile:
            return self

        def __exit__(self, *exc: Any) -> None:
            self._handle.close()

        def __iter__(self) -> Any:
            for line in self._handle:
                seen.append(line.strip())
                yield line

    def spying_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        handle = real_open(self, *args, **kwargs)
        return SpyFile(handle) if self == key_file else handle

    monkeypatch.setattr(Path, "open", spying_open)
    assert ChatProvider._resolve_key() == "secret-value"
    assert seen == ["FIRST=1", "THE_KEY=secret-value"]
