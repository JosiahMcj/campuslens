# Explore planner evaluation

How well each planner order maps a person's question to the right analyses, and how
long the person waits. Measured 2026-10-07 on the demonstration data (full scale,
2,133 students enrolled in Spring 2026) with a local 14-billion-parameter open model
running on the same machine, hidden reasoning turned off.

## Why we measured

Two questions asked live on the demonstration site went wrong:

- "how many cs students are enrolled" was mapped by the rules to enrollment by term,
  and the answer read "In Spring 2026, withheld (fewer than 10 students) students were
  enrolled, against 65 in Fall 2020." The person meant current Computer Science majors,
  and the sentence was broken. It took 41 s.
- "no how many computer science major students are there" was not mapped by the rules.
  The model planner then read the full catalog (every major, college, subject, course
  title, term and instructor, about 17,000 tokens), ran past the 55 s budget, and the
  person got "can't answer".

## What changed

- **A compact catalog for the model.** The planner now reads under 3,000 tokens: each
  analysis with a one-line purpose and its parameters' choices or value types, the
  measures and groupings of the general analysis, the 40 majors and 6 colleges, the term
  range and the current term, short synonyms, the planning rules and six worked
  examples (none of them in the evaluation sets). It lists no course, subject,
  instructor or student. The model writes courses, terms and instructors as free text,
  and code resolves them to catalog values before the plan is validated.
- **The catalog first, the question last.** The catalog is the system message and the
  question is the user message, so the model server keeps the catalog processed between
  questions: about 7 s for the first question after a restart, about 1 s after that.
- **A short reasoning sentence.** The model writes one sentence about what the person
  wants, then the plan. The sentence is discarded; the trace shows "Understood: ..."
  built from the validated plan instead ("Computer Science students enrolled in Spring
  2026, the current term").
- **Model first, rules as the safety net.** With a live model the default is now
  `model-first`. A timeout (20 s, `CABINET_EXPLORE_PLANNER_TIMEOUT`), invalid JSON, a
  value that matches nothing in the catalog, or an invalid plan sends the question to
  the rule planner. `rules-first` and `rules-only` remain.
- **Headcount rules.** "How many X students", "total enrollment" and "what's our
  biggest major" are a count in the current term (the latest fall or spring), filtered
  or grouped; enrollment by term is kept for questions about every term or a trend. A
  conversational lead-in ("no, ...", "actually, ...") is dropped before the rules read
  the question.
- **Grammar for withheld counts.** A withheld count reads "fewer than 10", never
  "withheld (fewer than 10 students) students". Enrollment by term names a withheld
  latest term as withheld and reports the latest term it can show, because a term's row
  is withheld when any part of it is small, so its total may be large.

## The evaluation sets

`backend/src/cabinet/explore/evalset.py` holds both sets and the runner.

- **Main set, 56 questions**: the owner's two live questions and five more headcount
  questions the owner listed, casual and misspelled versions of every analysis ("hw many
  psych majors r there", "whats the dfw rate in organic chem 1", "where are we losing
  the most students"), chained follow-ups, and two questions no analysis answers. We
  tuned the prompt and the rules against this set, so its scores are optimistic.
- **Held-out set, 22 questions**: written after the prompt was final. Nothing was
  tuned on it.

Each question lists the acceptable plans (analysis ids and the parameter values that
must be set). A plan is scored from the reader's "How this was answered" lines in the
response: every expected parameter must appear with the expected value, and any other
line may only be a display setting (order, rows shown, minimum sizes) or the current
term written out. A vague question lists every reasonable plan; for example "how are the
engineering students doing grade wise" accepts GPA for the College of Engineering and
Computing as one figure, by major, or by major with the D, F or withdrawal rate beside
it.

Every run went through `POST /explore` on a throwaway server (a copy of the
demonstration database, one question at a time), with the template writer
(`CABINET_EXPLORE_WRITER=template`) unless noted. Time is the whole request.

## Results

| Planner order | Main set (56) | Median | 90th pct | Held-out (22) | Median | 90th pct |
|---|---|---|---|---|---|---|
| Rules only, before | 40 (71%) | 0.0 s | 0.1 s | 18 (82%) | 0.1 s | 0.2 s |
| Rules first, before | 40 (71%) | 0.1 s | 55.0 s | 18 (82%) | 0.1 s | 21.5 s |
| Rules only, now | 52 (93%) | 0.1 s | 0.2 s | 20 (91%) | 0.1 s | 0.2 s |
| Model first, first build | 56 (100%) | 0.9 s | 1.6 s | 21 (95%) | 0.9 s | 1.2 s |
| **Model first, after review** | **55 (98%)** | **1.0 s** | **1.6 s** | **21 (95%)** | **0.9 s** | **1.3 s** |

The first build was run twice with the same scores (the second: 56 of 56, median 1.1 s;
21 of 22, median 1.1 s). The "after review" row is the final code, after the fixes in
"After the independent review" below; its one main-set miss is described there.

- **Rules first, before**: every question the rules could not map went to the model
  with the full catalog and timed out at 55 s (16 of 56 questions, 2 of 22), so the
  model never contributed an answer.
- **Model first, now**: the model's plan was used for 54 of 56 main questions and 19 of
  22 held-out ones. Two held-out plans were rejected (a parameter name the analysis
  does not have) and the rules answered both correctly. The slowest main question took
  19.7 s ("how are the engineering students doing grade wise", where the model wrote a
  long plan), still inside the 20 s budget. The first question after a restart took
  7.1 s while the model read the catalog (7.2 s after the review).
- **With the model also rewording the answer** (`CABINET_EXPLORE_WRITER` unset), the
  first 20 main questions were 20 of 20 correct, median 1.5 s, 90th percentile 3.5 s.
  The reworded sentences sometimes drop the scope ("In Spring 2026, there were 124
  students." for Computer Science), so we keep the template writer for live demos.

The owner's two questions now both answer "124 students were enrolled in Computer
Science in Spring 2026." in about a second (7 s for the very first question after a
restart).

### After the independent review

An independent review of the first build found no privacy or validation break, but
several ways to give a wrong answer without saying so. We fixed each and added a test
for each (`backend/tests/test_explore_model_planner.py`):

1. **A repeated table that a later step reads from is kept.** The first build merged
   "GPA by major, lowest first" and "GPA by major, highest first" into one step and
   pointed a later "hardest course for that major" at the lowest-GPA major. Now two
   steps that differ only in order are merged only when no later step reads from the
   dropped one.
2. **Close names no longer match different things.** "Chemical Engineering" matched
   Mechanical Engineering, "Biochemistry" matched Chemistry, "Calculus IV" matched
   Calculus I, "Organic Chemistry III" matched II, and the rules read "computer
   engineering" as Computer Science. A close spelling now has to match word for word
   (each word a typo of the same word) with the same numbers, and the
   computer-engineering synonym is gone. Such a value stays unresolved and the rules
   answer.
3. **"Fewer than 10" only for a cell that is itself under 10.** A count withheld to
   protect a neighbouring count may be large. The analyses now mark which withheld
   counts are themselves small (continuing registration, graduates by year, credit
   hours by term); any other withheld count reads "a withheld number of".
4. **"Understood" names the terms the analysis really reads.** A headcount over "Fall
   2023 to Fall 2025" counts Fall 2025 only, and the dropout rate ignores named terms;
   the line now says "in Fall 2025" and "over all the records", from the same function
   the analysis uses (`general.term_window`).
5. **Two terms, or "last year", count each term.** "Fall 2024 vs Fall 2025" answered
   Fall 2024 only and "CS students enrolled last year" answered the current term. The
   rules now plan one count per named term (last year is its fall and spring terms).
6. **"What is our biggest college?"** now ranks colleges, not the campus total.
7. **An odd plan shape never skips the fallback.** A reference whose step was not a
   whole number raised an error past the rules; it is now a rejected plan.
8. **A course title several subjects share** ("Senior Design I") no longer resolves to
   the first one; it stays unresolved.
9. **The model's explicit empty plan means "not answerable".** The rules are no longer
   asked to stretch an analysis onto a question the model found nothing for.
10. **One deadline for the planner call.** A retry after a connection error gets only
    the time left of the 20 s, and none when under a second would remain.
11. **Conversational lead-ins are dropped only before a comma or a question word**, so
    "No students on probation in nursing?" keeps its "No".

The reviewer's probe scripts showed each of these before the fixes and none after.

The one main-set miss after the review: "where are we losing the most students" was
planned as headcount growth by major (which majors shrank) rather than the dropout
rate by major. Both are reasonable readings; the set expects the dropout rate.

### The held-out miss

"online vs in person dfw rates": the model chose the withdrawal rate by modality, a
related but different measure; the D, F or withdrawal rate by section modality was
expected. The rules map it correctly.

### Native hidden reasoning

We also ran the eight hardest main questions with the model's own hidden reasoning
turned on instead of the reasoning sentence: 6 of 8 correct, median 8.2 s, slowest
14.4 s. The reasoning sentence with hidden reasoning off was faster and at least as
accurate, so the live setting stays at `CABINET_LLM_REASONING_EFFORT=none`.

## Targets

| Target | Result |
|---|---|
| 90% or more correct | 98% main, 95% held-out |
| Median under 12 s | 1.0 s (7.2 s for the first question after a restart) |
| 90th percentile under 20 s | 1.6 s main, 1.3 s held-out |

## Privacy

The model planner receives the question with any typed id or long number replaced and
the compact catalog. The catalog has no instructor, course, student or row, for every
role, so instructor names never reach the model for any role (before, executives' and
admins' prompts listed every instructor). An instructor named in a question is resolved
by code, and instructor-level results stay limited to the executive and admin roles.
The reasoning sentence is discarded and never shown, logged or recorded.

## Not done

- ~~**One general-analysis query is slow**~~: fixed on 2026-10-07, see "Rescaled to a
  medium-to-large university" below.
- **The model writer drops scope** (see above). We left the writer unchanged.
- The model is not deterministic. The main set scored 50, 51 and 54 of 56 across our
  earlier prompt revisions, 56 of 56 twice on the first build and 55 of 56 on the
  final code; the held-out set scored 21 of 22 in all three runs. Another run could
  differ by a question or two, and the rules catch most of what the model gets wrong.
- **A headcount grouped by term** ends its sentence with an "overall" figure across
  all the window's terms, which is easy to misread as a term's count. The rules now
  ask one count per term instead, but a model plan grouped by term still gets that
  sentence; we left the general analysis's template unchanged.

## Rescaled to a medium-to-large university (2026-10-07)

The results above were measured on the earlier, smaller school (6,225 students, about
2,100 enrolled a term, 40 majors in 6 colleges). The owner asked for data at the standard
of a medium-to-large university, so `data/school/` now builds about 16,000 students a fall
(15,982 in Fall 2025), 60 majors in 7 colleges (the new College of Social and Behavioral
Sciences holds Psychology, Sociology, Criminal Justice, Political Science and three new
majors), 900 instructors, and about 920,000 graded registrations. The compact catalog grew
with the majors and is still under 12,000 characters (under 3,000 tokens).

We timed the compute alone (rule planner, executor, no model, no HTTP) for every
question in both sets, for every acceptable plan in both sets that has no carried value,
and for every measure of the general analysis by every grouping (587 runs), on the
full-scale database:

| | Smaller school, before | Larger school (about 7 times the rows), after |
|---|---|---|
| D, F or withdrawal rate by section modality | 20.5 s | 1.5 s |
| Slowest of the 587 runs | 22.0 s (withdrawal rate by entry cohort) | 1.9 s ("how are the engineering students doing grade wise", two steps) |
| Every registration-level measure (D, F or W rate, withdrawal rate) | about 20 s each | 1.5 to 1.9 s |

Two changes did it, in `backend/src/cabinet/explore/general.py`:

1. **The registration row set joins each student's term record on the registration's own
   term.** It joined on the section's term, and SQLite then started from the term records
   and probed every section of the term for each student (17 million probes on the smaller
   school, 82 s a query at four times its size). Joining on `section_registrations.term_code`
   (always the section's term; `check.py` verifies it) lets SQLite follow the student's
   registrations through their index: the same rows, the same numbers, under a second.
2. **One scan per request.** Small-cell suppression checks every partition a cell belongs
   to, so one question ran the same row set three to five times. The analysis now reads
   per-student partial sums once per term window (every measure is a sum or a count) and
   builds each partition from them in memory, with each student counted once as before.

Over the 156 eval-set runs alone (rule plans and acceptable plans) the compute median is
0.10 s, the 90th percentile 0.47 s, the slowest 1.9 s. The rule planner still scores 52 of
56 on the main set and 20 of 22 on the held-out set against the larger school (scored
in-process from the same "How this was answered" lines), the same as on the smaller one.

No index was added; the database is the generator's own. The planner times above (a
model call of about 1 s) are unchanged by the larger catalog in kind; we did not re-run
the model-first evaluation against the larger school.

## Forward questions and redirects (2026-10-07)

Explore now answers forward-looking questions from the records and redirects student-level
questions instead of refusing them (docs/EXPLORE.md, "Questions about the future"). A third
set, `--set forward` (12 questions), covers it: the owner's "how many students have holds
and will drop", "how many students have holds", four forecast phrasings, a holds rate
question, three off-topic requests and a student id (the last four must not be answered).
A student question answered with totals for students like that is scored as protected.

Measured first on the smaller school (`generate.py` before the rescale, 2,133 students in
Spring 2026), one question at a time, template writer:

| Planner order | Main (56) | Held-out (22) | Forward (12) |
|---|---|---|---|
| Rules only | 52 (93%), median 0.0 s | 20 (91%) | 12 (100%) |
| Model first (qwen3:14b) | 55 (98%), median 1.0 s, p90 2.2 s | 21 (95%) | 12 (100%), median 0.1 s |

The main and held-out scores are the same as before the change (main's one model miss was
"how are the engineering students doing grade wise", planned as four steps; the model is
not deterministic). Before forward questions went to the rules first, model first scored
8 of 12 on the forward set: it planned the hold rate by term for "have holds and will
drop", fall-only enrollment for "will enrollment fall", and probation and suspension for
"at-risk students in nursing". The rules now plan forward questions first, and the model
plans only what they cannot map; a model plan of the hold rate split by hold status is
rejected (always 100%).

## Re-running

With a server running and a user who may use Explore:

```sh
PYTHONPATH=$PWD/backend/src CABINET_SCHOOL_DB=<the server's school.db> \
  .venv/bin/python -m cabinet.explore.evalset --url http://127.0.0.1:<port> \
  --email <user> --password-file <file holding the password> \
  --label model-first --set main --out results.jsonl
```

`--set held-out` runs the held-out set, `--set forward` the forward set, and `--only 0 1 2` runs chosen questions.
Keep runs one at a time: a local model serves one question at a time.
