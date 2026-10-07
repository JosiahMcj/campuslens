"""The fallback chat model: used only when the primary is unavailable, and
credited by its own label."""

from __future__ import annotations

from typing import Any

import pytest

from cabinet import provider as prov
from cabinet.provider import (
    ChatProvider,
    Explanation,
    FallbackChatProvider,
    ProviderUnavailable,
    provider_from_env,
)


def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CABINET_PROVIDER", "chat")
    monkeypatch.setenv("CABINET_LLM_BASE_URL", "https://primary.example/v1")
    monkeypatch.setenv("CABINET_LLM_MODEL", "primary-model")
    monkeypatch.setenv("CABINET_LLM_LABEL", "Gloo AI")
    monkeypatch.setenv("CABINET_LLM_API_KEY", "k1")
    monkeypatch.setenv("CABINET_LLM_FALLBACK_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("CABINET_LLM_FALLBACK_MODEL", "local-model")
    monkeypatch.setenv("CABINET_LLM_FALLBACK_LABEL", "local model")
    monkeypatch.setenv("CABINET_LLM_FALLBACK_API_KEY", "k2")
    monkeypatch.setattr(prov, "load_local_env", lambda *a, **k: None)


def test_fallback_is_built_only_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _env(monkeypatch)
    assert isinstance(provider_from_env(), FallbackChatProvider)
    monkeypatch.delenv("CABINET_LLM_FALLBACK_BASE_URL")
    p = provider_from_env()
    assert isinstance(p, ChatProvider) and not isinstance(p, FallbackChatProvider)


def test_primary_answers_when_up(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    calls: list[str] = []

    def post(
        self: ChatProvider,
        url: str,
        key: str,
        payload: dict[str, Any],
        timeout: float = 55,
    ) -> dict[str, Any]:
        calls.append(payload["model"])
        return {"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]}

    monkeypatch.setattr(ChatProvider, "_post_with_retry", post)
    monkeypatch.setattr("cabinet.analysts.build_prompt", lambda f, r: ("s", "u"))
    out = FallbackChatProvider().explain({}, "explore_planner")
    assert isinstance(out, Explanation)
    assert calls == ["primary-model"] and out.model_label == "Gloo AI"


def test_fallback_answers_when_primary_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    calls: list[tuple[str, str]] = []

    def post(
        self: ChatProvider,
        url: str,
        key: str,
        payload: dict[str, Any],
        timeout: float = 55,
    ) -> dict[str, Any]:
        calls.append((payload["model"], key))
        if payload["model"] == "primary-model":
            raise ProviderUnavailable("down", provider="chat")
        return {"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]}

    monkeypatch.setattr(ChatProvider, "_post_with_retry", post)
    monkeypatch.setattr("cabinet.analysts.build_prompt", lambda f, r: ("s", "u"))
    out = FallbackChatProvider().explain({}, "explore_planner")
    assert calls == [("primary-model", "k1"), ("local-model", "k2")]
    assert out.model_label == "local model"


def test_production_check_covers_the_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    monkeypatch.setenv("CABINET_LLM_FALLBACK_BASE_URL", "http://10.1.2.3:11434/v1")
    with pytest.raises(RuntimeError, match="CABINET_LLM_FALLBACK_BASE_URL"):
        prov.check_production_llm_base_url()
