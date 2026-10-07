# Demonstration University

A whole synthetic university for asking specific questions and getting real, checkable
answers. "Which major has the lowest GPA? In that major, what is historically the hardest
class, and which instructor has historically taught it?" The answers come from rows in a
SQLite database shaped like Ellucian's data model, never from a model's guess.

Demonstration University is fictional: a mid-size private Christian university with six
colleges, 40 programs, 906 courses, 220 instructors, and about 6,200 students across six
academic years, Fall 2020 (`202110`) to Spring 2026 (`202620`), with summer terms.

| Part | What is in it |
|---|---|
| Calendar | 17 terms with Banner codes, census and registration dates |
| Curriculum | 46 subjects, 906 courses with credit hours, levels, prerequisites, and the 40 programs that require them, including a core curriculum (composition, college algebra or calculus, Old and New Testament survey, Christian theology, public speaking, history, wellness, arts, psychology, science) |
| People | 220 fictional instructors with ranks, departments, hire and leave terms. About 6,200 pseudonymous students with entry term and type, residency, first-generation and Pell flags, and major history |
| Teaching | About 9,000 sections with caps, modality, meeting patterns, and an instructor of record |
| Outcomes | About 140,000 registrations with final grades, term and cumulative GPA, credits earned, class level, academic standing, graduations, withdrawals, and stop-outs |
| Services | Holds (financial, registrar, advising, library, student life) with amounts and dates, advisor relationships, advising appointments |
| Profiles and history | Gender, IPEDS race and ethnicity, age band at entry, athletes, honors; each student's status in every fall and spring term (enrolled, stopped out, withdrew, transferred out, suspended, dismissed, graduated) with full or part time and housing; transfer-out matches |

Every table and column is documented in `SCHEMA.md`, with the Ellucian Ethos resource it
stands in for. Eight planted facts with exact expected values are in `VERIFY.md`.

## Generate and check

```sh
make school-data     # generate var/school/school.db at scale 1.0 (about 3 s), then check it
make school-check    # check the existing database again (about 2 s)
```

Or directly, at any scale and location:

```sh
.venv/bin/python data/school/generate.py --scale 0.05 --out /tmp/school.db
.venv/bin/python data/school/check.py --db /tmp/school.db          # human-readable
.venv/bin/python data/school/check.py --db /tmp/school.db --json   # full report
```

The generator and the checker use the Python standard library only (they also run on
Python 3.9). The database is about 33 MB at scale 1.0 and lives in `var/school/`, which is
never committed. Delete it and run `make school-data` to rebuild it.

**Deterministic.** The seed is `20261005`. The same seed and scale always produce the same
rows, on any machine and any Python version we tried. `check.py` prints a canonical
SHA-256 over every table's rows in a fixed order, and `VERIFY.md` records the full-scale
hash, so a rebuilt database proves it is the documented one.

## What the checker verifies

`check.py` recomputes everything from raw rows and exits non-zero on any failure.

- **Schema.** The 24 tables and their columns are exactly as documented, and every foreign
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
- **Plausibility.** Mean grade points are between 2.8 and 3.2 (3.07 at full scale), and at
  least 70 % of courses with 30 or more graded registrations have a DFW rate between 5 %
  and 25 % (79 % at full scale).
- **Enrollment history.** Every student has a profile, every enrolled term matches a
  term record, the load agrees with the registered hours, graduated and transferred-out
  terms come only after the student left, and first-year retention, six-year
  graduation, and the part-time share are inside plausible bands.
- **Planted facts.** At full scale every value in `VERIFY.md` must match exactly. At any
  other scale each planted pattern is checked for direction.

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
Department sizes come from a first pass of the same simulation, so about 95 % of sections
are taught inside the instructor's own department.

Two honest limits. Because required courses are offered even for one student, about 15 %
of sections have fewer than five students (the average section has about 16, and the
average student sits in a section of about 21). And because pre-2020 coursework is an
opening balance, course-level history starts in Fall 2020.

## Privacy

- **Synthetic.** Every row is simulated from a seed. Nothing is drawn from, modelled on, or
  calibrated against any real institution's data.
- **Pseudonymous students.** Students have only an `S-` id. There are no names, birth
  dates, addresses, or contact details. Pell status is for aggregate questions only.
- **Fictional instructors.** Instructor names are random combinations of first and last
  name lists, every row is marked `fictional = 1`, and any resemblance to a real person is
  coincidental.
- **No course evaluations.** They are personnel-sensitive, so we do not invent them.
