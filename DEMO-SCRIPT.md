# Demo Script, four minutes

The script is word for word, with the timings as targets. We tune the pauses in
rehearsal, never the numbers. Every number on screen is computed in code from fictional
data, never by the model. The ones we say out loud are **−4.8 %, 42, 18, 12** from the
briefing, **2.66** and **39.6 %** from the lowest-GPA question, and **19.6 %** against
**12.1 %** from the dropout question.

The person demoing is "the presenter", and the on-screen persona is "the president",
who signs in as the executive account created in preflight.

---

## Starting the app

One-time setup, from the repo root (creates `.venv`, installs backend and UI deps).

```bash
make setup
make school-data   # builds Demonstration University for "ask anything" (about a minute)
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

We open `http://127.0.0.1:5200`. It opens on the sign-in screen, and the demo signs in
as `president@demo.test`. When the demo is over, we run this.

```bash
make stop   # stops both via pid files in var/
```

**Replay mode.** `make api REPLAY=1` starts the API with `CABINET_PROVIDER=replay`,
serving recorded responses from `var/replay/` or the committed golden run in
`data/golden/`, so the demo runs identically with the network down. We recorded the
golden run once with `make record-golden`, all three roles, and committed it in
`data/golden/` (the live model must be configured, see RUNBOOK.md). Whether the
model runs live or from replay, the numbers are the fixture's. The model only
explains them.

**Preflight (60 seconds before).** The two demo accounts exist (above), and the room
gets a fresh record. We run `make stop`, move `var/` aside with
`mv var var.before-demo-<time>`, and delete nothing in it. Then `make bootstrap-admin`
and `make user` again for the fresh database, then `make api REPLAY=1` and `make ui`
up (we check `var/api.log` and `var/ui.log` for errors). The audit log is empty, and it
stays empty with the browser on the sign-in screen, because the page runs nothing
before the question is asked. The offline deck (`docs/backup-demo.html`) sits open on
the second device.

---

## Beat 1, 0:00 to 0:25 · The problem

*Sign in as `president@demo.test` while speaking.*

> "Every university has the data. Getting one straight answer out of it still takes
> several offices and several days, and nobody wants to hand student records to an AI.
> This is CampusLens."

## Beat 2, 0:25 to 1:05 · The AI employees go to work

*Click the approved question "What should I know about spring registration?".*

> "I ask one question. A Chief of Staff hands narrow tasks to an Enrollment Analyst and
> a Student Success Analyst. Each one sees only the fields its job allows, and none of
> them ever sees a student's record."

*While the analysts work, point at the live task list. Then open **AI employees and data
access** from the sidebar for two seconds, and press **Back**.*

## Beat 3, 1:05 to 1:45 · The briefing, and one number opened

> "Spring registration is down 4.8 percent. Forty-two continuing students haven't
> registered. Eighteen of them have a small balance under a thousand dollars, and twelve
> haven't seen an advisor."

*Click **42**. The evidence page opens.*

> "Every number is computed in code and links to how it was worked out and the records
> behind it. The model only explains numbers it is given, and a checker rejects any
> sentence whose number doesn't match."

> (Optional) "The support indicators work the same way. There are four named rules,
> and for each pseudonymous student we show exactly which rules fired and why."

*Press **Back**.*

## Beat 4, 1:45 to 2:35 · Ask anything

*Type: "Which major has the lowest GPA, and in that major what is the hardest class
historically and who teaches it?"*

> "Now any question about the university. Watch it think."

*Let the trace run: understood, plan, reading the student records, each step, checking
every number. It folds into "Thought for".*

> "Mechanical Engineering, 2.66. Its hardest required course is Thermodynamics I, at a
> 39.6 percent D, F or withdrawal rate, and here is who has taught it most."

*Click **39.6 %**: the table opens at that exact cell. Then type "what majors have the
highest dropout rate".*

> "Mechanical Engineering again, 19.6 percent against 12.1 percent overall, and it tells
> you exactly what it counted as a dropout."

## Beat 5, 2:35 to 3:15 · People act, and a person decides

*Open **Staff actions**.*

> "The briefing turns into work. Each office gets its action, an owner, a due date and
> notes, and a staff member sends it to the office's mailbox when they're ready. Nothing
> goes out on its own."

*Set the Bursar action to In progress. Open **Decision** and press **Approve**.*

> "The one leadership decision is mine. I approve it, and CampusLens prepares the
> message to Financial Aid."

## Beat 6, 3:15 to 3:45 · The refusal

*Type: "Which students have met with a counselor this term?"*

> "Some questions it will never answer: counseling records, one student's file,
> or a guess about one person's future. It refuses before any model is called, and it writes that
> down."

*Open **Audit log**, filter to refusals, point at the entry.*

## Beat 7, 3:45 to 4:00 · Close

> "Answers your leaders can trust: computed, traceable, private, and a person always
> decides. That's CampusLens."

---

## Timing discipline

| Beat | Target | Hard limit |
|---|---|---|
| 1 | 0:00 to 0:25 | 0:30 |
| 2 | 0:25 to 1:05 | 1:10 |
| 3 | 1:05 to 1:45 | 1:50 |
| 4 | 1:45 to 2:35 | 2:40 |
| 5 | 2:35 to 3:15 | 3:20 |
| 6 | 3:15 to 3:45 | 3:50 |
| 7 | 3:45 to 4:00 | 4:00 |

If a beat runs long, skip the second Explore question first, then the AI employees
page. Never drop Beat 6: the refusal is the thesis. We time the whole run in each
rehearsal on the replay path and write the total here.

## Failure fallbacks

- **Model unavailable.** The UI shows its "model unavailable" state with retry. The
  presenter restarts the API in replay mode (`make stop && make api REPLAY=1`) and
  says, *"and this is why the demo does not depend on the network."*
- **UI down entirely.** The demo video plays from the second device with no network.
- **Numbers questioned.** We open `data/VERIFY.md`, where the row IDs behind 119, 125,
  42, 18, 12 are listed for hand counting, and `data/school/VERIFY.md` for the
  university's figures.
