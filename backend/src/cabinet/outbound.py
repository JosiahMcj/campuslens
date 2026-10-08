"""The outbound provider seam for dispatches (the governed execution step).

A dispatch is a message to a responsible OFFICE mailbox, composed in code
(``cabinet.questions.compose_dispatch``) and sent only when a named staff
member (or admin) clicks Send. This module is where the message can leave
the machine, and the default keeps everything on it:

- ``outbox`` (the default) — writes the message as an RFC 5322 file under
  ``<var>/outbox/<institution slug>/<dispatch id>.eml`` and reports the
  send as done with ``provider=outbox``. Nothing leaves the machine; the
  outbox IS the delivery, so a person can pick the message up and send it
  from their own mailbox.
- ``smtp`` — real delivery through stdlib ``smtplib`` (STARTTLS, or SMTPS
  on port 465), enabled only when ``CABINET_OUTBOUND=smtp`` AND every
  setting is present: ``CABINET_SMTP_HOST``, ``CABINET_SMTP_PORT``,
  ``CABINET_SMTP_FROM``, and ``CABINET_SMTP_PASSWORD_FILE`` (the password
  is read from the FILE the variable names, never from an env value, so it
  cannot leak through a process listing). ``CABINET_SMTP_USER`` is optional
  and defaults to the From address. In production a missing setting is a
  one-line startup refusal; outside production the provider is built but
  its first send fails loudly with the missing names.
- ``fake`` — for tests: records the messages it was asked to send in
  memory, returns a fake reference, and never touches the network or disk.

Every provider returns a ``provider_ref`` string on success (the outbox
file's relative path, the SMTP Message-ID, a fake counter) and raises
:class:`OutboundError` with a plain reason on failure; the API records the
failure on the dispatch row, so a failed send is never silent and the row
can be retried.
"""

from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Protocol

ENV_OUTBOUND = "CABINET_OUTBOUND"
ENV_SMTP_HOST = "CABINET_SMTP_HOST"
ENV_SMTP_PORT = "CABINET_SMTP_PORT"
ENV_SMTP_FROM = "CABINET_SMTP_FROM"
ENV_SMTP_USER = "CABINET_SMTP_USER"
ENV_SMTP_PASSWORD_FILE = "CABINET_SMTP_PASSWORD_FILE"

# The From address the outbox provider stamps on its .eml files: the
# cabinet itself, because the message has not really left the machine.
OUTBOX_FROM = "cabinet-outbox@localhost"

SMTP_TIMEOUT_SECONDS = 15
# Port 465 is the SMTPS (implicit TLS) convention; anything else uses
# STARTTLS after EHLO.
SMTPS_PORT = 465


class OutboundError(RuntimeError):
    """A send that did not happen, with a plain reason for the operator."""


class OutboundProvider(Protocol):
    """What a dispatch provider must do: deliver one composed message and
    return a reference for the audit record, or raise OutboundError."""

    name: str

    def send(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        dispatch_id: int,
        institution_slug: str,
    ) -> str: ...


def _build_message(
    *,
    to: str,
    subject: str,
    body: str,
    from_addr: str,
    dispatch_id: int,
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = from_addr
    message["To"] = to
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(idstring=f"dispatch-{dispatch_id}")
    message.set_content(body)
    return message


@dataclass
class OutboxProvider:
    """The default provider: delivery is a file on this machine.

    The .eml is written with 0600 permissions under
    ``<outbox dir>/<institution slug>/`` (0700 dirs), matching the dataset
    file permissions in ``cabinet.store``: a dispatch carries institution
    business and gets the same filesystem posture.
    """

    outbox_dir: Path
    name: str = "outbox"

    def send(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        dispatch_id: int,
        institution_slug: str,
    ) -> str:
        message = _build_message(
            to=to,
            subject=subject,
            body=body,
            from_addr=OUTBOX_FROM,
            dispatch_id=dispatch_id,
        )
        target_dir = self.outbox_dir / institution_slug
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(target_dir, 0o700)
            path = target_dir / f"{dispatch_id}.eml"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(message.as_bytes())
                handle.flush()
                os.fsync(handle.fileno())
            # os.open's mode is masked by umask; chmod enforces 0600 exactly.
            os.chmod(path, 0o600)
        except OSError as exc:
            raise OutboundError(f"the outbox write failed: {exc}") from exc
        return str(path.relative_to(self.outbox_dir))


@dataclass
class SmtpProvider:
    """Real delivery through stdlib smtplib: STARTTLS, or SMTPS on 465.

    The password is read from the file ``password_file`` names at send
    time, not at construction, so rotation needs no restart and the secret
    never sits in an environment variable.
    """

    host: str
    port: int
    from_addr: str
    user: str
    password_file: Path
    name: str = "smtp"

    def send(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        dispatch_id: int,
        institution_slug: str,
    ) -> str:
        try:
            mode = self.password_file.stat().st_mode & 0o777
        except OSError as exc:
            raise OutboundError(
                f"{ENV_SMTP_PASSWORD_FILE} is not readable: {exc}"
            ) from exc
        if mode & 0o077:
            # The same rule as the Ethos key files: a password readable by
            # group or others is refused before it is ever used.
            raise OutboundError(
                f"{ENV_SMTP_PASSWORD_FILE} names {self.password_file}, which "
                f"is readable by group or others (mode {mode:04o}); chmod 600 it"
            )
        try:
            password = self.password_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise OutboundError(
                f"{ENV_SMTP_PASSWORD_FILE} is not readable: {exc}"
            ) from exc
        message = _build_message(
            to=to,
            subject=subject,
            body=body,
            from_addr=self.from_addr,
            dispatch_id=dispatch_id,
        )
        try:
            if self.port == SMTPS_PORT:
                # A verifying context: the stdlib default for SMTP_SSL and
                # starttls() checks neither the certificate nor the host name,
                # which would hand the password to whoever answers the host.
                smtp: smtplib.SMTP = smtplib.SMTP_SSL(
                    self.host,
                    self.port,
                    timeout=SMTP_TIMEOUT_SECONDS,
                    context=ssl.create_default_context(),
                )
            else:
                smtp = smtplib.SMTP(self.host, self.port, timeout=SMTP_TIMEOUT_SECONDS)
                smtp.starttls(context=ssl.create_default_context())
            with smtp:
                smtp.login(self.user, password)
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise OutboundError(f"the SMTP send failed: {exc}") from exc
        return str(message["Message-ID"])


@dataclass
class FakeOutboundProvider:
    """The test provider: remembers what it was asked to send."""

    name: str = "fake"
    sent: list[dict[str, str]] = field(default_factory=list)

    def send(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        dispatch_id: int,
        institution_slug: str,
    ) -> str:
        self.sent.append(
            {
                "to": to,
                "subject": subject,
                "body": body,
                "dispatch_id": str(dispatch_id),
                "institution_slug": institution_slug,
            }
        )
        return f"fake-{len(self.sent)}"


@dataclass
class MisconfiguredSmtpProvider:
    """The development-mode stand-in for an incompletely configured
    ``smtp`` provider: present so startup succeeds outside production, but
    every send fails loudly with the names of the missing settings. In
    production the same configuration is a startup refusal instead
    (``outbound_from_env`` raises), because fail-closed is the rule there.
    """

    missing: list[str]
    name: str = "smtp"

    def send(self, **kwargs: str | int) -> str:
        raise OutboundError(
            "the smtp provider is not configured; missing: " + ", ".join(self.missing)
        )


def _smtp_settings() -> tuple[dict[str, str], list[str]]:
    """The configured smtp settings and the names of the missing ones."""
    settings = {
        ENV_SMTP_HOST: os.environ.get(ENV_SMTP_HOST, "").strip(),
        ENV_SMTP_PORT: os.environ.get(ENV_SMTP_PORT, "").strip(),
        ENV_SMTP_FROM: os.environ.get(ENV_SMTP_FROM, "").strip(),
        ENV_SMTP_PASSWORD_FILE: os.environ.get(ENV_SMTP_PASSWORD_FILE, "").strip(),
    }
    missing = [name for name, value in settings.items() if not value]
    port = settings[ENV_SMTP_PORT]
    if port:
        try:
            parsed = int(port)
        except ValueError:
            parsed = 0
        if not 0 < parsed < 65536:
            missing.append(f"{ENV_SMTP_PORT} (must be a port number, got {port!r})")
    return settings, missing


def outbound_from_env(
    *, outbox_dir: Path | None = None, production: bool = False
) -> OutboundProvider:
    """The configured outbound provider, chosen per call from the
    environment (``CABINET_OUTBOUND``), like ``provider_from_env`` for the
    model: ``make api`` takes effect on process start, and tests set the
    variable directly.

    ``outbox_dir`` is where the outbox provider writes; the API passes the
    database's sibling ``var/outbox`` so tests (tmp CABINET_DB) never write
    into the repo's var/.

    Raises RuntimeError (the caller turns it into the one-line startup
    refusal) for an unknown provider name in any mode, and for a partially
    configured ``smtp`` provider in production — fail closed.
    """
    which = os.environ.get(ENV_OUTBOUND, "").strip().lower() or "outbox"
    if which == "outbox":
        return OutboxProvider(
            outbox_dir if outbox_dir is not None else Path("var") / "outbox"
        )
    if which == "fake":
        return FakeOutboundProvider()
    if which == "smtp":
        settings, missing = _smtp_settings()
        if missing:
            if production:
                raise RuntimeError(
                    f"{ENV_OUTBOUND}=smtp but the configuration is "
                    f"incomplete; missing: {', '.join(missing)}"
                )
            return MisconfiguredSmtpProvider(missing)
        return SmtpProvider(
            host=settings[ENV_SMTP_HOST],
            port=int(settings[ENV_SMTP_PORT]),
            from_addr=settings[ENV_SMTP_FROM],
            user=os.environ.get(ENV_SMTP_USER, "").strip() or settings[ENV_SMTP_FROM],
            password_file=Path(settings[ENV_SMTP_PASSWORD_FILE]),
        )
    raise RuntimeError(
        f"{ENV_OUTBOUND} must be one of outbox, smtp, fake; got {which!r}"
    )
