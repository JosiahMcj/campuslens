# Metric Contracts

We expanded ROADMAP §4 into this document so a reviewer can break the metrics on paper
without opening any code. Every metric below is recomputable by hand from
`data/fixture.json`, the row-ID lists behind every planted number are in
`data/VERIFY.md`, and `data/check_fixture.py` recomputes them independently. We froze
the numbers before any UI existed, so the code can never drift to fit the demo. If
our code and this document disagree, the code is wrong until this document is
amended.

Field paths use the nested groups in the fixture (`SCHEMA.md`), and they are
`profile.*`, `enrollment.*`, `holds[]`, `advising.*`, `comparison.*`, and the three
term objects under `terms.*`, with **no** top-level `term` object. ROADMAP §4 writes
`hold.*`, but the fixture group is a list, so `hold.x` means "field `x` of an element
of `holds`". The group `counseling.*` exists in the fixture only to be refused and is
never read by any metric.

---

## 0. Global rules (apply to every metric)

- For **timezone**, all dates are America/Chicago (`meta.timezone`), ISO
  `YYYY-MM-DD` (`meta.date_format`), and comparisons are calendar dates, not
  timestamps.
- The **as-of date** is the **latest `enrollment.registration_date` in `students`**
  (`terms.current.as_of_rule`), never the wall clock. In this fixture that is
  **2026-11-20**, carried by STU-0001, and the demo never drifts.
- For **the three terms**, `terms.current` is Spring 2027 (`202720`, the term being
  registered for, start 2027-01-11, registration open 2026-11-02, close 2026-12-18).
  The second, `terms.prior_year`, is Spring 2026 (`202620`,
  `prior_year_equivalent_date` 2025-11-20), and the third, `terms.in_session`, is
  Fall 2026 (`202710`, the term in session at the as-of date, start 2026-08-24, end
  2026-12-11). "This term" in M4 means `terms.in_session`.
- For **the exact `--` rule**, a metric's finding carries either a real value or
  `null` with a `reason` string, and the UI renders `--` if and only if the value is
  `null`, with three consequences.
  1. A **zero denominator** always yields `null`, renders `--`, **never** `0 %`.
  2. **`0` is a real value** and renders as `0`, so `--` is never a synonym for
     zero.
  3. Each metric below lists its `null` conditions exhaustively, and any other input
     must produce a number or the metric function is broken.
- **Windows anchor to data timestamps** (the as-of date, the prior-year equivalent
  date, `terms.in_session.start_date`), never to "now".
- **The current in-progress bucket is never aggregated** beyond its
  cumulative-to-as-of definition, and there is no projection, trend, or forecast
  anywhere.
- **Students are people.** Metric output is counts and ratios of people who may need
  support, and no metric produces a score attached to a person.

## 0.1 Example records used in the worked calculations

We chose these **real rows** from `data/fixture.json` (seed `20260924`) out of the ID
lists in `data/VERIFY.md`.

```jsonc
// Current-term students (list `students`)
STU-0001: continuing=true,  registered,   hours=16, registration_date="2026-11-20" (the as-of date),
          holds=[], advising: last="2026-08-31" completed
STU-0120: continuing=true,  not_registered, hours=0, registration_date=null,
          holds=[{financial, 160.73, Bursar, "2026-10-22", resolved=false}],
          advising: last=null, appointment_status="none"
STU-0142: continuing=true,  not_registered,
          holds=[{financial, 1000.00, Bursar, "2026-10-19", resolved=false}],
          advising: last="2026-08-24" completed   // exactly the in-session start
STU-0138: continuing=true,  not_registered,
          holds=[{financial, <1000, Bursar, resolved=true}],      // VERIFY decoy D1
          advising: last appointment completed in spring 2026     // in M4
STU-0145: continuing=true,  not_registered,
          holds=[{academic, 140.83, Registrar, "2026-10-12", resolved=false}]  // decoy D3
STU-0176: continuing=false, not_registered                        // new student; M2 decoy

// Prior-year students (list `prior_year_students`)
PRI-0001: continuing=true, registered, registration_date="2025-11-10"  // ≤ 2025-11-20
PRI-0126: continuing=true, registered, registration_date in 2025-11-21…2025-12-19  // after the equivalent date
PRI-0132: continuing=true, never registered                          // registration_date=null
```

We planted these counts, fixed by the seed and checked by `check_fixture.py`, giving
M1 numerator 119 (STU-0001…STU-0119), M1 denominator 125 (PRI-0001…PRI-0125), M2 = 42
(STU-0120…STU-0161), M3 = 18 (STU-0120…STU-0137), and M4 = 12.

---

## M1. Spring registration vs. same point last year

- The formula is `registered_continuing(as_of) / registered_continuing(prior_year_equivalent_date) − 1`,
  and the source fields are `enrollment.term`, `enrollment.registration_status`,
  `enrollment.registration_date`, `terms.prior_year.prior_year_equivalent_date`, and
  `profile.continuing`.
- The grain is the student and the window is cumulative to the as-of date, with the
  prior-year window anchored to `terms.prior_year.prior_year_equivalent_date`
  (2025-11-20).
- The population matches `check_fixture.py` exactly.
  - Numerator. Rows in `students` with `profile.continuing = true`,
    `registration_status = "registered"`, and `registration_date ≤ as_of`, and every
    registered row satisfies the last clause in this fixture.
  - Denominator. Rows in `prior_year_students` with `profile.continuing = true`,
    `registration_status = "registered"`, and `registration_date` non-null and
    `≤ 2025-11-20`.
- The `--` rule is `null` (renders `--`) exactly when the prior-year count is 0,
  never `0 %`, and a computed change of exactly zero renders `0.0 %`. The planted
  value is −4.8 %.

| Row | In numerator? | In denominator? | Why |
|---|---|---|---|
| STU-0001 | yes | - | continuing ∧ registered ∧ 2026-11-20 ≤ as-of 2026-11-20 |
| STU-0120 | no | - | not registered |
| STU-0176 | no | - | `continuing = false`, excluded entirely |
| PRI-0001 | - | yes | registered 2025-11-10 ≤ 2025-11-20 |
| PRI-0126 | - | no | registered after 2025-11-20 (decoy) |
| PRI-0132 | - | no | never registered |

Our hand count over the full fixture gives numerator = **119** (STU-0001…STU-0119) and
denominator = **125** (PRI-0001…PRI-0125).

```
119 / 125 − 1 = 0.952 − 1 = −0.048 = −4.8 %   (exactly −6/125, report to one decimal)
```

We keep one break test for the reviewer here, and it is that an empty
`prior_year_students` must render `--` while `0.0 %` or `−100.0 %` breaks the
contract.

## M2. Continuing students not yet registered

- The formula is `count(profile.continuing = true ∧ enrollment.registration_status ≠ "registered")`,
  and the source fields are `profile.continuing` and `enrollment.registration_status`.
- The grain is the student and the window is the as-of date.
- The `--` rule is `null` exactly when `students` is empty. If the fixture has
  students and every continuing student is registered, the value is **0**, rendered
  `0`. The planted value is 42.

| Row | Counts? | Why |
|---|---|---|
| STU-0120 | yes | continuing ∧ not_registered |
| STU-0142 | yes | continuing ∧ not_registered |
| STU-0176 | no | not continuing (new student, also unregistered, decoy) |
| STU-0001 | no | registered |

Our hand count over the full fixture gives **42** rows, STU-0120…STU-0161, and these
42 IDs are the M2 population that M3 and M4 filter and what the evidence drawer
lists. STU-0176…STU-0185 are new and unregistered, excluded by the `continuing`
filter.

## M3. Of M2, financial hold under $1,000

- The formula is `count(row ∈ M2 ∧ ∃ h ∈ holds: h.category = "financial" ∧ h.resolved = false ∧ h.amount < 1000)`,
  and the source fields are `holds[].category`, `holds[].amount`, and
  `holds[].resolved`.
- The grain is the student, and a student counts once even with two qualifying holds
  (in this fixture no student carries more than one hold), and the window is the
  as-of date.
- The boundary is strict, `amount < 1000`, and a hold of exactly **$1,000.00 does
  not count** (STU-0142 carries one as a planted decoy).
- The `--` rule is `null` exactly when the M2 population is empty or the fixture is
  empty. If M2 is nonempty and no one qualifies, the value is **0**, rendered `0`.
  The planted value is 18.

| Row | Counts? | Why |
|---|---|---|
| STU-0120 | yes | unresolved financial hold, 160.73 < 1000 |
| STU-0142 | no | 1000.00 is not < 1000 (strict boundary, planted decoy D2) |
| STU-0138 | no | the financial hold is `resolved: true` (planted decoy D1) |
| STU-0145 | no | hold category is `academic`, not `financial` (planted decoy D3) |

Our hand count over the full fixture gives **18** of the 42 (STU-0120…STU-0137). For
a break test, flip STU-0142's amount to 999.99 and M3 must rise by one, and at
1000.00 it must not.

## M4. Of M2, no advising appointment this term

- **"This term" = the term in session at the as-of date**, `terms.in_session` (Fall
  2026, start 2026-08-24), not the spring term being registered for.
- The formula is `count(row ∈ M2 ∧ (advising.last_appointment_date is null ∨
  last_appointment_date < terms.in_session.start_date))`, and an appointment
  **exactly on 2026-08-24 counts as this term** (planted decoy D5, STU-0142 and
  STU-0143).
- The source fields are `advising.last_appointment_date`,
  `advising.appointment_status`, and `terms.in_session.start_date`.
- The grain is the student and the window is `terms.in_session`.
- The `appointment_status` vocabulary in the fixture is `"completed"` / `"none"`,
  with `"none"` exactly when `last_appointment_date` is `null`. The contract's
  cancelled-appointment rule, that a cancelled appointment does not count as
  advising received, is **not present in the fixture, and the rule applies if
  `"cancelled"` ever appears**. Nothing in M4's current computation depends on it,
  and the field `last_appointment_date` alone decides, per the formula above.
- The `--` rule is `null` exactly when the M2 population is empty, the fixture is
  empty, or `terms.in_session.start_date` is missing, and otherwise the count is
  real, including **0**. The planted value is 12. All three worked rows are in M2,
  and the in-session start is 2026-08-24.

| Row | Counts? | Why |
|---|---|---|
| STU-0120 | yes | `last_appointment_date = null`, never seen an advisor |
| STU-0138 | yes | last completed appointment in spring 2026 < 2026-08-24 |
| STU-0142 | no | appointment on 2026-08-24, the first day of the term, counts as this term (decoy D5) |

Our hand count over the full fixture gives **12** of the 42 (STU-0120…STU-0127 with
null or pre-term appointments, plus STU-0138…STU-0141 with spring-2026
appointments).

## M5. Unresolved holds by responsible office

- The formula, for each `responsible_office`, is `count(h ∈ holds over all
  current-term students where h.resolved = false)`, grouped by office, and the
  source fields are `holds[].category`, `holds[].responsible_office`, and
  `holds[].resolved`.
- The grain is the office and the population is **all current-term students**
  (`students`), not just M2. The unit is unresolved *hold records*, so a student
  with two unresolved holds at the same office would contribute 2, and in this
  fixture no student has more than one hold.
- The window is the as-of date, and with no unresolved holds the no-data rule renders
  an **empty table with the line "No unresolved holds"**, not `--`, because this
  metric is a table, not a scalar. An empty fixture renders the same empty table.
- The fixture-defined value is `Bursar 24, Registrar 2, Library 1, Student Life 1`,
  28 unresolved hold records in total.

We work the calculation on real rows.

| Hold | Office tally | Why |
|---|---|---|
| STU-0120 financial $160.73 unresolved | Bursar +1 | unresolved |
| STU-0142 financial $1,000.00 unresolved | Bursar +1 | unresolved. The $1,000 boundary is M3's, not M5's |
| STU-0138 financial hold resolved | +0 | `resolved: true` |
| STU-0145 academic unresolved | Registrar +1 | unresolved |

In our arithmetic over the full fixture, the 18 M3 holds (Bursar), the 3 D2
over-boundary financial holds (STU-0142…0144, Bursar), and the 3 D4 holds on
registered students (STU-0007, STU-0034, STU-0088, Bursar) make 24 Bursar. Add
STU-0145 and STU-0148 (Registrar), STU-0146 (Library), and STU-0147 (Student Life).

```
Bursar 18 + 3 + 3 = 24,  Registrar 2,  Library 1,  Student Life 1,  total 28
```

M3 and M5 answer different questions. M3 counts *students* in M2 with a small
unresolved financial hold. M5 counts *hold records* for everyone, by office. A
number that matches between them is coincidence, not a check.

## M6. Days until registration closes

- The formula is `terms.current.registration_close_date − as_of`, in whole calendar
  days, and the source field is `terms.current.registration_close_date`.
- The grain is the term (one value) and the window is the as-of date.
- The `--` rule is `null` exactly when `terms.current.registration_close_date` is
  missing. If the close date is before the as-of date, the value is **0** and the
  finding carries `closed: true`, never a negative number. The UI says
  "registration has closed" in that case. The fixture-defined value is **28 days**.

We work the calculation at term grain across three scenarios.

| Scenario | Computation | Renders |
|---|---|---|
| this fixture, close 2026-12-18, as-of 2026-11-20 | 2026-12-18 − 2026-11-20 = 28 | `28` |
| hypothetical, close 2026-11-18, as-of 2026-11-20 | negative → clamp per rule | `0`, "registration has closed" |
| hypothetical, close date absent | value is `null` | `--` |

## M7. Registered credit hours vs. prior year *(optional, first cut after Day 9 polish)*

- The formula is `sum(enrollment.registered_credit_hours over the M1 numerator
  population) / sum(registered_credit_hours over the M1 denominator population) − 1`,
  and the source field is `enrollment.registered_credit_hours`.
- The grain is the term (one ratio). The window is the as-of date and the prior-year
  equivalent, with the same populations as M1 so M1 and M7 stay comparable.
- The `--` rule is `null` exactly when the prior-year sum is 0, never `0 %`, and a
  computed change of zero renders `0.0 %`. The field `comparison.baseline` (a
  term-code string such as `"202620"`) only names the baseline term, and the metric
  computes from rows, never from it. The fixture-defined value is **−2.7 %**.

We work the calculation on real rows.

| Row | Contribution | Why |
|---|---|---|
| STU-0001 | +16 h to current sum | in the M1 numerator population |
| STU-0120 | +0 | not registered, so `registered_credit_hours` is 0 |
| STU-0176 | excluded | not continuing, outside M1's population |
| PRI-0001 | +16 h to prior-year sum | in the M1 denominator population |

In our arithmetic over the full fixture, the 119 registered continuing students carry
**1,821** hours, and the 125 prior-year students carry **1,872**.

```
1821 / 1872 − 1 = −17/624 ≈ −0.02724 = −2.7 %   (to one decimal)
```

## M8. Students with one or more support indicators

We added M8 on 2026-09-26. It is a count of people who may need support, computed
in code by named rules (`backend/src/cabinet/indicators.py`). It is not a model,
not a probability, and never a per-person total.

- The formula is `count(student ∈ students ∧ at least one support indicator rule
  (I1 to I4) fires for that student)`, and the source fields are the union of the
  fields the rules read, listed per rule below.
- The grain is the student. The window is as-of, with the same term anchors as §0
  (the derived as-of date, `terms.in_session.start_date`, and
  `terms.current.registration_close_date`).
- There is **no combination**. A student "has indicators" when at least one rule
  fires. There is no weighting, no sum across rules, no threshold beyond each
  rule's own definition, and no ordering by severity. The per-student map of fired
  rules exists only for the evidence drawer. The AI employees receive the
  aggregate count and per-rule counts with all row-level detail stripped (ROADMAP
  §3 layer 4).
- The `--` rule is `null` exactly when `students` is empty. A fixture with
  students and no fires is **0**, rendered `0`. The planted value is 22.

The registry is data, a tuple of rule objects a reviewer can read in one screen.
Each rule carries a one-sentence reason that names its fields, and the evidence
drawer shows that reason per pseudonymous id.

| Id | Rule | Fields read | Fixture count |
|---|---|---|---|
| I1 | continuing ∧ not registered ∧ ∃ unresolved financial hold with `amount < 1000` (the M3 boundary, strict) | `profile.continuing`, `enrollment.registration_status`, `holds[].category`, `holds[].resolved`, `holds[].amount` | 18 |
| I2 | continuing ∧ not registered ∧ no advising appointment in the term in session (the M4 test, `last_appointment_date` null or before `terms.in_session.start_date`, and on the start date counts as this term. When `terms.in_session.start_date` is missing, I2 fires only for students who never saw an advisor, because a dated appointment cannot be compared) | `profile.continuing`, `enrollment.registration_status`, `advising.last_appointment_date`, `terms.in_session.start_date` | 12 |
| I3 | two or more unresolved holds at different offices (any student, registered or not) | `holds[].resolved`, `holds[].responsible_office` | 0 |
| I4 | continuing ∧ not registered ∧ `0 ≤ registration_close_date − as_of ≤ 14` days (an already closed registration does not fire) | `profile.continuing`, `enrollment.registration_status`, `terms.current.registration_close_date` | 0 |

We work the calculation on the hand counts above. I1's population is M3's (18
rows, STU-0120…STU-0137), and I2's is M4's (12 rows, STU-0120…STU-0127 and
STU-0138…STU-0141). The overlap is 8 rows (STU-0120…STU-0127). No student carries
more than one hold, so I3 fires for no one. Registration closes 2026-12-18, which
is 28 days after the as-of date and outside I4's 14-day window, so I4 fires for
no one.

```
18 + 12 − 8 = 22
```

For a break test, flip STU-0142's amount to 999.99 and I1 and M8 both rise by
one. Move the close date to 2026-12-04 and I4 fires for all 42 M2 rows, so M8
becomes 42. `data/VERIFY.md` lists the 22 ids, and `data/check_fixture.py`
recomputes M8 and each rule's count independently.

---

## How to break these contracts on paper

We hand-counted every planted number before any UI existed, and we expect the reviewer
to redo that count on paper.

1. Open `data/VERIFY.md`, which lists the row IDs behind each planted number.
2. Count by hand in `data/fixture.json`, where the planted counts are 119, 125, 42,
   18, 12, 22, and any mismatch breaks the fixture contract. `python3
   data/check_fixture.py` recomputes the same counts and ID lists independently.
3. Recompute M1 as `119 / 125 − 1`. If it is not exactly −4.8 %, the planted counts
   are wrong, not the arithmetic.
4. Attack the planted boundaries. STU-0142's $1,000.00 hold is excluded by M3, and
   M4 excludes it for an appointment exactly on 2026-08-24. PRI-0126…PRI-0131
   registered after the equivalent date, so the M1 denominator excludes them, and
   STU-0176…STU-0185 are new unregistered students, so M2 excludes them. An empty
   fixture must hit every metric's contracted `--` or empty-table rule, and a zero
   must render `0`, not `--`.
5. Confirm no metric reads `counseling.counseling_notes` or
   `counseling.chaplain_contact`. Those fields exist only to be refused (ROADMAP
   §5).
