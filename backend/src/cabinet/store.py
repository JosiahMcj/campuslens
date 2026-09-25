"""Durable storage: every fact lives in ``var/cabinet.db``, scoped by
institution, and dataset documents live as files under ``var/data/``.

Tables (created and versioned by ``cabinet.migrations``):

- ``institutions(id, name, slug UNIQUE, created_at)`` — the tenants.
- ``users(..., institution_id NOT NULL, ...)`` and ``sessions(...)`` — the
  original auth tables, now tenanted; the password hashing and session signing
  stay in ``cabinet.auth``.
- ``datasets(id, institution_id, name, uploaded_by, uploaded_at, sha256,
  row_counts, is_active, deleted_at)`` — one row per uploaded dataset; the
  document itself is ``var/data/<institution slug>/<dataset id>.json``
  written with 0600 permissions. Deletion is soft (``deleted_at``);
  ``purge_deleted_datasets`` hard-deletes after the retention window.
- ``audit_events(institution_id, id, ts, type, actor, payload, prev_hash,
  hash)`` — the primary audit store (the JSONL file is replaced;
  ``python -m cabinet.audit export`` writes JSONL and ``verify`` checks
  both). Ids and the sha256 chain are per institution; scope 0 is the
  platform chain for pre-auth events.
- ``briefings(institution_id, dataset_id, question_id, produced_at,
  sections, dataset_sha256)`` — the latest produced briefing per question
  AND dataset (the primary key, migration 4), so ``GET /briefing`` survives
  restarts and re-activating an earlier dataset restores that dataset's own
  briefing. The dataset columns pin the briefing to the dataset it was
  computed from: activating another dataset retires it from view
  (``latest_briefing`` filters by the active dataset) without deleting it.
- ``decisions(institution_id, decision_id, approved_by, at, task,
  dataset_id, dataset_sha256)`` — approvals, pinned to their dataset the
  same way; the primary key ``(institution_id, decision_id, dataset_id)``
  is the idempotency anchor, so a re-approval after a dataset switch is a
  new row (migration 3).
- ``recordings(institution_id, role, key, json)`` — validated model outputs
  per institution; the key is the sha256 of the canonical received findings
  (which covers the dataset content and the question).

Dataset file bytes are verified against the stored sha256 on every load, so
a tampered or truncated file fails loudly instead of silently changing the
findings.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cabinet.audit import EVENT_TYPES, GENESIS_HASH, event_hash
from cabinet.fixture import parse_fixture
from cabinet.migrations import (
    BOOTSTRAP_NAME,
    BOOTSTRAP_SLUG,
    PLATFORM_INSTITUTION_ID,
    migrate,
)

DEMO_DATASET_NAME = "Demonstration (fictional)"
DATASET_FILE_MODE = 0o600
DATASET_DIR_MODE = 0o700
PURGE_AFTER_DAYS = 30


class StoreError(RuntimeError):
    """A storage-level failure (integrity, missing rows)."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ScopedAudit:
    """The audit log of one institution (or the platform scope 0).

    Same interface the JSONL ``AuditLog`` exposes to the rest of the
    code: ``append``, ``append_once``, ``events``. Ids and the hash chain
    are per scope, so two institutions both have an event id 1 and neither
    can read the other's chain.
    """

    def __init__(self, store: CabinetStore, institution_id: int) -> None:
        self._store = store
        self.institution_id = institution_id

    def append(
        self, event_type: str, *, actor: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return self._store.audit_append(
            self.institution_id, event_type, actor=actor, payload=payload
        )

    def append_once(
        self,
        event_type: str,
        match_field: str,
        match_value: Any,
        events: list[tuple[str, str, dict[str, Any]]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        return self._store.audit_append_once(
            self.institution_id, event_type, match_field, match_value, events
        )

    def events(self, event_type: str | None = None) -> list[dict[str, Any]]:
        return self._store.audit_events(self.institution_id, event_type)


class CabinetStore:
    """Thread-safe handle on the whole cabinet database.

    ``seed_fixture`` is the document seeded as the "Demonstration
    (fictional)" dataset for every newly created institution (the app
    passes ``CABINET_FIXTURE`` or ``data/fixture.json``). A store opened
    without it cannot create institutions.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        data_dir: str | Path | None = None,
        seed_fixture: str | Path | None = None,
    ) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._data_dir = (
            Path(data_dir) if data_dir is not None else self._path.parent / "data"
        )
        self._seed_fixture = Path(seed_fixture) if seed_fixture is not None else None
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            migrate(self._conn)
        self._scoped_audits: dict[int, ScopedAudit] = {}

    @property
    def path(self) -> Path:
        return self._path

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- institutions -----------------------------------------------------

    def create_institution(self, name: str, slug: str) -> int:
        """Create one institution and seed its fictional demonstration
        dataset. Returns the new institution id.

        The fallible work comes first (reading and validating the seed
        document), and the institution row, the dataset row, and the
        dataset file commit as one unit: on any failure nothing persists,
        so a crash cannot orphan a tenant with no active dataset.
        """
        name = name.strip()
        slug = slug.strip().lower()
        if not name or not slug:
            raise ValueError("institution name and slug must not be empty")
        if self._seed_fixture is None:
            raise ValueError(
                "this store has no seed fixture; cannot create an institution"
            )
        raw = self._seed_fixture.read_bytes()
        counts = self.row_counts_of(raw)  # validates before anything is stored
        sha256 = hashlib.sha256(raw).hexdigest()
        dataset_dir = self._data_dir / slug
        staged = self._stage_dataset_file(dataset_dir, raw)
        written: Path | None = None
        try:
            with self._lock:
                cursor = self._conn.execute(
                    "INSERT INTO institutions (name, slug, created_at)"
                    " VALUES (?, ?, ?)",
                    (name, slug, _now()),
                )
                institution_id = int(cursor.lastrowid)  # type: ignore[arg-type]
                _, written = self._add_dataset_locked(
                    institution_id,
                    name=DEMO_DATASET_NAME,
                    sha256=sha256,
                    staged=staged,
                    uploaded_by="system",
                    row_counts=counts,
                    activate=True,
                )
                self._conn.commit()
        except sqlite3.IntegrityError:
            self._conn.rollback()
            self._discard_staged(staged, dataset_dir)
            raise ValueError(
                f"an institution with slug {slug!r} already exists"
            ) from None
        except Exception:
            self._conn.rollback()
            if written is not None:
                with contextlib.suppress(FileNotFoundError):
                    written.unlink()
            self._discard_staged(staged, dataset_dir)
            raise
        return institution_id

    def ensure_bootstrap_institution(self) -> int:
        """The bootstrap institution, creating and seeding it on first use."""
        row = self.institution_by_slug(BOOTSTRAP_SLUG)
        if row is not None:
            return int(row["id"])
        return self.create_institution(BOOTSTRAP_NAME, BOOTSTRAP_SLUG)

    def institution_by_slug(self, slug: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM institutions WHERE slug = ?", (slug.strip().lower(),)
            ).fetchone()
        return dict(row) if row is not None else None

    def institution_by_id(self, institution_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM institutions WHERE id = ?", (institution_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def list_institutions(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM institutions ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]

    # -- users and sessions (the AuthStore surface) -------------------------

    def create_user(
        self,
        email: str,
        password: str,
        role: str,
        institution_id: int | None = None,
    ) -> int:
        """Insert one user and return its id.

        ``institution_id`` defaults to the bootstrap institution. Raises
        ``ValueError`` for an unknown role, an empty email, a duplicate
        email, or an institution that does not exist.
        """
        from cabinet.auth import USER_ROLES, hash_password

        email = email.strip().lower()
        if role not in USER_ROLES:
            raise ValueError(
                f"unknown role {role!r}; expected one of {', '.join(USER_ROLES)}"
            )
        if not email:
            raise ValueError("email must not be empty")
        if institution_id is None:
            bootstrap = self.institution_by_slug(BOOTSTRAP_SLUG)
            if bootstrap is None:
                raise ValueError(
                    "no institution given and the bootstrap institution does "
                    "not exist yet"
                )
            institution_id = int(bootstrap["id"])
        elif self.institution_by_id(institution_id) is None:
            raise ValueError(f"unknown institution id {institution_id}")
        with self._lock:
            try:
                cursor = self._conn.execute(
                    "INSERT INTO users (email, password_hash, role, institution_id,"
                    " created_at, disabled) VALUES (?, ?, ?, ?, ?, 0)",
                    (email, hash_password(password), role, institution_id, _now()),
                )
                self._conn.commit()
            except sqlite3.IntegrityError:
                raise ValueError(
                    f"a user with email {email!r} already exists"
                ) from None
        return int(cursor.lastrowid)  # type: ignore[arg-type]

    def user_by_email(self, email: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE email = ?", (email.strip().lower(),)
            ).fetchone()
        return dict(row) if row is not None else None

    def user_by_id(self, user_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def has_admin(self) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM users WHERE role = 'admin' LIMIT 1"
            ).fetchone()
        return row is not None

    # -- user administration (the API's /admin/users routes) -------------------

    def users_for(self, institution_id: int) -> list[dict[str, Any]]:
        """Every user of one institution, in id order."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM users WHERE institution_id = ? ORDER BY id",
                (institution_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def user_in_institution(
        self, institution_id: int, user_id: int
    ) -> dict[str, Any] | None:
        """One user of one institution — None when the id belongs to
        another institution (cross-tenant reads look like absence)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE id = ? AND institution_id = ?",
                (user_id, institution_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def enabled_admin_count(self, institution_id: int) -> int:
        """How many of the institution's admins are enabled right now; the
        last-admin protections key off this."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM users WHERE institution_id = ?"
                " AND role = 'admin' AND disabled = 0",
                (institution_id,),
            ).fetchone()
        return int(row["n"])

    def set_user_disabled(
        self, institution_id: int, user_id: int, disabled: bool
    ) -> dict[str, Any] | None:
        """Set one user's disabled flag and return the updated row; None
        when the user is not in this institution. Idempotent."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE users SET disabled = ? WHERE id = ? AND institution_id = ?",
                (1 if disabled else 0, user_id, institution_id),
            )
        return self.user_in_institution(institution_id, user_id)

    def set_user_role(
        self, institution_id: int, user_id: int, role: str
    ) -> dict[str, Any] | None:
        """Set one user's role and return the updated row; None when the
        user is not in this institution. Raises ValueError on an unknown
        role."""
        from cabinet.auth import USER_ROLES

        if role not in USER_ROLES:
            raise ValueError(
                f"unknown role {role!r}; expected one of {', '.join(USER_ROLES)}"
            )
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE users SET role = ? WHERE id = ? AND institution_id = ?",
                (role, user_id, institution_id),
            )
        return self.user_in_institution(institution_id, user_id)

    # -- datasets ------------------------------------------------------------

    def _dataset_dir(self, institution_id: int) -> Path:
        institution = self.institution_by_id(institution_id)
        if institution is None:
            raise StoreError(f"unknown institution id {institution_id}")
        return self._data_dir / str(institution["slug"])

    def dataset_path(self, dataset: dict[str, Any]) -> Path:
        """The file holding a dataset row's document."""
        return self._dataset_dir(int(dataset["institution_id"])) / (
            f"{dataset['id']}.json"
        )

    def _write_dataset_file(self, path: Path, raw: bytes) -> None:
        """Write a dataset document with 0600 permissions (0700 dirs)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, DATASET_DIR_MODE)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, DATASET_FILE_MODE)
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        # os.open's mode is masked by umask; chmod enforces 0600 exactly.
        os.chmod(path, DATASET_FILE_MODE)

    def _stage_dataset_file(self, dataset_dir: Path, raw: bytes) -> Path:
        """Write the document to a temp file in the dataset directory and
        return its path. This is the multi-MB part of an upload, and it
        runs BEFORE the store lock is taken — under the lock it stalled
        every other tenant's store call for the whole write + fsync. The
        temp file sits in the same directory as its final path so the
        commit-time ``os.replace`` is atomic."""
        staged = dataset_dir / f".staged-{secrets.token_hex(8)}.tmp"
        self._write_dataset_file(staged, raw)
        return staged

    @staticmethod
    def _discard_staged(staged: Path, dataset_dir: Path) -> None:
        """Remove a staged file whose dataset never committed, plus the
        dataset directory itself when staging created it and nothing else
        lives there (a failed first upload must leave no empty tenant
        directory behind)."""
        with contextlib.suppress(FileNotFoundError):
            staged.unlink()
        with contextlib.suppress(OSError):
            dataset_dir.rmdir()

    @staticmethod
    def row_counts_of(raw: bytes) -> dict[str, int]:
        """Row counts from a validated dataset document."""
        document = json.loads(raw.decode("utf-8"))
        fixture = parse_fixture(document)
        return {
            "students": len(fixture.students),
            "prior_year_students": len(fixture.prior_year_students),
        }

    def _add_dataset_locked(
        self,
        institution_id: int,
        *,
        name: str,
        sha256: str,
        staged: Path,
        uploaded_by: str,
        row_counts: dict[str, int],
        activate: bool,
    ) -> tuple[int, Path]:
        """Insert the dataset row and move its staged file into place,
        uncommitted; the caller commits (one transaction covering this and
        whatever the rows belong to) and removes the returned path if the
        commit fails. The staged file was written and fsync'd before the
        lock was taken, so under the lock there is only the INSERT and the
        atomic ``os.replace``. A failed INSERT leaves no file; on a
        replace/activation failure the partial file is removed here."""
        cursor = self._conn.execute(
            "INSERT INTO datasets (institution_id, name, uploaded_by,"
            " uploaded_at, sha256, row_counts, is_active, deleted_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 0, NULL)",
            (
                institution_id,
                name,
                uploaded_by,
                _now(),
                sha256,
                json.dumps(row_counts, sort_keys=True),
            ),
        )
        dataset_id = int(cursor.lastrowid)  # type: ignore[arg-type]
        path = self._dataset_dir(institution_id) / f"{dataset_id}.json"
        try:
            os.replace(staged, path)
            if activate:
                # The file was staged from exactly the bytes whose sha256
                # the row carries; the activation re-read is skipped.
                self._set_active_dataset_locked(
                    institution_id, dataset_id, verified=True
                )
        except Exception:
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
            with contextlib.suppress(FileNotFoundError):
                staged.unlink()
            raise
        return dataset_id, path

    def add_dataset(
        self,
        institution_id: int,
        *,
        name: str,
        raw: bytes,
        uploaded_by: str,
        row_counts: dict[str, int] | None = None,
        activate: bool = False,
    ) -> dict[str, Any]:
        """Store one validated dataset document and return its row.

        The sha256 is computed here from the stored bytes — never trusted
        from the caller. Validation happens before anything persists, the
        row and the file commit as one unit (on failure neither survives),
        and the dataset starts inactive unless ``activate``; the previously
        active dataset stays until it is deleted.
        """
        name = name.strip()
        if not name:
            raise ValueError("dataset name must not be empty")
        counts = row_counts if row_counts is not None else self.row_counts_of(raw)
        # The sha256 is computed here from the stored bytes — never trusted
        # from the caller — and the write + fsync is staged before the lock
        # is taken: up to 20 MB of hashing and I/O under the lock stalls
        # every other tenant's store calls for the duration.
        sha256 = hashlib.sha256(raw).hexdigest()
        staged = self._stage_dataset_file(self._dataset_dir(institution_id), raw)
        written: Path | None = None
        try:
            with self._lock:
                dataset_id, written = self._add_dataset_locked(
                    institution_id,
                    name=name,
                    sha256=sha256,
                    staged=staged,
                    uploaded_by=uploaded_by,
                    row_counts=counts,
                    activate=activate,
                )
                self._conn.commit()
        except Exception:
            self._conn.rollback()
            if written is not None:
                with contextlib.suppress(FileNotFoundError):
                    written.unlink()
            self._discard_staged(staged, staged.parent)
            raise
        dataset = self.dataset_row(institution_id, dataset_id)
        assert dataset is not None  # just committed
        return dataset

    def seed_demo_dataset(self, institution_id: int) -> dict[str, Any]:
        """Seed the fictional demonstration dataset for a new institution.

        Raises whatever the fixture loader raises for a malformed seed
        document — the app turns that into the one-line startup refusal.
        """
        if self._seed_fixture is None:
            raise ValueError("this store has no seed fixture")
        raw = self._seed_fixture.read_bytes()
        counts = self.row_counts_of(raw)  # validates before anything is stored
        return self.add_dataset(
            institution_id,
            name=DEMO_DATASET_NAME,
            raw=raw,
            uploaded_by="system",
            row_counts=counts,
            activate=True,
        )

    def dataset_row(
        self, institution_id: int, dataset_id: int, *, include_deleted: bool = False
    ) -> dict[str, Any] | None:
        """One dataset of one institution — None when the id belongs to
        another institution (cross-tenant reads look like absence)."""
        sql = "SELECT * FROM datasets WHERE id = ? AND institution_id = ?"
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        with self._lock:
            row = self._conn.execute(sql, (dataset_id, institution_id)).fetchone()
        return dict(row) if row is not None else None

    def datasets_for(self, institution_id: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM datasets WHERE institution_id = ?"
                " AND deleted_at IS NULL ORDER BY id",
                (institution_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def active_dataset(self, institution_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM datasets WHERE institution_id = ? AND is_active = 1"
                " AND deleted_at IS NULL ORDER BY id DESC LIMIT 1",
                (institution_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def set_active_dataset(self, institution_id: int, dataset_id: int) -> None:
        """Make one dataset the institution's active one, atomically.

        The dataset's document is verified first: a missing file or a
        sha256 mismatch is a :class:`StoreError` (the API turns it into a
        503 with a plain reason) and no UPDATE has run — the previously
        active dataset stays active. The verification read + hash (up to
        20 MB) happens OUTSIDE the store lock; the lock covers only the
        two UPDATEs. Every load still re-verifies against the stored
        sha256, so a tampered file fails loudly regardless.
        """
        dataset = self.dataset_row(institution_id, dataset_id)
        if dataset is None:
            raise StoreError(
                f"dataset {dataset_id} does not exist in institution "
                f"{institution_id}"
            )
        # Fail loudly now, not as a 500 on the next request, when the
        # document is gone or tampered.
        self.read_dataset_bytes(dataset)
        with self._lock, self._conn:
            self._set_active_dataset_locked(institution_id, dataset_id, verified=True)

    def _set_active_dataset_locked(
        self, institution_id: int, dataset_id: int, *, verified: bool = False
    ) -> None:
        self._conn.execute(
            "UPDATE datasets SET is_active = 0 WHERE institution_id = ?",
            (institution_id,),
        )
        cursor = self._conn.execute(
            "UPDATE datasets SET is_active = 1 WHERE id = ?"
            " AND institution_id = ? AND deleted_at IS NULL",
            (dataset_id, institution_id),
        )
        if cursor.rowcount != 1:
            raise StoreError(
                f"dataset {dataset_id} does not exist in institution "
                f"{institution_id}"
            )
        if not verified:
            dataset = self.dataset_row(institution_id, dataset_id)
            assert dataset is not None  # just activated
            # Fail loudly now, not as a 500 on the next request, when the
            # document is gone or tampered.
            self.read_dataset_bytes(dataset)

    def soft_delete_dataset(self, institution_id: int, dataset_id: int) -> None:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE datasets SET deleted_at = ? WHERE id = ?"
                " AND institution_id = ? AND deleted_at IS NULL",
                (_now(), dataset_id, institution_id),
            )
            if cursor.rowcount != 1:
                raise StoreError(
                    f"dataset {dataset_id} does not exist in institution "
                    f"{institution_id} (or is already deleted)"
                )

    def purge_deleted_datasets(
        self, *, older_than_days: int = PURGE_AFTER_DAYS
    ) -> list[dict[str, Any]]:
        """Hard-delete datasets soft-deleted more than ``older_than_days``
        ago: the row and the file. Returns the purged rows."""
        cutoff = (datetime.now(UTC) - timedelta(days=older_than_days)).isoformat()
        with self._lock:
            rows = [
                dict(row)
                for row in self._conn.execute(
                    "SELECT * FROM datasets WHERE deleted_at IS NOT NULL"
                    " AND deleted_at < ?",
                    (cutoff,),
                ).fetchall()
            ]
            for row in rows:
                path = self.dataset_path(row)
                with contextlib.suppress(FileNotFoundError):
                    path.unlink()
                self._conn.execute("DELETE FROM datasets WHERE id = ?", (row["id"],))
            self._conn.commit()
        return rows

    def read_dataset_bytes(self, dataset: dict[str, Any]) -> bytes:
        """The dataset document, verified against the stored sha256.

        A file that is missing on disk (the row outlived its document) is a
        StoreError with a plain reason, same as a tampered one.
        """
        path = self.dataset_path(dataset)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            raise StoreError(
                f"dataset file {path} is missing on disk; the dataset row "
                "exists but its document is gone"
            ) from None
        digest = hashlib.sha256(raw).hexdigest()
        if digest != dataset["sha256"]:
            raise StoreError(
                f"dataset file {path} does not match its recorded sha256 "
                f"({dataset['sha256']}); the file was changed on disk"
            )
        return raw

    # -- audit events ---------------------------------------------------------

    def audit_for(self, institution_id: int) -> ScopedAudit:
        """The scoped audit handle for one institution (cached, so analyst
        runs see a stable log object per institution)."""
        with self._lock:
            scoped = self._scoped_audits.get(institution_id)
            if scoped is None:
                scoped = ScopedAudit(self, institution_id)
                self._scoped_audits[institution_id] = scoped
            return scoped

    def _audit_append_locked(
        self, institution_id: int, event_type: str, actor: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if event_type not in EVENT_TYPES:
            raise ValueError(
                f"unknown audit event type {event_type!r}; "
                f"expected one of {', '.join(EVENT_TYPES)}"
            )
        row = self._conn.execute(
            "SELECT id, hash FROM audit_events WHERE institution_id = ?"
            " ORDER BY id DESC LIMIT 1",
            (institution_id,),
        ).fetchone()
        next_id = 1 if row is None else int(row["id"]) + 1
        prev_hash = GENESIS_HASH if row is None else str(row["hash"])
        event: dict[str, Any] = {
            "id": next_id,
            "ts": _now(),
            "type": event_type,
            "actor": actor,
            "payload": payload,
            "prev_hash": prev_hash,
        }
        event["hash"] = event_hash(event)
        self._conn.execute(
            "INSERT INTO audit_events (institution_id, id, ts, type, actor,"
            " payload, prev_hash, hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                institution_id,
                event["id"],
                event["ts"],
                event["type"],
                event["actor"],
                json.dumps(event["payload"], ensure_ascii=False),
                event["prev_hash"],
                event["hash"],
            ),
        )
        self._conn.commit()
        return event

    def audit_append(
        self,
        institution_id: int,
        event_type: str,
        *,
        actor: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Append one event to one institution's chain and return it."""
        with self._lock:
            return self._audit_append_locked(institution_id, event_type, actor, payload)

    def audit_append_once(
        self,
        institution_id: int,
        event_type: str,
        match_field: str,
        match_value: Any,
        events: list[tuple[str, str, dict[str, Any]]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        """Atomic check-and-append within one institution's chain (the
        idempotency primitive; same contract as the JSONL AuditLog's)."""
        with self._lock:
            existing = None
            for event in self.audit_events(institution_id, event_type):
                if event.get("payload", {}).get(match_field) == match_value:
                    existing = event
                    break
            if existing is not None:
                return [], existing
            appended = [
                self._audit_append_locked(institution_id, t, actor, payload)
                for t, actor, payload in events
            ]
            return appended, None

    def audit_events(
        self, institution_id: int, event_type: str | None = None
    ) -> list[dict[str, Any]]:
        """One institution's events in id order, payloads decoded."""
        sql = (
            "SELECT id, ts, type, actor, payload, prev_hash, hash FROM"
            " audit_events WHERE institution_id = ?"
        )
        params: list[Any] = [institution_id]
        if event_type is not None:
            sql += " AND type = ?"
            params.append(event_type)
        sql += " ORDER BY id"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [
            {
                "id": int(row["id"]),
                "ts": row["ts"],
                "type": row["type"],
                "actor": row["actor"],
                "payload": json.loads(row["payload"]),
                "prev_hash": row["prev_hash"],
                "hash": row["hash"],
            }
            for row in rows
        ]

    def audit_max_id(self, institution_id: int) -> int:
        """The highest event id on one institution's chain (0 when empty).
        Paired with :meth:`audit_event_ids_after` this is how a run finds
        exactly its own events without reading the whole table."""
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(id) AS max_id FROM audit_events"
                " WHERE institution_id = ?",
                (institution_id,),
            ).fetchone()
        return int(row["max_id"]) if row["max_id"] is not None else 0

    def audit_event_ids_after(
        self, institution_id: int, after_id: int
    ) -> list[int]:
        """The ids of one institution's events with id > ``after_id``, in
        order — exactly the events appended since ``after_id`` was read."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM audit_events WHERE institution_id = ?"
                " AND id > ? ORDER BY id",
                (institution_id, after_id),
            ).fetchall()
        return [int(row["id"]) for row in rows]

    def latest_task_assigned(
        self, institution_id: int, role: str
    ) -> dict[str, Any] | None:
        """The latest task.assigned event for one role on one institution's
        chain (indexed lookup, payload role condition), or None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id, ts, type, actor, payload, prev_hash, hash FROM"
                " audit_events WHERE institution_id = ?"
                " AND type = 'task.assigned'"
                " AND json_extract(payload, '$.role') = ?"
                " ORDER BY id DESC LIMIT 1",
                (institution_id, role),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": int(row["id"]),
            "ts": row["ts"],
            "type": row["type"],
            "actor": row["actor"],
            "payload": json.loads(row["payload"]),
            "prev_hash": row["prev_hash"],
            "hash": row["hash"],
        }

    # -- briefings ------------------------------------------------------------

    def save_briefing(
        self,
        institution_id: int,
        question_id: str,
        briefing: dict[str, Any],
        *,
        dataset_id: int,
        dataset_sha256: str,
    ) -> None:
        """Persist a produced briefing (one row per institution+dataset+
        question since migration 4; a re-ask on the same dataset replaces
        only that dataset's row — the previous dataset's briefing survives
        and is served again when it is re-activated). ``sections`` holds the
        whole briefing document, including its ``sections`` key. The
        briefing is pinned to the dataset it was computed from, so
        activating another dataset retires it from view."""
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO briefings (institution_id, question_id,"
                " produced_at, sections, dataset_id, dataset_sha256)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    institution_id,
                    question_id,
                    _now(),
                    json.dumps(briefing, ensure_ascii=False),
                    dataset_id,
                    dataset_sha256,
                ),
            )
            self._conn.commit()

    def latest_briefing(
        self, institution_id: int, dataset_id: int | None = None
    ) -> dict[str, Any] | None:
        """The most recently produced briefing for an institution, or None.
        With ``dataset_id``, only briefings computed from that dataset
        count — a briefing from a retired dataset is never served."""
        sql = "SELECT sections FROM briefings WHERE institution_id = ?"
        params: list[Any] = [institution_id]
        if dataset_id is not None:
            sql += " AND dataset_id = ?"
            params.append(dataset_id)
        sql += " ORDER BY produced_at DESC, question_id DESC LIMIT 1"
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return json.loads(row["sections"]) if row is not None else None

    # -- decisions --------------------------------------------------------------

    def record_decision(
        self,
        institution_id: int,
        decision_id: str,
        approved_by: str,
        task: dict[str, Any],
        *,
        dataset_id: int,
        dataset_sha256: str,
    ) -> bool:
        """Record an approval; True when this call created it (the primary
        key is the idempotency anchor). The approval is pinned to the
        dataset the decision text was computed from."""
        with self._lock:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO decisions (institution_id, decision_id,"
                " approved_by, at, task, dataset_id, dataset_sha256)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    institution_id,
                    decision_id,
                    approved_by,
                    _now(),
                    json.dumps(task, ensure_ascii=False),
                    dataset_id,
                    dataset_sha256,
                ),
            )
            self._conn.commit()
            return cursor.rowcount == 1

    def approved_decision_ids(
        self, institution_id: int, dataset_id: int | None = None
    ) -> set[str]:
        """The approved decision ids; with ``dataset_id``, only approvals
        made against that dataset count — approvals for text computed from
        a retired dataset are not shown as approved."""
        sql = "SELECT decision_id FROM decisions WHERE institution_id = ?"
        params: list[Any] = [institution_id]
        if dataset_id is not None:
            sql += " AND dataset_id = ?"
            params.append(dataset_id)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return {str(row["decision_id"]) for row in rows}

    def decision_task(
        self,
        institution_id: int,
        decision_id: str,
        *,
        dataset_id: int | None = None,
    ) -> dict[str, Any] | None:
        """The recorded approval's task. The primary key is per
        (institution, decision, dataset) since migration 3, so with
        ``dataset_id`` the row for that dataset is returned — the one the
        caller just failed to insert."""
        sql = (
            "SELECT task FROM decisions WHERE institution_id = ?"
            " AND decision_id = ?"
        )
        params: list[Any] = [institution_id, decision_id]
        if dataset_id is not None:
            sql += " AND dataset_id = ?"
            params.append(dataset_id)
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return json.loads(row["task"]) if row is not None else None

    # -- recordings ---------------------------------------------------------------

    def save_recording(
        self, institution_id: int, role: str, key: str, payload: dict[str, Any]
    ) -> None:
        """Persist one validated model output for an institution. The key
        is the sha256 of the canonical received findings (covering the
        dataset content and the question); a re-run of the same question on
        the same data replaces it."""
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO recordings (institution_id, role, key,"
                " json) VALUES (?, ?, ?, ?)",
                (institution_id, role, key, json.dumps(payload, ensure_ascii=False)),
            )
            self._conn.commit()

    def recording(
        self, institution_id: int, role: str, key: str
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT json FROM recordings WHERE institution_id = ?"
                " AND role = ? AND key = ?",
                (institution_id, role, key),
            ).fetchone()
        return json.loads(row["json"]) if row is not None else None


__all__ = [
    "CabinetStore",
    "ScopedAudit",
    "StoreError",
    "PLATFORM_INSTITUTION_ID",
    "DEMO_DATASET_NAME",
    "PURGE_AFTER_DAYS",
]
