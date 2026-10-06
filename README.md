# Golden Eagle AI Cabinet

*Governed AI employees helping university leaders turn SIS data into human-centered action.*
We built this for the Gloo AI Hackathon (Boulder, Oct 6-8, 2026), and `ROADMAP.md` is the
plan. In one sentence, Golden Eagle AI Cabinet transforms Ellucian SIS data into an
executive briefing by coordinating permission-limited AI employees across enrollment and
student success. It helps university leaders see what matters, understand why, and direct
timely human action.

We put all logic in a Python backend (FastAPI on `127.0.0.1:8910`, pytest, ruff, mypy, in
`.venv/`), and the React + TypeScript UI (Vite on `127.0.0.1:5200` with `strictPort`,
Vitest, eslint, `tsc`) is the thin interface layer. Everything binds to `127.0.0.1` only.

What runs today is the fixture dataset (`data/fixture.json`, fictional and seeded)
and the metric functions M1-M7 (`backend/src/cabinet/metrics.py`, contracts in
`CONTRACTS.md`). So are the permission gate, the append-only audit log, the
governance API, and the dashboard. We built all three AI employees with output
validation. The Enrollment Analyst covers M1, M2 and M7. The Student Success
Analyst covers M3, M4 and M5. The Chief of Staff dispatches the tasks and merges
the seven-section briefing. The golden replay runs for both approved questions and
all three roles are committed in `data/golden/`. Sign-in with five roles and
institution accounts with validated dataset upload are live as well. The human panel approves the leadership decision and
records it, and the audit log shows every grant and every refusal, including both
refusal demos. Beside the briefing, five figures show M1 to M4 and M8, and each
one opens its own evidence panel. Every analyst-written section carries an honest
source label, "Written by" the role that wrote it, and a small "About this
answer" detail says whether the text was replayed from an earlier live run or
written just now. We verified the whole system on the real path against the Day-8
matrix in `docs/VERIFICATION.md`, and all eight rows pass. The six-beat demo timed
at 204.9 seconds against the four-minute limit. We documented how to run, stop,
replay, and reset any of it in `RUNBOOK.md`.

An approved leadership decision can also go to its responsible office, and only
a named person can send it. The software composes the message from the verified
findings, never the model and never with a student identifier. A staff member
reviews it and clicks Send, and the default outbound provider writes it to a local
outbox file, so nothing leaves the machine. Real SMTP delivery stays off unless
someone configures it on purpose (`RUNBOOK.md`, "Sending an approved follow-up").

The emergency-aid review decision can also open a Financial Aid review queue.
Once leadership signs off, a person prepares the queue, and the Financial Aid office
gets one row for each of the students M3 counts, with the facts it needs to start its
own review (the qualifying hold's amount, date, and office, the registration status,
and the advising status). The software makes no determination about any student. A
fifth role, `aid`, works the queue by setting each row's status and keeping a note,
and the executive and the reviewer can read it. No row, fact, or note ever reaches a
model (`RUNBOOK.md`, "The Financial Aid review queue").

The briefing also reports M8, students with one or more support indicators
(`backend/src/cabinet/indicators.py`, contract in `CONTRACTS.md`). Each indicator
is a named, deterministic rule with a plain-language reason. The four rules test
for an unresolved financial hold under $1,000, no advising appointment this term,
unresolved holds at more than one office, or registration closing within 14 days.
A student has indicators when at least one rule fires. The rules never combine
into a weighting or sum, no model ever sees the per-student detail, and the
evidence drawer shows exactly which rules fired for each pseudonymous id, with the
rule's reason.

Explore answers specific questions over Demonstration University, the synthetic school
in `data/school/` ("Which major has the lowest GPA? In that major, what is historically
the hardest class, and which instructor has historically taught it?"). The question
becomes a plan of reviewed analyses, code computes every table, and the answer cites the
cells its numbers came from. The model sees only the analysis catalog and the finished
aggregate tables, never a student row. Counseling questions and questions about one
student are refused before any planning, groups under 10 students are withheld, and
instructor rows go to the executive and admin roles only (`POST /explore`,
`GET /explore/catalog`, `make school-data` then `make explore-check`, and
`docs/EXPLORE.md`).

What is left is rehearsal. The demo script and the offline deck in
`docs/backup-demo.html` are frozen, and the remaining items are the timed
rehearsals, the backup video, and the pitch.

## Setup

Requires Python 3.12 and Node 22 on `PATH`.

```bash
make setup   # creates .venv with python3.12, pip installs backend, npm ci in ui/
```

## One-command checks

Each check runs both stacks, and the `-python` and `-ui` variants run one.

| Command | What it runs |
|---|---|
| `make lint` | ruff (backend) + eslint (ui) |
| `make typecheck` | mypy (backend) + `tsc -b` (ui) |
| `make test` | pytest (backend) + vitest (ui) |
| `make check` | all three, both stacks, the same commands CI runs |

## Run

Before the first start we create the demo accounts once. The bootstrap admin owns
the first institution, and the president account is the executive the demo signs
in as. Each command prints its generated password exactly once and never logs it,
so we save it the moment it prints.

```bash
make bootstrap-admin EMAIL=admin@demo.test          # bootstrap institution + first admin (once)
make user EMAIL=president@demo.test ROLE=executive  # the president the demo signs in as
```

```bash
make api    # FastAPI, polls /health and prints "api started" on success
make ui     # Vite dev server, dev proxy /api to the API
make stop   # stops both, via pid files in var/ (never pkill by name)
make audit          # pip-audit (pinned runtime reqs) + npm audit --omit=dev
make check-config   # which CABINET_* variables are set (values redacted)
make build          # production UI into ui/dist
make serve          # one production process serving UI + API (needs CABINET_SECRET_KEY)
```

The app always opens on the sign-in screen, and the demo begins by signing in as
the president. Every route except `/health` and `/ready` needs a logged-in user,
and `RUNBOOK.md` has the curl flow under "Users and login".

`make api` serves FastAPI on `http://127.0.0.1:8910`, where `GET /health` returns
`{"ok": true}`, and `make ui` serves Vite on `http://127.0.0.1:5200`.

`make stop` stops only the processes this project started (pid files in `var/`, plus
their child processes), and if something else is still listening on 8910 or 5200
afterwards, it reports the pid and command without killing it. That process has
another owner.

Both servers run in the background, logs are in `var/api.log` and `var/ui.log`, and
`var/` is gitignored.

For production, `make build` compiles the UI into `ui/dist`, and `make serve`
runs one process that serves the built UI and the API together. We made the
production configuration fail closed. The API refuses to start without a
`CABINET_SECRET_KEY` of 32 bytes or more and an explicit `CABINET_BIND`.
`RUNBOOK.md` covers the rest under "Operating in production".

## Institutions and datasets

Every user belongs to an institution, and each institution's findings come from
its active dataset. A new institution starts with the seeded fictional
demonstration dataset. An admin uploads the institution's own data as one JSON
document in the `SCHEMA.md` shape, up to 20 MB. We validate the upload before
anything is stored. A document carrying personal identifiers (email, phone,
social security number, date of birth, a name on a student record) is rejected
with every problem listed. The admin then activates or
deletes datasets from the Institution screen.

We pin approvals to the dataset they were computed from. A newly activated
dataset starts with no briefing and no approvals.

Counseling data stays refused to every role and every AI employee. The one
exception is a count, and only with the institution's written consent. When the
counseling director authorizes it and an admin records that authorization in
Institution settings, the briefing shows how many students not yet registered have
had any counseling contact this term (M9). It shows no rows, names the person who
authorized it, and withholds any count under 10 as "fewer than 10". `RUNBOOK.md`
covers recording and revoking it.

For questions that need a whole school's history, we also ship Demonstration
University in `data/school/`. It is a synthetic, Ellucian-shaped SQLite database
covering Fall 2020 to Spring 2026, with 40 programs, 906 courses, 220 fictional
instructors, about 6,200 pseudonymous students, and about 140,000 graded
registrations. `make school-data` builds it into `var/school/school.db` in a few
seconds, and `make school-check` recomputes every GPA and every planted fact in
`data/school/VERIFY.md` from the raw rows. Nothing in the cabinet reads it yet, and
a governed question engine will. `data/school/README.md` explains the rest.

## UI states

The dashboard's three states can be induced on demand with query switches on
`http://127.0.0.1:5200` (combinable, details in `ui/README.md`).

- `?slow=1` induces **loading** by adding a 1.5 s client-side delay to API calls, so
  the loading panel is visible.
- `?fail=findings` induces **error with retry** by forcing the findings fetch to fail.
  The same state occurs naturally when the API is stopped.
- `?model=down` induces **model unavailable** by forcing the banner in the *Current
  measure and historical comparison* section while metrics and evidence keep
  rendering. The banner also appears on its own when an Ask produced a briefing
  whose analyst section is unavailable (the provider answered 503 or
  `{available:false}`).

`?evidence=M2` deep-links to the evidence drawer for a finding (M1-M8), and `#audit-log`
deep-links to the audit log.

The UI is also an installable web app, and it installs to a home screen. We
cache only the application shell for offline use, never an API response.
Offline, the shell opens and every number on it still needs the API.

## Findings CLI

```bash
.venv/bin/python -m cabinet.findings [--fixture PATH]   # default: data/fixture.json
```

This loads the fixture, derives the as-of date from the data (never the wall clock),
computes metrics M1-M7 per `CONTRACTS.md`, and prints the findings object as JSON.
Per finding it prints the value, the `display` string (`−4.8 %` style, or `--` per
the zero-denominator rule), the comparison, the source fields, the row IDs, and the
definition text. A `meta` block carries the as-of date, the fixture path, and the
terms used.

## Model provider and replay

The AI employees sit behind one provider interface,
`explain(findings, role) -> Explanation` (`backend/src/cabinet/provider.py`). The code
names no vendor or model, and we select and configure the provider by environment,
never by code changes.

| Variable | Values | Default |
|---|---|---|
| `CABINET_PROVIDER` | `chat` \| `replay` \| `fake` | `chat` |
| `CABINET_LLM_BASE_URL` | base URL of the configured model endpoint | unset |
| `CABINET_LLM_MODEL` | model id sent to the endpoint (never shown or recorded) | unset |
| `CABINET_LLM_LABEL` | what the UI shows as the source | `live model` |
| `CABINET_LLM_REASONING_EFFORT` | sent as `reasoning_effort`, since reasoning models otherwise spend the output budget thinking. Empty omits it | `low` |
| `CABINET_LLM_API_KEY` | the endpoint key, environment only, never in the repo | unset |
| `CABINET_LLM_API_KEY_FILE` + `CABINET_LLM_API_KEY_VAR` | read that one variable's line from another env file | unset |
| `CABINET_RECORD` | `1` records each validated response into `var/replay/`. `overwrite` also replaces existing recordings | off |
| `CABINET_REPLAY_DIR` | extra replay cache directory, searched first | unset |
| `CABINET_FIXTURE` | fixture path | `data/fixture.json` |
| `CABINET_AUDIT_PATH` | audit log path | `var/audit/events.jsonl` |
| `CABINET_DB` | users and sessions database (set but empty is a startup refusal, not the default) | `var/cabinet.db` |
| `CABINET_SESSION_TTL_HOURS` | session lifetime in hours | `12` |
| `CABINET_ENV` | `production` enables fail-closed checks, Secure cookies, HSTS, JSON access logs | unset (development) |
| `CABINET_SECRET_KEY` | signs session cookies (HMAC), required at 32+ bytes in production | unset (ephemeral outside production) |
| `CABINET_BIND` | bind address, host or host:port, required explicitly in production | `127.0.0.1:8910` |
| `CABINET_TRUSTED_PROXY` | the only addresses whose `X-Forwarded-For` is believed | `127.0.0.1` |
| `CABINET_UI_DIST` | built-UI directory served at `/` | `ui/dist` |
| `CABINET_LOCAL_ENV` | path of the env file to load, where service managers point | `cabinet.local.env` at the repo root |

**Security.** Every route except `/health` and `/ready` requires a logged-in
user with the right role. The five roles are admin, executive, staff, reviewer,
and aid (Financial Aid staff), and the executive reads the institution's audit log. Sessions are
server-side and HMAC-signed, passwords are scrypt-hashed, and every POST needs
the session's CSRF token. Sign-in throttling hard-locks an IP, or an IP and
email pair, after 5 failures in 15 minutes, answering 429 with a `Retry-After`
header. The bare email is never hard-blocked and instead pays a progressive
delay of 1, 2, 4, and 8 seconds, capped at 30. Every address may make 600
requests a minute, because a whole campus can share one address, and each
signed-in session may make 120. Over either limit the answer is "The Cabinet is
busy. Wait a minute and try again." Request bodies are capped at
256 KB (20 MB on the dataset upload route). We enforce the cap on the bytes
actually read, and on admin routes it applies only after authentication. The
audit log is a verifiable hash chain in the `audit_events` table
(`python -m cabinet.audit verify var/cabinet.db`). Admins manage their institution's users from the
Institution screen or with `make user EMAIL=… ROLE=…`. `make check-config`
shows the configuration with values redacted, and `make audit` scans the
dependencies. The threat model and control list live in `docs/SECURITY.md`.

The `CABINET_LLM_*` settings can also live in a gitignored `cabinet.local.env` at the
repo root (copy `cabinet.local.env.example`), and the real environment wins. With no
live configuration the chat path fails cleanly. `explain` raises a typed
`ProviderUnavailable`, and `GET /briefing/enrollment` answers HTTP 503
`{"available": false, "reason": ...}` while metrics and the audit log keep working.
The endpoint key is never logged, recorded, or written anywhere, and `fake` is a
deterministic stub (tests, offline development) that builds its text only from the
findings it receives.

**Replay** serves recorded responses, so the demo runs identically with the network
down. A cache miss is `ProviderUnavailable`, never a network call. Search order is
`CABINET_REPLAY_DIR` if set, then `var/replay/`, then the committed golden run in
`data/golden/`. Only validated output is ever recorded, and an existing recording is
never overwritten unless `CABINET_RECORD=overwrite`. We may re-key a golden file when
the findings object gains an aggregate the recorded text does not mention. In that
case we write the same text, re-validated, under the new key with a `rekeyed_from`
marker, and never a new text. We recorded the golden run once with
`make record-golden` (see `RUNBOOK.md`), and afterwards this works.

```bash
make api REPLAY=1                                  # same as CABINET_PROVIDER=replay
curl http://127.0.0.1:8910/briefing/enrollment     # the recorded run, network off
```

Every replay file is labelled with the provider and the model label that produced it,
never the model id. A recording never claims to be from the live model unless it
was. If the app or the room's network fails during the demo, the fallback is
`docs/backup-demo.html`. It is a self-contained offline deck that steps through the
six beats with real screenshots and the script's spoken lines.
To add a provider, implement the `Provider` protocol and add one entry to `_PROVIDERS`
in `backend/src/cabinet/provider.py`, and the analysts, validator, API, and
record/replay path are provider-agnostic.

We validate every analyst output before it is shown. Each claim must carry a finding ID
the role received (`[M2]` style), and every numeral in the text must equal a value,
`display`, or comparison number in the received findings
(`backend/src/cabinet/analysts.py`). The model never receives row IDs, and the
evidence drawer reads those from `GET /findings`. A failure is logged and returned as
unavailable, never rendered.

`POST /ask` accepts only the approved questions. The question registry
(`backend/src/cabinet/questions.py`) holds two of them. The first asks what the
president should know about spring registration, and the second asks where
unresolved holds are affecting continued enrollment. Each question carries its
own dispatch, decisions, and actions, and we refuse any other question.

The endpoint `GET /briefing/enrollment` runs the Enrollment Analyst once and caches
the validated result in-process, keyed by provider, model, and a hash of the received
findings. Repeat calls return the cached result and write no new audit events. The
page itself never calls this route on load, since the analysts run only inside
`POST /ask`, and the route stays for the API (tests, curl).
`GET /briefing/enrollment?refresh=1` or `POST /briefing/enrollment/refresh`
forces a fresh run. Failures are never cached.

## CI

`.github/workflows/ci.yml` runs on every push and pull request with two jobs, `python`
and `ui`, executing the same `make` lint, typecheck, and test commands as above. We want
`main` to require one approving review and both CI jobs. On a private repository that
needs a paid GitHub plan, so until then the reviewer's approval is the rule, not an
enforced gate.

## Layout

```
backend/            FastAPI package `cabinet` (src layout), pytest tests
  requirements.txt    runtime deps, pinned
  requirements-dev.txt  + ruff, mypy, pytest, httpx2, pinned
ui/                 Vite + React + TypeScript dashboard (briefing, evidence
                    drawer, audit log view, decision panel)
data/               fixture.json (fictional, seeded), golden/ (committed
                    replay run), VERIFY.md for hand-counting
cabinet.local.env.example   template for local live-model config (gitignored copy)
RUNBOOK.md          setup, run, stop, restart, replay, record, reset
```

## Contributing

We take one pull request per slice, and the template in
`.github/pull_request_template.md` asks for two lines, `Done when` and `Verified on
the real path by`. Keep CI green, and if you review our pull request, hand-check the
numbers against `data/VERIFY.md` before anything else.
