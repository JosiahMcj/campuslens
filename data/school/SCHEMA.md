# Demonstration University database schema

`data/school/generate.py` writes one SQLite file (default `var/school/school.db`) with the
28 tables below, and two support-program tables at the end. The shape follows Ellucian's Ethos data model, flattened into relational
tables a question engine can join. Each table names the Ethos resource it stands in for.
Where Ethos has no exact resource we say so instead of inventing one.

Conventions used throughout:

- Term codes are Banner style text: `202110` Fall 2020, `202120` Spring 2021, `202130`
  Summer 2021, through `202620` Spring 2026. Text comparison orders them in time.
- Dates are ISO `YYYY-MM-DD` text. Meeting times are `HH:MM` text, 24-hour clock.
- Ids are readable text keys rather than Ethos GUIDs: `S-100001` (student), `I-0001`
  (instructor), `MEEN 3310` (course), `202620-10042` (section, term plus CRN).
- Booleans are integers 0 or 1.
- Foreign keys are declared and hold (`PRAGMA foreign_key_check` is part of `check.py`).

## Institution and calendar

### `meta`
Generator metadata. **Ethos: none** (not institutional data).

| Column | Meaning |
|---|---|
| `key`, `value` | `institution`, `fictional` (`true`), `seed`, `scale`, `generator_version`, `first_term`, `last_term`, `notice` |

### `colleges`
**Ethos: `educational-institution-units`** (the college level).

| Column | Meaning |
|---|---|
| `college_code` | `CAS`, `CSB`, `COB`, `CEC`, `COE`, `CNH`, `CTA` |
| `name` | For example College of Engineering and Computing |

### `academic_periods`
**Ethos: `academic-periods`.** Seventeen terms, Fall 2020 to Spring 2026, with summer terms
2021 to 2025.

| Column | Meaning |
|---|---|
| `term_code` | Banner term code |
| `name` | `Fall 2020`, `Spring 2021`, `Summer 2021` |
| `season` | `Fall`, `Spring`, or `Summer` |
| `academic_year` | `2020-2021` |
| `sequence` | 1 to 17 in time order |
| `start_date`, `end_date` | First and last day of classes |
| `census_date` | Official enrollment count date |
| `registration_start_date` | When registration opened for the term |

### `grade_scale`
**Ethos: `grade-definitions`** (one grade scheme).

| Column | Meaning |
|---|---|
| `grade` | A, A-, B+, B, B-, C+, C, C-, D+, D, F, W, I, P, NP |
| `quality_points_per_hour` | 4.0 down to 0.0, null for W, I, P, NP |
| `counts_in_gpa` | 1 for letter grades A to F |
| `earns_credit` | 1 for A to D and P |
| `is_dfw` | 1 for D+, D, F, W |
| `description` | Plain-language meaning |

## Curriculum

### `subjects`
**Ethos: `subjects`** (with the owning unit). 46 subject codes.

| Column | Meaning |
|---|---|
| `subject_code` | `MEEN`, `MATH`, `BIBL` |
| `name` | Mechanical Engineering |
| `college_code` | Owning college |

### `academic_programs`
**Ethos: `academic-programs`** (the major is an `academic-disciplines` entry). Forty programs.

| Column | Meaning |
|---|---|
| `program_code` | Degree plus major, `BS-MEEN` |
| `major_code` | `MEEN` |
| `name` | Mechanical Engineering |
| `degree` | BS, BA, BBA, BSN, BSE, BSW, BM, BME, BFA, AA |
| `award_level` | `Bachelor` or `Associate` |
| `college_code` | Owning college |
| `subject_code` | The subject its electives come from (null for General Studies and Interdisciplinary Studies) |
| `credits_required` | 120, 124 (Nursing), 128 (engineering), 60 (AA: General Studies, Christian Studies) |

### `courses`
**Ethos: `courses`.** 906 catalog courses. The second digit of the number is the credit
hours (`MEEN 3310` is 3 hours, `EDUC 4690` is 6), and the first digit is the level.

| Column | Meaning |
|---|---|
| `course_id` | `MEEN 3310` |
| `subject_code`, `course_number` | `MEEN`, `3310` |
| `title` | Thermodynamics I |
| `credit_hours` | 1 to 6 |
| `course_level` | 1000, 2000, 3000, or 4000 |
| `grade_mode` | `standard` (letter grades) or `pass_fail` (P or NP) |
| `is_core` | 1 when the course is in any program's core curriculum |

### `course_prerequisites`
**Ethos: none as a separate resource.** We keep prerequisites as their own table.

| Column | Meaning |
|---|---|
| `course_id` | The course |
| `prerequisite_course_id` | A course that must be passed (D or better, or P) in an earlier term. All rows for a course apply together. |

### `program_requirements`
**Ethos: none** (degree requirements usually live in a degree audit system). One row per
course a program requires.

| Column | Meaning |
|---|---|
| `program_code`, `course_id` | The pair |
| `requirement_type` | `core` (core curriculum: composition, mathematics, Bible and theology, speaking, history, wellness, arts, psychology, science), `major` (the program's own subject), or `support` (another subject) |

## People

### `instructors`
**Ethos: `instructors`** (with the `persons` name fields). 900 instructors, about a third of them adjuncts. **Every name is
fictional**, generated from first and last name lists, and `fictional` is always 1.

| Column | Meaning |
|---|---|
| `instructor_id` | `I-0001` |
| `first_name`, `last_name` | Fictional |
| `fictional` | Always 1 (enforced by a CHECK constraint) |
| `academic_rank` | Professor, Associate Professor, Assistant Professor, Instructor, Lecturer, Adjunct Instructor |
| `subject_code`, `college_code` | Home department and college |
| `hire_date`, `hire_term` | Hire date and first term employed. Hires before Fall 2020 carry earlier term codes that are not rows in `academic_periods`. |
| `leave_term` | First term no longer employed, null if still employed. An instructor teaches or advises in a term only when `hire_term <= term < leave_term`. |

### `students`
**Ethos: `students`** (pseudonymous, no `persons` data). About 38,000 students over the six years, about
16,000 enrolled in a fall term. There are
**no names, birth dates, addresses, emails, or phone numbers**, only the pseudonymous id.

| Column | Meaning |
|---|---|
| `student_id` | `S-` plus digits |
| `entry_term` | Term of entry. Students already enrolled at Fall 2020 have earlier codes (`201710`, Fall 2016 to Fall 2019) that are not rows in `academic_periods`. |
| `entry_type` | `first_time` or `transfer` |
| `residency` | `in_state`, `out_of_state`, `international` |
| `first_generation` | 1 for first-generation college students |
| `pell_recipient` | 1 for Pell recipients. For aggregate use only. |
| `transfer_credits` | Credit hours accepted from elsewhere (transfer or dual credit), no GPA |
| `opening_credits_earned`, `opening_gpa_hours`, `opening_quality_points` | Institutional work completed before Fall 2020, carried in as an opening balance. Zero for students who entered in the window. |
| `enrollment_status` | `active`, `graduated`, `withdrawn`, `stopped_out`, `academic_suspension` (suspended, may still return), `academic_dismissal` |
| `exit_term` | Last enrolled term for students who left (graduated, withdrew, stopped out, dismissed, or did not return from a suspension). Null for active students and for suspended students who may still return. |
| `class_level` | Class level at the start of the student's latest enrolled term |

### `student_academic_programs`
**Ethos: `student-academic-programs`.** Program history, one row per program a student was
in. A change of major closes one row and opens the next from the following term.

| Column | Meaning |
|---|---|
| `record_id` | Row id |
| `student_id`, `program_code` | The pair |
| `start_term`, `end_term` | First and last term in the program (`end_term` null while current) |
| `status` | `active`, `changed`, `graduated`, `withdrawn`, `stopped_out`, `dismissed`, `inactive` (did not return from a suspension) |

Every `student_term_records` row is covered by exactly one of these rows with the same
program (checked).

## Teaching

### `sections`
**Ethos: `sections`.** About 31,000 sections (about 2,650 a fall), offered from demand each
term and sized by course (see README).

| Column | Meaning |
|---|---|
| `section_id` | `202620-10042` (term plus CRN) |
| `term_code`, `course_id` | When and what |
| `section_number` | `01` in person, `W01` online, `H01` hybrid |
| `crn` | Course reference number, unique within the term |
| `modality` | `in_person`, `online`, `hybrid` |
| `meeting_days` | `MWF`, `TR`, or one day (`M`, `T`, `W`, `R`, `F`). Null for online sections. |
| `start_time`, `end_time` | `HH:MM`, null for online sections |
| `capacity` | Enrollment cap. Registrations never exceed it. |

### `section_instructors`
**Ethos: `section-instructors`.** One instructor of record per section.

| Column | Meaning |
|---|---|
| `section_id`, `instructor_id` | The pair |
| `role` | Always `primary` |

## Enrollment and outcomes

### `section_registrations`
**Ethos: `section-registrations`.**

| Column | Meaning |
|---|---|
| `registration_id` | Row id, also the key of the final grade |
| `student_id`, `section_id`, `term_code` | Who, what, when (the term always equals the section's term) |
| `registration_date` | Between the term's registration start and one week after classes begin |
| `status` | `registered`, or `withdrawn` when the final grade is W |

No student is registered in two sections that meet at the same time, and no student takes
two sections of one course in a term.

### `final_grades`
**Ethos: `student-transcript-grades`** (final grades). One row per registration.

| Column | Meaning |
|---|---|
| `registration_id` | The registration |
| `grade` | A grade from `grade_scale` |
| `credit_hours` | The course's credit hours |
| `quality_points` | Grade points per hour times credit hours (0 for non-letter grades) |
| `gpa_hours` | Credit hours for letter grades A to F, else 0 |
| `earned_hours` | Credit hours for A to D and P, else 0 |

### `student_term_records`
**Ethos: `student-grade-point-averages`** (term and cumulative GPA) with the class level of
**`student-academic-periods`**. One row per student per enrolled term.

| Column | Meaning |
|---|---|
| `student_id`, `term_code` | The pair |
| `program_code` | The student's program during the term |
| `class_level` | From credits earned before the term: Freshman under 30, Sophomore under 60, Junior under 90, else Senior |
| `attempted_hours` | Credit hours of every registration except W |
| `earned_hours`, `gpa_hours`, `quality_points` | Sums over the term's final grades |
| `term_gpa` | quality points / GPA hours, rounded half up to 2 decimals, null without GPA hours |
| `cumulative_earned_hours` | transfer + opening + earned hours through this term |
| `cumulative_gpa_hours`, `cumulative_quality_points` | opening balance + sums through this term |
| `cumulative_gpa` | cumulative quality points / cumulative GPA hours, half up to 2 decimals |

**Recomputing GPA.** Every attempt counts, including repeats (there is no repeat
exclusion). Quality points are exact in tenths (A- is 3.7, so 3 hours give 11.1).
`check.py` rebuilds every term and cumulative value from `final_grades` plus the opening
balance on `students` and requires an exact match.

### `academic_standings`
**Ethos: `student-academic-standings`** (values as in `academic-standings`). One row per
student per enrolled term.

| Column | Meaning |
|---|---|
| `student_id`, `term_code` | The pair |
| `standing` | `Good Standing`, `Academic Probation`, `Continued Probation`, `Academic Suspension` |

The rule, applied in term order: cumulative GPA null or at least 2.00 gives Good Standing.
Otherwise a student whose previous standing was none or Good Standing goes on Academic
Probation. Otherwise a term GPA below 2.00 gives Academic Suspension, and anything else
gives Continued Probation. A student suspended for the first time sits out the next fall
or spring term and may then return. A second suspension dismisses the student
(`enrollment_status` `academic_dismissal`).

## Services

### `person_holds`
**Ethos: `person-holds`.**

| Column | Meaning |
|---|---|
| `hold_id` | Row id |
| `student_id` | Who |
| `category` | `financial`, `registrar`, `advising`, `library`, `student_life` |
| `description` | For example Account balance past due |
| `amount` | Dollars owed (financial and library holds), else 0 |
| `responsible_office` | Student Accounts, Registrar, Academic Advising, Library, Student Life |
| `term_code` | Term the hold was placed in |
| `start_date`, `end_date` | Placed and released. `end_date` null means still active. A hold is active on day d when `start_date <= d` and `end_date` is null or later than d. |

Financial, registrar, and advising holds block registration. No registration date falls on
a day such a hold is active, because a blocking hold still active when a student registers
is released that day (checked). Holds placed when a student leaves with a balance stay
active unless the student returns.

### `student_advisor_relationships`
**Ethos: `student-advisor-relationships`.** Advisors are instructors, usually in the student's
program or college. A change of major or an advisor leaving starts a new relationship.

| Column | Meaning |
|---|---|
| `relationship_id` | Row id |
| `student_id`, `advisor_id` | The pair (`advisor_id` is an `instructor_id`) |
| `advisor_type` | Always `primary` |
| `start_term`, `end_term` | First and last term (`end_term` null while current). A relationship never extends past the advisor's employment (checked). |

### `student_appointments`
**Ethos: `student-appointments`.** Advising appointments during fall and spring terms.

| Column | Meaning |
|---|---|
| `appointment_id` | Row id |
| `student_id`, `advisor_id`, `term_code` | Who, with whom, when |
| `appointment_date` | Date of the appointment |
| `appointment_type` | `registration_advising`, `academic_planning`, `academic_recovery` (students on probation), `degree_audit` (seniors) |
| `status` | `completed`, `no_show`, `cancelled` |

## Profiles, term-by-term enrollment, and outcomes elsewhere

These three tables are derived after the simulation from their own seeded random
streams (one per student), so the 21 simulated tables above, and every planted fact in
`VERIFY.md`, are unchanged by them. They hold what a real registrar's warehouse carries
for institutional research.

### `student_profiles`
**Ethos: `persons`** (the demographic fields only: gender, ethnicity, races; never a
name or a birth date) and the student characteristics a registrar keeps (athletics,
honors). One row per student.

| Column | Meaning |
|---|---|
| `student_id` | The student |
| `gender` | `female` or `male`. The share of women follows the entering major (88 % in Nursing, about 16 % in Mechanical Engineering, 56 % by default). |
| `race_ethnicity` | IPEDS categories: `white`, `hispanic`, `black`, `asian`, `two_or_more`, `american_indian`, `pacific_islander`, `unknown`, and `nonresident` (U.S. Nonresident, exactly the international students). Drawn from first-generation and Pell status only, never from ability, so any outcome gap by race runs through those. |
| `age_band_at_entry` | `under_20`, `20_24`, `25_34`, `35_plus`. Transfers and part-time students are older. No birth date is stored. |
| `athlete` | 1 for an intercollegiate athlete (about 1 in 9 full-time first-time students, more in Kinesiology and Sport Management) |
| `honors` | 1 for the honors program (first-time students with strong records) |

### `student_term_enrollment`
**Ethos: `student-academic-periods`** (the academic period enrollment status and the
academic load) with **`housing-assignments`** (on or off campus). One row per student per
fall and spring term, from entry (or Fall 2020) to Spring 2026.

| Column | Meaning |
|---|---|
| `student_id`, `term_code` | The pair |
| `status` | `enrolled`; or, in a term the student was not enrolled: `graduated` (after the degree), `stopped_out` (no degree, may return; also any gap before a return), `withdrawn` (left the university), `transferred_out` (found enrolled at another college from that term on), `suspended` (sitting out an academic suspension), `dismissed` |
| `academic_load` | `full_time` (12 or more credit hours registered at census, withdrawals included) or `part_time`; null when not enrolled |
| `census_hours` | Credit hours registered in the term (all registrations, W included); null when not enrolled |
| `housing` | `on_campus` or `off_campus` for the academic year; null when not enrolled. First-year students mostly live on campus; first-year students who did not come back lived on campus less often. |

Every enrolled fall and spring `student_term_records` row has exactly one `enrolled`
row here and the reverse (checked); the load agrees with the registrations (checked).

### `subsequent_enrollment`
**Ethos: none.** Stands in for a National Student Clearinghouse StudentTracker match: a
student who left without a degree and was later found enrolled at another college.

| Column | Meaning |
|---|---|
| `student_id` | The student (only students who withdrew or stopped out; checked) |
| `found_term` | First term found enrolled elsewhere, after their last term here |
| `sector` | `four_year` or `two_year` |

### What these make answerable

First-year retention (first-time fall entrants enrolled the next fall), four- and
six-year graduation (graduation term against entry term, in academic years), time to
degree, dropout (left without a degree, not enrolled in the next two fall and spring
terms or since, not transferred out), stop-out, transfer-out, and every measure by
gender, race and ethnicity, age at entry, athletes, honors, housing, and full or part
time. Class level, credits attempted and earned, major changes
(`student_academic_programs.status = 'changed'`), course passes and withdrawals, holds,
advising contacts, and probation were already in the tables above.

At full scale: first-year retention 81.4 % (first-time fall entrants Fall 2020 to Fall
2024), six-year graduation 61.8 % for the Fall 2020 entrants (through Spring 2026,
where the data ends; 41 % within four years), 12 % of enrolled terms part-time. `check.py` keeps these inside
plausible bands.

## Graduate outcomes

What graduates did next, and whether they gave back. A real university keeps these outside
the student system, so each table names the source it stands in for. **Ethos: none** for
all four: Ethos has no resource for first destinations, Clearinghouse matches, professional
school applications, or gifts.

These four tables are drawn after the simulation from their own seeded stream (one per
graduate), so the 24 tables above are byte for byte unchanged (`VERIFY.md` proves it with
`original_tables_sha256`). One set of draws per graduate feeds all four, so they agree.

Nothing is observed after the **data end**, the last day of Spring 2026 (2026-05-08):

- the survey covers bachelor's graduates whose six-month point (graduation date, the last
  day of the graduation term, plus 183 days) is on or before the data end: the classes of
  Fall 2020 through Summer 2025;
- a graduate is followed for graduate school "within a year" only once a full year has
  passed: the classes through Fall 2024;
- enrollments and gifts are dated on or before the data end;
- a medical school application appears once its decision is known (entering classes
  through 2026).

Associate graduates (General Studies, Christian Studies) are not surveyed and are not in
the graduate school match or the medical school applications; they are alumni and can
give. Students who left without a degree are not alumni here.

### `first_destination`
Stands in for a **NACE-style first-destination survey** (career services, six months after
graduation). One row per bachelor's graduate who answered; graduates who did not answer
have no row. About 64 % of eligible graduates answered (the knowledge rate).

| Column | Meaning |
|---|---|
| `student_id` | The graduate |
| `graduation_term` | Their graduation term (equals `students.exit_term`) |
| `collected_date` | The six-month point: graduation date plus 183 days |
| `outcome` | `employed_full_time`, `employed_part_time`, `graduate_school` (enrolled in graduate or professional school), `military_service` (military or volunteer service, including mission work), `seeking` (still seeking employment or school), `not_seeking` |
| `employer_sector` | For the employed and in service: `business`, `healthcare`, `education`, `government`, `nonprofit`, `church_ministry`, `military`; null otherwise |
| `starting_salary` | Annual starting salary in dollars, rounded to $500, for `employed_full_time` only, and only when the graduate reported one (about 86 %); null otherwise |

Salaries follow the major (Computer Science, engineering, and Nursing highest; education,
ministry, the arts lowest) and rise modestly with final GPA. A respondent whose
Clearinghouse enrollment began by the six-month point answered `graduate_school`.

### `graduate_enrollment`
Stands in for a **National Student Clearinghouse StudentTracker** match of graduates. One
row per bachelor's graduate found enrolled in graduate or professional school after
graduating (their first such enrollment), on or before the data end.

| Column | Meaning |
|---|---|
| `student_id` | The graduate |
| `enrollment_begin_date` | First day of the enrollment (after the graduation date) |
| `program_type` | `masters`, `doctoral`, `medical` (MD or DO), `law`, `other_professional` (physical therapy, physician assistant, pharmacy, and the like) |
| `institution_control` | `public` or `private` |

Every `medical` row is an accepted medical school applicant starting in August of their
entering year (checked).

### `medical_school_applications`
Stands in for the pre-health advising office's record of **AAMC/AACOM application
outcomes**. One row per bachelor's graduate who applied to medical school (first cycle
only), for cycles decided by the data end.

| Column | Meaning |
|---|---|
| `student_id` | The applicant |
| `entering_year` | The entering class applied for (the year after graduating, or two years after with a gap year) |
| `applied_to` | `MD`, `DO`, or `MD and DO` |
| `accepted` | 1 when accepted to at least one school |

Applicants come mostly from Biology, Biomedical Sciences, and Chemistry. About 48 % are
accepted, more with a higher GPA (AAMC reports about 41 % nationally).

### `alumni_gifts`
Stands in for an **advancement (alumni giving) system such as Ellucian CRM Advance**. One
row per gift made by a graduate after graduating.

| Column | Meaning |
|---|---|
| `gift_id` | Row id |
| `student_id` | The donor (always a graduate; checked) |
| `gift_date` | After the graduation date, on or before the data end |
| `fiscal_year` | Fiscal year ending June 30 (`2025` is July 2024 to June 2025; 2026 runs to the data end) |
| `amount` | Whole dollars, at least $5 |
| `designation` | `annual_fund`, `scholarships`, `athletics`, `college_department`, `missions_ministry` |

About 10 % of alumni have given at least once. The chance of a first gift grows each year
after graduating (young alumni give least); ministry and theology graduates, athletes, and
honors graduates give more often.

### What these make answerable

What graduates of each major earn (median starting salary), whether grades matter for
earnings (salary by final GPA band), employment and graduate school rates, medical school
acceptance of applicants (by major and GPA band), alumni giving participation, average
gift, and total giving, by every grouping a graduate has (major, college, first-generation,
Pell, gender, race and ethnicity, athletes, honors, final GPA band). At full scale: 64.1 %
knowledge rate, $46,000 median starting salary, 16.8 % in graduate school within a year,
47.8 % medical school acceptance, 10.4 % giving participation.

## Support programs

`data/school/interventions.py` adds these two tables after every core table is
written, from its own seeded stream, and never changes a core row: the canonical hash
in `check.py` covers the 28 documented tables only (so `VERIFY.md` still holds), and the
program tables have their own `programs_sha256`. **Ethos: none** for both; they stand
in for a student-success program's own records.

### `support_programs`

| Column | Meaning |
|---|---|
| `program_id` | `ai_tutoring`, `theology_bridge`, `fit_advising` |
| `name`, `eligibility_rule`, `offer`, `owner_office` | Plain text the Support programs page shows |
| `start_term` | `202510` (Fall 2024) for all three |
| `primary_outcome`, `secondary_outcome` | Column names in `support_program_terms` |

### `support_program_terms`

One row per student the program's rule names in a fall or spring term, before and
after the program started (the rules are in the module; `cabinet.interventions` applies
the same rules as SQL and a test checks they name the same students every term).

| Column | Meaning |
|---|---|
| `program_id`, `student_id`, `term_code` | The key |
| `period` | `before` or `after` the start term |
| `gpa_at_eligibility` | Cumulative GPA entering the term (tutoring, bridge) or at its end (fit advising) |
| `offered`, `accepted` | 1 for every eligible student from the start term; `accepted` null before it |
| `term_gpa` | Tutoring: GPA in the term |
| `returned_next_term` | Enrolled in the next fall or spring term; null when they graduated or there is no next term |
| `financial_hold_next_term` | Bridge: a financial hold placed in the next term, among those who returned |
| `changed_major_next_term` | Fit advising: a different program the next term, among those who returned |

For every `before` row and every eligible student who did not take part, the outcome
columns are copied exactly from the core tables (`check.py` verifies it). Only
participants after the start term differ, by the planted effects in the module's
notes. These are the program's follow-up records; the core enrollment and GPA tables
are not changed by them.

## Deliberately absent

- **Course evaluations.** They are personnel-sensitive, so we do not invent them.
- **Employer and school names.** Outcomes carry a sector or a program type, never an
  employer, a graduate school, or any free text.
- **Personal data.** No names, birth dates, addresses, or contact details for students, and
  no `persons` table.
- **Prerequisite history before Fall 2020.** Courses completed before the window (and
  transfer credit) are summarized in the opening balance and `transfer_credits`, not as
  rows. That is why `check.py` verifies prerequisites only for first-time students who
  entered in the window.
