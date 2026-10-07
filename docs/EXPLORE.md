# Explore: specific questions over Demonstration University

Explore answers specific questions about the synthetic university in `data/school/`, like a
real school's institutional research office would. Ask "Which major has the lowest GPA? In
that major, what is historically the hardest class, and which instructor has historically
taught it?" and CampusLens answers in a few plain sentences, shows the three tables the
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
4. **Protected data is checked first, then answered around.** A question about
   counseling or spiritual care, about one student, or asking to predict what a student
   will do is caught in code before any planning or model call, and recorded as
   `data.refused`. The protected data is never answered, but the reply is not a dead end:
   totals for students like that, or related questions. A forward-looking question about
   a group ("how many will drop out", "will enrollment fall") is answered with the closest
   historical totals. Only off-topic requests ("code me a website") get the "Not something
   CampusLens answers" card.

## Questions about the future

CampusLens does not forecast. A question about what a group will do ("how many students
have holds and will drop", "what % will graduate", "will enrollment fall next year",
"at-risk students in nursing") is marked forward-looking (`is_forward_looking`) and
answered with the closest historical totals, led by one plain line: "CampusLens doesn't
forecast, so here's what the records show." It never produces a risk score, a list, or
anything per student.

- The rule planner reads `historical_form` of the question: "will drop out" or "at risk"
  is the dropout rate (with the stop-out rate beside it), "will graduate" the
  graduation rate, "will come back" retention, "will enrollment fall" enrollment by term.
  A "how many" question also counts the group now ("356 students with a hold were
  enrolled in Spring 2026").
- Forward questions go to the rules first even in the `model-first` order (rules 12 of
  12 on the forward evaluation set, the model 8 of 12; docs/EXPLORE-EVAL.md). A question
  the rules cannot map goes to the model with a short instruction after the question
  (`FORWARD_HINT`, in the user message; the cached catalog is unchanged).
- **Holds.** The general analysis has a `hold` grouping and filter: a hold placed on the
  student's account in the term counted (the hold rate's definition; for a figure read
  once per student, such as the dropout rate, their latest term). "How many students have
  holds" is a headcount of students with a hold this term; "dropout rate for students with
  holds" compares students with and without one. The hold rate itself cannot be split by
  hold status.

## Set up

```sh
make setup-python
make school-data      # writes var/school/school.db (about 25 s), then checks it (about 20 s)
make explore-check    # the full-scale check (below), then prints the owner's example
```

Without the database every Explore route answers 503 with "The demonstration university
data is not installed. Run make school-data." and nothing else. `CABINET_SCHOOL_DB` points
Explore at another copy (the tests use a reduced-scale copy in a temporary directory). The
connection is read-only.

## What can be asked

Explore runs only the eighteen reviewed analyses in
`backend/src/cabinet/explore/catalog.py` (the general one, `measure_by_group`, lives in
`explore/general.py`). `GET /explore/catalog` lists them with seventeen example
questions.

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
| `measure_by_group` | One reviewed measure by up to two groupings (below) | measure, group by, then by, a value of any grouping, term window, order, rows |

Every parameter takes a value from a list. Majors, colleges, subjects, courses, terms,
academic years, instructors, and hold categories come from the database, and the rest are
fixed lists. Nothing a person types is ever placed into SQL. Typed text only chooses an
analysis and values from these lists.

The definitions are the ones in `data/school/VERIFY.md`. A graded registration is a letter
grade A to F or a W in a letter-graded course, and the DFW rate is D+, D, F, and W over
graded registrations. A student counts once, in the program and cumulative GPA of their
latest term. Rates are rounded exactly as `data/school/check.py` rounds them, so the
planted facts come back to the digit.

Some questions the rule planner maps (the tests check all 49 phrasings in
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

A question no analysis answers gets "CampusLens can't answer that from the approved
analyses yet." with the three example questions closest to it.

## The general analysis: a measure by group

Most questions a university's leaders ask have one shape: a measure, for groups of
students, sometimes for one part of the school. "What majors have the highest dropout
rate?", "Retention by first-generation status", "Graduation rate for Pell students by
college", "How many international students are in Nursing?", "Average GPA of athletes
vs non-athletes". `measure_by_group` answers that shape with reviewed parts only:

- a **measure**, chosen from the list below. Each is a reviewed SQL aggregate over one
  of five reviewed row sets (a student once, a student per term, an entering class, a
  graduate, a graded registration), with a written definition that "How this was
  answered" shows as the step's first note;
- up to two **groupings** from an allow-list: major, college, class level, term, entry
  cohort, residency, first-generation status, Pell status, gender, race and ethnicity
  (IPEDS categories), age at entry, admit type, full-time or part-time, housing,
  athletes, honors program, and section modality (registrations only);
- **filters**: one value of any grouping (a major, "international", "Pell
  recipients", "women", "Fall 2022 entrants"), and a term window;
- an order and a row limit.

Every value is checked against the allowed values and bound as a parameter. A
measure and a grouping that do not fit together (a retention rate by term, a headcount
by section modality) are refused by the same allow-list for both planners.

| Measure | Definition (short form) |
|---|---|
| headcount | Students enrolled in the term, each once (the latest fall or spring by default) |
| average cumulative GPA | Each student once, at their latest enrolled term (the same as average GPA by major) |
| average credits earned | Cumulative credit hours earned, each student once at their latest term |
| dropout rate | Left without a degree, not enrolled in either of the next two fall or spring terms or since, and not found enrolled at another college. Students enrolled Fall 2020 to Spring 2026, in the major of their latest term; a student last enrolled in Fall 2025 without a degree is left out (too recent to tell) |
| transfer-out rate | Left without a degree and later found enrolled at another college |
| major-change rate | Students who changed major at least once, counted in the major of their latest term |
| major change-out rate | Of the students ever in a major, the share who left it for another major (counted in the major they left; "What % of students change majors, by major?") |
| attrition rate | Of the students ever in a major, the share who left the university from it without a degree, transfers out included ("What is the attrition rate for each major?") |
| early major-fit rate | First-time fall entrants who, in their first year, earned a D, F or W in two or more courses their major requires or changed major twice; major as at entry |
| first-year D, F or withdrawal rate in major courses | First-time students' graded registrations in their first fall and spring, in courses their major requires |
| Pell, first-generation, international, part-time, on-campus share | Share of enrolled students in the term |
| probation and suspension rates | Share of enrolled student terms ending on probation (or continued probation), or in suspension |
| stop-out rate | Fall and spring student terms, without a degree that term, after which the student was not enrolled in the next fall or spring term |
| credit completion rate | Credit hours earned over credit hours attempted |
| average credits attempted per term | Per enrolled student term |
| advising contact rate | Fall and spring student terms with a completed advising appointment |
| hold rate | Fall and spring student terms in which a hold was placed |
| first-year retention rate | First-time fall entrants (Fall 2020 to Fall 2024) enrolled the next fall; major and housing as at entry |
| 4-year graduation rate | First-time fall entrants (Fall 2020 to Fall 2022) who graduated within four academic years |
| 6-year graduation rate | First-time Fall 2020 entrants who graduated within six academic years (the only class the records follow that long) |
| average time to degree | Years from a graduate's first term to their graduation term |
| graduates | Students who graduated, in the major they graduated in |
| D, F or withdrawal rate; course withdrawal rate | Over graded registrations, with the student's major and class level in that term |

The cohort measures read first-time students unless admit type is asked about, as the
federal graduation rate does. The rule planner maps common phrasings to this analysis
(the tests check 66 of them, the owner's "what majors have teh highest drop out rate"
first), and it chains: "Which major has the highest dropout rate, and what is its
hardest class?" runs the general analysis, then the hardest required course of its top
major. The answer names the top group, its figure with its counts, the figure for all
students, and, for dropout, stop-out, retention, and graduation rates, the definition
in one plain sentence.

## Support programs

Two more analyses read the support programs (`cabinet.interventions`;
`data/school/interventions.py` builds their records). `program_impact` answers
"Did the AI tutoring program work?" and "Is the theology funding bridge helping?" with
three labelled comparisons for each of the program's outcomes: everyone the rule
named before the program against everyone it named since; participants against
non-participants (naive); and participants against non-participants with a similar GPA
(fairer), each with a 95 % range and the line "This comparison isn't a randomized
trial." `program_reach` answers "How many students are eligible for tutoring this
term?": eligible students counted from the rule applied to the records, and how many
took part. Both are aggregate; any group under 10 is withheld with its partner. The
per-student outreach lists are never an Explore answer; they live on the Support
programs page (`cabinet.outreach`) for the executive, the admin and the program's
office, need a person's approval, are audited, and send nothing.

## How an answer is computed

1. **Privacy check** (`explore/privacy.py`). Counseling and spiritual care, a student id or
   a numbered student, a request for individual students, and predictions about one
   student or risk scores are caught here; the API records `question.asked` and
   `data.refused` (categories `counseling`, `individual_student`, `prediction`) before
   anything else runs. None of them is answered with the protected data, and none gets
   a dead-end card:
   - **Counseling, chaplain and spiritual care** (notes, records or totals): no figure
     at all, not even a total. The reply is a calm, explicit denial, shown as a normal
     answer (not the off-topic card): "Access denied. Counseling and chaplain notes are
     outside the Chief of Staff's authorized scope, and they aren't needed to answer
     registration questions. This request has been recorded in the audit log." Explore
     answers as the Chief of Staff, so the denial names it (`counseling_message` names
     any AI employee, such as the Enrollment Analyst, and says "CampusLens's" when none is
     known). Three related questions follow (retention, holds, advising). The briefing
     may still show its one authorized aggregate count.
   - **One student or a list of students**: the question is rewritten as a question about
     totals (`aggregate_form`: ids and names removed, "which students" read as "how
     many students") and planned by the **rule planner only**, so a typed name or id never
     reaches a model. The answer starts "CampusLens can't look up one student, but here
     are totals for students like that." ("Which students are on probation in Nursing?"
     gets probation in Nursing; "Did Jane Doe pass MEEN 3310?" gets the course's D, F or
     withdrawal rate). A student named only by id or name, with no group, course or major,
     gets that line and example questions instead. Small-cell withholding applies as
     everywhere.
   - **A prediction about students** ("Who will be suspended next term?") is answered the
     same way, led by "CampusLens doesn't forecast or name students, so here's what the
     records show for the group."
   - **Off-topic** requests with nothing to do with the student records ("code me a
     website", "write a poem", "what's the weather") are matched by rules
     (`is_off_topic`), recorded as `data.refused` with category `off_topic`, and get the
     card headed "Not something CampusLens answers". A model planner's empty plan for a
     question that names nothing in the records (no student, course, major, term, ...)
     gets the same card; one that does gets "can't answer that from the approved analyses
     yet" with suggestions.
   - **Forward-looking questions about groups** are not caught: see "Questions about
     the future" below.
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
   - The **model planner** runs only with a live provider, and plans first by default
     (`CABINET_EXPLORE_PLANNER`: `model-first` by default; `rules-first` asks the model
     only for a question the rules cannot map; `rules-only` never asks it). It reads the
     **compact catalog** (`explore/compact.py`, under 3,000 tokens): each analysis with a
     one-line purpose and its parameters' choices or value types, the measures and
     groupings of the general analysis, the majors and colleges, the term range and the
     current term, short synonyms ("CS" is Computer Science), the planning rules, and six
     worked examples. It lists no course, subject, instructor, or student. The system
     message is that catalog and the user message is the question alone, so a server
     that keeps the processed start of the last prompt does not read the catalog again.
     The model writes one short `reasoning` sentence and then the plan; the sentence is
     discarded, never shown, logged, or recorded. Courses, subjects, terms and
     instructors are free text ("Organic Chemistry 1", "Fall 2024", "Alicia Shelby"), and
     code resolves each to a catalog value (codes, names, titles, synonyms, close
     spellings) before the plan is checked against the catalog: every id, parameter,
     value, and reference. A close spelling must match word for word, with the same
     numbers ("Calculus IV" is never Calculus I), and a title several subjects share
     stays unresolved. Code also repairs three shapes a small model writes: a ranking
     with no grouping ranks majors (or the column a later step takes from it), the
     same table asked twice in two orders is asked once unless a later step reads
     from it, and a row count the catalog does not offer becomes the next one it
     does. The model's explicit empty plan means no analysis answers the question,
     and the rules are not asked. The model has its own time
     budget (`CABINET_EXPLORE_PLANNER_TIMEOUT`, default 20 s, one deadline that a retry
     shares; every other call has 55 s).
     A timeout, invalid JSON, a value that resolves to nothing, a value outside the
     catalog, a bad reference, or an unavailable provider sends the question to the
     rule planner, and the response says why in `fallbacks`. The question reaches the
     model with any typed id or long number replaced (`redact_question`). The measured
     accuracy and latency of each order are in `docs/EXPLORE-EVAL.md`.
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
   template answer states must also be in it, or the template answer is shown. (In
   testing, a rewording of the owner's question compared majors and courses the
   question did not ask about and named no instructor; this rule shows the template
   answer in such a case.)
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
- **Withheld figures stay withheld.** Whenever the withheld rows of a table add up to
  fewer than 10 students, the next smallest row is withheld too, until they hold 10 or
  more, so subtracting the visible rows from a total shown elsewhere (a course's
  all-terms row, a group's whole, a college's total) never recovers a group under 10.
  Majors are protected within their college, in each term and across terms
  (enrollment, credit hours, graduates by year, advising, standing, growth). In the
  general analysis every attribute that defines a cell (its groupings and its filters)
  is checked this way against its siblings, so two filtered questions ("Nursing,
  in-state" and "Nursing, out-of-state") cannot be subtracted from Nursing's total to
  reveal a small third group. A figure over a term window other than all terms is shown
  only when the same group or course had no term with 1 to 9 students, so two windows
  cannot be subtracted to reveal one small class. This applies to a course's terms, its instructors, and the student groups
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
  list students, name an id in any form, or ask who will do something are treated as
  questions about individuals (answered only with totals for students like that), and any
  id or long number a person types is replaced before the question is recorded.
- **Names are masked before the model and the audit log by an allow list**
  (`safe_text` in `explore/privacy.py`). The model planner and the audit log receive the
  question with only allow-listed words kept, in any case or position: common English and
  campus words, catalog names (majors, colleges, subjects, course codes and titles,
  instructors, terms), campus phrases (Student Accounts, Main Campus, ...), US states,
  known acronyms and the planner's own vocabulary. Every other word becomes "[name]"
  ("[name withheld]" in the log), an email, handle or quoted single word "[id]", and a
  student id or long number "[number withheld]".
- **A separate, stricter detector decides the guarded path** (`mask_names`): a person
  named after did/is/will/how did ("Did May pass", "How did Christian do"), a capitalized
  name starting a sentence before a record verb ("Ravi failed MEEN 3310"), unknown words
  in capitals ("Did KIM pass"), lowercase names before a record verb or a possessive
  ("has jose garcia registered", "mary jane watson's gpa"), emails and handles. Such a
  question is audited as `individual_student` (or `prediction`) and answered only with
  group totals from the rule planner, or with suggestions; the model planner is not
  asked. A counseling word used as a name ("Will Faith graduate?") is read as a person.
  The words the detector finds are masked in the audit log even when they are common
  words ("May").
- **The residual limit.** A name that is also an allow-listed word and is not in a name
  position the detector knows can still pass unmasked: "Lee" or "May" in a sentence the
  patterns do not recognize, or a student who shares a course or major name. The tests
  (`tests/test_explore_name_mask.py`) check every adversarial phrasing we have and that
  none of 240+ ordinary questions loses a word.
- **Instructor identities are for the executive and admin roles.** Staff and
  reviewers never see an instructor's id or name, not even in the parameters under "How
  this was answered", and the model planner receives the instructor list only for those
  two roles.
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
| `POST /explore/stream {question}` | executive, admin, staff, reviewer | the same answer as `POST /explore`, streamed (below); same CSRF, rate bucket, refusals, and audit |
| `GET /explore/catalog` | executive, admin, staff, reviewer | the analyses (title, description) and 17 example questions |

The aid role gets a 403 on both. The response of `POST /explore` is
`{refused, message?, answer: [{text, claims: [{table, row, column}]}], steps: [{analysis_id,
title, params_plain, fields_read, table: {columns, rows}, notes}], source}` plus `planner`
(rule, model, or recorded), `notes` (a named term or college the chosen analysis does not
filter by, and any part of the question no analysis answered), `fallbacks`,
`suggestions` when the question could not be mapped, and `redirect` (`counseling`,
`individual_student` or `prediction`) when protected data was asked for and answered
around. `refused: true` is only for off-topic requests. `params_plain` holds only the
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

## The live trace (POST /explore/stream)

While a question is answered the screen shows what is really happening, line by line,
under "Thinking": "Understood: Dropout rate by major", "Chose 1 approved analysis",
"Reading six years of student records (fictional data): 38,374 students, Fall
2020 to Spring 2026", "Computing: Dropout rate by major", "Withheld 2 small groups
(fewer than 10 students) to protect privacy", "Writing the answer from the tables",
"Checked 5 numbers against the tables". When the answer arrives the trace folds into
one line above it, "Thought for 6 s · 7 steps", which opens it again.

`POST /explore/stream` returns `application/x-ndjson`, one JSON event per line, in the
order the pipeline reaches each point: `planning`, `understood` (text), `plan` (step
descriptions, `planner` rules or model), `reading` (the school's size), `step` (index,
total, description) before each step reads anything, `suppression` (count withheld),
`writing`, `verifying` (numbers checked and matched), then `done` with exactly the body
`POST /explore` returns, `refused` with the refusal body, or `error`. Events never
carry a student row, an id, an instructor name for staff, or the question's text; their
numbers are counts of the whole school or of the finished, suppressed tables. A
refusal is decided before the first event. The screen falls back to its own timed
steps when the server does not stream (an older server, or a network that buffers the
response); screen readers hear only the step lines, politely, and every motion is off
under reduced motion.
