# Explore: any reasonable question, verified over HTTP

We asked a throwaway CampusLens server these questions over HTTP on 2026-10-06. The server ran this branch on port 8981 with its own temporary users database and its own copy of the school database (`make school-data`, scale 1.0, seed 20261005), the replay provider (rule planner, template answers, no model), and the ask rate limit raised for the run. Each question went to `POST /explore/stream`, signed in through `POST /auth/login`. The answers below are the `done` event's sentences, copied as returned. Every number in them is a cell of the step tables. The server was stopped afterwards.

## Seventeen questions as an executive

**1. what majors have teh highest drop out rate**

Plan: measure_by_group

> Public Health has the highest dropout rate: 29.9% (26 of 87 students), against 17.8% overall.
>
> Next is Mechanical Engineering at 27.3%.
>
> Dropping out here means leaving without a degree, missing the following fall and spring terms, and not enrolling at another college.

**2. What is first-year retention by first-generation status?**

Plan: measure_by_group

> Continuing-generation students have a first-year retention rate of 76.4% (1,648 of 2,158 entering students), against 70.8% for first-generation students (74.7% overall).
>
> First-year retention here is the share of fall entrants enrolled again the next fall.

**3. Graduation rate for Pell students by college**

Plan: measure_by_group

> College of Education has the lowest 6-year graduation rate for Pell recipients: 33.3% (8 of 24 entering students), against 43.8% overall.
>
> Next is College of Business at 34.1%.
>
> It follows the first entering class in the records, the only one they cover for the whole window.

**4. How many international students are in Nursing?**

Plan: measure_by_group

> That figure is withheld: the group is too small to show without risking identifying someone.

**5. Average GPA of athletes vs non-athletes**

Plan: measure_by_group

> Athletes have an average cumulative GPA of 3.06 (545 students with a GPA), against 3.00 for non-athletes (3.00 overall).

**6. What is the 4-year graduation rate by major?**

Plan: measure_by_group

> Theology has the lowest 4-year graduation rate: 0.0% (0 of 18 entering students), against 28.5% overall.
>
> Next is Electrical Engineering at 6.2%.
>
> It counts first-time fall entrants who graduated within the window shown.

**7. Do students who live on campus retain better than commuters?**

Plan: measure_by_group

> Students living on campus have a first-year retention rate of 79.8% (1,867 of 2,340 entering students), against 58.9% for students living off campus (74.7% overall).
>
> First-year retention here is the share of fall entrants enrolled again the next fall.

**8. How long does it take transfer students to graduate?**

Plan: measure_by_group

> First-time students have an average time to degree of 4.26 years (1,755 graduates), against 3.27 for transfer students (4.03 overall).

**9. What is the stop-out rate by class level?**

Plan: measure_by_group

> The stop-out rate was 13.0% for freshmen and 8.1% for seniors, 10.2% overall.
>
> A stop-out here is a student not enrolled in the next fall or spring term without having graduated.

**10. What is the DFW rate for online vs in-person sections?**

Plan: measure_by_group

> Online sections have the highest D, F or withdrawal rate: 12.1% (2,954 of 24,387 graded registrations), against 10.1% overall.
>
> Next is in-person sections at 9.8%.

**11. What is the 6-year graduation rate by race and ethnicity?**

Plan: measure_by_group

> U.S. Nonresident students have the lowest 6-year graduation rate: 33.3% (7 of 21 entering students), against 47.4% overall.
>
> Next is students whose race and ethnicity are unknown at 40.0%.
>
> It follows the first entering class in the records, the only one they cover for the whole window.

**12. Probation rate by gender**

Plan: measure_by_group

> Men have a probation rate of 2.8% (364 of 13,125 student terms), against 2.1% for women (2.4% overall).

**13. What share of students are Pell recipients by college?**

Plan: measure_by_group

> College of Nursing and Health Sciences has the highest Pell share: 36.2% (157 of 434 students), against 33.1% overall in Spring 2026.
>
> Next is College of Arts and Sciences at 35.0%.

**14. Which majors do students switch out of most?**

Plan: measure_by_group

> Information Technology has the highest major-change rate: 12.7% (15 of 118 students), against 7.1% overall.
>
> Next is Health Sciences at 12.4%.

**15. How many students are enrolled by age at entry?**

Plan: measure_by_group

> The headcount was 1,631 for students under 20 at entry and 42 for students 35 and over at entry, 2,133 overall in Spring 2026.

**16. Which major has the highest dropout rate, and what is its hardest class?**

Plan: measure_by_group, dfw_by_course

> Public Health has the highest dropout rate: 29.9% (26 of 87 students), against 17.8% overall.
>
> Dropping out here means leaving without a degree, missing the following fall and spring terms, and not enrolling at another college.
>
> In Public Health, the historically hardest required course is MATH 1314 College Algebra, with a D, F or withdrawal rate of 25.0% (978 of 3,906 graded registrations over 152 sections).
>
> Next is PUBH 3310 Epidemiology at 20.4%.

**17. Which major has the lowest GPA? In that major, what is historically the hardest class, and which instructor has historically taught it?**

Plan: gpa_by_major, dfw_by_course, course_instructors

> Mechanical Engineering has the lowest average cumulative GPA, 2.62 across 250 students.
>
> In Mechanical Engineering, the historically hardest required course is MEEN 3310 Thermodynamics I, with a D, F or withdrawal rate of 41.8% (38 of 91 graded registrations over 10 sections).
>
> I-0001 Alicia Shelby (fictional) has taught it most: 7 sections in 7 terms, with a D, F or withdrawal rate of 56.7%.
>
> I-0002 Anthony Jennings (fictional) taught 3 sections, with a D, F or withdrawal rate of 12.9%.

## The live trace of question 1

The events `POST /explore/stream` sent before the answer, in order:

```
{"type": "planning", "text": "Matching the question to the approved analyses"}
{"type": "understood", "text": "Dropout rate by major"}
{"type": "plan", "steps": ["Dropout rate by major"], "planner": "rules"}
{"type": "reading", "text": "Reading six years of student records (fictional data): 6,225 students, Fall 2020 to Spring 2026"}
{"type": "step", "index": 0, "total": 1, "title": "Dropout rate by major"}
{"type": "writing", "text": "Writing the answer from the tables"}
{"type": "verifying", "checked": 5, "matched": 5, "text": "Checked 5 numbers against the tables"}
```

## The same server as staff

**What has Alicia Shelby taught?**

> Instructor results are shown to the executive and admin only.

Parameters shown: Instructor: an instructor (names are shown to the executive and admin)

**Who has taught MEEN 3310?**

> Instructor results are shown to the executive and admin only, so this shows Thermodynamics I as a whole: 10 sections in 10 terms with a D, F or withdrawal rate of 41.8%.

Parameters shown: Course: MEEN 3310 Thermodynamics I

**what majors have teh highest drop out rate**

> Public Health has the highest dropout rate: 29.9% (26 of 87 students), against 17.8% overall.
>
> Next is Mechanical Engineering at 27.3%.
>
> Dropping out here means leaving without a degree, missing the following fall and spring terms, and not enrolling at another college.

Parameters shown: Measure: dropout rate; Grouped by: major; Ranked: highest first

**Tell me about S 100023**

> Refused: CampusLens answers with totals only, never about a single student, by id or by description.

**How many students saw a c0unselor?**

> Refused: CampusLens does not answer questions about counseling or spiritual care, even as totals.

No instructor name or id appears in any staff response or event, and no student id appears anywhere.
