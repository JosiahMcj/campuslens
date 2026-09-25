"""The uvicorn entrypoint: ``uvicorn cabinet.app:app``.

The app is built lazily, on first ATTRIBUTE ACCESS (PEP 562 module
``__getattr__``), not at import: a process that imports this module only to
call ``create_app()`` itself (tests, tooling) builds nothing and never sees
a duplicate dev-mode ephemeral-key warning. Uvicorn's import_from_string
loads the module and then does ``getattr(module, "app")``, so the build
still happens at load time, in the loader — a startup refusal's SystemExit
propagates and the process exits, exactly as an eager module-level app did.
(An ASGI wrapper class would instead receive the refusal inside uvicorn's
lifespan probe, which swallows it: the process then binds and serves 500s,
alive but dead — fail-open. That shape must not come back.)
"""

from __future__ import annotations

from typing import Any

from cabinet.api import create_app

_app: Any = None


def __getattr__(name: str) -> Any:
    if name == "app":
        global _app
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
