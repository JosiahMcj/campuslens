# Verification of the ROADMAP §8 matrix, run on the real path

*I induced every row below on the running app, the real FastAPI on
127.0.0.1:8910 and the real Vite UI on 127.0.0.1:5200, driven through headless
Chrome over CDP. Nothing here is inferred from tests. The model side ran in
replay mode (`make api REPLAY=1`), because that is how the demo runs, and the
rows that need the deterministic stub say so. I made no live model call and read
no key.*

My CDP driver and scratch files lived in `var/verify/` (gitignored). The evidence
worth keeping is in `docs/screenshots/day8-*.png` and `docs/verification/logs/`.
The exact commands are quoted per row.

## Row 1. Real path (replay standing in for live). PASS

I started from a fresh `var/`, and I moved the audit log aside first, since it
held six events a browser tab wrote at startup (see Open). Then `make api
REPLAY=1` and `make ui`, and through CDP I typed
*"What should I know about spring registration?"* into the question field and
clicked Ask.

| Check | Command / click | Observed | Result |
|---|---|---|---|
| Page loads to the question field | open `http://127.0.0.1:5200` | question field + computed briefing render | PASS |
| Ask the approved question | CDP: type into `#question-input`, click Ask | accepted; dispatch panel plays | PASS |
| All seven sections render, in order | read every `main h2` | sections 1 to 7 in the proposal's wording and order | PASS |
| Sections 1/2/3/7 labelled honestly | read `.analyst-source` per section | "Written by the Chief of Staff / Enrollment Analyst / Student Success Analyst (recorded run)" on all four | PASS |
| Claim opens to evidence (§1) | click FindingLink `[M1]` in section 1 | drawer: value −4.8 %, formula, 7 source fields, 244 rows | PASS |
| Claim opens to evidence (§2) | click FindingLink `[M1]` in section 2 | same M1 drawer | PASS |
| Claim opens to evidence (§3) | click FindingLink `[M3]` in section 3 | drawer: value 18, formula, 4 source fields, 18 rows | PASS |

Evidence. `docs/screenshots/day8-row1-question.png`,
`day8-row1-briefing.png`, `day8-row1-evidence-drawer.png`.

## Row 2. Empty data. PASS

I served an empty-but-valid fixture (the real fixture's `terms` with both student
lists empty) with the **fake provider**. Replay has no recording keyed to an
empty findings hash, so the stub is what renders the briefing here.
`CABINET_FIXTURE=$PWD/var/verify/empty-fixture.json CABINET_AUDIT_PATH=… CABINET_PROVIDER=fake make api`.

| Check | Command / click | Observed | Result |
|---|---|---|---|
| Every metric renders `--` per contract | `curl /findings` | M1/M2/M3/M4/M6/M7 display `--` each with a plain reason; M5 displays "No unresolved holds"; `as_of: null` | PASS |
| No crash | `POST /ask` | accepted; section 1: "No findings are available for this briefing [M2]." | PASS |
| Page renders the empty state | CDP page load | finding list `["--","--","--","--","No unresolved holds","--","--"]`, every `--` styled missing, no error panel | PASS |
| Page briefing says there is no data | CDP: type question, Ask | section 1 reads "No findings are available for this briefing [M2]." | PASS |

Evidence. `docs/verification/logs/row2-empty.txt`,
`docs/screenshots/day8-row2-empty.png`. The API-side empty state is also locked
by `backend/tests/test_fixture_config.py::test_empty_fixture_renders_dash_dash_per_contract`
(passing).

## Row 3. Provider down. PASS

I configured `CABINET_PROVIDER=chat CABINET_LLM_BASE_URL=http://127.0.0.1:9` with
a dummy key. Nothing listens there, and the call is refused before any request
leaves the process.

| Check | Command / click | Observed | Result |
|---|---|---|---|
| Analyst route reports unavailability | `curl /briefing/enrollment` | HTTP 503 `{"available":false,"reason":"chat endpoint connection error: [Errno 61] Connection refused"}` | PASS |
| Metrics and evidence still render | `curl /findings`; CDP: open M1 drawer | 200 with real values; drawer shows formula, 7 fields, 244 rows | PASS |
| "Model unavailable" state with retry | CDP page load | banner + "Check again" in both analyst sections; clicking it re-asks and returns to the same honest state | PASS |
| `/ask` degrades, never invents | `curl -X POST /ask` | accepted; sections 1/2/3/7 `unavailable` with the reason; sections 4/5/6 fully populated | PASS |

The documented fallback is replay, `make stop && make api REPLAY=1`, recorded in
DEMO-SCRIPT.md ("Failure fallbacks") and RUNBOOK.md, and exercised as row 4.
Evidence. `docs/verification/logs/row3-provider-down.txt`,
`docs/screenshots/day8-row3-provider-down.png`.

## Row 4. Replay determinism. PASS

I ran twice from a clean `var/`, moving the audit log aside between runs. Each
run was `make api REPLAY=1`, then `POST /ask`, saving the response.

| Check | Command | Observed | Result |
|---|---|---|---|
| Run 1 ≡ run 2 | canonical-JSON compare of sections 1/2/3/7 | byte-identical (sha256 per section in the log excerpt) | PASS |
| Sections ≡ golden run | compare section texts to `data/golden/*.json` | §2 == enrollment recording, §3 == student-success recording, §1/§7 == chief recording's two halves | PASS |

Evidence. `docs/verification/logs/row4-replay-diff.txt` (raw responses
`var/verify/row4-run1.json` and `row4-run2.json`).

## Row 5. Refusal. PASS

| Check | Command / click | Observed | Result |
|---|---|---|---|
| §5 refusal tests (automated) | `pytest` the four refusal tests | 4 passed in 0.18 s | PASS |
| Student Success Analyst handed the Enrollment task | `POST /governance/request` with the enrollment field list | refused: `enrollment.registered_credit_hours`, `enrollment.registration_date`; `data.refused` #11 | PASS |
| Enrollment Analyst asks for `holds.amount` | CDP: "Show a denied data request" button | refusal sentence shown; `data.refused` #12 highlighted in the log | PASS |
| Hand-typed out-of-scope question | CDP: type "Which students are in counseling?", Ask | refused with a sentence by the Chief of Staff; third `data.refused` | PASS |
| `data.refused` visible in the audit view | CDP: filter to `data.refused` | all three refusals listed with their sentences; API agrees (3 events) | PASS |

Evidence. `docs/verification/logs/row5-refusals.txt`,
`docs/screenshots/day8-row5-refusals.png`.

## Row 6. Restart. PASS

With `CABINET_PROVIDER=fake make api` running, I started `POST /ask` and sent
`kill -9` to this project's own pid (from `var/api.pid`, verified by `ps`) 0.3 s
in, then restarted.

| Check | Command | Observed | Result |
|---|---|---|---|
| Kill lands only on the demo API | `kill -9 $(cat var/api.pid)` | process dead, port 8910 free, nothing else touched | PASS |
| Audit log intact across the kill | `GET /events` after restart | 24 events, ids continuous 1 to 24 | PASS |
| Torn line handled | induced torn tail (see note), restart | warning logged; 39 torn bytes moved to `events.jsonl.torn-20260925T042754178195Z`; no intact event lost | PASS |
| Briefing regenerates | `POST /ask` after restart | accepted; sections 1/2/3/7 available | PASS |
| No duplicate `task.created` on re-approve | `POST /decisions/approve` twice | first `created: true` (events 35, 36); second `created: false`, same task; exactly **1** `task.created` in the whole log | PASS |

A note on the torn line. The fake provider answers in milliseconds, so the
killed `/ask` had already completed and fsynced, and the log ended clean. To
exercise the recovery path the row asks about, I appended a 39-byte partial JSON
line (the prefix of the next event) to `var/audit/events.jsonl` while the API was
down. Those are exactly the bytes a kill mid-append leaves. The expected warning,
quoted for row 8, is this.

```
var/audit/events.jsonl: truncated 39 torn byte(s) from the end of the audit
log (a killed process's partial write); kept a copy at
var/audit/events.jsonl.torn-20260925T042754178195Z
```

Evidence. `docs/verification/logs/row6-restart.txt`.

## Row 7. Timing. PASS

I scripted the six beats through CDP on the replay path (`node
var/verify/row7.mjs`), with the DEMO-SCRIPT pauses added as sleeps.

| Beat | Action | Action time | With pause | Demo window |
|---|---|---|---|---|
| 1 | type question + Ask | 0.02 s | 25.8 s | 0:30 |
| 2 | dispatch, visibly scoped | 0.27 s | 35.3 s | 0:40 |
| 3 | scroll the briefing | 1.61 s | 61.6 s | 1:10 |
| 4 | open one claim to evidence | 0.66 s | 40.7 s | 0:50 |
| 5 | approve the decision | 0.31 s | 25.3 s | 0:30 |
| 6 | audit log + refusal | 1.24 s | 16.2 s | 0:20 |

**Total 204.9 s, under the four-minute limit**, and every beat inside its own
window. On replay the software costs under five seconds. The four minutes are
the spoken script. Evidence. `docs/verification/logs/row7-timing.txt`,
`docs/screenshots/day8-row7-beat{2,4,5,6}-*.png`.

## Row 8. Logs. PASS

After all of the above, I read both logs.

- `var/api.log` (the row-7 replay process) held INFO lines only, startup, and
  200s with the expected 404s from `GET /briefing` before the first Ask. No
  warnings, errors, or tracebacks. The one expected warning, row 6's torn-line
  truncation quoted above, happened in the row-6 process's log, since each
  `make api` start truncates `var/api.log`. It is preserved in
  `docs/verification/logs/row6-restart.txt`, and its `.torn-*` sibling file is
  still in `var/audit/`.
- `var/ui.log` was clean, with one caveat I handled honestly. My verifying shell
  exports both `NO_COLOR` and `FORCE_COLOR`, which makes Node print
  `Warning: The 'NO_COLOR' env is ignored…` on any node launch. That is my
  environment, not the app. Relaunching with `env -u FORCE_COLOR -u NO_COLOR
  make ui` produces a spotless log (three lines, Vite ready plus the Local URL).

Evidence. `docs/verification/logs/row8-api.log`,
`docs/verification/logs/row8-ui-clean-env.log`.

## Open

All three items are resolved.

- **Opening the page wrote six audit events before any question was asked.**
  The UI's initial load fetched `GET /briefing/enrollment` and
  `GET /briefing/student-success`, and each cache miss ran the analyst, logging
  `task.assigned` + `data.granted` + `finding.produced`. I confirmed this is
  where the six pre-row-1 events came from. Their provider and label are the
  golden recording's, which pytest cannot produce (`conftest.py` points tests at
  an empty golden dir). Their timestamp matches the first `make api` and
  `make ui`, when a browser tab from an earlier session reconnected. It was
  honest logging, but DEMO-SCRIPT preflight's "audit log empty" was only true if
  the log was reset *after* the browser was on the question screen.
  **Resolved.** The page no longer calls `GET /briefing/<role>` on load. It
  fetches `GET /findings` and `GET /briefing` only, and the analysts and chief
  run only inside `POST /ask` (the "Check again" button re-runs `POST /ask`
  too). Locked by
  `backend/tests/test_governance.py::test_page_load_gets_write_no_audit_events`,
  and the preflight now states the log stays empty with the page open.
- **Empty-data action sentences read awkwardly.** With the empty fixture,
  section 5 said "Review the -- students with no advising contact this term."
  (the contracted `--` interpolated into prose). Cosmetic, and the contracted
  empty states themselves all render correctly.
  **Resolved.** Prose that interpolates a finding `display` now says "not
  available" when the `display` is `--` (section 5 actions, the section 6
  decision, backend and UI), and `--` stays only in the evidence list and tiles.
  Locked by
  `backend/tests/test_fixture_config.py::test_empty_fixture_prose_never_interpolates_a_bare_dash_dash`.
- **The source label stacked provenance**, "(recorded run) (live model)".
  Accurate (a recording made by the live model), but worth a wording pass.
  **Resolved.** One parenthetical now. Replay reads "Written by the \<role\>
  (recorded live run)", and live reads "Written by the \<role\> (live model)".
  A configured label replaces "live model" only when it differs. The fake
  provider keeps its `Test stub, not a live model` tag.

## House bar

- I launched it the way the demo launches (`make api REPLAY=1` + `make ui`), and
  I checked the logs, row 8.
- Nothing was sent anywhere. The only outbound attempt was row 3's deliberate
  connection to 127.0.0.1:9, refused. All data fictional, all writes inside the
  project.
- `make check` was green before and after this run (155 pytest + 56 vitest,
  ruff, mypy, eslint, tsc).

## User management on the real path. PASS

*I ran this on the real API on `127.0.0.1:8920` and the real Vite UI on
`127.0.0.1:5210`, started with `CABINET_DB=$PWD/var/h5-demo.db
CABINET_BIND=127.0.0.1:8920 make api REPLAY=1` (replay mode, no live model
call). I drove both through headless Chrome over CDP. The browser had its own
pid file and debug port 9443, and an isolated context held the second user's
session.*

| Step | How | Expected | Result |
|---|---|---|---|
| Bootstrap | `make bootstrap-admin EMAIL=admin@h5.example.edu` | password printed once | PASS |
| Admin signs in, opens Institution | CDP: `#login-email`/`#login-password`, masthead link | Users section lists the admin, own row marked "you" | PASS (`docs/screenshots/h5-users.png`) |
| Add a staff user | CDP: add form, role Staff | 201; callout shows the one-time password with "Shown once. Share it privately; it is not stored."; table gains the row | PASS (`docs/screenshots/h5-password-once.png`) |
| Staff signs in with that password | CDP isolated context, paste password | staff briefing view; masthead "staff@h5.example.edu · Staff"; no Institution link | PASS |
| Admin disables the staff user | CDP: Disable → inline confirmation | row flips to Disabled, Enable offered | PASS |
| Disabled user's next request | CDP: reload the staff tab | session rejected (401), app returns to sign in | PASS |
| Disabled user signs in again | CDP: same email and password | generic refusal: "That email and password did not work…" | PASS (`docs/screenshots/h5-disabled.png`) |
| Audit | `GET /events?type=admin.changed`; `python -m cabinet.audit verify var/h5-demo.db` | `created` and `disabled` events with payload {action, target_user_id, role, by}; chain verifies | PASS |
| Password hygiene | `grep` both one-time passwords against the exported audit log and `var/api.log` | zero occurrences | PASS |

After the run, `make check` was green (282 pytest + 102 vitest, ruff, mypy,
eslint, tsc). I stopped all three demo processes (api, vite, Chrome) through
their own pid files.
