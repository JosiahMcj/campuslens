# Security posture

We wrote this document to state the threat model, the controls in place, what we
deliberately do not cover yet, and how to report a vulnerability. We implemented
the controls in `backend/src/cabinet/auth.py` (users, sessions, passwords),
`backend/src/cabinet/security.py` (roles, CSRF, rate limits, headers, body
cap), and `backend/src/cabinet/audit.py` (the hash-chained audit log), and
`backend/tests/test_security.py` tests them.

## Threat model

**Assets**

- **Student data uploads.** Institutions upload enrollment, holds, and advising
  records for real students, which are FERPA-covered education records. The
  seeded fixture that ships with the repository is fictional, so the upload
  path is where real data enters.
- **The audit log** (`var/audit/events.jsonl`). It records who asked what, what
  data was granted, what was refused, what was approved, and which users were
  created or changed. Its value is its integrity, because an attacker who can
  rewrite it can erase or fabricate accountability.
- **The model key** (`CABINET_LLM_API_KEY`). Spending it costs money, so we
  never let it appear in logs, responses, recordings, or the repository.
- **The session secret** (`CABINET_SECRET_KEY`). It signs session cookies.

**Attackers**

- An unauthenticated internet or campus-network user probing the API.
- A logged-in user trying to act outside their role, such as a staff member
  approving a decision or reading the audit log.
- A script driving a victim's browser, meaning cross-site request forgery
  against the cookie-based session.
- A credential stuffer or password guesser against the login route.
- Someone who obtains a copy of the SQLite database, from a backup or a stolen
  laptop, but not the environment.
- Someone with write access to the audit log file who tries to edit history
  after the fact.

We treat the hosting operator, compromise of the model endpoint, and denial of
service beyond what the rate limits absorb as currently out of scope. The "What
is NOT covered" section explains.

## Controls

**Authentication and sessions.** Users live in `var/cabinet.db` (`CABINET_DB`),
in `users(id, email unique, password_hash, role, institution_id, created_at,
disabled)`. We hash passwords with `hashlib.scrypt` (N=2^14, r=8, p=1, per-user
16-byte salt), standard library only. We keep sessions as server-side rows with
a random 256-bit id and a configurable expiry (`CABINET_SESSION_TTL_HOURS`, default 12 h). The `cabinet_session` cookie carries
`<session id>.<HMAC-SHA256(session id, CABINET_SECRET_KEY)>`, so a leaked
database alone cannot mint valid cookies. The cookie is HttpOnly,
SameSite=Strict, Path=/, and Secure when `CABINET_ENV=production`, and login is
`POST /auth/login`. The error is generic, so it allows no account
enumeration, and an unknown email still pays the scrypt cost. We throttle failed
attempts two ways, where 5 failures per 15 minutes per IP and per IP+email pair
hard-lock with 429 and `Retry-After`. The bare email is never hard-blocked,
because otherwise anyone could lock a specific account out from elsewhere. It
instead pays a progressive delay of 1, 2, 4, 8 seconds, capped at 30. The
throttle and rate-limit dicts evict keys idle longer than their window, so they
stay bounded. `POST /auth/logout` destroys the session, and `GET /auth/me`
returns the user and the session's CSRF token. We create the first user with
`make bootstrap-admin EMAIL=…`, which prints a generated password exactly once
and never logs it, and further users come from `make user EMAIL=… ROLE=…`.

**Authorization.** There are five roles, where `admin` can do everything and
`executive` can ask, approve, read, and read the audit log, and the president
runs the audit-log walkthrough. `staff` reads briefings and findings and prepares
and sends approved office messages, with no approval and no audit log, and `reviewer` reads everything including the audit
log and changes nothing. `aid` is Financial Aid office staff. It reads the
briefing and findings like staff and works the Financial Aid review queue, and
it cannot ask, sign off on a decision, prepare or send a message, or read the audit log.

| Role | Ask and sign off | Read briefing and findings | Audit log | Prepare the aid queue | Read the aid queue | Update aid queue rows | Manage users and datasets |
|---|---|---|---|---|---|---|---|
| `admin` | yes | yes | yes | yes | yes | yes | yes |
| `executive` | yes | yes | yes | yes | yes | no | no |
| `staff` | no | yes | no | yes | no | no | no |
| `reviewer` | no | yes | yes | no | yes | no | no |
| `aid` | no | yes | no | no | yes | yes | no |

**Student ids in the findings.** `GET /findings` is open to every role, but the
pseudonymous student ids behind each figure (every `row_ids` list, M5's per-office
`hold_row_ids`, and M8's per-student `row_rules`) go to the `executive` and `admin`
roles only (`ROW_ROLES` in `cabinet/security.py`), because their work acts on the
records. For `staff`, `reviewer`, and `aid` the server empties every list in its usual
shape, removes the per-student indicator map, and adds `rows_withheld: true` and
`row_counts`, the number of records behind the figure. Those roles keep every figure
and every per-office and per-indicator count, and the evidence drawer tells them the
list is shown to executives and administrators only. The cached findings are never
modified, so the executive's and admin's view stays whole.

Every route except `GET /health`, `GET /ready`, and `POST
/auth/login` requires a session, and each route has an explicit role allow-list
in `cabinet/security.py` (`ROUTE_ROLES`). No session is a 401, and the wrong
role is a 403. We write both to the audit log as `data.refused` events, with the
actor set to the user id, or to `anonymous` when there is no session.

Explore (`docs/EXPLORE.md`) adds `POST /explore` and `GET /explore/catalog` for the
executive, admin, staff, and reviewer roles, and the aid role gets a 403. Inside the route,
instructor-level rows go to the executive and admin roles only. Staff and reviewers get
the course as a whole, and the withheld rows are a `data.refused` event. Explore reads the
school database through a read-only connection, its SQL is fixed and parameterized, and a
question only ever chooses an analysis and values from lists.

**CSRF.** Every POST must carry `X-CSRF-Token` equal to the session's token,
which the UI reads from `GET /auth/me`, and anything else is a 403. In
production we added a second layer that checks an Origin or Referer header, when
present, against the request's own host.

**Rate limits.** We run in-process token buckets. Every client IP gets 600
requests per minute across all routes, including the public `/health`,
`/ready`, and `/auth/login`. That is generous on purpose, because everyone on
a campus network can reach us from one shared address. Each signed-in session
then has its own bucket of 120 requests per minute, which is what paces one
person. `POST /ask`, `POST /explore` and the dispatch Send are capped at 5 per minute per
session and per IP, because they spend model calls or send a message, and the
login throttling above applies on top. Over the limit is a 429 with
`Retry-After` and the plain message "CampusLens is busy. Wait a minute and try
again." The variables `CABINET_RATE_GENERAL_PER_MIN`,
`CABINET_RATE_SESSION_PER_MIN` and `CABINET_RATE_ASK_PER_MIN` change the three
limits. These are single-process limits, which the "What is NOT covered"
section explains.

**Headers and payload hygiene.** Every response carries
`Content-Security-Policy` (`default-src 'self'; connect-src 'self'; img-src
'self' data:; style-src 'self'; frame-ancestors 'none'; base-uri 'self';
form-action 'self'`), `X-Content-Type-Options: nosniff`, `Referrer-Policy:
no-referrer`, `Permissions-Policy` denying camera, microphone, and geolocation,
`Cache-Control: no-store` on API JSON, and HSTS in production. The policy
allows no `'unsafe-inline'`, because the API serves only JSON and the UI is
served separately with no inline styles. We cap request bodies at 256 KB, and
anything larger is a 413. `/admin/datasets` carries a whole dataset document,
so its 20 MB cap applies only after the session and admin role checks, and a
pre-auth caller's body is never buffered. A POST that carries
a body must declare `Content-Type: application/json`, and anything else is a
415. We enforce the cap on the bytes actually read, so a chunked body without a
Content-Length cannot bypass it.

**Audit log integrity.** Every event carries `prev_hash` and `hash`, a sha256
chain over the canonical JSON of the event, starting from a fixed genesis.
`python -m cabinet.audit verify var/audit/events.jsonl` replays the chain and
exits non-zero on any break, whether an edit, an insertion, or a reorder, and a
test tampers with one line and watches verify fail. `GET /events`, open to
admin, executive, and reviewer only, includes the hashes. The chain detects
modification of retained events, but it cannot detect deletion of whole
trailing events, so the moved-aside and `.torn-*` files matter and the app
never deletes them. The torn-tail repair removes only bytes that were never a
complete event, namely a killed process's partial final line, so it cannot
break the chain. The vocabulary is eighteen frozen event types, where the
original eight are `question.asked`, `task.assigned`, `data.granted`,
`data.refused`, `finding.produced`, `briefing.produced`, `decision.approved`,
and `task.created`, and dataset administration added `dataset.uploaded`,
`dataset.activated`, and `dataset.deleted`. User administration added
`admin.changed`, which covers user creation, disable and enable, role changes,
and now office address book changes, with the payload `action`, target user id,
`role`, and `by`. Recording or revoking the counseling authorization is one
`admin.changed` event with action `counseling_authorization`, and its payload
carries `by`, `authorized`, `authorized_by`, and `document_reference` as typed,
never counseling data. The dispatch events are `task.dispatched`, `task.sent`, and
`task.send_failed` (a provider refusal or failure, with the error and never the
message body), and their actor is the named person who composed or sent the
message. The Financial Aid review queue added `aid.queued` (decision, dataset,
and student count, never a student id) and `aid.updated` (row id, decision,
the status before and after, and whether the note changed, never a student id
and never the note text), and their actor is the acting user's email. The
audit log is append-only and outlives the dataset purge, so neither event names
a student. Explore added `explore.answered`, which closes each answered question with
the task id, the question event id, the analysis ids of its steps, their row counts, and
which planner and writer ran, never a value. Explore also writes the existing
`question.asked` (any student-id-shaped token in the question is replaced first, and
`POST /ask` applies the same redaction to its `question.asked` and `data.refused`
events before either is written, so a typed id never reaches the chain or a reviewer),
`data.refused` (counseling, one student, or a prediction, refused before planning, and
withheld instructor rows), and one `data.granted` per step with the analysis id, the fields
it read, and `aggregate_only: true`.

**User administration.** Institution admins manage their institution's users
from the Institution screen or `/admin/users`, which requires the admin role
and the same CSRF and rate-limit middleware as every other route. The institution comes from the session, and a user id from another
institution is a 404, never a 403. Creating a user returns a generated one-time
password in the response, which is the only place it ever appears. The store
keeps only its scrypt hash, which is never logged, and no audit payload carries
it. An admin cannot disable their own account, and an institution's last
enabled admin can be neither disabled nor demoted. A disabled account cannot
log in, and its existing session is rejected on the next request.

**The counseling authorization.** `GET` and `PUT
/admin/institution/counseling-authorization` are admin only, under the same CSRF
token and rate limits as every other route, and the institution comes from the
session. Recording needs both the name and title of the person who authorized it
and the document reference, each at most 200 characters, and anything else is a
422 that records nothing. Recording or revoking recomputes the findings on the next
request. A change waits for any question being answered for that institution, and
after a revoke a stored Chief of Staff section that cited the count is served as
unavailable until the question is asked again. The authorization never grants a field. The counseling fields stay refused
to every role, so `POST /governance/request` for `counseling.counseling_notes` is
refused and logged as `data.refused` exactly as before, authorization or not. What
it allows is one count computed in code (M9), with no rows and withheld below 10.
The Chief of Staff's `data.granted` event for it is separate from the aggregate
grant and records `aggregate_only: true`, the fields read, and the authorization
reference. No analyst ever receives it.

**Dispatches, the governed execution step.** An approved leadership decision
can be sent to its responsible office, and only by a named person. We compose
the message in code from the findings (`cabinet.questions.compose_dispatch`,
one template per decision id), so no model writes it and no student identifier
can enter it. `GET /decisions/{id}/dispatch` lets every role read the draft.
`POST /decisions/{id}/dispatch` lets staff, executive, and admin compose the
draft, with a loud 409 before approval. `POST /decisions/{id}/dispatch/send`
is for staff and admin only, and an executive's Send answers a loud 403 with a
`data.refused` event. Send carries the same CSRF token as every POST and sits
under the tighter rate bucket that also paces `POST /ask`. The recipient is
always an office mailbox from the institution's address book (`GET`/`PUT
/admin/offices`, admin only), never a student address, and an office without a
configured mailbox makes Send refuse with a clear message. There is one
dispatch per task per dataset, and a second Send is a 409 that returns the
earlier record. The outbound provider (`cabinet.outbound`) is `outbox` by
default. It writes the message to
`var/outbox/<institution slug>/<dispatch id>.eml` (0600) and reports it sent with `provider=outbox`, so nothing leaves
the machine. `CABINET_OUTBOUND=smtp` delivers for real with stdlib smtplib
(STARTTLS, or SMTPS on port 465), and we enable it only when every setting is
present, namely `CABINET_SMTP_HOST`, `CABINET_SMTP_PORT`, `CABINET_SMTP_FROM`,
and `CABINET_SMTP_PASSWORD_FILE`. The password is read from the named file,
never from an environment value, so it cannot leak through a process listing.
In production a missing setting is a one-line startup refusal. A failed send
is recorded on the dispatch row (`status=failed` plus the error) and can be
retried.

**Secrets and configuration.** `CABINET_SECRET_KEY`, at least 32 bytes, signs
sessions. We keep the model key in the environment or `cabinet.local.env`, which
is gitignored, and it is never logged, recorded, or returned. The Ethos import
(`make import-ethos`) adds two more secrets, both read from files named by
environment variables and never from env values, so they cannot leak through a
process listing or a dumped environment. `CABINET_ETHOS_API_KEY_FILE` holds the
institution's Ethos Integration API key, used only for the `/auth` token
exchange and redacted from every error. `CABINET_PSEUDONYM_KEY_FILE` holds the
keyed hash key that pseudonymises student and advisor ids at the institution's
edge. It is never stored, never printed, never sent anywhere, and no reverse
mapping table is written. Losing or rotating the pseudonym key makes re-imports
unlinkable to earlier ones, which is the point. With
`CABINET_ENV=production` the app fails closed, so we refuse to start without
`CABINET_SECRET_KEY`, and we refuse to bind without an explicit `CABINET_BIND`.
The default bind is 127.0.0.1, and anything else must be chosen on purpose,
while `make check-config` prints which `CABINET_*` variables are set with all
values redacted. Outside production, a missing secret degrades to an ephemeral key, so
sessions do not survive restarts, with a stderr warning rather than a silent
default.

**The model endpoint.** Teams use a hosted chat-completions endpoint over https:
`CABINET_LLM_BASE_URL=https://<provider host>/v1`, `CABINET_LLM_MODEL=<model name>`,
and the key in `CABINET_LLM_API_KEY` (or the variable `CABINET_LLM_API_KEY_VAR` names
in another env file), in the environment or the gitignored `cabinet.local.env`, never
committed. With `CABINET_ENV=production` the app refuses to start, with one line on
stderr, unless the base URL is `https` or plain `http` to a loopback address
(`127.0.0.1`, `localhost`, `::1`) for a self-hosted model on the same machine, so the
key and the findings never travel in the clear. An unset base URL still starts, and
asks then answer unavailable. The model client refuses every redirect: a 3xx answer
is typed unavailability naming the redirect, the request is never repeated to the
`Location`, and the `Authorization` header therefore never reaches another host. A
test runs a real local endpoint that answers 302 and checks that nothing reaches the
redirect target.

**Small operational counts.** M5 counts unresolved holds per responsible office
(for example Library 1, Registrar 2), and M8 counts students per support indicator,
some of them 0. We decided these are operational work counts, not population
statistics: they tell an office how many items are in its own queue, so the briefing
and the evidence drawer keep showing them exactly to every signed-in role, as before
(the student ids behind them go to executives and administrators only, above). They
are not sent to a model when they are small. Just before a live model call, every
office count and indicator count under 10 (the same minimum as M9), zero included,
is replaced by the words "fewer than 10", so the model reads the office list and
the indicator list with those phrases (`permissions.coarsen_small_counts`, applied
in `ChatProvider`). Counts of 10 or more, and the M5 and M8 totals, go as before. The
validator accepts "fewer than 10" (or "fewer than ten") in a claim that cites M5 or
M8 only while that finding has a count under 10, and a bare "10" is still rejected.
We applied this to the live prompt only, not to the findings a role receives,
because those findings are the key of every recorded answer: changing them would
orphan the committed replay recordings, and they cannot be re-recorded offline. So
replay keeps working unchanged, and a recorded answer made before this change may
still name a small office count in the demo. The next live recording will not.

**The model's output and the corrective retry.** The model sees only the
findings its role received, and we show nothing it writes until the validator
passes it. Every claim must cite a finding the role received, and every
number and date must come from the findings that claim cites. When an answer
fails, the runner asks the model once more with the same inputs plus one short
correction that states the validator's reason in plain words, for example
"Your previous answer cited M7, which you did not receive. Use only the
findings you were given." The correction names finding IDs and the rule, and
it never adds a finding value or any other data. We do not send the rejected
answer back. The second answer goes through the same validator, and a second
failure leaves the section unavailable, exactly as before. Only a validated
answer is shown or recorded, and a recording is always keyed to the original
inputs, never to the correction. `CABINET_VALIDATION_RETRIES` sets the number
of corrective tries (default 1, capped at 3), and 0 turns the retry off. A
replayed recording never takes this path, because it was validated when it
was made. The audit trail carries the count as `validation_retries` on the
existing `finding.produced` and `briefing.produced` events, with no new event
type.

**Dependency audits.** `make audit` runs `pip-audit` over the pinned runtime
requirements and `npm audit --omit=dev` for the UI, and both are currently
clean, with no advisories and none accepted.

**The Financial Aid review queue.** The queue gives the Financial Aid office
the facts it needs to start its own review of the students M3 counts, and the
software makes no determination about any student. `POST
/decisions/{id}/aid-queue` (executive, staff, and admin, through the
`/decisions/` prefix rule) prepares it once per decision per dataset, only
after the emergency-aid review decision is signed off for the active dataset,
and it answers a loud 409 with a `data.refused` event before that. `GET
/aid-queue` is open to aid, admin, executive, and reviewer, and staff cannot
read the rows. `PATCH /aid-queue/{id}` is for aid and admin only, carries the
CSRF token like every state-changing request, and accepts a status from a
fixed list and a note of at most 1,000 characters, stored as typed. A row id
from another institution is a 404. The rows never reach a model, because the
model payloads are built from the findings alone and the modules that build
them do not import the queue. A test runs both questions after a note is
saved and checks that no payload carries a queued student id, a queue field,
or the note.

## What is NOT covered

- **Single-process rate limits.** The buckets live in the API process, so
  behind more than one process or replica they under-count. The alternative is
  limiting at the proxy, for example nginx `limit_req` or the platform's own
  rate limiting, keyed by IP.
- **No WAF and no request inspection beyond the above**, because the reverse
  proxy is the place for that if it becomes necessary.
- **No MFA yet.** We run password plus session only, and SSO or MFA is a future
  milestone.
- **HTTPS is terminated by the proxy.** The API itself binds 127.0.0.1 and
  speaks plain HTTP, and HSTS plus the Secure cookie are correct only because
  the proxy answers HTTPS. Do not expose the API port directly.
- **Tail truncation of the audit log.** Deleting whole trailing events is not
  detectable by the hash chain alone. The chain is also unkeyed, so an attacker
  who can rewrite the log file can rebuild the chain from their edit onward.
  The chain catches single-event tampering, corruption, and anyone who cannot
  recompute hashes, and retention plus off-box copies of the log are the
  mitigation for a filesystem-level attacker.
- **Account recovery and self-service password change.** These flows do not
  exist yet, so an admin recreates a user, from the CLI or the Institution
  screen, and hands over the new one-time password.
- **Per-user audit attribution of business events.** Who asked and who approved
  still records the cabinet role as actor. The dispatch events are the exception
  and the direction of travel. `task.dispatched` and `task.sent` record the
  acting user's email, because a named person sending is the point of the
  feature. `aid.queued` and `aid.updated` follow the same rule.

## Data-handling posture

We seed all bundled demonstration data as fictional, from `data/fixture.json`
and `data/generate_fixture.py`, and the demo never touches real student
records. Uploads are built, and the validator checks each uploaded dataset
against [SCHEMA.md](../SCHEMA.md) before accepting it. It rejects columns that
look like personal identifiers, including names, email addresses, phone
numbers, social security numbers, and dates of birth. Each institution is a
separate tenant, and an institution's uploaded data stays in that tenant,
isolated in storage and on every route. Deleting a dataset is a soft delete,
and `python -m cabinet.datasets purge-deleted` hard-deletes datasets past the
30 day retention window. An institution can ask us to delete its data at any
time, and the audit log then retains only the fact of deletion.

Counseling free text is never stored. An upload (the admin screen or the Ellucian
import) may carry `counseling.counseling_notes`, but before the document is written
every note that carries text is replaced by the fixed marker "note on file; text
removed at upload", and an empty or non-text note becomes null. M9, the authorized
aggregate, counts a student when a note exists or the chaplain flag is set, so its
count is the same and the words never reach the disk, a backup, or a model. The
bundled fictional fixture keeps its five invented notes, because it is committed demo
data loaded from the repository rather than uploaded, and the refusal of the field is
real only if the field has content.

Files at rest are readable by the service's own user only. `cabinet.db` is created
with mode 0600 before SQLite opens it, and an existing one (with any journal, `-wal`,
or `-shm` file) is tightened to 0600 at startup and by `make migrate`. Dataset
documents are 0600 in 0700 directories. A backup directory is 0700 and every file in
it (the database copy, the dataset copies, and `manifest.json`) is 0600, and
`make restore` writes the database and dataset files back at 0600. The generated
Demonstration University database for Explore is fictional and is not covered by this
rule.

## Reporting a vulnerability

Email us at **contact@example.edu**, a placeholder mailbox that we will replace
with our real mailbox before go-live. Please include the route, the role you
were logged in as, and a reproduction, and do not open a public issue for a
vulnerability.
