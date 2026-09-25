"""Production access log: one JSON line per request on stdout, with a
request id echoed back as ``X-Request-ID``.

Enabled only when ``CABINET_ENV=production`` (``make serve``); uvicorn's own
human-readable access log is then off (``--no-access-log`` in
``deploy/run-production.sh``), so every request logs exactly one structured
line, shaped for a log collector:

    {"ts": "...", "request_id": "...", "method": "GET", "path": "/findings",
     "status": 200, "duration_ms": 3, "client_ip": "127.0.0.1"}

The client IP is uvicorn's ``scope["client"]``: with ``--proxy-headers`` and
``--forwarded-allow-ips`` restricted to ``CABINET_TRUSTED_PROXY``, it is the
real client address when the request came through the trusted reverse proxy
and the direct peer otherwise — a spoofed ``X-Forwarded-For`` from anywhere
else never reaches this line.

The request id is the inbound ``X-Request-ID`` when the client (or proxy)
sent one, else a fresh random one; the response always carries it, so a
support report can be traced to exactly one log line. Outside production
nothing here is installed and the response carries no request id.
"""

from __future__ import annotations

import json
import secrets
import sys
import time
from datetime import UTC, datetime
from typing import Any

from starlette.types import Message, Receive, Scope, Send

REQUEST_ID_HEADER = "x-request-id"


class RequestLogMiddleware:
    """ASGI middleware: request id + one JSON access-log line per request."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = self._request_id(scope)
        started = time.monotonic()
        status = 500

        async def send_logged(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
                headers = list(message.get("headers", []))
                headers.append(
                    (REQUEST_ID_HEADER.encode(), request_id.encode("ascii"))
                )
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_logged)
        finally:
            client = scope.get("client")
            line = {
                "ts": datetime.now(UTC).isoformat(),
                "request_id": request_id,
                "method": scope.get("method", ""),
                "path": scope.get("path", ""),
                "status": status,
                "duration_ms": round((time.monotonic() - started) * 1000, 1),
                "client_ip": client[0] if client else None,
            }
            print(json.dumps(line, separators=(",", ":")), file=sys.stdout, flush=True)

    @staticmethod
    def _request_id(scope: Scope) -> str:
        headers: list[tuple[bytes, bytes]] = list(scope.get("headers", []))
        for name, value in headers:
            if name.lower() == REQUEST_ID_HEADER.encode():
                try:
                    inbound = value.decode("ascii").strip()
                except UnicodeDecodeError:
                    break
                # Bound what is echoed back into a response header.
                if 0 < len(inbound) <= 128:
                    return inbound
                break
        return secrets.token_hex(16)
