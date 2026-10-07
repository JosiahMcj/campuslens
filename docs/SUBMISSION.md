# CampusLens hackathon submission

## One-sentence pitch

CampusLens transforms Ellucian SIS data into an executive briefing. It does so
by coordinating permission-limited AI employees across enrollment and student success,
helping university leaders see what matters, understand why, and direct timely human action.

## Thirty-second pitch

University leaders have extensive student data, but answering one important question can
still require several departments and multiple reports. CampusLens gives each
analytical function a permission-limited AI employee. An AI Chief of Staff coordinates
their work, verifies the evidence, and produces one executive briefing that separates staff
actions from leadership decisions. Our first workflow answers a practical question. What
should the president know about spring registration? The result is faster institutional
understanding while people remain responsible for every consequential decision.

## What is built

We built the working prototype this pitch describes, a Python API that carries all of the
logic with a thin web interface over it. Both run locally and bind to the loopback address
only. We chose a small scope on purpose. One question, one briefing, one dataset.

The dataset is fictional and seeded, holding 185 current-term student records and 135
prior-year records, and every metric is computed deterministically in code. The configured
model endpoint explains the verified numbers and is never permitted to invent one, because
a validator examines every analyst sentence before it renders. Each claim must name a
finding the role received, each numeral must equal a computed value, and anything failing
that examination is refused and recorded rather than shown.

Three AI employees do the work. A Chief of Staff assigns narrow tasks, an Enrollment
Analyst and a Student Success Analyst answer them, and each role sees only the fields its
job allows. Every grant and every refusal lands in an append-only audit log that survives
restarts.

Every claim in the briefing opens to evidence demonstrating the formula, the exact source
fields, and the row identifiers behind the number. Operational actions and the leadership
decision stay visually separate throughout the briefing. Approving the decision records one
simulated follow-up task to Financial Aid. Nothing is sent anywhere.

We also built the failure path, a replay mode that serves recorded responses so the entire
demonstration runs with the network down. We verified the application on the real path
rather than only in tests, covering empty input, an unavailable model endpoint, and a
restart mid-run. Two replay runs came back byte-identical. The full six-beat demonstration
takes 204.9 seconds, comfortably under the four-minute limit.

## How a judge runs it in five minutes

Setup needs Python 3.12 and Node 22, and a single `make setup` from the repo root installs
everything. Two commands start the demo, `make api REPLAY=1` for the API and `make ui` for
the interface, and the page then lives at `http://127.0.0.1:5200`. Ask the approved
question about spring registration, then follow the six beats in order. The first beat asks
the question, the second demonstrates the Chief of Staff dispatching two visibly scoped
tasks, and the third reads through the seven-section briefing. The fourth beat opens the
headline number to its evidence, the fifth approves the leadership decision, and the sixth
filters the audit log to the recorded refusals. Run `make stop` when done. The replay path
needs no key and no network.

## The numbers and where they come from

Four numbers carry the demo, and every one of them is hand-countable from the fixture.
Spring registration is 4.8 percent below the same date last year, a figure computed as 119
divided by 125, minus one. Forty-two continuing students have not registered, and of those,
eighteen hold an unresolved financial balance under $1,000 while twelve have no advising
appointment this term. The file `data/VERIFY.md` lists the row identifiers behind each of
119, 125, 42, 18, and 12, and `data/check_fixture.py` recomputes the same quantities and
fails on any disagreement. A judge can recount each one. The fixture generator uses a
fixed seed and no wall-clock
values, and the as-of date is derived from the data rather than the clock.

## Currently out of scope

We kept a deliberate list of exclusions, and we name them plainly here. The prototype has no
production SIS access and uses no real student data. It makes no automatic changes to
student records, and it performs no automatic email, text message, or case creation. It is
not an unrestricted executive chatbot, and it does no predictive retention modeling or
financial-aid eligibility decisions. It includes no tutor, international-student,
or spiritual-care agents, and it is not a complete mobile application.

## Format and deadline

The submission format and the deadline are still to be confirmed on the hackathon page. We
will complete this section when the page states them.
