"""Configuration report: ``make check-config`` runs this module.

Prints every CABINET_* variable the app reads and whether it is set —
values are NEVER printed, only a redacted marker and the value's length,
so the report is safe to paste into a ticket. Also prints the effective
mode and, in production, whether the fail-closed startup checks would
pass.
"""

from __future__ import annotations

import os

from cabinet.auth import (
    ENV_BIND,
    ENV_DB,
    ENV_ENV,
    ENV_SECRET_KEY,
    ENV_SESSION_TTL_HOURS,
    MIN_SECRET_KEY_BYTES,
    PRODUCTION,
    check_production_bind,
    resolve_secret_key,
    session_ttl,
)
from cabinet.provider import load_local_env
from cabinet.security import ENV_RATE_ASK, ENV_RATE_GENERAL, ENV_RATE_SESSION

# Every CABINET_* variable the code reads, grouped by concern.
KNOWN_VARIABLES: tuple[str, ...] = (
    # mode and secrets
    ENV_ENV,
    ENV_SECRET_KEY,
    ENV_BIND,
    # storage (one database holds users, institutions, datasets,
    # audit events, briefings, decisions, and recordings; dataset files
    # live under var/data/)
    ENV_DB,
    "CABINET_FIXTURE",
    # sessions
    ENV_SESSION_TTL_HOURS,
    # serving
    "CABINET_UI_DIST",
    "CABINET_LOCAL_ENV",
    "CABINET_TRUSTED_PROXY",
    # provider
    "CABINET_PROVIDER",
    "CABINET_RECORD",
    "CABINET_REPLAY_DIR",
    "CABINET_GOLDEN_DIR",
    "CABINET_LLM_BASE_URL",
    "CABINET_LLM_MODEL",
    "CABINET_LLM_LABEL",
    "CABINET_LLM_REASONING_EFFORT",
    "CABINET_LLM_API_KEY",
    "CABINET_LLM_API_KEY_FILE",
    "CABINET_LLM_API_KEY_VAR",
    "CABINET_VALIDATION_RETRIES",
    # the Ethos import (cabinet.ellucian)
    "CABINET_ETHOS_BASE_URL",
    "CABINET_ETHOS_API_KEY_FILE",
    "CABINET_PSEUDONYM_KEY_FILE",
    "CABINET_ETHOS_RESOURCES",
    "CABINET_ETHOS_TIMEZONE",
    "CABINET_EXPORTS_DIR",
    # the outbound provider for dispatches (cabinet.outbound)
    "CABINET_OUTBOUND",
    "CABINET_SMTP_HOST",
    "CABINET_SMTP_PORT",
    "CABINET_SMTP_FROM",
    "CABINET_SMTP_USER",
    "CABINET_SMTP_PASSWORD_FILE",
    # rate limits
    ENV_RATE_GENERAL,
    ENV_RATE_SESSION,
    ENV_RATE_ASK,
)

# Values that are configuration rather than secrets may show their length;
# keys and passwords never show anything but the redacted marker.
SECRET_VARIABLES = frozenset({ENV_SECRET_KEY, "CABINET_LLM_API_KEY"})


def main() -> int:
    load_local_env()
    production = os.environ.get(ENV_ENV, "").strip().lower() == PRODUCTION
    # A set-but-empty CABINET_DB is a configuration error, not an unset
    # variable: every command would silently fall back to var/cabinet.db.
    if ENV_DB in os.environ and not os.environ[ENV_DB].strip():
        print(
            f"{ENV_DB} is set but empty; set it to a database path or "
            "unset it to use the default (var/cabinet.db)"
        )
        return 1
    try:
        session_ttl()
    except RuntimeError as exc:
        # Startup refuses the same way; surface it here too.
        print(str(exc))
        return 1
    print(f"mode: {ENV_ENV}={os.environ.get(ENV_ENV, 'development (unset)')}")
    for name in KNOWN_VARIABLES:
        value = os.environ.get(name)
        if value is None or value == "":
            print(f"{name}: unset")
        elif name in SECRET_VARIABLES:
            print(f"{name}: set (redacted, {len(value.encode('utf-8'))} bytes)")
        else:
            print(f"{name}: set ({len(value)} chars)")
    if production:
        problems: list[str] = []
        try:
            secret = os.environ.get(ENV_SECRET_KEY, "").strip()
            if len(secret.encode("utf-8")) < MIN_SECRET_KEY_BYTES:
                problems.append(
                    f"{ENV_SECRET_KEY} missing or shorter than "
                    f"{MIN_SECRET_KEY_BYTES} bytes"
                )
            check_production_bind()
        except RuntimeError as exc:
            problems.append(str(exc))
        if problems:
            print("production startup checks: WOULD FAIL —")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        print("production startup checks: would pass")
    else:
        # resolve_secret_key is only called to surface the ephemeral warning
        # state, not for its value.
        _, ephemeral = resolve_secret_key(production=False)
        if ephemeral:
            print(
                f"note: {ENV_SECRET_KEY} unset or shorter than "
                f"{MIN_SECRET_KEY_BYTES} bytes — sessions use an ephemeral "
                "key and do not survive restarts (fine outside production)"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
