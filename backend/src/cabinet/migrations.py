"""Versioned schema migrations for ``var/cabinet.db``, stdlib only.

    .venv/bin/python -m cabinet.migrations [--db PATH]

The runner keeps a ``schema_migrations(version, name, applied_at)`` table.
On open, :func:`migrate` applies every known migration that is not yet
recorded, in order, each in its own transaction. Every statement is a plain
``execute()`` inside that transaction (never ``executescript``, which issues
an implicit COMMIT and would leave a half-applied migration behind a crash):
a migration that fails midway rolls back whole, records nothing in
``schema_migrations``, and is retried cleanly on the next start. A database
whose recorded
version is not in this build's migration list — a newer deployment's
database, or a hand-edited one — raises :class:`SchemaVersionError`; the
API turns that into a one-line startup refusal, never a traceback and never
a half-upgraded schema.

Migration 1 is the baseline. It also upgrades a pre-migration database in
place: such a database is recognized by a ``users`` table with no
``schema_migrations`` table; its users are moved into the rebuilt
``users`` table (``institution_id NOT NULL``) under a newly created
bootstrap institution.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from collections.abc import Callable
from datetime import UTC, datetime

# Audit scope for events that happen before a user is known (failed logins,
# anonymous refusals). Not a real institution row; the per-institution id
# sequence and hash chain treat it as their own scope.
PLATFORM_INSTITUTION_ID = 0

BOOTSTRAP_SLUG = "bootstrap"
# The display name of the first institution. The slug stays ``bootstrap``
# (scripts, exports and outbox paths use it); only the name a person reads
# changed, in migration 8.
BOOTSTRAP_NAME = "Demonstration University"
LEGACY_BOOTSTRAP_NAME = "Bootstrap Institution"

# The database (users, sessions, the audit chain, briefings) and its SQLite
# side files are readable by the service's own user only.
DB_FILE_MODE = 0o600
SQLITE_SIDE_FILES = ("-journal", "-wal", "-shm")


def ensure_private_db_file(path: str | os.PathLike[str]) -> None:
    """Create the database file with mode 0600 before SQLite opens it, and
    tighten an existing one (and any journal, -wal, or -shm beside it) to
    0600. SQLite gives its side files the database file's mode, so a 0600
    database keeps them private too. ``:memory:`` is left alone."""
    name = os.fspath(path)
    if name == ":memory:" or name.startswith("file:"):
        return
    fd = os.open(name, os.O_WRONLY | os.O_CREAT, DB_FILE_MODE)
    os.close(fd)
    # os.open's mode is masked by umask and ignored for an existing file;
    # chmod enforces 0600 exactly.
    os.chmod(name, DB_FILE_MODE)
    for suffix in SQLITE_SIDE_FILES:
        side = name + suffix
        if os.path.exists(side):
            os.chmod(side, DB_FILE_MODE)


SCHEMA_VERSION = 8


class SchemaVersionError(RuntimeError):
    """The database records a schema version this build does not know."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {str(row[0]) for row in rows}


def _migration_1(conn: sqlite3.Connection) -> None:
    """Baseline: tenancy, datasets, DB audit log, briefings, decisions,
    recordings — plus the in-place upgrade of a pre-migration users table."""
    tables = _table_names(conn)
    upgrading_h1 = "users" in tables

    # Plain execute() statements only: executescript() commits implicitly,
    # which would leave a half-applied migration behind a crash.
    for statement in (
        """
        CREATE TABLE IF NOT EXISTS institutions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            csrf_token TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS datasets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            institution_id INTEGER NOT NULL REFERENCES institutions(id),
            name TEXT NOT NULL,
            uploaded_by TEXT NOT NULL,
            uploaded_at TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            row_counts TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 0,
            deleted_at TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS audit_events (
            institution_id INTEGER NOT NULL,
            id INTEGER NOT NULL,
            ts TEXT NOT NULL,
            type TEXT NOT NULL,
            actor TEXT NOT NULL,
            payload TEXT NOT NULL,
            prev_hash TEXT NOT NULL,
            hash TEXT NOT NULL,
            PRIMARY KEY (institution_id, id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS briefings (
            institution_id INTEGER NOT NULL REFERENCES institutions(id),
            question_id TEXT NOT NULL,
            produced_at TEXT NOT NULL,
            sections TEXT NOT NULL,
            PRIMARY KEY (institution_id, question_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS decisions (
            institution_id INTEGER NOT NULL REFERENCES institutions(id),
            decision_id TEXT NOT NULL,
            approved_by TEXT NOT NULL,
            at TEXT NOT NULL,
            task TEXT NOT NULL,
            PRIMARY KEY (institution_id, decision_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS recordings (
            institution_id INTEGER NOT NULL REFERENCES institutions(id),
            role TEXT NOT NULL,
            key TEXT NOT NULL,
            json TEXT NOT NULL,
            PRIMARY KEY (institution_id, role, key)
        )
        """,
    ):
        conn.execute(statement)

    if upgrading_h1:
        # The old users.institution_id was a nullable free-text column. Every
        # existing user joins the bootstrap institution; the table is
        # rebuilt because SQLite cannot tighten a column to NOT NULL.
        conn.execute(
            "INSERT OR IGNORE INTO institutions (name, slug, created_at)"
            " VALUES (?, ?, ?)",
            (BOOTSTRAP_NAME, BOOTSTRAP_SLUG, _now()),
        )
        bootstrap_id = int(
            conn.execute(
                "SELECT id FROM institutions WHERE slug = ?", (BOOTSTRAP_SLUG,)
            ).fetchone()[0]
        )
        # users_h2 is a temporary build table: a database left over from a
        # crash in the pre-transactional runner may still carry it.
        conn.execute("DROP TABLE IF EXISTS users_h2")
        conn.execute(
            """
            CREATE TABLE users_h2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                institution_id INTEGER NOT NULL REFERENCES institutions(id),
                created_at TEXT NOT NULL,
                disabled INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            "INSERT INTO users_h2 (id, email, password_hash, role,"
            " institution_id, created_at, disabled)"
            " SELECT id, email, password_hash, role, ?, created_at, disabled"
            " FROM users",
            (bootstrap_id,),
        )
        conn.execute("DROP TABLE users")
        conn.execute("ALTER TABLE users_h2 RENAME TO users")
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                institution_id INTEGER NOT NULL REFERENCES institutions(id),
                created_at TEXT NOT NULL,
                disabled INTEGER NOT NULL DEFAULT 0
            )
            """
        )


def _migration_2(conn: sqlite3.Connection) -> None:
    """Review fixes: pin briefings and approvals to the dataset they were
    computed from (activating a new dataset must invalidate both), and index
    the audit log for the per-type latest-event lookups."""
    for table in ("briefings", "decisions"):
        columns = {
            str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")
        }
        if "dataset_id" not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN dataset_id INTEGER")
        if "dataset_sha256" not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN dataset_sha256 TEXT")
        # Rows written before this migration belong to whatever dataset was
        # active when they were produced — the institution's active dataset.
        conn.execute(
            f"UPDATE {table} SET"
            " dataset_id = ("
            "   SELECT d.id FROM datasets d"
            f"   WHERE d.institution_id = {table}.institution_id"
            "     AND d.is_active = 1 AND d.deleted_at IS NULL"
            "   ORDER BY d.id DESC LIMIT 1),"
            " dataset_sha256 = ("
            "   SELECT d.sha256 FROM datasets d"
            f"   WHERE d.institution_id = {table}.institution_id"
            "     AND d.is_active = 1 AND d.deleted_at IS NULL"
            "   ORDER BY d.id DESC LIMIT 1)"
            " WHERE dataset_id IS NULL"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_events_institution_type_id"
        " ON audit_events (institution_id, type, id)"
    )


def _migration_3(conn: sqlite3.Connection) -> None:
    """The decisions primary key gains ``dataset_id``.

    Migration 2 pinned approvals to their dataset as data, but the primary
    key stayed ``(institution_id, decision_id)`` — so after a dataset
    switch the re-approval's INSERT OR IGNORE hit the old row, inserted
    nothing, and the API answered ``created: False`` with no events. SQLite
    cannot alter a primary key in place, so the table is rebuilt (the same
    pattern migration 1 used for the old users table): create, copy, drop,
    rename — inside this migration's transaction. ``dataset_id`` stays
    nullable in the new key (a pre-migration-2 row whose institution had no
    active dataset to backfill from keeps its NULL; SQLite treats NULLs as
    distinct, which is harmless because new approvals always carry a real
    dataset id).
    """
    # decisions_v3 is a temporary build table: drop a leftover from a crash
    # in a pre-transactional runner before recreating it.
    conn.execute("DROP TABLE IF EXISTS decisions_v3")
    conn.execute(
        """
        CREATE TABLE decisions_v3 (
            institution_id INTEGER NOT NULL REFERENCES institutions(id),
            decision_id TEXT NOT NULL,
            approved_by TEXT NOT NULL,
            at TEXT NOT NULL,
            task TEXT NOT NULL,
            dataset_id INTEGER,
            dataset_sha256 TEXT,
            PRIMARY KEY (institution_id, decision_id, dataset_id)
        )
        """
    )
    conn.execute(
        "INSERT INTO decisions_v3 (institution_id, decision_id, approved_by,"
        " at, task, dataset_id, dataset_sha256)"
        " SELECT institution_id, decision_id, approved_by, at, task,"
        " dataset_id, dataset_sha256 FROM decisions"
    )
    conn.execute("DROP TABLE decisions")
    conn.execute("ALTER TABLE decisions_v3 RENAME TO decisions")


def _migration_4(conn: sqlite3.Connection) -> None:
    """The briefings primary key gains ``dataset_id``.

    Migration 2 pinned briefings to their dataset as data, but the primary
    key stayed ``(institution_id, question_id)`` — so a re-ask after a
    dataset switch was an INSERT OR REPLACE that DESTROYED the previous
    dataset's briefing row, and re-activating that dataset found its
    briefing gone (404 until asked again). Briefings are meant to be keyed
    per dataset, like decisions since migration 3, and SQLite cannot alter
    a primary key in place — same rebuild pattern: create, copy, drop,
    rename, inside this migration's transaction. ``dataset_id`` stays
    nullable in the new key for the same reason as in migration 3 (a row
    with no active dataset to backfill from keeps its NULL; new briefings
    always carry a real dataset id).
    """
    # briefings_v4 is a temporary build table: drop a leftover from a crash
    # in a pre-transactional runner before recreating it.
    conn.execute("DROP TABLE IF EXISTS briefings_v4")
    conn.execute(
        """
        CREATE TABLE briefings_v4 (
            institution_id INTEGER NOT NULL REFERENCES institutions(id),
            dataset_id INTEGER,
            question_id TEXT NOT NULL,
            produced_at TEXT NOT NULL,
            sections TEXT NOT NULL,
            dataset_sha256 TEXT,
            PRIMARY KEY (institution_id, dataset_id, question_id)
        )
        """
    )
    conn.execute(
        "INSERT INTO briefings_v4 (institution_id, dataset_id, question_id,"
        " produced_at, sections, dataset_sha256)"
        " SELECT institution_id, dataset_id, question_id, produced_at,"
        " sections, dataset_sha256 FROM briefings"
    )
    conn.execute("DROP TABLE briefings")
    conn.execute("ALTER TABLE briefings_v4 RENAME TO briefings")


def _migration_5(conn: sqlite3.Connection) -> None:
    """The governed execution step: dispatches and office contacts.

    ``dispatches`` holds one message per approved task per dataset (the
    UNIQUE constraint is the anchor; a re-compose or a double-clicked Send
    hits it instead of duplicating). The message itself is composed in code
    from the findings (``cabinet.questions``) and is only ever sent by a
    named staff member's click; ``status`` moves draft -> sent (or failed),
    and a sent row is never resent — the API answers 409 with the earlier
    record. ``sent_by``/``sent_at``/``provider``/``provider_ref`` stay NULL
    until a real send happens, so a draft can never masquerade as sent.

    ``office_contacts`` maps an office name to its mailbox per institution.
    The recipient of a dispatch is always one of these office mailboxes,
    configured by the institution's admin — never a student address, which
    the schema has no column for.
    """
    for statement in (
        """
        CREATE TABLE IF NOT EXISTS office_contacts (
            institution_id INTEGER NOT NULL REFERENCES institutions(id),
            office TEXT NOT NULL,
            email TEXT NOT NULL,
            PRIMARY KEY (institution_id, office)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS dispatches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            institution_id INTEGER NOT NULL REFERENCES institutions(id),
            task_id TEXT NOT NULL,
            dataset_id INTEGER NOT NULL,
            to_office TEXT NOT NULL,
            channel TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('draft', 'sent', 'failed')),
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            sent_by TEXT,
            sent_at TEXT,
            provider TEXT,
            provider_ref TEXT,
            error TEXT,
            UNIQUE (institution_id, task_id, dataset_id)
        )
        """,
    ):
        conn.execute(statement)


def _migration_6(conn: sqlite3.Connection) -> None:
    """The Financial Aid review queue: ``aid_reviews``.

    One row per student in the M3 population, created once per authorized
    emergency-aid review decision per dataset. The UNIQUE constraint is the
    idempotency anchor, so a second request (or a double click) cannot queue
    a student twice. ``facts_json`` holds only the record fields the aid
    office needs to start its own review (the M3 hold, registration status,
    advising status), never a counseling field. ``status`` and ``note`` are
    set by a person in the aid role; the system never fills them in.
    ``dataset_id`` ties the rows to their dataset, so purging a dataset
    purges its queue with it.
    """
    for statement in (
        """
        CREATE TABLE IF NOT EXISTS aid_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            institution_id INTEGER NOT NULL REFERENCES institutions(id),
            dataset_id INTEGER NOT NULL,
            decision_id TEXT NOT NULL,
            student_id TEXT NOT NULL,
            facts_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open'
                CHECK (status IN ('open', 'in_review', 'closed')),
            note TEXT NOT NULL DEFAULT '',
            updated_by TEXT,
            updated_at TEXT,
            created_at TEXT NOT NULL,
            UNIQUE (institution_id, decision_id, dataset_id, student_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_aid_reviews_institution_dataset"
        " ON aid_reviews (institution_id, dataset_id)",
    ):
        conn.execute(statement)


# The counseling aggregate authorization, one set of columns on the
# institution row (migration 7). Kept as a module constant so the store, the
# migration and its test agree on the exact column names.
COUNSELING_AUTHORIZATION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("counseling_aggregate_authorized", "INTEGER NOT NULL DEFAULT 0"),
    ("counseling_aggregate_authorized_by", "TEXT"),
    ("counseling_aggregate_document_reference", "TEXT"),
    ("counseling_aggregate_recorded_by", "TEXT"),
    ("counseling_aggregate_recorded_at", "TEXT"),
)


def _migration_7(conn: sqlite3.Connection) -> None:
    """The counseling aggregate authorization on the ``institutions`` row.

    An institution's counseling director may authorize, in writing, one
    aggregate figure (M9, a count with no rows); an admin records that
    authorization here. Every existing institution starts unauthorized
    (``DEFAULT 0``), so upgrading a database changes nothing a user can see.
    SQLite has no ``ADD COLUMN IF NOT EXISTS``, so each column is checked
    first: a database that already carries a column (a hand repair, or a
    retried migration) is upgraded without a "duplicate column" failure.
    """
    existing = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(institutions)")
    }
    for name, declaration in COUNSELING_AUTHORIZATION_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE institutions ADD COLUMN {name} {declaration}")


def _migration_8(conn: sqlite3.Connection) -> None:
    """The first institution's display name: "Bootstrap Institution"
    becomes "Demonstration University". Only a row that still carries the
    old name under the ``bootstrap`` slug is renamed, so an institution an
    admin has already renamed keeps its name. The slug is unchanged. A new
    database is created with the new name already (migration 1 seeds
    ``BOOTSTRAP_NAME``), so there this is a no-op.
    """
    conn.execute(
        "UPDATE institutions SET name = ? WHERE slug = ? AND name = ?",
        (BOOTSTRAP_NAME, BOOTSTRAP_SLUG, LEGACY_BOOTSTRAP_NAME),
    )


MIGRATIONS: list[tuple[int, str, Callable[[sqlite3.Connection], None]]] = [
    (1, "h2 tenancy baseline", _migration_1),
    (2, "r3 dataset pinning and audit index", _migration_2),
    (3, "r3b decisions keyed per dataset", _migration_3),
    (4, "r4 briefings keyed per dataset", _migration_4),
    (5, "dispatches and office contacts", _migration_5),
    (6, "financial aid review queue", _migration_6),
    (7, "counseling aggregate authorization", _migration_7),
    (SCHEMA_VERSION, "demonstration institution display name", _migration_8),
]


def recorded_versions(conn: sqlite3.Connection) -> list[int]:
    """Versions in ``schema_migrations`` (empty when the table is absent)."""
    if "schema_migrations" not in _table_names(conn):
        return []
    return sorted(
        int(row[0]) for row in conn.execute("SELECT version FROM schema_migrations")
    )


def migrate(conn: sqlite3.Connection) -> list[int]:
    """Apply pending migrations; return the versions applied this call.

    Raises :class:`SchemaVersionError` when the database records a version
    this build does not know — the caller must refuse to start.
    """
    known = {version for version, _, _ in MIGRATIONS}
    recorded = recorded_versions(conn)
    unknown = [version for version in recorded if version not in known]
    if unknown:
        raise SchemaVersionError(
            f"the database records schema version(s) {unknown} that this "
            f"build does not know (this build knows {sorted(known)}); the "
            "database was written by a newer or different build — refusing "
            "to start rather than guess"
        )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    applied: list[int] = []
    for version, name, migration in MIGRATIONS:
        if version in recorded:
            continue
        # Explicit BEGIN: Python's legacy isolation only opens implicit
        # transactions around DML, so `with conn:` alone would let DDL
        # (CREATE/ALTER/DROP) auto-commit and leave a half-applied migration
        # behind a crash. Inside an explicit transaction every statement,
        # DDL included, rolls back together.
        conn.execute("BEGIN")
        try:
            migration(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at)"
                " VALUES (?, ?, ?)",
                (version, name, _now()),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        applied.append(version)
    return applied


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m cabinet.migrations")
    parser.add_argument(
        "--db",
        default=None,
        help="database path (default: CABINET_DB or var/cabinet.db)",
    )
    args = parser.parse_args(argv)
    if args.db:
        db_path = args.db
    else:
        from cabinet.auth import db_path_from_env

        try:
            db_path = str(db_path_from_env())
        except RuntimeError as exc:
            print(f"migrate: {exc}", file=sys.stderr)
            return 1
    ensure_private_db_file(db_path)
    conn = sqlite3.connect(db_path)
    try:
        before = recorded_versions(conn)
        applied = migrate(conn)
        after = recorded_versions(conn)
    except SchemaVersionError as exc:
        print(f"migrate: {exc}", file=sys.stderr)
        return 1
    except sqlite3.Error as exc:
        print(
            f"migrate: {db_path}: the migration failed ({exc}); nothing was "
            "recorded — fix the database and re-run",
            file=sys.stderr,
        )
        return 1
    finally:
        conn.close()
    if applied:
        print(f"{db_path}: applied migration(s) {applied}; now at version {after}")
    else:
        print(f"{db_path}: already at version {after or before or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
