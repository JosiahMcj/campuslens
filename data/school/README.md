# Demonstration University

A whole synthetic university for asking specific questions and getting real, checkable
answers. "Which major has the lowest GPA? In that major, what is historically the hardest
class, and which instructor has historically taught it?" The answers come from rows in a
SQLite database shaped like Ellucian's data model, never from a model's guess.

Demonstration University is fictional: a medium-to-large private Christian university with
seven colleges, 60 undergraduate programs, 906 courses, 900 instructors (about a third of
them adjuncts), and about 16,000 students enrolled each fall (15,982 in Fall 2025), across
six academic years, Fall 2020 (`202110`) to Spring 2026 (`202620`), with summer terms. About
3,500 first-time students and 900 transfers enter each fall. There are no graduate students:
the schema has associate and bachelor's programs only.

| Part | What is in it |
|---|---|
| Calendar | 17 terms with Banner codes, census and registration dates |
| Curriculum | 46 subjects, 906 courses with credit hours, levels, prerequisites, and the 60 programs that require them, including a core curriculum (composition, college algebra or calculus, Old and New Testament survey, Christian theology, public speaking, history, wellness, arts, psychology, science) |
| People | 900 fictional instructors with ranks, departments, hire and leave terms. About 38,000 pseudonymous students over the six years with entry term and type, residency, first-generation and Pell flags, and major history |
| Teaching | About 31,000 sections (about 2,650 a fall) with caps, modality, meeting patterns, and an instructor of record. Survey lectures seat 120 to 200, composition and public speaking 25, upper-level courses 30 to 35 |
| Outcomes | About 920,000 registrations with final grades, term and cumulative GPA, credits earned, class level, academic standing, graduations, withdrawals, and stop-outs |
| Services | Holds (financial, registrar, advising, library, student life) with amounts and dates, advisor relationships, advising appointments |
| Profiles and history | Gender, IPEDS race and ethnicity, age band at entry, athletes, honors; each student's status in every fall and spring term (enrolled, stopped out, withdrew, transferred out, suspended, dismissed, graduated) with full or part time and housing; transfer-out matches |
| Graduate outcomes | A first-destination survey six months after graduating (outcome, sector, starting salary; about 64 % answered), a Clearinghouse-style graduate school match, medical school applications and acceptances, and alumni gifts by fiscal year (about 10 % of alumni have given) |

Every table and column is documented in `SCHEMA.md`, with the Ellucian Ethos resource it
stands in for. Fourteen planted facts with exact expected values are in `VERIFY.md`.

## Generate and check

```sh
make school-data     # generate var/school/school.db at scale 1.0 (about 25 s), then check it (about 20 s)
make school-check    # check the existing database again (about 20 s)
```

Or directly, at any scale and location:

```sh
.venv/bin/python data/school/generate.py --scale 0.01 --out /tmp/school.db
.venv/bin/python data/school/check.py --db /tmp/school.db          # human-readable
.venv/bin/python data/school/check.py --db /tmp/school.db --json   # full report
```

The generator and the checker use the Python standard library only (they also run on
Python 3.9). The database is about 245 MB at scale 1.0 and lives in `var/school/`, which is
never committed. Scale 1.0 is the documented university; `--scale` (0.01 to 2.0) shrinks or
grows every cohort and the faculty together, and the tests use 0.01 and 0.02. Delete it and run `make school-data` to rebuild it.

A database generated before graduate outcomes were added has 24 tables and no outcome
tables; Explore's outcome measures (salaries, graduate school, medical school, giving) say
so instead of answering. Run `make school-data` to regenerate it with all 28: the 24
original tables come out byte for byte identical, and only the four outcome tables are
new.

**Deterministic.** The seed is `20261005`. The same seed and scale always produce the same
rows, on any machine and any Python version we tried. `check.py` prints a canonical
SHA-256 over every table's rows in a fixed order, and `VERIFY.md` records the full-scale
hash, so a rebuilt database proves it is the documented one.

## What the checker verifies

`check.py` recomputes everything from raw rows and exits non-zero on any failure.

- **Schema.** The 28 tables and the 2 support-program tables and their columns are exactly as documented, and every foreign
  key resolves.
- **Privacy.** Student ids are pseudonymous (`S-` plus digits), there are no personal
  columns, and every instructor is marked fictional.
- **Grades and GPA.** Every grade row matches the grade scale. Every term and cumulative
  GPA, credit total, class level, and academic standing is rebuilt from the grades (plus the
  pre-2020 opening balance) and must match exactly. All GPAs lie between 0.00 and 4.00.
- **Operations.** No student is in two sections that meet at once, no section exceeds its
  cap, no student takes two sections of one course in a term, registration dates fall in
  the window, instructors teach and advise only while employed, no student registers while
  a financial, registrar, or advising hold is active, and no history row ends before it
  starts.
- **Progress.** Prerequisites are respected for at least 95 % of registrations (it is
  99.7 % at full scale, the rest are instructor-permission overrides). Graduates have at
  least their program's required credits and a GPA of at least 2.00, and nobody registers
  after leaving.
- **Plausibility.** Mean grade points are between 2.8 and 3.2 (3.08 at full scale), and at
  least 70 % of courses with 30 or more graded registrations have a DFW rate between 5 %
  and 25 % (86 % at full scale).
- **Enrollment history.** Every student has a profile, every enrolled term matches a
  term record, the load agrees with the registered hours, graduated and transferred-out
  terms come only after the student left, and first-year retention, six-year
  graduation, and the part-time share are inside plausible bands (at full scale: 81.4 %
  retention, 61.8 % six-year graduation for the Fall 2020 entrants, 12.2 % of enrolled
  terms part time).
- **Graduate outcomes.** Survey rows only for bachelor's graduates past their six-month
  point, salaries only for full-time employment, a graduate school answer always matched
  by an enrollment, medical enrollments only for accepted applicants, gifts only from
  graduates after graduating and in the right fiscal year, and the knowledge rate (60 to
  70 %), medical school acceptance (40 to 50 %), and giving participation (5 to 12 %)
  inside plausible bands at full scale.
- **Planted facts.** At full scale every value in `VERIFY.md` must match exactly, and the
  24 tables that existed before graduate outcomes must hash to the value recorded before
  outcomes were added. At any other scale each planted pattern is checked for direction.

## How the university is simulated

The generator runs term by term. New students enter each fall (and a few each spring),
already-enrolled students carry an opening balance from before Fall 2020, and each fall and
spring a persistence step decides who returns, who stops out (and may come back), and who
withdraws. Each student plans a schedule from their program's requirements in prerequisite
order, then electives. Sections are opened from that demand (required courses always, an
elective only when at least five students want it), given a modality and a meeting time,
and staffed from the course's department, preferring instructors who taught it before.
Students enroll in a random order into open seats without time conflicts. Grades come from
a latent score per registration (ability, program, course difficulty, instructor, noise).
GPA, standing, graduation, changes of major, holds, and advising follow from the grades.
A first academic suspension sits out a term, and a second one dismisses the student. A
blocking hold (financial, registrar, advising) still active on a registration day is
released that day, as if the student cleared it in order to register.
Department sizes come from a first pass of the same simulation, so about 99 % of sections
are taught inside the instructor's own department.

Sections are sized by course (`section_cap` in `generate.py`): survey lectures most students
take (Old and New Testament survey, General Psychology, U.S. History, Christian theology)
seat 120 to 200, labs and one-hour courses 24, composition and public speaking 25, College
Algebra 35, other lower-level courses 40 to 45, and upper-level courses 30 to 35. The
average section has about 30 students, the average student sits in a section of about 58
(the big lectures), and under 1 % of sections have fewer than five students.

Two honest limits. Required courses are offered even for one student, so a few upper-level
sections are very small. And because pre-2020 coursework is an opening balance,
course-level history starts in Fall 2020.

## Privacy

- **Synthetic.** Every row is simulated from a seed. Nothing is drawn from, modelled on, or
  calibrated against any real institution's data.
- **Pseudonymous students.** Students have only an `S-` id. There are no names, birth
  dates, addresses, or contact details. Pell status is for aggregate questions only.
- **Fictional instructors.** Instructor names are random combinations of first and last
  name lists, every row is marked `fictional = 1`, and any resemblance to a real person is
  coincidental.
- **No course evaluations.** They are personnel-sensitive, so we do not invent them.
