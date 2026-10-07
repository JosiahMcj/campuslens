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

Term codes are Banner style: `202110` is Fall 2020, `202120` Spring 2021, `202130`
Summer 2021, and so on to `202620` Spring 2026.

## The planted facts at scale 1.0

**1. The major with the lowest average cumulative GPA is Mechanical Engineering.** Its 250
students average 2.623, against 2.925 for the next lowest (Chemistry), a margin of
0.302. The average over all students is 3.001. Only majors with at least 20 students compete.

**2. Within Mechanical Engineering, the historically hardest required course is MEEN 3310
Thermodynamics I.** Its DFW rate is 41.8 % (38 of 91 graded registrations) over 10 sections
in 10 terms. The next hardest required course is MEEN 3350 Manufacturing Processes at 34.1 %.
Only courses with at least 8 sections in at least 4 terms compete. The course is small, as
upper-level engineering courses are, so its 10 sections are all there is.

**3. Two instructors have taught Thermodynamics I.** I-0001 (Alicia Shelby, fictional,
Professor) taught the most sections, 7 sections in 7 terms with a DFW rate of 56.7 % (34 of
60). I-0002 (Anthony Jennings, fictional, Associate Professor) taught 3 sections, in Spring
2022, Spring 2024, and Spring 2025, with a DFW rate of 12.9 % (4 of 31). The margin on
sections taught is 7 to 3.

**4. Organic Chemistry I (CHEM 2323) dropped sharply after an instructor change.** I-0003
(Naomi Faraday, fictional) taught all 7 sections before Fall 2023, the last in Spring 2023,
and left after that term (`leave_term` `202330`, the first term she was no longer employed).
Her DFW rate was 47.3 % (44 of 93). I-0004 (Chloe Merriweather, fictional) was hired for
Fall 2023 (`hire_term` `202410`) and taught all 8 sections since, with a DFW rate of 14.5 %
(17 of 117).

**5. College Algebra (MATH 1314) has a large equity gap.** First-generation students have a
DFW rate of 42.8 % (581 of 1,357) against 15.6 % (397 of 2,549) for continuing-generation
students, a gap of 27.2 points. It is the largest first-generation gap of any 1000-level
course with at least 100 graded registrations in each group. The next largest is EGR 1304
at 7.5 points.

**6. Continuing spring registration fell 4.8 %.** 2,073 continuing students registered for
Spring 2026 (`202620`) against 2,178 for Spring 2025 (`202520`), and 2073 / 2178 − 1 =
−4.82 %, which rounds to −4.8 %. This matches the story in the cabinet's fixture
(`data/fixture.json`, metric M1), which compares Spring 2027 with Spring 2026 at the same
point in registration. The school database ends one year earlier and counts census
registrations, so the two are consistent in direction and size, not the same students.

**7. Computer Science grew fastest.** Its headcount went from 65 in Fall 2020 (`202110`) to
137 in Fall 2025 (`202610`), growth of 110.8 %. The next fastest is Accounting at 53.8 %. Only majors with at least 40 students in Fall 2020 compete.

**8. Online sections had a much higher withdrawal rate in Spring 2021.** In `202120` the W
rate was 16.7 % online (566 of 3,393) against 4.3 % in person (172 of 4,041), a gap of 12.4
points. That is the largest online over in-person gap of any fall or spring term. The next
largest is Spring 2024 (`202420`) at 2.2 points.

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
  generator runs at a reduced scale. The floors do not bind at scale 1.0, where 25 to 37
  first-time students enter Mechanical Engineering each fall.
- For Spring 2026 the persistence step admits exactly round(0.952 × the Spring 2025
  continuing count) continuing students, choosing the most likely to persist first.

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
-- 38 | 91

-- 6. Continuing registration, Spring 2026 and Spring 2025
SELECT r.term_code, COUNT(DISTINCT r.student_id)
FROM section_registrations r JOIN students st USING (student_id)
WHERE r.term_code IN ('202520', '202620') AND st.entry_term < r.term_code
GROUP BY r.term_code;
-- 202520 | 2178
-- 202620 | 2073
```

## Expected values

`check.py` parses this block. Regenerate it only by changing the generator on purpose, then
paste the `planted` section and `canonical_sha256` of `check.py --json` here.

<!-- planted-values -->
```json
{
  "lowest_gpa_major": {
    "major": "MEEN",
    "name": "Mechanical Engineering",
    "students": 250,
    "avg_cumulative_gpa": 2.623,
    "next_lowest_major": "CHEM",
    "next_lowest_avg": 2.925,
    "margin": 0.302,
    "all_students_avg": 3.001
  },
  "hardest_required_course": {
    "major": "MEEN",
    "course": "MEEN 3310",
    "title": "Thermodynamics I",
    "sections": 10,
    "terms": 10,
    "dfw": 38,
    "graded": 91,
    "dfw_rate_pct": 41.8,
    "next_course": "MEEN 3350",
    "next_dfw_rate_pct": 34.1
  },
  "course_instructors": {
    "course": "MEEN 3310",
    "instructors": [
      {
        "instructor_id": "I-0001",
        "name": "Alicia Shelby",
        "sections": 7,
        "terms": 7,
        "dfw": 34,
        "graded": 60,
        "dfw_rate_pct": 56.7
      },
      {
        "instructor_id": "I-0002",
        "name": "Anthony Jennings",
        "sections": 3,
        "terms": 3,
        "dfw": 4,
        "graded": 31,
        "dfw_rate_pct": 12.9
      }
    ]
  },
  "instructor_change": {
    "course": "CHEM 2323",
    "change_term": "202410",
    "before_instructors": "I-0003",
    "before_sections": 7,
    "before_dfw": 44,
    "before_graded": 93,
    "before_dfw_rate_pct": 47.3,
    "after_instructors": "I-0004",
    "after_sections": 8,
    "after_dfw": 17,
    "after_graded": 117,
    "after_dfw_rate_pct": 14.5
  },
  "equity_gap": {
    "course": "MATH 1314",
    "first_gen_dfw": 581,
    "first_gen_graded": 1357,
    "first_gen_dfw_rate_pct": 42.8,
    "continuing_gen_dfw": 397,
    "continuing_gen_graded": 2549,
    "continuing_gen_dfw_rate_pct": 15.6,
    "gap_points": 27.2,
    "largest_gap_course_1000_level": "MATH 1314",
    "second_largest_gap_course": "EGR 1304",
    "second_largest_gap_points": 7.5
  },
  "spring_registration": {
    "term": "202620",
    "prior_term": "202520",
    "continuing_registered": 2073,
    "prior_continuing_registered": 2178,
    "change_pct": -4.8
  },
  "fastest_growing_major": {
    "major": "CSCI",
    "name": "Computer Science",
    "fall_2020": 65,
    "fall_2025": 137,
    "growth_pct": 110.8,
    "next_major": "ACCT",
    "next_growth_pct": 53.8
  },
  "online_withdrawals": {
    "term": "202120",
    "online_w": 566,
    "online_graded": 3393,
    "online_w_rate_pct": 16.7,
    "in_person_w": 172,
    "in_person_graded": 4041,
    "in_person_w_rate_pct": 4.3,
    "gap_points": 12.4,
    "next_term": "202420",
    "next_gap_points": 2.2
  },
  "canonical_sha256": "5463f7014fe01057410da2d69563cdb409edf8254042185ac754211d56a642a4"
}
```
