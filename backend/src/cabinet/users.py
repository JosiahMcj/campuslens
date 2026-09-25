"""User management CLI.

    .venv/bin/python -m cabinet.users bootstrap-admin --email admin@example.edu
    .venv/bin/python -m cabinet.users add --email exec@example.edu \
        --role executive --institution two-rivers

``bootstrap-admin`` creates the bootstrap institution (seeded with the
fictional demonstration dataset) and its first admin, and refuses when an
admin already exists — after bootstrapping, admins create further users
through ``add``. ``add`` attaches the user to an institution (``--institution``
slug; default the bootstrap institution). Both commands generate the
password and print it exactly once, on stdout; it is never logged or
written anywhere. The database is ``CABINET_DB`` (default
``var/cabinet.db``); ``cabinet.local.env`` is honored like the API does.
"""

from __future__ import annotations

import argparse
import sys

from cabinet.api import fixture_path_from_env
from cabinet.auth import USER_ROLES, AuthStore, db_path_from_env, generate_password
from cabinet.provider import load_local_env


def _store() -> AuthStore:
    load_local_env()
    try:
        db_path = db_path_from_env()
    except RuntimeError as exc:
        # A set-but-empty CABINET_DB: one line, never a traceback.
        print(f"users: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    return AuthStore(db_path, seed_fixture=fixture_path_from_env())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m cabinet.users")
    sub = parser.add_subparsers(dest="command", required=True)

    bootstrap = sub.add_parser(
        "bootstrap-admin",
        help="create the bootstrap institution and its first admin (once)",
    )
    bootstrap.add_argument("--email", required=True)

    add = sub.add_parser("add", help="create another user")
    add.add_argument("--email", required=True)
    add.add_argument("--role", required=True, choices=USER_ROLES)
    add.add_argument(
        "--institution",
        default=None,
        help="institution slug (default: the bootstrap institution)",
    )

    args = parser.parse_args(argv)
    store = _store()
    password = generate_password()
    if args.command == "bootstrap-admin":
        if store.has_admin():
            print(
                "bootstrap-admin: an admin already exists; create further "
                "users with `add` (or disable the old admin first)",
                file=sys.stderr,
            )
            return 1
        institution_id = store.ensure_bootstrap_institution()
        role = "admin"
    else:
        role = args.role
        if args.institution is not None:
            institution = store.institution_by_slug(args.institution)
            if institution is None:
                print(
                    f"add: no institution with slug {args.institution!r} "
                    "(create it with `make institution NAME=... SLUG=...`)",
                    file=sys.stderr,
                )
                return 1
            institution_id = int(institution["id"])
        else:
            institution_id = store.ensure_bootstrap_institution()
    try:
        user_id = store.create_user(
            args.email, password, role, institution_id=institution_id
        )
    except ValueError as exc:
        print(f"{args.command}: {exc}", file=sys.stderr)
        return 1
    print(
        f"created {role} user {args.email.strip().lower()} (id {user_id},"
        f" institution {institution_id})"
    )
    print(f"password (shown once, never stored or logged): {password}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
