<p align="center"><img src="brand/campuslens-logo.png" alt="CampusLens" width="240"></p>

# CampusLens hackathon submission

## One-sentence pitch

CampusLens gives university leaders straight answers about their students, computed from the
student records and traceable to the source, by a team of permission-limited AI employees
that never see a student's record and never act without a person's approval.

## The problem

Universities hold a great deal of student data, yet one leadership question ("Why is spring
registration down?", "Which majors lose the most students?") still takes several offices,
several reports and several days. The data sits in the student information system, the
people who can query it are few, and handing student records to an AI tool is a privacy risk
most institutions rightly refuse to take.

## What we built

CampusLens answers in two ways, and both keep the same rules.

**The weekly briefing.** A president asks an approved question such as "What should I know
about spring registration?". An AI Chief of Staff assigns narrow tasks to an Enrollment
Analyst and a Student Success Analyst, and each sees only the fields its job allows. The
answer is a seven-section briefing that separates staff actions, which offices can take now,
from the leadership decision, which only a person approves.

**Ask anything.** Any other question about the university ("Which major has the lowest GPA,
what is its hardest class, and who has taught it?", "What majors have the highest dropout
rate?") is answered from reviewed analyses computed in code over the institution's records.
While it works, CampusLens shows what it is doing step by step. Each number in the answer
links to the exact table cell it came from, and "How this was answered" shows every step.

**Work that gets done.** Staff actions become a tracked worklist with an owner, a status, a
due date, notes and a history, and a named staff member can send an action to the
responsible office's mailbox. The approved leadership decision is dispatched the same way.
Nothing is ever sent on its own.

## The guardrails

- **The model never sees a student.** Every number is computed in code. The model only
  explains numbers it is given, and a validator rejects any sentence whose number does not
  match a computed value.
- **Small groups stay hidden.** Any group under 10 students is withheld, including groups
  that could otherwise be worked out by subtracting one answer from another.
- **Some questions are refused before any model call.** Counseling records, questions about
  a single student, and predictions about individuals are refused, and each refusal is
  recorded.
- **Each role sees only its job.** Administrators, executives, staff, reviewers and
  Financial Aid each see different fields, and student-level records reach only the roles
  that need them.
- **Everything is on the record.** Every grant, refusal, approval and send is written to a
  hash-chained audit log that anyone authorized can read in plain words.
- **The model runs where the institution chooses.** CampusLens works with any standard
  chat-completions endpoint over https, or a model on the institution's own machine.
- **Students are people who may need support,** never risk scores.

## Integrations

- **Ellucian.** An Ethos connector imports the fields CampusLens needs through an exact allow
  list, after the data steward's written authorization (`docs/ELLUCIAN.md`,
  `docs/DATA-ACCESS.md`).
- **Office mailboxes.** Approved sends go to each office's mailbox. By default they are
  written to an outbox on the server, and real delivery is switched on only when the
  institution configures its mail server.
- **Uploads.** An administrator can load a dataset file, and counseling free text is
  stripped at upload.

## How a judge runs it

Setup needs Python 3.12 and Node 22. Run `make setup` once, then `make school-data` to build
the demonstration university, then `make api REPLAY=1` and `make ui`, and open
`http://127.0.0.1:5200`. Replay mode serves recorded model answers, so the demo needs no key
and no network. `RUNBOOK.md` covers production serving and a live model endpoint.

## The data

All data is fictional. Demonstration University is a medium-to-large university with about
16,000 students enrolled each fall: 38,374 students and 923,229 graded registrations from
Fall 2020 to Spring 2026, generated from a fixed seed. The briefing's
four headline numbers are hand-countable from `data/fixture.json` (`data/VERIFY.md`), and
`data/school/VERIFY.md` explains how to recount the university's figures.

## Team

Josiah McJunkin, Sharon Li, Dylan Poirier and Obinna Amadi.

## Currently out of scope

CampusLens makes no changes to student records, sends nothing without a person, makes no
individual predictions or financial-aid eligibility decisions, and reads no counseling or
spiritual-care records. Real student data enters only after the Registrar's written
authorization.
