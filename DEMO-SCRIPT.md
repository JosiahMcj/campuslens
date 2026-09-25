# Demo Script, four minutes

The demo is the scope (ROADMAP §2), and I wrote this script word for word with the
timings as targets from the six beats. I tune the pauses in rehearsals on Oct 4-5,
never the numbers. All numbers come from the fixture, not the model. They are
**−4.8 %, 42, 18, 12**.

The person demoing is "the presenter", and the on-screen persona is "the president".
The president signs in as the executive account created in preflight, and the masthead
names that account for the whole demo.

The seven briefing sections follow PROPOSAL.md's "Executive Briefing Format" in order
and wording. The first six are executive summary, current measure and historical
comparison, student groups most affected, evidence and source fields, operational
actions, and leadership decisions (stated separately). The last is known limitations,
missing data, or conflicting definitions.

---

## Starting the app

One-time setup, from the repo root (creates `.venv`, installs backend and UI deps).

```bash
make setup
```

The demo accounts are also one-time. Each generated password prints exactly once and is
never logged, so it goes straight into the presenter's notes.

```bash
make bootstrap-admin EMAIL=admin@demo.test             # once ever; it refuses once one exists
make user EMAIL=president@demo.test ROLE=executive     # the president the demo signs in as
```

Then two terminals, or run both, since each target backgrounds itself and logs to
`var/`.

```bash
make api
make ui
```

`make api` starts FastAPI on `http://127.0.0.1:8910`, and `make ui` starts Vite on
`http://127.0.0.1:5200` (`strictPort`, dev proxy `/api` to 8910).

I open `http://127.0.0.1:5200`. It opens on the sign-in screen, and the demo signs in
as `president@demo.test`. When the demo is over, I run this.

```bash
make stop   # stops both via pid files in var/
```

**Replay mode.** `make api REPLAY=1` starts the API with `CABINET_PROVIDER=replay`,
serving recorded responses from `var/replay/` or the committed golden run in
`data/golden/`, so the demo runs identically with the network down. I recorded the
golden run once with `make record-golden`, all three roles, and committed it in
`data/golden/` (the live model must be configured, see RUNBOOK.md). Whether the
model runs live or from replay, the numbers are the fixture's. The model only
explains them.

**Preflight (60 seconds before).** The two demo accounts exist (above), and the room
gets a fresh record. I run `make stop`, move `var/` aside with
`mv var var.before-demo-<time>`, and delete nothing in it. Then `make bootstrap-admin`
and `make user` again for the fresh database, then `make api REPLAY=1` and `make ui`
up (I check `var/api.log` and `var/ui.log` for errors). The audit log is empty, and it
stays empty with the browser on the sign-in screen, because the page runs nothing
before the question is asked. The offline deck (`docs/backup-demo.html`) sits open on
the second device.

---

## Beat 1, 0:00 to 0:30 · The question

**On screen.** The sign-in screen, then the briefing shell. The masthead names the
signed-in president, and a single question field sits beside the approved-question
buttons.

**Clicked.** The presenter signs in as the president (`president@demo.test`, the
password printed once at account creation). That takes ten seconds at most, typed
while the opening line is spoken, so the beat keeps its thirty seconds. The shell
opens, and the presenter clicks the question field (or the approved question's
button), types (or pastes, per rehearsal) the one approved question, and presses
Enter.
*"What should I know about spring registration?"*

**Said.**
> "Every fall, a president asks a simple question. What should I know about spring
> registration? Today the answer lives in six spreadsheets and three inboxes. This is
> the Golden Eagle AI Cabinet, governed AI employees that turn student-system data into
> one briefing a leader can act on. Watch what happens when I ask."

## Beat 2, 0:30 to 1:10 · The cabinet goes to work, visibly scoped

**On screen.** The Chief of Staff accepts the question and dispatches two tasks, one to
the Enrollment Analyst, one to the Student Success Analyst. Each task card shows the
exact fields that analyst is allowed to see. An audit event appears for each grant.

**Clicked.** Nothing yet. The presenter lets the dispatch play, then clicks "Fields
granted" on one task card so the audience sees the permission list.

**Said.**
> "The Chief of Staff does not answer itself. It assigns the work. The Enrollment
> Analyst gets enrollment numbers, and nothing else. The Student Success Analyst gets
> holds and advising, and nothing else. Every field granted is logged, and as you will
> see in a minute, every field refused is logged too. These are employees with job
> descriptions, not a chatbot with the database password."

## Beat 3, 1:10 to 2:20 · The briefing

**On screen.** The executive briefing renders, the seven sections in the proposal's
order, as listed above. The operational actions and the leadership decisions are
visually separated.

**Clicked.** The presenter scrolls slowly through the briefing, pausing on the student
groups most affected section.

**Said.**
> "Here is the briefing. Spring registration is down **4.8 percent** versus the same
> date last year. **Forty-two** continuing students have not registered. These are not
> risk scores. They are forty-two people who may need support. Of the forty-two,
> **eighteen** have a financial hold under a thousand dollars. That is the kind a
> small grant or a payment plan often clears. **Twelve** have not met with an advisor
> this term. Below, the cabinet separates the operational actions from the one
> decision that belongs to leadership. Student Success reviews the students with no
> advising contact, and Financial Aid reviews the small-balance cases. Every number
> on this page traces to a row in the data, and I will prove that next."

## Beat 4, 2:20 to 3:10 · Open one claim

**On screen.** The presenter opens the evidence drawer on the 4.8 % claim. The drawer
shows the source fields, the formula, and the rows behind the number.

**Clicked.** Click the −4.8 % figure in the executive summary section. The evidence
drawer slides open showing the formula `119 / 125 − 1 = −4.8 %`, the source fields
(`enrollment.registration_status`, `enrollment.registration_date`,
`comparison.prior_year_equivalent_date`, `profile.continuing`), and the row-ID lists
behind 119 and 125. Scroll the row list briefly.

**Said.**
> "Pick any number. I will take the headline. Here is the formula, the exact fields it
> read, and the one hundred nineteen students behind the numerator and the one hundred
> twenty-five behind the denominator. Anyone can count these by hand from the raw
> data, and `data/VERIFY.md` lists them. The AI explains the numbers. It is never allowed to
> invent one. A test extracts every numeral in this briefing and fails the build if it
> is not in the data."

## Beat 5, 3:10 to 3:40 · The human decides

**On screen.** The decision panel. The leadership decision, whether to authorize a
focused emergency-aid eligibility review for students below a defined balance
threshold, stated separately from the operational actions, with an Approve button.

**Clicked.** The presenter clicks **Approve**. A confirmation appears. One simulated
follow-up task to Financial Aid created, and a `decision.approved` event lands in the
audit log. Nothing is sent anywhere.

**Said.**
> "The cabinet advises. The president decides. Approving authorizes the eligibility
> review, not an aid decision, which stays out of scope. It sends the review to
> Financial Aid as a follow-up task, simulated, because nothing here ever emails a
> student or touches a record. The approval itself becomes part of the record."

## Beat 6, 3:40 to 4:00 · The audit log and the refusal

**On screen.** The audit log view. Reading the audit log belongs to the executive
role, and the president signed in with that role in Beat 1. This walkthrough runs
under the same account. The full event list, `question.asked`,
`task.assigned`, `data.granted`, `finding.produced`, `briefing.produced`,
`decision.approved`, `task.created`, and two `data.refused` events. First, the
Enrollment Analyst's task requested `hold.amount` and was denied before any model call.
Second, the out-of-scope question.

**Clicked.** The presenter filters the audit log to `data.refused` and clicks the
first refusal so the denied field list is visible. Then the presenter clicks into the
second question field, types *"Which students are in counseling?"*, and submits it.
The Chief of Staff refuses with a sentence, and a second `data.refused` event
appears.

**Said.**
> "Finally, the part I built first. One of the analysts asked for a field outside its
> role, hold amounts, and was refused before any model was called. And when I ask
> something outside the approved use case, which students are in counseling, the
> cabinet refuses in plain words, and the refusal is recorded. The counseling fields
> exist in this fictional data precisely so that refusal is real. Governed AI is not a
> promise. It is the architecture here. Thank you."

---

## Timing discipline

| Beat | Target | Hard limit |
|---|---|---|
| 1 | 0:00 to 0:30 | 0:30 |
| 2 | 0:30 to 1:10 | 1:15 |
| 3 | 1:10 to 2:20 | 2:25 |
| 4 | 2:20 to 3:10 | 3:15 |
| 5 | 3:10 to 3:40 | 3:45 |
| 6 | 3:40 to 4:00 | 4:00 |

The sign-in adds no seconds. It rides inside Beat 1's window, typed while the opening
line is spoken, so the pauses, the table above, and the four-minute total stand.

If a beat runs long, the recovery is always the same. Skip the scroll, keep the
sentence, never drop Beat 6. The refusal is the thesis. I timed the six beats end
to end at 204.9 seconds on the replay path, and every beat sat inside its window.

## Failure fallbacks

- **Model unavailable.** The UI shows its "model unavailable" state with retry. The
  presenter restarts the API in replay mode (`make stop && make api REPLAY=1`) and
  says, *"and this is why the demo does not depend on the network."*
- **UI down entirely.** The offline deck (`docs/backup-demo.html`) opens on the
  second device with no network. It steps through the six beats with real
  screenshots and this script's spoken lines, word for word. After the Day-10
  rehearsal, the backup video recorded from the replay path joins it on the second
  device.
- **Numbers questioned.** I open `data/VERIFY.md`, where the row IDs behind 119, 125,
  42, 18, 12 are listed for hand counting.
