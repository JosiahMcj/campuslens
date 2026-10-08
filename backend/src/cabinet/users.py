"""User management CLI.

    .venv/bin/python -m cabinet.users bootstrap-admin --email admin@example.edu
    .venv/bin/python -m cabinet.users add --email exec@example.edu \
        --role executive --institution two-rivers

    .venv/bin/python -m cabinet.users demo-mailboxes [--institution two-rivers]
    .venv/bin/python -m cabinet.users demo-accounts --out ~/demo-accounts.txt

``bootstrap-admin`` creates the bootstrap institution (seeded with the
fictional demonstration dataset) and its first admin, and refuses when an
admin already exists — after bootstrapping, admins create further users
through ``add``. ``add`` attaches the user to an institution (``--institution``
slug; default the bootstrap institution). Both commands generate the
password and print it exactly once, on stdout; it is never logged or
written anywhere. ``demo-mailboxes`` gives every office the demonstration
data names an address ``<office-slug>@demo.test`` (``make demo-mailboxes``),
so a demo can show Send working into the on-machine outbox; it only adds
offices that have no mailbox, never replaces one, and refuses unless the
institution's active dataset is the fictional demonstration data.
``demo-accounts`` (``make demo-accounts OUT=...``) creates one sign-in per
demonstration persona (``DEMO_PERSONAS``: the president, IT, Finance /
Student Accounts, Financial Aid, the Registrar, Student Life, staff and a
reviewer) as ``<persona>@demo.test``, with the same fictional-data guard; an
account that already exists is left alone (its password is never reset).
The generated passwords are appended to ``--out`` (required; created mode
600 and never through a symbolic link; keep it outside the repository).
``reset-password --email`` issues one account a new password, printed once,
and ends its sessions: how an administrator gives a person IT created their
first password (IT never sees one). The
database is ``CABINET_DB`` (default ``var/cabinet.db``);
``cabinet.local.env`` is honored like the API does.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from cabinet.api import InstitutionRuntime, fixture_path_from_env
from cabinet.auth import (
    USER_ROLES,
    AuthStore,
    db_path_from_env,
    generate_password,
    hash_password,
)
from cabinet.provider import load_local_env
from cabinet.staffactions import action_specs
from cabinet.store import CabinetStore, StoreError

# A reserved top-level domain (RFC 2606): an address here can never reach a
# real mailbox, even through a live mail provider.
DEMO_MAILBOX_DOMAIN = "demo.test"


# One sign-in per demonstration persona: (address, role, display name).
DEMO_PERSONAS: tuple[tuple[str, str, str], ...] = (
    ("president@demo.test", "executive", "President"),
    ("it@demo.test", "it", "IT"),
    ("finance@demo.test", "finance", "Finance"),
    ("studentaccounts@demo.test", "studentaccounts", "Student Accounts"),
    ("aid@demo.test", "aid", "Financial Aid"),
    ("admissions@demo.test", "admissions", "Admissions"),
    ("advising@demo.test", "advising", "Advising and Student Success"),
    ("provost@demo.test", "provost", "Academic Affairs"),
    ("ir@demo.test", "ir", "Institutional Research"),
    ("careers@demo.test", "careers", "Career Services"),
    ("advancement@demo.test", "advancement", "Advancement"),
    ("international@demo.test", "international", "International Student Services"),
    ("athletics@demo.test", "athletics", "Athletics"),
    ("registrar@demo.test", "registrar", "Registrar"),
    ("studentlife@demo.test", "studentlife", "Student Life"),
    ("staff@demo.test", "staff", "Staff"),
    ("reviewer@demo.test", "reviewer", "Reviewer"),
)


class NotDemonstrationData(ValueError):
    """The active dataset is real (not the fictional demonstration data)."""


def demo_mailbox(office: str) -> str:
    """The demonstration address for one office: student-success@demo.test."""
    slug = re.sub(r"[^a-z0-9]+", "-", office.lower()).strip("-") or "office"
    return f"{slug}@{DEMO_MAILBOX_DOMAIN}"


def seed_demo_mailboxes(
    store: CabinetStore, institution_id: int
) -> list[tuple[str, str]]:
    """Give each office the demonstration data names (the staff actions'
    offices: Student Success, Financial Aid and every office with holds) a
    ``@demo.test`` mailbox, keeping every mailbox already set. Returns the
    (office, address) pairs added; adding any writes one ``admin.changed``
    audit event, like a save in Institution settings. Raises
    NotDemonstrationData unless the active dataset is the fictional one."""
    runtime = InstitutionRuntime(store, institution_id)
    if not runtime.fictional:
        raise NotDemonstrationData(
            "the active dataset is not the fictional demonstration data; set "
            "real office mailboxes in Institution settings, Offices"
        )
    existing = {
        str(row["office"]): str(row["email"])
        for row in store.office_contacts_for(institution_id)
    }
    offices = list(
        dict.fromkeys(spec.office for spec in action_specs(runtime.findings))
    )
    added = [
        (office, demo_mailbox(office)) for office in offices if office not in existing
    ]
    if not added:
        return []
    book = sorted([*existing.items(), *added])
    store.set_office_contacts(institution_id, book)
    store.audit_append(
        institution_id,
        "admin.changed",
        actor="demo-mailboxes",
        payload={
            "action": "office_contacts",
            "offices": [office for office, _ in book],
        },
    )
    return added


def seed_demo_accounts(
    store: CabinetStore, institution_id: int
) -> list[tuple[str, str, str, str]]:
    """Create the demonstration personas' sign-ins that do not exist yet.
    Returns (email, role, display name, password) for each account created;
    an existing address is skipped, never changed. Raises
    NotDemonstrationData unless the active dataset is the fictional one."""
    if not InstitutionRuntime(store, institution_id).fictional:
        raise NotDemonstrationData(
            "the active dataset is not the fictional demonstration data; "
            "create real accounts with `make user`"
        )
    created = []
    for email, role, name in DEMO_PERSONAS:
        if store.user_by_email(email) is not None:
            continue
        password = generate_password()
        store.create_user(email, password, role, institution_id=institution_id)
        created.append((email, role, name, password))
    return created


def write_private(path: Path, text: str) -> None:
    """Append ``text`` to ``path``, creating it readable by its owner only.
    A symbolic link at ``path`` is refused (O_NOFOLLOW), so the passwords
    can never be written through a link to somewhere else."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)


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

    demo = sub.add_parser(
        "demo-mailboxes",
        help="give each office in the demonstration data a @demo.test mailbox",
    )
    demo.add_argument(
        "--institution",
        default=None,
        help="institution slug (default: the bootstrap institution)",
    )

    accounts = sub.add_parser(
        "demo-accounts",
        help="create a sign-in for each demonstration persona (@demo.test)",
    )
    accounts.add_argument(
        "--institution",
        default=None,
        help="institution slug (default: the bootstrap institution)",
    )
    accounts.add_argument(
        "--out",
        required=True,
        help="append the generated passwords to this file (mode 600; keep "
        "it outside the repository)",
    )

    reset = sub.add_parser(
        "reset-password",
        help="issue a new one-time password for one account (printed once)",
    )
    reset.add_argument("--email", required=True)

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
    if args.command == "demo-mailboxes":
        return _demo_mailboxes(store, args.institution)
    if args.command == "demo-accounts":
        return _demo_accounts(store, args.institution, args.out)
    if args.command == "reset-password":
        return _reset_password(store, args.email)
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


def _demo_mailboxes(store: AuthStore, slug: str | None) -> int:
    if slug is not None:
        institution = store.institution_by_slug(slug)
        if institution is None:
            print(f"demo-mailboxes: no institution with slug {slug!r}", file=sys.stderr)
            return 1
        institution_id = int(institution["id"])
    else:
        institution_id = store.ensure_bootstrap_institution()
    try:
        added = seed_demo_mailboxes(store, institution_id)
    except (NotDemonstrationData, StoreError) as exc:
        print(f"demo-mailboxes: {exc}", file=sys.stderr)
        return 1
    if not added:
        print("demo-mailboxes: every office already has a mailbox; nothing changed")
        return 0
    for office, email in added:
        print(f"{office}: {email}")
    print(
        f"demo-mailboxes: added {len(added)} mailbox"
        f"{'' if len(added) == 1 else 'es'}; with the default outbox, sent "
        "messages are written to the outbox folder next to the database "
        "(var/outbox/ by default) and never leave this machine"
    )
    return 0


def reset_password(store: CabinetStore, email: str) -> str | None:
    """Give one account a new generated password and end its sessions;
    returns the password, or None for an unknown email."""
    user = store.user_by_email(email)
    if user is None:
        return None
    password = generate_password()
    with store._lock:
        store._conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(password), int(user["id"])),
        )
        store._conn.execute(
            "DELETE FROM sessions WHERE user_id = ?", (int(user["id"]),)
        )
        store._conn.commit()
    store.audit_append(
        int(user["institution_id"]),
        "admin.changed",
        actor="reset-password",
        payload={
            "action": "password_reset",
            "target_user_id": int(user["id"]),
            "role": user["role"],
            "by": "reset-password",
        },
    )
    return password


def _reset_password(store: AuthStore, email: str) -> int:
    password = reset_password(store, email)
    if password is None:
        print(f"reset-password: no account {email.strip().lower()!r}", file=sys.stderr)
        return 1
    print(
        f"new password for {email.strip().lower()} "
        f"(shown once, never stored or logged): {password}"
    )
    return 0


def _demo_accounts(store: AuthStore, slug: str | None, out: str) -> int:
    if slug is not None:
        institution = store.institution_by_slug(slug)
        if institution is None:
            print(f"demo-accounts: no institution with slug {slug!r}", file=sys.stderr)
            return 1
        institution_id = int(institution["id"])
    else:
        institution_id = store.ensure_bootstrap_institution()
    path = Path(out).expanduser()
    try:
        # Prove the file can be written (and is not a link) BEFORE any
        # account exists, so a password is never generated with nowhere to go.
        write_private(path, "")
    except OSError as exc:
        print(f"demo-accounts: cannot write {path} ({exc.strerror})", file=sys.stderr)
        return 1
    try:
        created = seed_demo_accounts(store, institution_id)
    except (NotDemonstrationData, StoreError, ValueError) as exc:
        print(f"demo-accounts: {exc}", file=sys.stderr)
        return 1
    if not created:
        print("demo-accounts: every persona already has a sign-in; nothing changed")
        return 0
    lines = [f"{email}\t{name}\t{password}\n" for email, _, name, password in created]
    try:
        write_private(path, "".join(lines))
    except OSError as exc:
        print(
            f"demo-accounts: could not write {path} ({exc.strerror}); the "
            "accounts were created but their passwords are lost — issue new "
            "ones with `make reset-password EMAIL=...`",
            file=sys.stderr,
        )
        return 1
    for email, role, name, _ in created:
        print(f"created {email} ({name}, role {role})")
    print(f"demo-accounts: passwords appended to {path} (mode 600)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
