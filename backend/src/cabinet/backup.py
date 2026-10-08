"""Backup and restore, stdlib only.

    .venv/bin/python -m cabinet.backup create [--out DIR]
    .venv/bin/python -m cabinet.backup restore <backup dir>

``create`` snapshots the database with the SQLite backup API (never a file
copy of a live database — the API may hold un-checkpointed pages) plus the
dataset files under ``var/data/``, into ``var/backups/<UTC timestamp>/``
with a ``manifest.json`` carrying sha256s of everything. The manifest is
verified against the written copy before the command reports success.

The source database is opened READ-ONLY (``file:...?mode=ro``): a path
that does not exist or is not a SQLite database is refused with a plain
reason and nothing is created (a plain ``sqlite3.connect`` would silently
CREATE an empty database at a misconfigured ``CABINET_DB`` and then
"successfully" back it up). A database with zero rows in every cabinet
table is likewise refused unless ``--allow-empty`` — an empty snapshot is
almost always a misconfigured job, not a real backup.

``restore`` verifies the backup's hashes first, moves any existing
``cabinet.db`` / ``data/`` aside (never deletes: the aside copy is named
``*.pre-restore-<timestamp>``), copies the backup in, and verifies what it
wrote. Run it after ``make stop`` — the Makefile target refuses while the
servers are up.

Nothing in a backup or restore path is ever deleted: backups are new
directories, restores move aside. Retention of old backups is the
operator's call.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cabinet.migrations import DB_FILE_MODE, ensure_private_db_file

# A backup holds the whole database and every dataset: its directories are
# 0700 and every file in it 0600, like the live copies.
BACKUP_DIR_MODE = 0o700


def _make_private(path: Path) -> None:
    """0600 for a file, 0700 for a directory (umask cannot loosen it)."""
    os.chmod(path, BACKUP_DIR_MODE if path.is_dir() else DB_FILE_MODE)


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _data_files(data_dir: Path) -> list[Path]:
    if not data_dir.exists():
        return []
    return sorted(p for p in data_dir.rglob("*") if p.is_file())


_CABINET_TABLES = (
    "institutions",
    "users",
    "sessions",
    "datasets",
    "audit_events",
    "briefings",
    "decisions",
    "recordings",
    "dispatches",
    "office_contacts",
    "aid_reviews",
    "staff_actions",
    "staff_action_notes",
    "staff_action_history",
)


def _open_source_readonly(db_path: Path) -> sqlite3.Connection:
    """Open the source database read-only, refusing — before anything is
    created — a path that does not exist or is not a SQLite database."""
    if not db_path.exists():
        raise SystemExit(f"backup: {db_path} does not exist; nothing to back up")
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        # Force SQLite to actually read the file: connect alone is lazy, and
        # a non-database only fails on first access.
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlite3.Error as exc:
        raise SystemExit(
            f"backup: {db_path} is not a readable SQLite database: {exc}"
        ) from None
    return conn


def _row_total(conn: sqlite3.Connection) -> int:
    """Total rows across the cabinet tables (0 for a fresh/empty schema)."""
    total = 0
    for table in _CABINET_TABLES:
        present = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        if present:
            total += int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    return total


def _make_private_dirs(directory: Path, root: Path) -> None:
    """Create ``directory`` and make it and every directory between it and
    ``root`` (both included) 0700."""
    directory.mkdir(parents=True, exist_ok=True)
    current = directory
    while True:
        _make_private(current)
        if current == root or current.parent == current or root not in current.parents:
            break
        current = current.parent


def create_backup(
    db_path: str | Path,
    data_dir: str | Path,
    out_dir: str | Path | None = None,
    *,
    allow_empty: bool = False,
) -> Path:
    """Snapshot the database and dataset files; return the backup directory."""
    db_path = Path(db_path)
    data_dir = Path(data_dir)
    source = _open_source_readonly(db_path)
    try:
        if not allow_empty and _row_total(source) == 0:
            raise SystemExit(
                f"backup: {db_path} has no rows in any cabinet table; "
                "refusing to snapshot an empty database (a misconfigured "
                "CABINET_DB is the usual cause — pass --allow-empty to "
                "override)"
            )
        if out_dir is None:
            out = db_path.parent / "backups" / _timestamp()
        else:
            out = Path(out_dir)
        if out.exists():
            raise SystemExit(f"backup: {out} already exists; backups are never reused")
        out.mkdir(parents=True, mode=BACKUP_DIR_MODE)
        _make_private(out)

        # The SQLite online backup API — consistent even while the API
        # process holds the database open. The file is created 0600 first.
        ensure_private_db_file(out / "cabinet.db")
        destination = sqlite3.connect(str(out / "cabinet.db"))
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()

    backup_data = out / "data"
    copied: dict[str, str] = {}
    for path in _data_files(data_dir):
        relative = path.relative_to(data_dir)
        target = backup_data / relative
        _make_private_dirs(target.parent, out)
        shutil.copy2(path, target)
        _make_private(target)
        copied[str(relative)] = _sha256(target)

    manifest = {
        "tool": "golden-eagle-cabinet backup",
        "version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "cabinet_db_sha256": _sha256(out / "cabinet.db"),
        "data_files": copied,
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _make_private(out / "manifest.json")

    # Verify the backup before reporting success: reread every copied byte.
    problems: list[str] = []
    if _sha256(out / "cabinet.db") != manifest["cabinet_db_sha256"]:
        problems.append("cabinet.db copy does not match its manifest hash")
    for name, expected in copied.items():
        if _sha256(backup_data / name) != expected:
            problems.append(f"data file {name} does not match its hash")
    if problems:
        raise SystemExit("backup: verification failed: " + "; ".join(problems))
    print(
        f"backup written to {out} (database + {len(copied)} dataset file(s),"
        " hashes verified)"
    )
    return out


def _verify_backup(backup: Path) -> dict[str, Any]:
    manifest_path = backup / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"restore: {backup} has no manifest.json")
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    db_copy = backup / "cabinet.db"
    if not db_copy.exists():
        problems.append("cabinet.db is missing from the backup")
    elif _sha256(db_copy) != manifest.get("cabinet_db_sha256"):
        problems.append("cabinet.db does not match its manifest hash")
    for relative, expected in manifest.get("data_files", {}).items():
        path = backup / "data" / relative
        if not path.exists():
            problems.append(f"data file {relative} is missing from the backup")
        elif _sha256(path) != expected:
            problems.append(f"data file {relative} does not match its hash")
    if problems:
        raise SystemExit(
            f"restore: the backup at {backup} failed verification: "
            + "; ".join(problems)
        )
    return manifest


def _move_aside(path: Path) -> None:
    """Move an existing destination aside; never delete it."""
    aside = path.with_name(f"{path.name}.pre-restore-{_timestamp()}")
    path.rename(aside)
    print(f"restore: moved existing {path} aside to {aside}")


def restore_backup(
    backup_dir: str | Path, db_path: str | Path, data_dir: str | Path
) -> None:
    """Restore a verified backup over the live paths (which are moved
    aside, never deleted). The servers must be stopped first."""
    backup = Path(backup_dir)
    db_path = Path(db_path)
    data_dir = Path(data_dir)
    _verify_backup(backup)

    if db_path.exists():
        _move_aside(db_path)
    if data_dir.exists():
        _move_aside(data_dir)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup / "cabinet.db", db_path)
    ensure_private_db_file(db_path)
    problems: list[str] = []
    if _sha256(db_path) != _sha256(backup / "cabinet.db"):
        problems.append("the restored cabinet.db does not match the backup")
    manifest = _verify_backup(backup)
    count = 0
    for relative in manifest.get("data_files", {}):
        source = backup / "data" / relative
        target = data_dir / relative
        _make_private_dirs(target.parent, data_dir)
        shutil.copy2(source, target)
        _make_private(target)
        if _sha256(target) != _sha256(source):
            problems.append(f"the restored data file {relative} does not match")
        count += 1
    if problems:
        raise SystemExit("restore: verification failed: " + "; ".join(problems))
    print(
        f"restored {backup} -> {db_path} and {data_dir} "
        f"({count} dataset file(s), hashes verified)"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m cabinet.backup")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="snapshot the database and dataset files")
    create.add_argument(
        "--out",
        default=None,
        help="backup directory (default: var/backups/<UTC timestamp>)",
    )
    create.add_argument(
        "--allow-empty",
        action="store_true",
        help="back up even when the database has no rows in any cabinet table",
    )
    restore = sub.add_parser("restore", help="restore a backup (stop the API first)")
    restore.add_argument("source", help="the backup directory to restore from")
    args = parser.parse_args(argv)

    from cabinet.auth import db_path_from_env
    from cabinet.provider import load_local_env

    load_local_env()
    try:
        db_path = db_path_from_env()
    except RuntimeError as exc:
        print(f"backup: {exc}", file=sys.stderr)
        return 1
    # CabinetStore's default dataset directory; not configurable by env.
    data_dir = db_path.parent / "data"
    try:
        if args.command == "create":
            create_backup(db_path, data_dir, args.out, allow_empty=args.allow_empty)
        else:
            restore_backup(args.source, db_path, data_dir)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"backup: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
