"""What Institution settings shows about the outside connections, read-only.

``GET /admin/connections`` (admin) answers two plain questions for the
institution's administrator, without ever showing a credential:

- **Ellucian (student records).** Whether the import from the student
  information system is configured on this server (each setting set or
  not, never its value, never a file path or key), and the last import:
  when it arrived, its name, and whether it is the data in use. The import
  itself runs from the command line (``make import-ethos``, docs/ELLUCIAN.md);
  nothing here can start one.
- **Outgoing mail.** Where "Send to office" delivers: the outbox folder on
  this machine (nothing leaves it), or the institution's mail server.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Request

from cabinet.ellucian import ENV_API_KEY_FILE, ENV_BASE_URL, ENV_PSEUDONYM_KEY_FILE
from cabinet.outbound import ENV_OUTBOUND
from cabinet.store import CabinetStore

# The dataset rows the Ethos importer stores carry this uploader.
ETHOS_UPLOADER = "ethos-import"

# The settings the import needs, in the words the page shows.
ELLUCIAN_SETTINGS: tuple[tuple[str, str], ...] = (
    (ENV_BASE_URL, "Ellucian Ethos address"),
    (ENV_API_KEY_FILE, "Ethos access key"),
    (ENV_PSEUDONYM_KEY_FILE, "Student pseudonym key"),
)

router = APIRouter()


def ellucian_status(store: CabinetStore, institution_id: int) -> dict[str, Any]:
    settings = [
        {"label": label, "set": bool(os.environ.get(name, "").strip())}
        for name, label in ELLUCIAN_SETTINGS
    ]
    imports = [
        row
        for row in store.datasets_for(institution_id)
        if row.get("uploaded_by") == ETHOS_UPLOADER and row.get("deleted_at") is None
    ]
    last = max(imports, key=lambda row: str(row["uploaded_at"]), default=None)
    return {
        "configured": all(setting["set"] for setting in settings),
        "settings": settings,
        "last_import": (
            {
                "name": last["name"],
                "at": last["uploaded_at"],
                "in_use": bool(last["is_active"]),
            }
            if last is not None
            else None
        ),
    }


def outbound_status() -> dict[str, Any]:
    which = os.environ.get(ENV_OUTBOUND, "").strip().lower() or "outbox"
    return {"provider": "smtp" if which == "smtp" else "outbox"}


@router.get("/admin/connections")
def get_admin_connections(request: Request) -> dict[str, Any]:
    store: CabinetStore = request.app.state.auth
    institution_id = int(request.scope["cabinet_user"]["institution_id"])
    return {
        "ellucian": ellucian_status(store, institution_id),
        "outbound": outbound_status(),
    }


__all__ = ["ELLUCIAN_SETTINGS", "ellucian_status", "outbound_status", "router"]
