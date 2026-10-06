# Explore: specific questions over Demonstration University

Explore answers specific questions about the synthetic university in `data/school/`, like a
real school's institutional research office would. Ask "Which major has the lowest GPA? In
that major, what is historically the hardest class, and which instructor has historically
taught it?" and the Cabinet answers in a few plain sentences, shows the three tables the
answer came from, and shows how each table was computed.

Four rules hold for every answer.

1. **Code computes every number.** Every number in an answer is a cell of a table that
   reviewed code computed from the school database. The model never computes a number.
2. **The model never sees a student row.** When a model is configured, it sees the list of
   approved analyses (to plan) and the finished aggregate tables (to reword the answer).
   Nothing else reaches it.
3. **Every answer shows how it was answered.** The response carries each step's analysis,
   its parameters in plain words, the fields it read, and its table. Each sentence links
   every number it uses to the table cell it came from.
4. **Refusals come first.** A question about counseling or spiritual care, about one
   student, or asking to predict what a student will do is refused in code before any
   planning or model call, and the refusal is recorded.

## Set up

```sh
make setup-python
make school-data      # writes var/school/school.db (about 3 s), then checks it
make explore-check    # the full-scale check (below), then prints the owner's example
```

Without the database every Explore route answers 503 with "The demonstration university
data is not installed. Run make school-data." and nothing else. `CABINET_SCHOOL_DB` points
Explore at another copy (the tests use a reduced-scale copy in a temporary directory). The
connection is read-only.

## What can be asked

Explore runs only the sixteen reviewed analyses in
`backend/src/cabinet/explore/catalog.py`. `GET /explore/catalog` lists them with twelve
example questions.

| Analysis | What it computes | Parameters |
|---|---|---|
| `gpa_by_major` | Average cumulative GPA per major, ranked | order, college, major, minimum students (20), rows |
| `gpa_by_college` | Average cumulative GPA per college, ranked | order |
| `dfw_by_course` | DFW rate per course, ranked | required by major, subject, level, term range, minimum sections (8) and terms (4), order, rows |
| `course_dfw_trend` | One course's DFW rate per term and over all terms | course |
| `course_instructors` | Each instructor of record for a course, with sections, terms, DFW rate | course |
| `instructor_history` | The courses one instructor taught | instructor, rows |
| `equity_gap` | DFW rate by student group in a course or a major, with the gap in points | course or major, group (first-generation, Pell, residency, entry cohort, entry type) |
| `headcount_growth` | Each major's headcount in two terms and the growth, ranked | from and to terms, major, college, order, minimum starting headcount (40) |
| `enrollment_by_term` | Students enrolled per term, new and continuing | major, college, season |
| `continuing_registration_change` | Continuing students registered against the same term a year earlier | term (latest spring) |
| `withdrawal_by_modality` | W rate online, in person, and hybrid per term, with the online gap | term, order |
| `standing_by_major` | Probation and suspension rates per major, ranked | term, college, major, order |
| `graduations` | Graduates by major (ranked) or by academic year | major, college, academic year, group by |
| `holds_by_office` | Holds, students affected, and amounts owed per office | active only, term placed, category |
| `advising_coverage` | Share of each major's students with a completed advising appointment | term (latest fall or spring), college, major, order |
| `credit_hours_by_term` | Credit hours attempted and earned per term | major, college, season |

Every parameter takes a value from a list. Majors, colleges, subjects, courses, terms,
academic years, instructors, and hold categories come from the database, and the rest are
fixed lists. Nothing a person types is ever placed into SQL. Typed text only chooses an
analysis and values from these lists.

The definitions are the ones in `data/school/VERIFY.md`. A graded registration is a letter
grade A to F or a W in a letter-graded course, and the DFW rate is D+, D, F, and W over
graded registrations. A student counts once, in the program and cumulative GPA of their
latest term. Rates are rounded exactly as `data/school/check.py` rounds them, so the
planted facts come back to the digit.

Some questions the rule planner maps (the tests check all 39 phrasings in
`RULE_PHRASINGS`):

- Which major has the lowest GPA? In that major, what is historically the hardest class,
  and which instructor has historically taught it?
- Which required courses in Nursing have the highest DFW rate?
- How has the DFW rate in Organic Chemistry I changed by term?
- What is the first-generation equity gap in College Algebra?
- Which major grew fastest from Fall 2020 to Fall 2025?
- How much did continuing spring registration change in Spring 2026?
- Which term had the largest gap between online and in-person withdrawal rates?
- Which offices hold the most active holds?
- What is the hardest class in Chemistry, and who taught it?

A question no analysis answers gets "The Cabinet can't answer that from the approved
analyses yet." with the three example questions closest to it.

## How an answer is computed

1. **Refusal check** (`explore/privacy.py`). Counseling and spiritual care, a student id or
   a numbered student, a request for individual students, and predictions about students
   are refused here. The API records `question.asked` and `data.refused`, and nothing else
   runs.
2. **Plan** (`explore/planner.py`). The question becomes a list of steps, each an analysis
   id with parameters. A later step can take a parameter from an earlier step's top row, so
   the owner's question becomes three steps: the lowest-GPA major, the hardest course that
   major requires, and the instructors of that course. Two planners sit behind one
   interface.
   - The **rule planner** matches keywords, synonyms, and the database's names (majors,
     courses, instructors, terms). Replay and fake modes use it, and so do the tests.
   - The **model planner** runs only with a live provider. It receives only the catalog
     (analysis ids, titles, parameter names, and allowed values) and the question, and it
     must answer with a JSON plan. The plan is checked against the catalog: every id,
     parameter, value, and reference. Invalid JSON, a value outside the catalog, a bad
     reference, or an unavailable provider falls back to the rule planner, and the
     response says so in `fallbacks`.
   - Validated model plans are recorded under `var/replay/explore/` when `CABINET_RECORD=1`
     (`CABINET_REPLAY_DIR` overrides the place), keyed by the question and the catalog's
     hash. Replay reads each replay directory's `explore/` folder, including
     `data/golden/explore/` for committed recordings, and a recording is never replaced
     without `CABINET_RECORD=overwrite`.
3. **Execute** (`explore/execute.py`). Each step runs in code against a read-only
   connection. Before a step reads anything the API records `data.granted` with the
   analysis id, the fields it reads, and `aggregate_only: true`.
4. **Write** (`explore/answer.py`). A template builds one to four sentences from the
   tables, and every number in them is a table cell. With a live provider the model may
   reword them. It receives only the tables and must answer `{"sentences": [...]}`. Each
   sentence then passes the cabinet's numeral validator (`analysts.check_numbers_against`):
   every number must be a cell value, rates in percent form, no student id, and no risk
   language. Anything else is discarded and the template answer is shown. `source` says
   which one you see: "Written from computed tables (no model)" or "Written by the Chief of
   Staff from computed tables". A reworded sentence must also keep each number with its
   row: the row named nearest to a number in the sentence must hold that number, so a
   true figure cannot be attached to the wrong major or course.
5. **Record.** `explore.answered` closes the run with the step ids, their row counts, and
   which planner and writer ran. It never carries a value.

The command-line check tool runs the same code offline and writes nothing, not even audit
events:

```sh
.venv/bin/python -m cabinet.explore "Who has taught Organic Chemistry I?"
.venv/bin/python -m cabinet.explore --role staff --json "Who has taught MEEN 3310?"
```

## Privacy rules

- **Aggregates only.** No analysis returns a student id. The executor scans every result
  for an `S-` id and refuses to return one, and the tests check every response with the
  same pattern.
- **Groups under 10 are withheld.** Any statistic over fewer than 10 students reads "fewer
  than 10" and is left out of every ranking, with a note saying how many were withheld.
  Ranked analyses also set a minimum group size of their own (20 students for majors, 8
  sections in 4 terms for courses, 40 students for growth), named in each step's
  parameters.
- **Withheld figures stay withheld.** When exactly one row of a table would be withheld,
  the smallest other row is withheld too, so the hidden figure cannot be worked out by
  subtracting the visible rows from a total shown elsewhere (a course's all-terms row, a
  group's whole). This applies to a course's terms, its instructors, and the student groups
  of an equity gap. Both instructor analyses withhold the same rows. In enrollment by term,
  a withheld new or continuing count withholds the whole row, and a college that holds a
  major under 10 students is withheld from the college ranking.
- **What the threshold counts.** The minimum applies to the number of students a figure
  describes (the group). A count inside a group of 10 or more, such as 7 D, F, or W grades
  among 38 graded registrations, is shown. We do not claim that every combination of
  separate questions is safe from differencing. The rules above close the direct
  subtractions between the tables Explore returns, and the data is synthetic.
- **No counseling data exists here.** The school database has no counseling or
  spiritual-care table. Questions about them (counseling, chaplains, prayer, chapel,
  faith, mental health, and similar words) are refused before planning anyway, so the
  refusal is recorded. Course titles and major names are set aside before that check, so
  "What is the DFW rate in Theories of Counseling?" is answered. Questions that rank or
  list students, name an id in any form, or ask who will do something are refused as
  questions about individuals, and any id or long number a person types is replaced
  before the question is recorded.
- **Instructor rows are for the executive and admin roles.** Staff and reviewers asking
  about a course's instructors get the course as a whole (sections, terms, instructor
  count, DFW rate) with a sentence saying instructor-level results are available to the
  executive and admin roles only, and a `data.refused` event records it. An instructor's
  teaching history is withheld the same way.
- **Instructor names are fictional.** Every instructor in the school database is generated
  and marked fictional, and every answer and table labels the names so.

## Routes and roles

| Route | Roles | Notes |
|---|---|---|
| `POST /explore {question}` | executive, admin, staff, reviewer | CSRF, the ask rate bucket (5 per minute per session and per IP by default), at most 500 characters |
| `GET /explore/catalog` | executive, admin, staff, reviewer | the analyses (title, description) and 12 example questions |

The aid role gets a 403 on both. The response of `POST /explore` is
`{refused, message?, answer: [{text, claims: [{table, row, column}]}], steps: [{analysis_id,
title, params_plain, fields_read, table: {columns, rows}, notes}], source}` plus `planner`
(rule, model, or recorded), `notes` (a named term or college the chosen analysis does not
filter by, and any part of the question no analysis answered), `fallbacks`, and
`suggestions` when the question could not be mapped. If an answer cannot be finished, the
response says so in `message`, the run is recorded with `answered: false`, and nothing is
guessed. Demonstration University is one shared fictional dataset. Users of every
institution query the same data, and each question's audit events land on the asker's own
institution chain.

## How to add an analysis

1. Write the function in `explore/catalog.py`. It takes the read-only connection, the
   resolved parameters, and the vocabulary, and returns a `Result` of rows (dicts keyed by
   column) and notes. Use bound parameters only, apply `_suppressed` to every group with
   fewer than 10 students, and keep withheld groups out of any ranking.
2. Register an `Analysis` in `ANALYSES` with its id, title, description, typed parameters,
   the fields it reads, and its columns (plain labels, a kind such as `count` or `pct`, and
   an `entity` on a column a later step may chain from).
3. Teach the rule planner in `explore/planner.py` at least one phrasing and add it to
   `RULE_PHRASINGS`, then add a template sentence in `explore/answer.py`.
4. Run `make check`. The tests run every analysis, check every response for student ids,
   map every phrasing, and check that every template sentence takes its numbers from table
   cells. If the analysis reproduces a planted fact, add it to the full-scale check in
   `backend/tests/test_explore.py`.
