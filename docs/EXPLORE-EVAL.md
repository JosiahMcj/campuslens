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
| **Model first, now** | **56 (100%)** | **0.9 s** | **1.6 s** | **21 (95%)** | **0.9 s** | **1.2 s** |

A second model-first run on the final code gave the same scores: 56 of 56 (median
1.1 s, 90th percentile 1.6 s) and 21 of 22 held-out (median 1.1 s, 90th percentile
1.4 s), with the same held-out miss.

- **Rules first, before**: every question the rules could not map went to the model
  with the full catalog and timed out at 55 s (16 of 56 questions, 2 of 22), so the
  model never contributed an answer.
- **Model first, now**: the model's plan was used for 54 of 56 main questions and 19 of
  22 held-out ones. Two held-out plans were rejected (a parameter name the analysis
  does not have) and the rules answered both correctly. The slowest main question took
  19.7 s ("how are the engineering students doing grade wise", where the model wrote a
  long plan), still inside the 20 s budget. The first question after a restart took
  7.1 s while the model read the catalog.
- **With the model also rewording the answer** (`CABINET_EXPLORE_WRITER` unset), the
  first 20 main questions were 20 of 20 correct, median 1.5 s, 90th percentile 3.5 s.
  The reworded sentences sometimes drop the scope ("In Spring 2026, there were 124
  students." for Computer Science), so we keep the template writer for live demos.

The owner's two questions now both answer "124 students were enrolled in Computer
Science in Spring 2026." in about a second (7 s for the very first question after a
restart).

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
| 90% or more correct | 100% main, 95% held-out |
| Median under 12 s | 0.9 s (7.1 s for the first question after a restart) |
| 90th percentile under 20 s | 1.6 s main, 1.2 s held-out |

## Privacy

The model planner receives the question with any typed id or long number replaced and
the compact catalog. The catalog has no instructor, course, student or row, for every
role, so instructor names never reach the model for any role (before, executives' and
admins' prompts listed every instructor). An instructor named in a question is resolved
by code, and instructor-level results stay limited to the executive and admin roles.
The reasoning sentence is discarded and never shown, logged or recorded.

## Not done

- **One general-analysis query is slow**: the D, F or withdrawal rate by section modality
  takes about 22 s to compute on the full-scale data, on every run. The planner is not
  involved; the query needs its own work.
- **The model writer drops scope** (see above). We left the writer unchanged.
- The model is not deterministic. The main set scored 50, 51 and 54 of 56 across our
  earlier prompt revisions and 56 of 56 in both runs of the final one; the held-out set
  scored 21 of 22 in both runs. Another run could differ by a question or two, and the
  rules catch most of what the model gets wrong.

## Re-running

With a server running and a user who may use Explore:

```sh
PYTHONPATH=$PWD/backend/src CABINET_SCHOOL_DB=<the server's school.db> \
  .venv/bin/python -m cabinet.explore.evalset --url http://127.0.0.1:<port> \
  --email <user> --password-file <file holding the password> \
  --label model-first --set main --out results.jsonl
```

`--set held-out` runs the held-out set, and `--only 0 1 2` runs chosen questions.
Keep runs one at a time: a local model serves one question at a time.
