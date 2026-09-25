import importlib
import sys

import pytest
from fastapi.testclient import TestClient
from uvicorn.importer import import_from_string

from cabinet.api import create_app


def test_health() -> None:
    # Built from create_app() under the conftest env (CABINET_DB in tmp_path)
    # like every other test — importing the module-level cabinet.app:app
    # would seed the repo's var/ with ambient settings during collection.
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_uvicorn_entrypoint_fails_closed_on_a_startup_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """uvicorn's loader does ``getattr(module, 'app')`` at load time, so the
    PEP 562 build in cabinet.app runs in the loader and a startup refusal
    (here a set-but-empty CABINET_DB) raises SystemExit right there — the
    process exits instead of binding and serving 500s (the lazy-wrapper
    shape let uvicorn's lifespan probe swallow the refusal and stay alive;
    this test fails against it because import_from_string returns the
    wrapper without raising)."""
    monkeypatch.setenv("CABINET_DB", "")
    monkeypatch.delitem(sys.modules, "cabinet.app", raising=False)
    with pytest.raises(SystemExit) as excinfo:
        import_from_string("cabinet.app:app")
    assert excinfo.value.code == 1


def test_importing_cabinet_app_builds_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plain ``import cabinet.app`` (tooling, tests) must not build the
    app — the double ephemeral-key warning fix depends on it."""
    monkeypatch.delitem(sys.modules, "cabinet.app", raising=False)
    module = importlib.import_module("cabinet.app")
    assert "app" not in module.__dict__  # built only on first access
