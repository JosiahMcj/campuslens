# Demonstration University: planted facts and how to verify them

We generate Demonstration University with `data/school/generate.py` (seed `20261005`,
scale 1.0, deterministic, no wall-clock values). We planted the facts below through the
generator's grade, enrollment, and staffing model, not by writing answers into tables, so
every value here can be recomputed from the raw rows. `data/school/check.py` does exactly
that. It reads the JSON block at the end of this file and fails if any value, or the
canonical hash of the whole database, differs.

```sh
make school-data     # generate var/school/school.db at scale 1.0, then check it
make school-check    # check the existing database again
```

At any other scale the checker compares the direction of each pattern instead of the exact
values (the reduced-scale test in `backend/tests/test_school_data.py` relies on this).

## Definitions every value depends on

These are the rules a question engine must apply to reproduce the numbers.

| Term | Definition |
|---|---|
| Graded registration | A `final_grades` row in a letter-graded course (`courses.grade_mode = 'standard'`) whose grade is a letter (A to F) or W. Pass/no pass courses, I, P, and NP are never in a DFW denominator. |
| DFW rate | Graded registrations with grade D+, D, F, or W, divided by all graded registrations. |
| Students in a major | Each student counted once, in the program on their latest `student_term_records` row (`program_code` of the row with the greatest `term_code`). Their cumulative GPA is that row's `cumulative_gpa`. Students whose cumulative GPA is null are left out. |
| Required course of a major | Any `program_requirements` row for the major's program, whether core, major, or support. |
| Continuing student in a term | A student with at least one `section_registrations` row in the term whose `students.entry_term` is earlier than the term. New entrants of that term are not continuing. |
| Headcount of a major in a term | `student_term_records` rows for the term with that `program_code`. |
| Withdrawal rate by modality | W grades divided by graded registrations, by `sections.modality`, comparing `online` with `in_person` (hybrid sections are left out). |
| Graduate | A student with a `student_academic_programs` row of status `graduated` (each graduate has exactly one). Their major is that program's; their final GPA is `cumulative_gpa` on their `student_term_records` row for the graduation term (`end_term`); their graduation date is that term's `academic_periods.end_date`. |
| Data end | The latest `academic_periods.end_date`, 2026-05-08 (the last day of Spring 2026). |
| Survey-eligible graduate | A bachelor's graduate whose graduation date plus 183 days (SQLite `date(end_date, '+183 days')`) is on or before the data end: the classes through Summer 2025. |
| Knowledge rate | `first_destination` rows (respondents) over survey-eligible graduates. |
| Starting salary | `first_destination.starting_salary`, present only for `employed_full_time` respondents who reported one. Medians are exact here; Explore shows them rounded to the nearest $500. A major competes with at least 30 salaries. |
| GPA band | Final GPA 2.00 to 2.49, 2.50 to 2.99, 3.00 to 3.49, 3.50 to 4.00. |
| Employed | Outcome `employed_full_time` or `employed_part_time`, over respondents. |
| Graduate school within a year | A bachelor's graduate followed for a full year (graduation date plus one year, SQLite `'+1 year'`, on or before the data end: the classes through Fall 2024) with a `graduate_enrollment` row beginning on or before graduation date plus one year. |
| Medical school acceptance | `accepted` over `medical_school_applications` rows (one per applicant). GPA bands are on the applicant's final GPA. |
| Giving participation | Graduates with at least one `alumni_gifts` row, over all graduates. A major competes with at least 100 graduates. |

Term codes are Banner style: `202110` is Fall 2020, `202120` Spring 2021, `202130`
Summer 2021, and so on to `202620` Spring 2026.

## The planted facts at scale 1.0

**1. The major with the lowest average cumulative GPA is Mechanical Engineering.** Its
1,242 students average 2.663, against 2.885 for the next lowest (Electrical Engineering),
a margin of 0.222. The average over all students is 3.019. Only majors with at least 20
students compete.

**2. Within Mechanical Engineering, the historically hardest required course is MEEN 3310
Thermodynamics I.** Its DFW rate is 39.6 % (196 of 495 graded registrations) over 15
sections in 11 terms. The next hardest required course is MEEN 3350 Manufacturing Processes
at 30.9 %. Only courses with at least 8 sections in at least 4 terms compete. It is a
60-seat engineering lecture, one or two sections a term.

**3. Two instructors have taught Thermodynamics I.** I-0001 (Alicia Shelby, fictional,
Professor) taught the most sections, 10 sections in 8 terms with a DFW rate of 52.0 % (169
of 325). I-0002 (Anthony Jennings, fictional, Associate Professor) taught 5 sections, in
Spring 2022, Spring 2024, and Spring 2025, with a DFW rate of 15.9 % (27 of 170). The
margin on sections taught is 10 to 5.

**4. Organic Chemistry I (CHEM 2323) dropped sharply after an instructor change.** I-0003
(Naomi Faraday, fictional) taught all 8 sections before Fall 2023, the last in Spring 2023,
and left after that term (`leave_term` `202330`, the first term she was no longer employed).
Her DFW rate was 40.4 % (320 of 792). I-0004 (Chloe Merriweather, fictional) was hired for
Fall 2023 (`hire_term` `202410`) and taught all 9 sections since, with a DFW rate of 11.4 %
(97 of 852). It is a 150-seat lecture, one or two sections a term.

**5. College Algebra (MATH 1314) has a large equity gap.** First-generation students have a
DFW rate of 40.9 % (3,587 of 8,777) against 15.8 % (2,539 of 16,101) for
continuing-generation students, a gap of 25.1 points. It is the largest first-generation gap
of any 1000-level course with at least 100 graded registrations in each group. The next
largest is PHYS 1401 at 3.6 points.

**6. Continuing spring registration fell 4.8 %.** 13,541 continuing students registered for
Spring 2026 (`202620`) against 14,224 for Spring 2025 (`202520`), and 13541 / 14224 − 1 =
−4.80 %, which rounds to −4.8 %. This matches the story in the cabinet's fixture
(`data/fixture.json`, metric M1), which compares Spring 2027 with Spring 2026 at the same
point in registration. The school database ends one year earlier and counts census
registrations, so the two are consistent in direction and size, not the same students.

**7. Computer Science grew fastest.** Its headcount went from 355 in Fall 2020 (`202110`) to
708 in Fall 2025 (`202610`), growth of 99.4 %. The next fastest is Software Development at
46.6 %. Only majors with at least 40 students in Fall 2020 compete.

**8. Online sections had a much higher withdrawal rate in Spring 2021.** In `202120` the W
rate was 15.8 % online (3,866 of 24,445) against 4.2 % in person (989 of 23,705), a gap of
11.6 points. That is the largest online over in-person gap of any fall or spring term. The
next largest is Fall 2025 (`202610`) at 2.4 points.

**9. Computer Science has the highest median starting salary.** $77,500 over 185 salaries,
against $72,000 for Software Development; Theatre is lowest at $32,500. The median over all
5,515 reported salaries is $46,000. 9,163 of 14,296 survey-eligible bachelor's graduates
answered the first-destination survey, a knowledge rate of 64.1 %.

**10. Starting salary rises with final GPA, modestly.** Medians by band: $41,250 (2.00 to
2.49, 342 salaries), $43,500 (2.50 to 2.99), $46,500 (3.00 to 3.49), $48,000 (3.50 to 4.00,
1,212 salaries). Part of the rise is the mix of majors in each band.

**11. 77.0 % of survey respondents are employed** (7,060 of 9,163, full or part time); in
Nursing 85.5 % (585 of 684).

**12. 16.8 % of bachelor's graduates enrolled in graduate or professional school within a
year** (2,099 of 12,498 followed for a full year).

**13. Medical school acceptance is 47.8 % of applicants** (98 of 205). Applicants with a
final GPA of 3.50 or more: 62.7 % (52 of 83); below 3.50: 37.7 % (46 of 122). Biology:
42.7 % (44 of 103).

**14. 10.4 % of alumni have given back** (1,867 donors of 17,895 graduates; 3,565 gifts,
$422,565). Christian Ministry gives most: 21.2 % of 293 graduates, then Theology at
20.0 %. Athletes 13.0 %, non-athletes 10.2 %.

## The original tables are unchanged

Graduate outcomes (facts 9 to 14) live in four tables added after the first eight facts
were planted, drawn from their own random stream. `original_tables_sha256` below is the
canonical hash over the 24 tables that existed before them, computed the same way as the
whole-database hash. It equals `4df6c637…cb234`, the whole-database hash this file
recorded before the outcome tables existed, so every row of those 24 tables, including
`meta` (the generator version is still 3), is byte for byte what it was. Facts 1 to 8
are therefore unchanged as well, and `check.py` compares both hashes.

## How the generator produces them

- Grades come from one latent score per registration: student ability, a program shift
  (Mechanical Engineering students carry the largest negative shift), course difficulty,
  the instructor's grading effect, a course-and-instructor effect for the planted pairs,
  and noise. The score maps to letter grades by fixed cut points, and low scores become W
  with a fixed probability.
- Thermodynamics I and Organic Chemistry I have planted course-and-instructor effects.
  I-0001 and I-0003 grade harder, I-0002 and I-0004 grade easier. The staffing rule assigns
  I-0002 to Thermodynamics I in three spring terms and I-0001 in every other term, and
  assigns Organic Chemistry I to I-0003 through Spring 2023 and to I-0004 from Fall 2023.
- College Algebra applies an extra penalty to first-generation students in that course only.
- Online sections carry a small extra withdrawal probability, much larger in Spring 2021.
- The share of entering students choosing Computer Science grows about 13 % a year.
- Mechanical Engineering, Biology, and Chemistry have a minimum entering cohort each fall
  (10, 6, and 3 first-time students) so the planted patterns still have students when the
  generator runs at a reduced scale. The floors do not bind at scale 1.0, where 96 to 147
  first-time students enter Mechanical Engineering each fall.
- For Spring 2026 the persistence step admits exactly round(0.952 × the Spring 2025
  continuing count) continuing students, choosing the most likely to persist first.
- Graduate outcomes (`derive_outcomes` in `generate.py`) draw from one stream per
  graduate. Starting salary is a median by major (computing, engineering, and nursing
  highest; education, ministry, the arts, and associate degrees lowest) times 1 + 0.14 ×
  (GPA − 3.2), with lognormal noise, rounded to $500. Medical school application rates
  are highest in Biology, Biomedical Sciences, and Chemistry and fall with GPA;
  acceptance is 76 % at a GPA of 3.70 or more down to 8 % below 3.00. A gift's yearly
  chance grows with years since graduating; Christian Ministry graduates (2.6 times),
  other ministry and theology majors, athletes (1.5 times), and honors graduates
  (1.3 times) give more often.

## Recompute by hand

Each value can be checked with a short SQL query against `var/school/school.db`. Two
examples follow, and `check.py` holds the rest.

```sql
-- 2. DFW rate of Thermodynamics I
SELECT SUM(g.grade IN ('D+','D','F','W')), COUNT(*)
FROM final_grades g JOIN section_registrations r USING (registration_id)
JOIN sections s USING (section_id)
WHERE s.course_id = 'MEEN 3310'
  AND g.grade IN ('A','A-','B+','B','B-','C+','C','C-','D+','D','F','W');
-- 196 | 495

-- 6. Continuing registration, Spring 2026 and Spring 2025
SELECT r.term_code, COUNT(DISTINCT r.student_id)
FROM section_registrations r JOIN students st USING (student_id)
WHERE r.term_code IN ('202520', '202620') AND st.entry_term < r.term_code
GROUP BY r.term_code;
-- 202520 | 14224
-- 202620 | 13541
```

## Expected values

`check.py` parses this block. Regenerate it only by changing the generator on purpose, then
paste the `planted` section, `canonical_sha256`, and `original_tables_sha256` of
`check.py --json` here.

<!-- planted-values -->
```json
{
  "lowest_gpa_major": {
    "major": "MEEN",
    "name": "Mechanical Engineering",
    "students": 1242,
    "avg_cumulative_gpa": 2.663,
    "next_lowest_major": "ELEN",
    "next_lowest_avg": 2.885,
    "margin": 0.222,
    "all_students_avg": 3.019
  },
  "hardest_required_course": {
    "major": "MEEN",
    "course": "MEEN 3310",
    "title": "Thermodynamics I",
    "sections": 15,
    "terms": 11,
    "dfw": 196,
    "graded": 495,
    "dfw_rate_pct": 39.6,
    "next_course": "MEEN 3350",
    "next_dfw_rate_pct": 30.9
  },
  "course_instructors": {
    "course": "MEEN 3310",
    "instructors": [
      {
        "instructor_id": "I-0001",
        "name": "Alicia Shelby",
        "sections": 10,
        "terms": 8,
        "dfw": 169,
        "graded": 325,
        "dfw_rate_pct": 52.0
      },
      {
        "instructor_id": "I-0002",
        "name": "Anthony Jennings",
        "sections": 5,
        "terms": 3,
        "dfw": 27,
        "graded": 170,
        "dfw_rate_pct": 15.9
      }
    ]
  },
  "instructor_change": {
    "course": "CHEM 2323",
    "change_term": "202410",
    "before_instructors": "I-0003",
    "before_sections": 8,
    "before_dfw": 320,
    "before_graded": 792,
    "before_dfw_rate_pct": 40.4,
    "after_instructors": "I-0004",
    "after_sections": 9,
    "after_dfw": 97,
    "after_graded": 852,
    "after_dfw_rate_pct": 11.4
  },
  "equity_gap": {
    "course": "MATH 1314",
    "first_gen_dfw": 3587,
    "first_gen_graded": 8777,
    "first_gen_dfw_rate_pct": 40.9,
    "continuing_gen_dfw": 2539,
    "continuing_gen_graded": 16101,
    "continuing_gen_dfw_rate_pct": 15.8,
    "gap_points": 25.1,
    "largest_gap_course_1000_level": "MATH 1314",
    "second_largest_gap_course": "PHYS 1401",
    "second_largest_gap_points": 3.6
  },
  "spring_registration": {
    "term": "202620",
    "prior_term": "202520",
    "continuing_registered": 13541,
    "prior_continuing_registered": 14224,
    "change_pct": -4.8
  },
  "fastest_growing_major": {
    "major": "CSCI",
    "name": "Computer Science",
    "fall_2020": 355,
    "fall_2025": 708,
    "growth_pct": 99.4,
    "next_major": "SWDV",
    "next_growth_pct": 46.6
  },
  "online_withdrawals": {
    "term": "202120",
    "online_w": 3866,
    "online_graded": 24445,
    "online_w_rate_pct": 15.8,
    "in_person_w": 989,
    "in_person_graded": 23705,
    "in_person_w_rate_pct": 4.2,
    "gap_points": 11.6,
    "next_term": "202610",
    "next_gap_points": 2.4
  },
  "starting_salary": {
    "eligible_graduates": 14296,
    "respondents": 9163,
    "knowledge_rate_pct": 64.1,
    "salary_reporters": 5515,
    "median_all": 46000,
    "highest_major": "CSCI",
    "highest_name": "Computer Science",
    "highest_median": 77500,
    "highest_reporters": 185,
    "next_major": "SWDV",
    "next_median": 72000,
    "lowest_major": "THEA",
    "lowest_median": 32500
  },
  "salary_by_gpa_band": {
    "2.00-2.49": {
      "reporters": 342,
      "median": 41250
    },
    "2.50-2.99": {
      "reporters": 1553,
      "median": 43500
    },
    "3.00-3.49": {
      "reporters": 2408,
      "median": 46500
    },
    "3.50-4.00": {
      "reporters": 1212,
      "median": 48000
    }
  },
  "employment": {
    "respondents": 9163,
    "employed": 7060,
    "employment_rate_pct": 77.0,
    "nursing_respondents": 684,
    "nursing_employed": 585,
    "nursing_employment_rate_pct": 85.5
  },
  "graduate_school": {
    "tracked_graduates": 12498,
    "enrolled_within_1yr": 2099,
    "rate_pct": 16.8
  },
  "medical_school": {
    "applicants": 205,
    "accepted": 98,
    "rate_pct": 47.8,
    "gpa_3_50_up": {
      "applicants": 83,
      "accepted": 52,
      "rate_pct": 62.7
    },
    "gpa_below_3_50": {
      "applicants": 122,
      "accepted": 46,
      "rate_pct": 37.7
    },
    "biology": {
      "applicants": 103,
      "accepted": 44,
      "rate_pct": 42.7
    }
  },
  "alumni_giving": {
    "alumni": 17895,
    "donors": 1867,
    "participation_pct": 10.4,
    "gifts": 3565,
    "dollars": 422565,
    "highest_major": "MINS",
    "highest_name": "Christian Ministry",
    "highest_alumni": 293,
    "highest_participation_pct": 21.2,
    "next_major": "THEO",
    "next_participation_pct": 20.0,
    "athletes": 1606,
    "athletes_participation_pct": 13.0,
    "non_athletes_participation_pct": 10.2
  },
  "canonical_sha256": "18e09671427b075f8693780c471b51e0dae6d14ebd4b7708febeb3021fef430c",
  "original_tables_sha256": "4df6c6378721b0c1cb487ee51528a580f24626f363f9da2ab6df11d43d0cb234"
}
```
