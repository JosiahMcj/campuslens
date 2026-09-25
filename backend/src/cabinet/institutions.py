"""Institution management CLI.

    .venv/bin/python -m cabinet.institutions add --name "Two Rivers" \
        --slug two-rivers
    .venv/bin/python -m cabinet.institutions list

Creating an institution seeds it with the fictional demonstration dataset
(``CABINET_FIXTURE``, default ``data/fixture.json``) so onboarding and the
demo work on day one. The database is ``CABINET_DB`` (default
``var/cabinet.db``); ``cabinet.local.env`` is honored like the API does.
"""

from __future__ import annotations

import argparse
import sys

from cabinet.api import fixture_path_from_env
from cabinet.auth import db_path_from_env
from cabinet.provider import load_local_env
from cabinet.store import CabinetStore


def _store() -> CabinetStore:
    load_local_env()
    try:
        db_path = db_path_from_env()
    except RuntimeError as exc:
        # A set-but-empty CABINET_DB: one line, never a traceback.
        print(f"institutions: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    return CabinetStore(db_path, seed_fixture=fixture_path_from_env())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m cabinet.institutions")
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser(
        "add", help="create an institution (seeds the fictional demo dataset)"
    )
    add.add_argument("--name", required=True)
    add.add_argument("--slug", required=True)

    sub.add_parser("list", help="list institutions")

    args = parser.parse_args(argv)
    store = _store()
    if args.command == "add":
        try:
            institution_id = store.create_institution(args.name, args.slug)
        except ValueError as exc:
            print(f"add: {exc}", file=sys.stderr)
            return 1
        print(
            f"created institution {args.name.strip()!r} (id {institution_id},"
            f" slug {args.slug.strip().lower()!r}) with the fictional"
            " demonstration dataset"
        )
        return 0
    for institution in store.list_institutions():
        print(
            f"{institution['id']}\t{institution['slug']}\t{institution['name']}"
            f"\t{institution['created_at']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
