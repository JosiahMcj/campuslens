"""Production serving: /ready dependency checks, the built UI served
from the same process, and the /api prefix the UI client speaks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cabinet.api import create_app
from cabinet.security import CSP


def _dist(tmp_path: Path, *, hashed_asset: bool = True) -> Path:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        "<html><head><title>Cabinet</title></head><body>built</body></html>\n"
    )
    if hashed_asset:
        assets = dist / "assets"
        assets.mkdir()
        (assets / "index-a1b2c3d4.js").write_text("console.log('built');\n")
    return dist


def test_ready_ok() -> None:
    client = TestClient(create_app())
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"ready": True}


def test_ready_names_missing_ui(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CABINET_UI_DIST", str(tmp_path / "no-such-dist"))
    client = TestClient(create_app())
    response = client.get("/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["ready"] is False
    assert body["checks"]["ui_dist"] is False
    assert any("make build" in reason for reason in body["reasons"])


def test_ready_names_unreadable_golden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    golden = Path(tmp_path) / "golden"  # conftest's CABINET_GOLDEN_DIR
    (golden / "broken.json").write_text("{not json")
    client = TestClient(create_app())
    response = client.get("/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["checks"]["replay_golden"] is False
    assert any("golden replay" in reason for reason in body["reasons"])


def test_ready_stays_public() -> None:
    # No session, and it writes no data.refused event (public route).
    client = TestClient(create_app())
    assert client.get("/ready").status_code == 200
    assert client.get("/health").json() == {"ok": True}


def test_built_ui_served_with_cache_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CABINET_UI_DIST", str(_dist(tmp_path)))
    client = TestClient(create_app())

    index = client.get("/")
    assert index.status_code == 200
    assert "built" in index.text
    assert index.headers["cache-control"] == "no-store"
    assert index.headers["content-security-policy"] == CSP
    assert index.headers["x-content-type-options"] == "nosniff"

    asset = client.get("/assets/index-a1b2c3d4.js")
    assert asset.status_code == 200
    assert "javascript" in asset.headers["content-type"]
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"

    # SPA fallback: an unknown client-side route gets index.html, no-store.
    fallback = client.get("/briefing/some-client-route")
    assert fallback.status_code == 200
    assert "built" in fallback.text
    assert fallback.headers["cache-control"] == "no-store"


def test_api_prefix_stripped_and_unknown_api_path_is_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CABINET_UI_DIST", str(_dist(tmp_path)))
    client = TestClient(create_app())

    assert client.get("/api/health").json() == {"ok": True}
    assert client.get("/api/ready").json() == {"ready": True}

    # An unknown /api/* path is a plain 404 — never the HTML shell.
    missing = client.get("/api/no-such-route")
    assert missing.status_code == 404
    assert "text/html" not in missing.headers.get("content-type", "")

    # Auth is unchanged through the prefix: no session is a 401.
    assert client.get("/api/findings").status_code == 401
    assert client.get("/findings").status_code == 401


def test_no_mount_without_dist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CABINET_UI_DIST", str(tmp_path / "missing"))
    client = TestClient(create_app())
    assert client.get("/").status_code == 404
    assert client.get("/health").status_code == 200


def test_production_request_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CABINET_ENV", "production")
    monkeypatch.setenv("CABINET_BIND", "127.0.0.1")
    monkeypatch.setenv("CABINET_UI_DIST", str(_dist(tmp_path)))
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    request_id = response.headers["x-request-id"]
    out = capsys.readouterr().out
    lines = [json.loads(line) for line in out.splitlines() if line.strip()]
    entry = next(line for line in lines if line.get("path") == "/health")
    assert entry["request_id"] == request_id
    assert entry["method"] == "GET"
    assert entry["status"] == 200

    # An inbound request id is honoured and echoed.
    echoed = client.get("/health", headers={"X-Request-ID": "trace-123"})
    assert echoed.headers["x-request-id"] == "trace-123"


def test_development_has_no_request_id(tmp_path: Path) -> None:
    client = TestClient(create_app())
    assert "x-request-id" not in client.get("/health").headers
