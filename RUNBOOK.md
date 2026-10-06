# Runbook for the Golden Eagle AI Cabinet

This runbook covers setup, run, stop, restart, replay, reset, backup, and
production, and we run everything on `127.0.0.1` only until the production
section. By default nothing in development sends anything anywhere. The
dispatch outbox writes files on this machine, and real email stays off unless
someone configures it on purpose. `README.md` covers the architecture, and
`DEMO-SCRIPT.md` covers the demo itself.

## Setup

Requires Python 3.12 and Node 22 on `PATH`.

```bash
make setup   # creates .venv (python3.12), pip-installs the backend, npm ci in ui/
make check   # lint + typecheck + tests, both stacks, must be green
```

## Run

Before the first start we create the demo accounts once, and the full reference
is "Users, institutions, and login" below. Each command prints its generated
password exactly once and never logs it, so we save it the moment it prints.

```bash
make bootstrap-admin EMAIL=admin@demo.test          # bootstrap institution + first admin (once)
make user EMAIL=president@demo.test ROLE=executive  # the president the demo signs in as
```

```bash
make api
make ui
```

`make api` starts FastAPI on `http://127.0.0.1:8910`. It polls `/health` for up to
8 s and prints "api started" only on success. On failure it shows the last 20 lines
of `var/api.log` and exits non-zero. `make ui` starts Vite on
`http://127.0.0.1:5200` with the same health-polling contract.

Both servers run in the background with pid files in `var/`, so open
`http://127.0.0.1:5200`. It opens on the sign-in screen, and the demo signs in as
the president.

## Users, institutions, and login

Every API route except `/health` and `/ready` requires a logged-in user. Users,
sessions, institutions, datasets, audit events, briefings, decisions, and
recordings live in `var/cabinet.db` (override with `CABINET_DB`). Dataset
documents live under `var/data/<institution slug>/<dataset id>.json` with 0600
permissions.

`make bootstrap-admin` creates the first institution and its first admin,
and the generated password prints exactly once and is never logged. The first
institution is shown as "Demonstration University" and keeps the slug
`bootstrap`.
`make institution` adds another tenant, and `make user` adds a login in one of
five roles, which are admin, executive, staff, reviewer, and aid (the Financial Aid office). Every new
institution is seeded with the fictional demonstration dataset ("Demonstration
(fictional)"), so the demo and onboarding work from the first login.

```bash
make bootstrap-admin EMAIL=admin@example.edu   # bootstrap institution + first admin (once)
make institution NAME="Two Rivers College" SLUG=two-rivers  # another tenant
make user EMAIL=admin@two-rivers.example.edu ROLE=admin INSTITUTION=two-rivers
make user EMAIL=exec@example.edu ROLE=executive  # INSTITUTION defaults to bootstrap
```

Admins manage their own institution's users from the Institution screen or the
API under the same rules as the CLI. The one-time password is shown exactly
once in the creation response, and we never store or log it. An admin cannot
disable their own account, and the institution's last enabled admin can be
neither disabled nor demoted. Every change is one `admin.changed` audit event.

```bash
curl -b /tmp/cookies http://127.0.0.1:8910/admin/users            # list
curl -b /tmp/cookies -X POST http://127.0.0.1:8910/admin/users \
  -H 'Content-Type: application/json' -H "X-CSRF-Token: $CSRF" \
  -d '{"email": "staff@example.edu", "role": "staff"}'            # 201 + one_time_password
curl -b /tmp/cookies -X POST .../admin/users/<id>/disable -H "X-CSRF-Token: $CSRF"
curl -b /tmp/cookies -X POST .../admin/users/<id>/enable  -H "X-CSRF-Token: $CSRF"
curl -b /tmp/cookies -X PATCH .../admin/users/<id> \
  -H 'Content-Type: application/json' -H "X-CSRF-Token: $CSRF" \
  -d '{"role": "executive"}'
```

Log in once, then send the session cookie and the CSRF token on every POST.

```bash
curl -i -c /tmp/cookies -X POST http://127.0.0.1:8910/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email": "admin@example.edu", "password": "<printed once at bootstrap>"}'
# -> sets the cabinet_session cookie (HttpOnly, SameSite=Strict) and
#    returns {"user": {...}, "csrf_token": "..."}
CSRF=$(curl -s -b /tmp/cookies http://127.0.0.1:8910/auth/me | python3 -c 'import json,sys; print(json.load(sys.stdin)["csrf_token"])')
curl -b /tmp/cookies -X POST http://127.0.0.1:8910/ask \
  -H 'Content-Type: application/json' -H "X-CSRF-Token: $CSRF" \
  -d '{"question": "What should I know about spring registration?"}'
```

The roles are `admin` (everything, including dataset and user administration
for its own institution), `executive` (ask, approve, and read), `staff` (read
briefing and findings, no approve), and `reviewer` (read only). The executive
reads the audit log, and the president the demo signs in as runs the audit-log
walkthrough. Wrong or missing credentials are 401, the wrong role is 403, and
both are written to the audit log as `data.refused`. Sessions expire after
12 h.

Login throttling hard-locks at 5 failed attempts in 15 minutes per IP and per
IP-plus-email pair, and the answer is 429 with a `Retry-After` header. The
bare email is never hard-blocked, and it pays a progressive delay of 1, 2, 4,
and 8 seconds, capped at 30. The full threat model and control list is
`docs/SECURITY.md`.

We derive tenancy from the session. Every route resolves the institution from
the session's user, and no route accepts an institution id from the client. A
dataset id belonging to another institution is a 404, never a 403, so
existence does not leak.

We cap request bodies at 256 KB, and the dataset upload route allows 20 MB. We
enforce the cap on bytes actually read, and on admin routes it applies only
after authentication.

## Stop

```bash
make stop
```

This stops only the processes this project started (pid files in `var/`, plus their
children), and before killing it checks the pid's command line is what this project
starts (`uvicorn cabinet.app:app` / `vite`). We built it this way so a stale, reused
pid is reported and its pid file removed, never killed. Anything else holding 8910
or 5200 is reported with its pid and command, never killed.

## Restart

```bash
make stop && make api && make ui
```

Everything survives restarts because we keep the durable state in
`var/cabinet.db`. It holds the audit chains, produced briefings, decisions,
users, and the dataset registry, and the dataset documents stay under
`var/data/`.

## Institutions and datasets

Each institution's findings are computed from its active dataset. A new
institution starts with the seeded "Demonstration (fictional)" dataset (from
`CABINET_FIXTURE`, default `data/fixture.json`), and every data-bearing
response carries `meta.fictional: true` for it.

An institution's admin uploads its own data as a JSON document in the
`SCHEMA.md` shape, up to 20 MB.

```bash
curl -b /tmp/cookies -X POST http://127.0.0.1:8910/admin/datasets \
  -H 'Content-Type: application/json' -H "X-CSRF-Token: $CSRF" \
  --data-binary @dataset.json
# -> 201 {"dataset": {...}, "validation": {"row_counts": ...,
#    "counseling": "present, will always be refused", "fictional": false}}
# or 422 {"detail": ..., "errors": [...every problem, with JSON paths...]}
```

We validate before anything is stored. No field outside the `SCHEMA.md`
shape is accepted, and obvious PII columns such as `email`, `phone`, `ssn`,
`dob`, or `name` anywhere in a student record are rejected with a clear error.
Student ids must be pseudonymous, `STU-` or `PRI-` style or another opaque
token without spaces. Counseling fields are allowed but flagged "present, will
always be refused".

```bash
curl -b /tmp/cookies http://127.0.0.1:8910/admin/datasets           # list
curl -b /tmp/cookies -X POST .../admin/datasets/<id>/activate -H "X-CSRF-Token: $CSRF"
curl -b /tmp/cookies -X DELETE .../admin/datasets/<id> -H "X-CSRF-Token: $CSRF"
```

Activating recomputes that institution's findings, and briefings, decisions,
and caches follow the new data while other institutions are untouched.
Approvals are pinned to the dataset they were computed from, so a newly
activated dataset starts with no briefing and no approvals, and the previous
dataset stays until deleted. Deletion is soft (`deleted_at`), and the row and
file stay for a 30-day retention window before `make purge-deleted` removes
them for good. The active dataset cannot be deleted, so activate another
first.

## Importing from Ellucian Ethos

The institution's real student information system (Ellucian Banner through the
Ethos Integration API) feeds the same upload path, pseudonymised at the
institution's edge, so no identifiable student record ever reaches the
cabinet. The full mapping and the per-tenant settings are in `docs/ELLUCIAN.md`.
The request we send to the registrar is `docs/DATA-ACCESS.md`.

```bash
# one time, outside the repo, mode 0600:
#   echo "<ethos api key>"  > /secure/path/ethos-api-key
#   openssl rand -hex 32    > /secure/path/pseudonym-key
export CABINET_ETHOS_BASE_URL=https://ethos.example.edu
export CABINET_ETHOS_API_KEY_FILE=/secure/path/ethos-api-key
export CABINET_PSEUDONYM_KEY_FILE=/secure/path/pseudonym-key

make import-ethos INSTITUTION=two-rivers TERM=202720 DRY_RUN=1   # export and validation report only
make import-ethos INSTITUTION=two-rivers TERM=202720             # also stores it, inactive
```

The export lands in `var/exports/<slug>-<term>-<timestamp>-<suffix>.json`
(mode 0600) and is validated by the same `validate_upload` as the admin screen
before anything is stored. A real import is stored with
`uploaded_by=ethos-import`, stays inactive until an admin activates it, is
audited as `dataset.uploaded` with `importer: "ethos"`, and then the export copy
is removed. The stored dataset is the retained copy (purged 30 days after
deletion), and exports are kept only on `DRY_RUN=1`. A missing, unreadable, or
group or world readable key file is a one line refusal before any network
access, with nothing written. The pseudonym key never leaves the institution.
Without it the export's `S-` and `A-` ids cannot be reversed, and no reverse
table exists.

## Backup and restore

```bash
make backup                          # -> var/backups/<UTC timestamp>/
make stop
make restore FROM=var/backups/<timestamp>
make api
```

`make backup` snapshots the database with the SQLite backup API, never a file
copy of the live database, plus every dataset file, and it writes a
`manifest.json` with sha256 hashes. We verify the copy against the manifest
before the command reports success. `make restore FROM=...` refuses while the
servers are running, verifies the backup's hashes first, moves any existing
`var/cabinet.db` and `var/data/` aside as `*.pre-restore-<timestamp>`, copies
the backup in, and verifies what it wrote. Nothing in a backup or restore path
is ever deleted.

## Migrations

The database schema is versioned in the `schema_migrations` table. The app
applies known pending migrations at startup and refuses to start on a schema
version it does not know, which means a database written by a newer build. The
refusal is one clear line on stderr, not a traceback. `make migrate` runs the
migrations explicitly and shows what applied. Migration 8 renames the first
institution from "Bootstrap Institution" to "Demonstration University" when it
still has the old name. A name an admin already changed is left alone, and the
slug stays `bootstrap`.

```bash
make migrate
```

## Operating in production

We serve everything from one process, the API and the built UI in the same
uvicorn, with a reverse proxy in front for HTTPS.

```bash
make build      # tsc + vite build -> ui/dist (hashed assets, immutable cache;
                # index.html is no-store so a deploy is picked up at once)
CABINET_SECRET_KEY=$(python3 -c "import secrets;print(secrets.token_hex(32))") \
    make serve  # production: serves UI + API on CABINET_BIND (default
                # 127.0.0.1:8910); stop with `make stop`
```

`make serve` backgrounds `deploy/run-production.sh`, the same command the
launchd plist and the systemd unit exec, so we keep the production flags in
exactly one place.

- `CABINET_ENV=production` fails closed. Without a `CABINET_SECRET_KEY` of 32+
  bytes, which signs the session cookies, or without an explicit
  `CABINET_BIND`, startup is a one-line refusal, and the default bind is
  `127.0.0.1`. Outside production a missing secret degrades to an ephemeral
  key with a stderr warning, and sessions do not survive restarts. Production
  also sets Secure cookies and HSTS. `make check-config` shows which
  `CABINET_*` variables are set, values redacted.
- One process, on purpose. The rate limiters, the login lockout, and the
  briefing caches are in-process (see `docs/SECURITY.md`), and a second
  process would split them. To scale, we put more at the proxy or raise the
  in-process limits, never a second process.
- We trust proxy headers (`X-Forwarded-For`) only from
  `CABINET_TRUSTED_PROXY`, which defaults to `127.0.0.1`, the proxy on the
  same host. The per-IP limits and the access log see the real client, and a
  spoofed header from anywhere else is ignored.
- Access logs are one JSON line per request on stdout
  (`{"ts","request_id","method","path","status","duration_ms","client_ip"}`),
  and every response carries the id as `X-Request-ID`, which is the join key.
  SIGTERM is a graceful shutdown, and in-flight requests finish within 10 s.

`/health` is liveness, meaning the process answers, and `make serve` polls it.
`/ready` is readiness, and it checks that the database opens, the schema
version is one this build knows, a secret is configured, `ui/dist` is present,
and the golden replay run is readable. On any failure it answers 503 and names
the reasons. We point the proxy's health check at `/ready` and a load balancer's
liveness at `/health`.

The UI shell (`/`, the hashed assets, and the SPA fallback routes) is public.
We serve the same bytes to everyone with no data in them, and every data route
still requires a session under a strict content security policy.

### Reverse proxy and HTTPS

`deploy/Caddyfile` is the automatic-HTTPS front. The hostname comes from
`CABINET_DOMAIN`, and Caddy obtains and renews the certificate itself.

```bash
CABINET_DOMAIN=cabinet.example.edu caddy validate --config deploy/Caddyfile --adapter caddyfile
CABINET_DOMAIN=cabinet.example.edu caddy run --config deploy/Caddyfile --adapter caddyfile
```

It proxies to `CABINET_BIND`, caps request bodies at 21 MB (one MB above the
app's 20 MB upload cap, so the app's own 413 is what a client sees), and sets
HSTS. `deploy/nginx.conf` is the equivalent for nginx, with edge rate limits
matching the in-process ones, and certbot owns the certificate
(`certbot --nginx -d <domain>`). Stock Caddy has no rate limiting, so the
in-process limits (600 per minute per IP, 120 per minute per signed-in session,
and 5 per minute on `POST /ask` and the dispatch Send) still apply and see the real client IP. The Caddyfile names the
plugin to use when the edge must limit too.

### Keeping it alive

The service definitions are validated but never installed by this repo, and
`deploy/checklist.md` has the install commands.

- macOS runs `deploy/launchd/com.goldeneagle.cabinet.plist` with `RunAtLoad`,
  `KeepAlive`, the working directory, `CABINET_LOCAL_ENV` pointing at the env
  file (secrets are not in the plist), stdout and stderr log paths, and
  `AbandonProcessGroup`, and we lint it with `plutil -lint`.
- Linux runs `deploy/systemd/cabinet.service` with an `EnvironmentFile` for
  the secrets, `Restart=always`, and hardening (`NoNewPrivileges`,
  `ProtectSystem=strict` with `ReadWritePaths` limited to `var/`), and we
  verify it on the target host with `systemd-analyze verify`.

Both `cd` to the deploy directory and exec `deploy/run-production.sh`, and we
edit the `/opt/golden-eagle-cabinet` paths to the real location. The env file
(0600, service user) carries `CABINET_SECRET_KEY` and the `CABINET_LLM_*`
settings. `CABINET_LOCAL_ENV` makes the app read it from anywhere, with the
real environment still winning.

### Scheduled backups

`deploy/backup.sh` runs `make backup` and rotates `var/backups/` to the newest
14 snapshots. Only timestamp-shaped directories are rotated, and the
`*.pre-restore-*` asides are never touched. We schedule it with
`deploy/launchd/com.goldeneagle.cabinet-backup.plist` (daily at `03:17` local)
or `deploy/systemd/cabinet-backup.timer` (same schedule, `Persistent=true` so
a missed run catches up). The restore drill and the rotation of the model key
are in `deploy/checklist.md`, the full first-deploy walkthrough.

## The three providers

- **`chat`** (default) is the configured model endpoint, any service that speaks the
  chat-completions API, and we choose the provider by environment, never by code
  change. Configure it with environment variables or a gitignored
  `cabinet.local.env` at the repo root (copy `cabinet.local.env.example`), and the
  real environment wins.

  ```
  CABINET_LLM_BASE_URL=https://your-endpoint.example/v1
  CABINET_LLM_MODEL=your-model-id
  CABINET_LLM_LABEL=live model        # what the UI shows as the source
  CABINET_LLM_REASONING_EFFORT=low    # keeps reasoning models from thinking past the answer
  CABINET_LLM_API_KEY=your-key-here
  ```

  Or read the endpoint key from one named variable's line in another env file
  (`CABINET_LLM_API_KEY_FILE` + `CABINET_LLM_API_KEY_VAR`), and no other line of that
  file is ever read. With no configuration, `GET /briefing/enrollment` answers 503
  with a reason naming the missing `CABINET_*` variables, and the rest of the app
  keeps working. The endpoint key and the model id are never logged, recorded, or
  returned to the UI, because responses carry the label only.

  When the model's answer fails validation (for example it cites a finding its
  role did not receive), we ask it once more with the same inputs plus one
  short correction that states the reason in plain words and adds no data.
  The new answer is validated again, and a second failure leaves the section
  unavailable. Set `CABINET_VALIDATION_RETRIES=0` to turn this off, or a
  higher number for more tries (capped at 3). Each try can take up to about a
  minute on a slow endpoint, so one retry can double a section's worst-case
  time. This is separate from the provider's own single retry on HTTP 429,
  5xx, and refused connections. The count of corrective tries appears as
  `validation_retries` on the `finding.produced` and `briefing.produced`
  events. Replay never retries.

- **`replay`** serves recorded responses with no network. `make api REPLAY=1` is the
  shorthand, and the search order is `CABINET_REPLAY_DIR` if set, then `var/replay/`,
  then the committed golden run in `data/golden/`.

- **`fake`** is a deterministic stub for tests and offline development, run with
  `CABINET_PROVIDER=fake make api`.

## Sending an approved follow-up (the dispatch)

After an executive approves a leadership decision, the decision panel offers
"Prepare the message to \<office\>". Preparing composes the message in code
from the findings and stores it as a draft. A staff member or admin then
clicks "Send as \<their address\>", and the message goes to the office
mailbox. The executive prepares and approves but never sends, and we never
compose, queue, or send anything without that click. Until then the follow-up
task reads "Waiting for the message to be sent", and the panel shows "Sent"
once the message has left.

The recipient must exist in the institution's office address book first, or
Send refuses with a message naming the office. An admin manages the book in
**Institution settings, Offices** (`/institution`). The table lists every
office in the book and every office the current decision routes to, so an
office still without a mailbox shows up as a gap. Add, change, or remove a
mailbox in the row, add another office with "Add an office", then press
"Save the office contacts". We check each mailbox and office name before
anything is sent, and an error appears under the field it belongs to. When
the decision panel finds no mailbox, it links an admin straight to that
section and tells other roles that an administrator adds it.

The same book is reachable from the command line, with the same session and
CSRF flow as the user routes above.

```bash
curl -b /tmp/cookies http://127.0.0.1:8910/admin/offices
curl -b /tmp/cookies -X PUT http://127.0.0.1:8910/admin/offices \
  -H 'Content-Type: application/json' -H "X-CSRF-Token: $CSRF" \
  -d '{"offices": [{"office": "Financial Aid", "email": "financial-aid@example.edu"},
                   {"office": "Bursar", "email": "bursar@example.edu"}]}'
```

Each Save (or PUT) replaces the book whole, we validate every entry, and the change is
one `admin.changed` audit event. The table holds offices only. No student
address belongs in it, and the dispatch routes have no other place to get a
recipient from.

`CABINET_OUTBOUND` chooses the outbound provider.

- **`outbox`** (default) writes the message to
  `var/outbox/<institution slug>/<dispatch id>.eml` (0600) and marks the
  dispatch sent with `provider=outbox`. Nothing leaves the machine. The .eml
  file is the delivery, and a person opens it and sends it from their own
  mailbox.
- **`smtp`** is real delivery with stdlib smtplib (STARTTLS, or SMTPS on port
  465). It requires every one of these settings, and in production a missing
  one is a one-line startup refusal.

  ```
  CABINET_OUTBOUND=smtp
  CABINET_SMTP_HOST=smtp.example.edu
  CABINET_SMTP_PORT=587
  CABINET_SMTP_FROM=cabinet@example.edu
  CABINET_SMTP_USER=cabinet@example.edu        # optional; defaults to FROM
  CABINET_SMTP_PASSWORD_FILE=/secure/path/smtp-password   # read from the file, never an env value; chmod 600, a looser mode is refused
  ```

- **`fake`** records sends in memory, for tests.

We audit every draft and every send (`task.dispatched`, then `task.sent` with
the provider and its reference). A sent message is never resent, because the
repeat click is a 409 returning the earlier record. A failed send is recorded
on the dispatch row with the provider's error so it can be retried.

## The Financial Aid review queue

The emergency-aid review decision asks the Financial Aid office to review the
students M3 counts. Once leadership signs off on that decision, a staff
member, the executive, or an admin can prepare a review queue for the office
from the decision panel ("Prepare the Financial Aid review queue"). The queue
holds one row per M3 student, with the facts the office needs to start its
own review. Those facts are the qualifying hold's amount, date, and office,
the registration status, and the advising status. The cabinet makes no
determination about any student. The office records its own status (open,
in review, or closed) and a free-text note for each row.

The queue is worked by a fifth role, `aid`, for Financial Aid staff. An aid
user reads the briefing like staff and lands on the queue after sign-in. It
cannot ask, sign off, prepare or send a message, or read the audit log.
Create one with the CLI or the admin API.

```bash
make user EMAIL=aid@example.edu ROLE=aid   # INSTITUTION defaults to bootstrap
curl -b /tmp/cookies -X POST http://127.0.0.1:8910/admin/users \
  -H 'Content-Type: application/json' -H "X-CSRF-Token: $CSRF" \
  -d '{"email": "aid@example.edu", "role": "aid"}'      # 201 + one_time_password
```

The routes, with the same session and CSRF flow as above.

```bash
curl -b /tmp/cookies -X POST http://127.0.0.1:8910/decisions/D-spring-registration-1/aid-queue \
  -H 'Content-Type: application/json' -H "X-CSRF-Token: $CSRF"
# -> {"count": 18, "created": true, ...}, or 409 before sign-off
curl -b /tmp/cookies http://127.0.0.1:8910/aid-queue    # aid, admin, executive, reviewer
curl -b /tmp/cookies -X PATCH http://127.0.0.1:8910/aid-queue/<row id> \
  -H 'Content-Type: application/json' -H "X-CSRF-Token: $CSRF" \
  -d '{"status": "in_review", "note": "Called the student.",
       "expected_updated_at": null}'   # aid and admin
```

Preparing the queue twice returns the same rows with `"created": false`.
Each queue belongs to the dataset it was read from, so activating another
dataset shows that dataset's queue (empty until someone prepares it), and
purging a dataset purges its queue. A note holds at most 1,000 characters,
we store it exactly as typed, and no model ever receives it. We audit the
queue as `aid.queued` (decision, dataset, and count) and every change as
`aid.updated` with the acting user, the row id, and the status before and
after. The note text and the student id stay out of the audit log.

Every save must carry `expected_updated_at`, the row's `updated_at` as it was
read (`null` for a row nobody has saved yet). A save without it is refused
with 422 and asks you to reload the row and send its `updated_at`. When the row
changed since it was read, the save is refused with 409 and the message "This
row changed since you opened it. Reload to see the latest." Read the row again
with GET /aid-queue and send its current `updated_at`. Only rows of the active
dataset can be changed. A row of an inactive or deleted dataset is a 404.

## Recording a counseling authorization

Per-student counseling data stays refused to everyone. An institution can allow one
aggregate figure (M9, CONTRACTS.md), the count of continuing students not yet
registered who have had any counseling contact this term. We record it only after the
institution's counseling director has authorized it in writing.

An admin records it in Institution settings, under "Counseling figure". Enter the
director's name and title as written and the document reference, then press "Record
the authorization". The same section shows who recorded it and when, and "Revoke the
authorization" turns it off behind a confirmation. The API does the same.

```bash
curl -b /tmp/cookies http://127.0.0.1:8910/admin/institution/counseling-authorization
curl -b /tmp/cookies -X PUT http://127.0.0.1:8910/admin/institution/counseling-authorization \
  -H 'Content-Type: application/json' -H "X-CSRF-Token: $CSRF" \
  -d '{"authorized": true, "authorized_by": "Dr. Example, Director of Counseling",
       "document_reference": "memo 2026-09-26"}'
# -> {"authorized": true, "authorized_by": ..., "recorded_by": ..., "recorded_at": ...}
curl -b /tmp/cookies -X PUT http://127.0.0.1:8910/admin/institution/counseling-authorization \
  -H 'Content-Type: application/json' -H "X-CSRF-Token: $CSRF" \
  -d '{"authorized": false}'                                  # revoke
```

The findings recompute on the next request. While the authorization is recorded,
the next ask of the spring registration question gives the Chief of Staff the count
or the withheld marker, and that briefing keeps its own copy of M9. It shows M9 in
section 3 and in section 4's evidence list as "aggregate, authorized", with no rows.
A briefing for the other question, or one produced before the authorization, never
shows it. A revoke removes
M9 at once. A stored Chief of Staff section that cited it is held back until the
question is asked again, and the next ask runs without it. A change waits for a
question that is being answered, so no run sees half of it. A count under 10 shows as "fewer than 10", which is what the fictional
demonstration dataset shows (its raw count is 2). The evidence for M9 shows the
authorization and the fields read, never a row. Each change is one `admin.changed`
event with action `counseling_authorization`. The beat-6 refusal and every
`/governance/request` for a counseling field stay refused either way.

## Replay and the golden run

The golden run is a committed, reviewable recording in `data/golden/` that REPLAY
mode falls back to. The demo works with no key and no network, and we keep it
independent of the network this way. We record it once the live model is configured.

```bash
make record-golden
```

This runs each of the three roles (Chief of Staff, Enrollment, Student Success)
once through the normal path with recording on. That path is the permission gate,
the provider, and output validation. Each golden file is written into
`data/golden/` from the validated output itself, **only if the output validated**.
An invalid answer is never recorded, and an existing recording is never overwritten
unless `CABINET_RECORD=overwrite`. The golden write never reads `var/replay/`, so a
stale recording there cannot leak into the golden run. `make record-golden` refuses
to run while the API is up, because both hold the same `var/cabinet.db`. Stop it
first with `make stop`, otherwise the target exits 2 with the reason.
We commit the new files in `data/golden/`.

To record extra runs into `var/replay/` instead, run this.

```bash
CABINET_RECORD=1 make api            # each validated live answer is saved
curl http://127.0.0.1:8910/briefing/enrollment
```

## Logs and the audit log

- `var/api.log` and `var/ui.log` are the server logs, and `var/` is gitignored.
- `var/cabinet.db` is the whole durable state, which is users, sessions,
  institutions, datasets, audit events, briefings, decisions, and recordings
  (override with `CABINET_DB`). Back it up with `make backup`.
- `var/data/<institution slug>/<dataset id>.json` holds the dataset documents
  at 0600. Each is verified against its recorded sha256 on load, and a file
  changed on disk fails loudly with a 503, never silently different findings.
- The audit log lives in the `audit_events` table as one hash chain per
  institution, each event with `prev_hash` and `hash`, and scope 0 is the
  platform chain for pre-auth events. The executive reads the chain in the
  demo walkthrough, and any signed-in user of the institution can inspect it
  with `curl -b /tmp/cookies http://127.0.0.1:8910/events`. Verify all chains
  with `.venv/bin/python -m cabinet.audit verify var/cabinet.db`, which exits
  non-zero on any tampering. Export one institution's chain to JSONL for
  review with `.venv/bin/python -m cabinet.audit export var/cabinet.db
  out.jsonl --institution <slug>`, and `verify` also accepts the JSONL file.
- `var/replay/` holds the recordings made with `CABINET_RECORD=1`.
- `var/backups/` holds the `make backup` snapshots.

## Resetting an institution's audit chain safely

We never delete the database, because it is the record. To start an institution
over, we back up first with `make backup`, then delete and recreate the
institution's data through the admin routes or by restoring an older backup.
The exported JSONL from `python -m cabinet.audit export` is the archival
format for a chain we are retiring.

## Fixture

`data/fixture.json` is the fictional dataset we seeded. Regenerate it byte-identically
with `python3 data/generate_fixture.py`, and verify the planted values with
`data/check_fixture.py` against `data/VERIFY.md`. It seeds every new
institution's "Demonstration (fictional)" dataset. Point the seed at another
file with `CABINET_FIXTURE=/path/to/fixture.json`, which affects only
institutions created afterwards. A malformed seed fixture fails startup with
one clear line naming the problem, not a traceback, and an empty-but-valid
fixture renders every metric as `--` per the contract.

### Demonstration University (the synthetic school)

`data/school/` generates a whole fictional university (Fall 2020 to Spring 2026)
as an Ellucian-shaped SQLite database for specific historical questions. Build it
with `make school-data`, which writes `var/school/school.db` (about 33 MB, about
3 s) and then runs the checker. Re-check an existing database with
`make school-check`, which exits non-zero if any GPA, standing, schedule,
capacity, or planted fact in `data/school/VERIFY.md` disagrees with the raw rows.
Nothing runs in the background, so there is nothing to stop. To reset, delete
`var/school/school.db` and run `make school-data` again, and the same seed gives
the same rows (the canonical hash in `VERIFY.md` proves it). The data is
synthetic, students are pseudonymous ids with no names, and instructor names are
fictional. Tables are documented in `data/school/SCHEMA.md`.

### Explore (questions over the school data)

Explore needs `var/school/school.db` (`make school-data`). Without it, `POST /explore`
and `GET /explore/catalog` answer 503 with "The demonstration university data is not
installed. Run make school-data." It runs inside the API process, so starting, stopping,
and restarting the API covers it. `make explore-check` runs the full-scale check (the
owner's example and five more planted facts) and prints the owner's example with its
three tables. With the API running, ask as a signed-in executive:

```sh
curl -s -b cookies.txt -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
  -d '{"question": "Which major has the lowest GPA? In that major, what is historically the hardest class, and which instructor has historically taught it?"}' \
  http://127.0.0.1:8910/explore
# -> {"refused": false, "answer": [{"text": ..., "claims": [...]}, ...],
#     "steps": [...three tables...], "source": "Written from computed tables (no model)"}
```

`.venv/bin/python -m cabinet.explore "<question>"` answers offline from the same code and
writes nothing. Each question writes `question.asked`, then `data.refused` (refused
questions) or one `data.granted` per step and `explore.answered`, on the asker's
institution chain. Replay and fake modes use the rule planner and the template answer.
A live provider may plan and reword, and `CABINET_RECORD=1` records validated plans under
`var/replay/explore/` (details in `docs/EXPLORE.md`).

## Known issues

- **Live-model latency can reach ~55 s** (the client timeout, and the endpoint
  gateway may cut longer requests server-side). A timeout is a single attempt,
  never retried, and 429/5xx and refused connections retry once after a 2 s
  pause. One explanation stays within ~60 s of wall time, and the UI keeps
  polling for 200 s, showing "Still working…" past 60 s. For the demo, use
  replay with `make api REPLAY=1` and the golden run in `data/golden/`.
- The UI's "model unavailable" banner appears whenever the briefing endpoint answers
  503, and metrics, evidence, and the audit log keep working regardless.
