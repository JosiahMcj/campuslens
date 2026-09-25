"""Serving the built UI from FastAPI, and the ``/api`` prefix the UI
client speaks.

The Vite build (``make build``) lands in ``ui/dist`` — hashed assets under
``assets/`` plus an ``index.html``. In production the same FastAPI process
serves the API and the built UI, so there is one service to run and one
origin to secure. The UI client (``ui/src/api.ts``) calls ``/api/...``; the
Vite dev proxy strips that prefix in development, and
:class:`ApiPrefixMiddleware` strips it here, so the API's routes, role table,
and public-route list never change shape between dev and production.

Static behaviour:

- ``assets/*`` (Vite content-hashed files) get ``Cache-Control: public,
  max-age=31536000, immutable`` — a hashed name never changes content.
- ``index.html`` (and every SPA fallback) gets ``Cache-Control: no-store``
  so a new deploy is picked up immediately.
- Any GET path that is not an API route and not a file falls back to
  ``index.html`` (SPA client-side routing). An unknown ``/api/*`` path is a
  plain 404, never the HTML shell.
- The security middleware wraps everything mounted here, so the CSP and
  the other security headers apply to the UI exactly as to API JSON.

The directory served is ``CABINET_UI_DIST`` (default ``ui/dist`` next to the
repo root). When the directory does not exist at startup nothing is mounted
(development without a build) and ``GET /ready`` reports it.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles
from starlette.types import Receive, Scope, Send

ENV_UI_DIST = "CABINET_UI_DIST"
DEFAULT_UI_DIST = Path(__file__).resolve().parents[3] / "ui" / "dist"

IMMUTABLE_CACHE = "public, max-age=31536000, immutable"
NO_STORE = "no-store"

# Scope key set by ApiPrefixMiddleware so the SPA fallback can tell a
# stripped API path (404, never HTML) from a client-side route (index.html).
API_SCOPE_KEY = "cabinet.api_path"


def ui_dist_from_env() -> Path:
    """The built-UI directory: CABINET_UI_DIST (tests point it at a tmp
    directory), else ui/dist."""
    override = os.environ.get(ENV_UI_DIST)
    return Path(override) if override else DEFAULT_UI_DIST


class ApiPrefixMiddleware:
    """Strip the ``/api`` prefix the UI client sends, before any routing or
    security handling. Pure ASGI so it wraps the whole middleware stack.

    ``/api/health`` is exactly ``/health``; ``/api`` is ``/``. The original
    path is recorded in the scope so the SPA fallback below refuses to serve
    ``index.html`` for an unknown API path.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            path = scope.get("path", "")
            if path == "/api":
                scope["path"] = "/"
                scope[API_SCOPE_KEY] = path
            elif path.startswith("/api/"):
                scope["path"] = path[len("/api") :]
                scope[API_SCOPE_KEY] = path
        await self.app(scope, receive, send)


class SpaStaticFiles(StaticFiles):
    """StaticFiles with SPA fallback and the deploy cache-header rules."""

    async def get_response(self, path: str, scope: Scope) -> Any:
        if API_SCOPE_KEY in scope:
            # A stripped /api/* path that matched no API route is a plain
            # 404 — serving the HTML shell here would hide API typos as 200s.
            raise HTTPException(status_code=404)
        served = path
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or scope["method"] not in ("GET", "HEAD"):
                raise
            served = "index.html"
            response = await super().get_response("index.html", scope)
        if served.startswith("assets/"):
            # Vite content-hashed files: a new build is a new name.
            response.headers["Cache-Control"] = IMMUTABLE_CACHE
        elif self._serves_index(served):
            response.headers["Cache-Control"] = NO_STORE
        return response

    def _serves_index(self, path: str) -> bool:
        """True when serving ``path`` actually serves an index.html: the
        root (normpathed to "."), a directory under ``html=True``, or the
        SPA fallback. Those responses must never be cached."""
        full_path, stat_result = self.lookup_path(path)
        if stat_result is not None and stat.S_ISDIR(stat_result.st_mode):
            full_path = os.path.join(full_path, "index.html")
        return os.path.basename(full_path) == "index.html"
