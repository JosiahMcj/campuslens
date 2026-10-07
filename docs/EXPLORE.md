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
   every number it uses to the table cell it came from. A sentence writes its numbers for
   a reader (a GPA to 2 decimals, "41.8%", a growth of 100% or more as a whole number);
   the table keeps full precision.
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

Explore runs only the seventeen reviewed analyses in
`backend/src/cabinet/explore/catalog.py`. `GET /explore/catalog` lists them with twelve
example questions.

| Analysis | What it computes | Parameters |
|---|---|---|
| `gpa_by_major` | Average cumulative GPA per major, ranked | order, college, major, minimum students (20), rows |
| `gpa_by_college` | Average cumulative GPA per college, ranked | order |
| `dfw_by_course` | D, F or withdrawal (DFW) rate per course, ranked | required by major, subject, level, term range, minimum sections (8) and terms (4), order, rows |
| `course_dfw_trend` | One course's D, F or withdrawal rate per term and over all terms | course |
| `course_instructors` | Each instructor of record for a course, with sections, terms, D, F or withdrawal rate | course |
| `instructor_history` | The courses one instructor taught | instructor, rows |
| `equity_gap` | D, F or withdrawal rate by student group in a course or a major, with the gap in points | course or major, group (first-generation, Pell, residency, entry cohort, entry type) |
| `headcount_growth` | Each major's headcount in two terms and the growth, ranked | from and to terms, major, college, order, minimum starting headcount (40) |
| `enrollment_by_term` | Students enrolled per term, new and continuing | major, college, season |
| `continuing_registration_change` | Continuing students registered against the same term a year earlier | term (latest spring) |
| `withdrawal_by_modality` | W rate online, in person, and hybrid per term, with the online gap | term, order |
| `withdrawal_by_course_modality` | Each course's W rate online against in person (fall and spring), ranked by the online rate | subject, minimum online students (30), order, rows |
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

Some questions the rule planner maps (the tests check all 43 phrasings in
`RULE_PHRASINGS`, and 40 more wordings of the planted questions with other words
and typos, such as "worst", "toughest", "lowest-performing", "who teaches", and
"teh"):

- Which major has the lowest GPA? In that major, what is historically the hardest class,
  and which instructor has historically taught it?
- Which major has the lowest GPA, what is its hardest class, and who has taught it?
- Which required courses in Nursing have the highest DFW rate?
- How has the DFW rate in Organic Chemistry I changed by term?
- What is the first-generation equity gap in College Algebra?
- Which major grew fastest from Fall 2020 to Fall 2025?
- How much did continuing spring registration change in Spring 2026?
- Which term had the largest gap between online and in-person withdrawal rates?
- Which course has the highest withdrawal rate online?
- Which offices hold the most active holds?
- What is the hardest class in Chemistry, and who taught it?

A question no analysis answers gets "The Cabinet can't answer that from the approved
analyses yet." with the three example questions closest to it.

## How an answer is computed

1. **Refusal check** (`explore/privacy.py`). Counseling and spiritual care, a student id or
   a numbered student, a request for individual students, and predictions about students
   are refused here. The API records `question.asked` and `data.refused`, and nothing else
   runs. The screen shows a refusal as a calm note headed "Not something the Cabinet
   answers", not as an error: "Individual counseling and spiritual-care records are never
   disclosed." for counseling (the briefing may still show an authorized aggregate count),
   and a line about totals only for a single student or a prediction.
2. **Plan** (`explore/planner.py`). The question becomes a list of steps, each an analysis
   id with parameters. A later step can take a parameter from an earlier step's top row, so
   the owner's question becomes three steps: the lowest-GPA major, the hardest course that
   major requires, and the instructors of that course. Two planners sit behind one
   interface.
   - The **rule planner** matches keywords, synonyms, and the database's names (majors,
     courses, instructors, terms). It splits a question into its parts at question marks,
     colons, and a new question word after a comma or an "and", so "Which major has the
     lowest GPA, what is its hardest class, and who has taught it?" is three chained
     steps, and it corrects common typos ("teh") first. Replay and fake modes use it,
     and so do the tests.
   - The **model planner** runs only with a live provider, and by default only for a
     question the rules cannot map (`CABINET_EXPLORE_PLANNER`, `rules-first` by default;
     `model-first` asks the model every time and falls back to the rules). We chose
     rules first after measuring a local model: with the full catalog it did not answer
     within the 55 s request budget on any of six questions, twice over, while the rules
     map every planted question. It receives only the catalog (analysis ids, titles,
     parameter names, and allowed values) and the question, and it must answer with a
     JSON plan. The plan is checked against the catalog: every id, parameter, value, and
     reference. Invalid JSON, a value outside the catalog, a bad reference, or an
     unavailable provider is never used, and the response says so in `fallbacks`.
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
   language. A number may also be the one rounded form the template writes for that cell
   (`answer.reader_number`: a GPA to 2 decimals, half up, so 2.623 reads 2.62; a percentage
   of 100 or more as a whole number, so 110.8 reads 111), and never any other rounding: 42%
   does not pass for a cell of 41.8. The screen links the rounded number to its cell the
   same way. Anything else is discarded and the template answer is shown. `source` says
   which one you see: "Calculated directly from the records" or "Written by the Chief of
   Staff from the records". A reworded sentence must also keep each number with its
   row: the row named nearest to a number in the sentence must hold that number, so a
   true figure cannot be attached to the wrong major or course. A rewording may change
   the words, never drop a fact: every figure and every major, course, or name the
   template answer states must also be in it, or the template answer is shown. (On our
   local model the rewording of the owner's question compared majors and courses the
   question did not ask about and named no instructor, so this rule now shows the
   template there.)
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
  count, DFW rate) with one sentence saying instructor results are shown to the executive
  and admin only (the screen says it once), and a `data.refused` event records it. An instructor's
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
`suggestions` when the question could not be mapped. `params_plain` holds only the
parameters a reader cares about, in plain words ("Ranked: lowest first", "Only majors with
at least 20 students"); codes and the number of rows shown stay out of it, and the command
line tool prints the exact record. The screen hides code columns (term codes, major and
college codes, instructor ids) when the table also carries the name, and keeps the row's
name in view while a narrow table scrolls. If an answer cannot be finished, the
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
   an `entity` on a column a later step may chain from). A parameter's `shown` says how a
   reader sees it ("Only courses with at least {} students online"), or `None` to leave it
   out; a code column keeps a name column beside it (`major` with `major_name`) so the
   screen can hide the code.
3. Teach the rule planner in `explore/planner.py` at least one phrasing and add it to
   `RULE_PHRASINGS`, then add a template sentence in `explore/answer.py`.
4. Run `make check`. The tests run every analysis, check every response for student ids,
   map every phrasing, and check that every template sentence takes its numbers from table
   cells. If the analysis reproduces a planted fact, add it to the full-scale check in
   `backend/tests/test_explore.py`.
