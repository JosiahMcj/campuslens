# Roles, department accounts and the inbox

Depending on who signs in, CampusLens shows different pages. The president
sees everything, each department sees its own pages, and IT sees the accounts
and the system but nothing about students. People can send each other an
alert ("look at this before the meeting"), and it lands in the other person's
inbox.

The server enforces every rule here. The UI hides what a role cannot use, and
the API refuses it as well. Every route is listed in `ROUTE_ROLES` /
`ROUTE_ROLE_PREFIXES` in `backend/src/cabinet/security.py`. A refusal is a
403 and a `data.refused` audit event. The student directory and the student
ids behind a figure stay with `ROW_ROLES` (the executive and the admin). No
new role widens that.

## Who sees what

| Persona (sign-in) | Role | Sidebar pages | Explore questions | Student ids behind a figure |
|---|---|---|---|---|
| President (`president@`) | `executive` | Inbox, every department overview, the full briefing, key figures, evidence, staff actions, decision, data access, Financial Aid review, find a student, audit log, sign-in activity | yes, including instructor-level rows | yes |
| IT (`it@`) | `it` | Inbox, Accounts, Sign-in activity, Connections, Audit log | **no** | no (no findings at all) |
| Finance (`finance@`) | `finance` | Inbox, Finance overview, Data (University budget) | yes, aggregate only (home questions are budget questions) | no |
| Student Accounts (`studentaccounts@`) | `studentaccounts` | Inbox, Student Accounts overview, Data (Student finances) | yes, aggregate only | no |
| Financial Aid (`aid@`) | `aid` | Inbox, the briefing pages (read only), Financial Aid review, Financial Aid overview | no (unchanged) | no |
| Registrar (`registrar@`) | `registrar` | Inbox, Registrar overview | yes, aggregate only | no |
| Student Life (`studentlife@`) | `studentlife` | Inbox, Student Life overview | yes, aggregate only | no |
| Admissions (`admissions@`) | `admissions` | Inbox, Admissions overview, Data | yes, aggregate only | no |
| Advising and Student Success (`advising@`) | `advising` | Inbox, Advising overview, Data | yes, aggregate only | no |
| Academic Affairs (`provost@`) | `provost` | Inbox, Academic Affairs overview, Data | yes, aggregate only | no |
| Institutional Research (`ir@`) | `ir` | Inbox, Institutional Research overview, Data | yes, aggregate only | no |
| Career Services (`careers@`) | `careers` | Inbox, Career Services overview, Data | yes, aggregate only | no |
| Advancement (`advancement@`) | `advancement` | Inbox, Advancement overview, Data | yes, aggregate only | no |
| International Student Services (`international@`) | `international` | Inbox, International overview, Data | yes, aggregate only | no |
| Athletics (`athletics@`) | `athletics` | Inbox, Athletics overview, Data | yes, aggregate only | no |
| Staff (`staff@`) | `staff` | Inbox and the briefing pages, as before | yes, aggregate only | no |
| Reviewer (`reviewer@`) | `reviewer` | Inbox, the briefing pages and the audit log, as before | yes, aggregate only | no |
| Platform admin | `admin` | Everything, as before, plus the inbox and every overview; Institution settings | yes | yes |

All demonstration sign-ins are `@demo.test`. More detail:

- **Explore.** The president and the admin may ask anything and get
  instructor-level rows. The department accounts, staff and reviewers get
  aggregate answers, and instructor rows are withheld by the existing rule
  (`INSTRUCTOR_ROLES` in `cabinet/explore/catalog.py`). Financial Aid keeps
  its earlier rule: no Explore, because its work is the review queue. IT
  never asks. Its work is accounts and the system.
- **Department accounts** read the briefing's aggregate figures, never the
  student ids behind them (`GET /findings` strips rows for every role outside
  `ROW_ROLES`). Their sidebar shows only their own pages, and their home
  screen greets them by office with three questions for that office. Each of
  those questions was checked to answer from the school data with the rule
  planner, so they work without the model.
- **IT** has its own workspace. The UI never requests a finding, the
  briefing, a decision or Explore for IT, and the API refuses IT all of them
  (`READ_ROLES` excludes `it`). IT may add, enable, disable and re-role the
  department and staff accounts (`IT_MANAGED_ROLES`: finance, studentaccounts,
  registrar, studentlife, the other department accounts, staff). Any action on an admin, executive, IT, aid or
  reviewer account, or any grant of those roles, is a logged 403: the aid
  office and the reviewer read the aid queue's per-student rows. **IT never
  sees a password.** An account IT creates answers without its one-time
  password, and an administrator issues the first one with
  `make reset-password EMAIL=…` (printed once; it also ends the account's
  sessions). Otherwise IT could create an account and sign in as it. IT
  also reads the audit log, the outside connections and sign-in activity.
- **The president** sees every page and every panel. Two things still need
  another person, as before: Institution settings (the admin manages the
  datasets and the office mailboxes) and the Send step of an office message
  (staff or the admin send; the president decides).

## Department overviews

`GET /departments/overview?department=<id>` (`cabinet/departments.py`, and
`cabinet/departments_more.py` for the later departments). The overview is
computed in code from the same read-only school data Explore uses
(`CABINET_SCHOOL_DB`). Each overview has four headline figures and tables for
the current term. The president and admin open a directory of every
department (one card each); a department account opens its own.

**Finance and Student Accounts are separate** (owner direction 2026-10-07):
all student money belongs to Student Accounts, all campus budgeting to
Finance, and neither role reads the other's figures, overview, Data
dashboard or attachments.

- **Finance** (`finance`, the CFO's office). The university's own books for
  the current fiscal year, July to June: spending against budget by
  division, revenue by source against budget, net tuition and the tuition
  discount rate by fiscal year. Institutional figures, not student data, so
  no small-group rule applies. Finance reads the University budget
  dashboard on the Data page and **not** Student finances (default; the
  owner wants them separate). Its Explore stays aggregate and totals-only
  like every department, with budget questions as its home examples.
- **Student Accounts** (`studentaccounts`, the Bursar). Students with an
  open account hold, the open balance, the average balance per student,
  holds placed this term, open holds by office, students by balance owed,
  and a Student accounts section (the billing ledger: past-due balances and
  aging, the on-time payment rate and payment-plan share by college). It
  reads the Student finances dashboard (and may split charts by Pell
  status). It never sees the university budget: the budget Explore
  analyses, the budget dashboard and the budget overview figures are 403.
- **Registrar.** Students enrolled this term, the share enrolled full time,
  open Registrar holds, students not in good standing, enrollment by class
  level, and academic standing.
- **Student Life.** Students living on campus, the share who are first
  generation, students who stopped out this term, the advising no-show rate,
  advising appointments by type, and housing by load.
- **Admissions.** The entering class (first-time, transfer, share from
  outside the state); entering classes by admit type, residency and college.
- **Financial Aid** (`aid`). Pell recipients enrolled and their share, Pell
  first-generation students, Pell recipients with an open account hold;
  Pell share by class level and college; Pell by first-generation status.
  The aid role keeps its own app (review queue) and gets the overview as an
  added page.
- **Advising and Student Success.** Advising coverage, students who met an
  advisor, missed appointments, changes of major; coverage by college,
  appointments by type, who left without a degree by group.
- **Academic Affairs (Provost).** D, F and withdrawal rate, sections,
  average section size, instructors employed; D/F/W by college, the hardest
  courses (30 or more registrations), section sizes by format, instructors
  by rank (staff, not students).
- **Institutional Research.** Enrolled, full-time share, first-year
  retention of the latest cohort, degrees awarded; fall enrollment,
  retention and degrees by year.
- **Career Services.** First-destination survey results, median starting
  salary (rounded to $500, withheld under 10 salaries), medical school
  acceptance; outcomes, outcomes by college, graduate programs.
- **Advancement.** Alumni donors, giving participation, giving in the latest
  fiscal year, average gift; giving by year and designation, participation
  by college.
- **International Student Services.** International students enrolled, their
  share, the new class, those with an open hold; by college, each fall, by
  class level.
- **Athletics.** Athletes enrolled and their share, GPA, first-year
  retention against other students; GPA, retention, good standing and
  athletes by class level. No sport-level grouping exists in the data.

Not built: Financial Aid review counts (the review queue lives in the
Cabinet's store, not the school data) and Housing (it stays a table in
Student Life).

Every figure is a count, a share or a sum. Any group of fewer than 10
students reads "Fewer than 10" (the same minimum group size as the
counseling aggregate). A withheld cell is never left alone in a
row or column of a table: if only one would be withheld, the next-smallest
is withheld too, so no cell can be had by subtracting the shown ones from a
total shown elsewhere (the departments' `protect` helper). A department account reads its own overview. Naming
another department is a logged 403. The president and the admin read every
overview. Without the school data the answer is 503 with the same sentence
Explore uses.

## The inbox and Send alert

Every account has an inbox (`cabinet/inbox.py`, table `inbox_messages`,
migration 10; migration 11 adds the `chart` kind).

- **Send alert** is offered under every Explore answer, in the evidence
  panel of every briefing figure, on every overview figure, and on every
  Data page chart ("Send to department", for the whole chart or one point
  on it). The sender
  picks a person (any enabled account of the institution except themselves),
  writes a short note (required, at most 1,000 characters) and can add a
  review-by date.
- **Both people must be allowed to read the attachment.** A briefing
  figure needs a role that reads the briefing (not IT); an overview figure
  needs a role that reads that department's overview; an Explore answer
  needs a role that uses Explore (not IT, not Financial Aid). Otherwise the
  send is a logged 403, and the person picker only offers the people who
  may read it (`GET /inbox/recipients?kind=…&ref=…`). A plain note goes to
  anyone. The same rule is applied again every time the message is shown,
  so a reader whose role changes loses the attachment.
- **Figures are never frozen.** For a briefing figure or an overview figure
  only its reference is stored. Each time the message is shown, the figure
  is re-read from the live data (title, value and definition, never student
  ids). A figure that no longer exists, for example after a new dataset is
  activated or one is purged, shows "no longer available"; the note stays.
  The counseling aggregate (M9) can never be attached.
- **A chart is computed again for the reader.** A Data page chart is stored
  as its reference only (`chart=retention&college=ENG&at=2024-2025`: the
  chart, the one group or comparison, and optionally a term and a group on
  it). Each time the message is shown, `cabinet.dashboards` computes the
  series again after the same checks `GET /data/series` makes for the
  READER's role: the chart must be on one of their dashboards and every
  attribute it narrows or splits by must be open to them (Pell status is for
  the finance roles only). Otherwise the reader sees "not available to you".
  Withheld points stay withheld; the recipient sees the chart redrawn, the
  chosen point's value (or the withheld text), and Ask about this.
- **An Explore answer is the sender's quote.** Explore keeps no copy of its
  answers, so the server cannot rebuild one. The alert stores the question
  and at most six sentences, each redacted like a question
  (student-id-shaped tokens removed), and labels them "quoted by the
  sender". If the quote names an instructor (an instructor id, a title and
  surname such as "Dr. Shelby", a full instructor name, or Explore's
  "(fictional)" label) and the recipient may not see instructor-level
  results, only the question travels, and the recipient can ask it under
  their own role.
- **The recipient** sees the alert with an unread badge in the sidebar.
  Opening it marks it read. **Mark reviewed** records that they looked. The
  **sender** sees each alert under Sent as "Not opened yet", "Read" or
  "Reviewed".
- **Audit.** Each send, read and review is one event (`inbox.sent`,
  `inbox.read`, `inbox.reviewed`). The events carry ids, roles and the source
  kind and ref, never the note text and never a student id. In the audit log
  they are under "Inbox alerts".
- **Limits.** CSRF is checked like on every POST. `POST /inbox` has its
  own per-session and per-IP bucket (`CABINET_RATE_INBOX_PER_MIN`, default
  10 a minute), so alerts and questions never spend each other's allowance. Read and review answer 404 to anyone but the recipient, so
  message ids do not leak. A recipient from another institution is a 404.

`GET /admin/sessions` (IT, the admin and the president) lists, for each
account, how many sessions are signed in now and when the account was last
seen. It never shows a session id or a token.

## New routes and their roles

| Route | Roles |
|---|---|
| `GET /departments/overview` | admin, executive, aid and every department account (each reads its own only) |
| `GET /inbox` | every role |
| `GET /inbox/recipients` | every role |
| `POST /inbox` | every role (what can be attached depends on the sender's role) |
| `POST /inbox/{id}/read`, `POST /inbox/{id}/reviewed` | every role, recipient only (404 otherwise) |
| `GET /admin/sessions` | admin, it, executive |
| `GET/POST /admin/users`, `POST /admin/users/{id}/disable\|enable`, `PATCH /admin/users/{id}` | admin, and now it (department and staff accounts only) |
| `GET /admin/connections` | admin, and now it |
| `GET /events` (audit log) | admin, executive, reviewer, and now it |
| `POST /explore`, `/explore/stream`, `GET /explore/catalog` | admin, executive, staff, reviewer, and now every department account except aid |
| `GET /findings`, `/briefing`, `/decisions`, `/questions`, `/staff-actions` | every role except it |

## The university budget

The university's own budget (`data/school/budget.py`: budget against
actual, revenue, the tuition discount) is shown to the president
(`executive`), the Finance office (`finance`) and the administrator
(`admin`) only (never Student Accounts) (`BUDGET_ROLES` in `cabinet/explore/finance.py`):

- **Explore.** A question another role asks that plans one of the three
  budget analyses (`budget_vs_actual`, `revenue_by_source`,
  `tuition_discount`) is a 403 with "The university budget is shown to the
  president, the finance office and the administrator only." and a
  `data.refused` event (category `institutional_budget`) before anything is
  read. The executor refuses such a step as well. Student-account totals
  (past due, on-time payment, payment plans) are aggregate student data and
  answer for every Explore role, with the usual small-group rules.
- **Data page.** The University budget dashboard is on the president's and
  the finance office's Data page (`data_roles.py`). Any other role asking for
  one of its charts gets a 403 and a `data.refused` event. Its charts take no
  student filter or comparison (422).
- **Finance overview.** Read by finance, the president and the admin; other
  department accounts (Student Accounts included) get a 403.

## Creating the demonstration sign-ins

```bash
make demo-accounts OUT=$HOME/campuslens-demo-accounts.txt
```

This creates `president@`, `it@`, `finance@`, `studentaccounts@`, `aid@`,
`admissions@`, `advising@`, `provost@`, `ir@`, `careers@`, `advancement@`,
`international@`, `athletics@`, `registrar@`, `studentlife@`, `staff@` and
`reviewer@demo.test` in the bootstrap
institution (`INSTITUTION=<slug>` for another). Each gets a generated
password, appended to `OUT` (required; created with mode 600, and never
written through a symbolic link). Keep that file **outside the
repository**. The file is checked before any account is created.

- An account that already exists is left alone. Its password is never reset.
- The command refuses unless the institution's active dataset is the
  fictional demonstration data.
- It uses `CABINET_DB` like every other command. With the API stopped or
  running, the new accounts can sign in at once.
- **Real data switches them off.** When an admin activates a dataset that is
  not the fictional one, every enabled `@demo.test` account of that
  institution is disabled (one `admin.changed` event each), except
  administrators and the person activating, so nobody is locked out.

Add a single account in any role with
`make user EMAIL=… ROLE=finance|studentaccounts|registrar|studentlife|admissions|…|it`, or from
**Accounts** (IT) or **Institution settings** (admin).

## Known and accepted

- **The recipient list is an address book.** `GET /inbox/recipients` shows
  every account's email and role to every signed-in person of the same
  institution (narrowed per attachment). That is what an inbox needs; it
  never crosses institutions.
- **IT's audit log includes question text.** The audit log records each
  question as asked (student-id-shaped tokens redacted), and IT reads the
  audit log. The questions are about the school, never answers or rows.

## Approved decisions reach the department

When the president approves a leadership decision, the server puts it in
the inbox of every enabled account of the department that owns the
follow-up (`DEPARTMENT_ROLES` in `cabinet/inbox.py`: Financial Aid → `aid`,
Bursar and Student Accounts → `studentaccounts`, Finance → `finance`,
Admissions → `admissions`, Academic Advising → `advising`, Provost → `provost`,
Institutional Research → `ir`, Career Services → `careers`, Advancement →
`advancement`, International Student Services → `international`, Athletics →
`athletics`, Registrar → `registrar`, Student Success → `studentlife`), from the president. The message names the decision, the
approved action and the proposed deadline (one week from today, never past
the registration close date while that is ahead), and lists the briefing
figures behind it. Only the reference is stored: the figures are re-read
from the current data each time it is opened, never a student row, and the
message reads "no longer available" once another dataset is active. One
`inbox.sent` event per recipient carries `reason: "decision.approved"`. The
department clicks **Acknowledge**; the decision card shows each account as
delivered, opened or acknowledged. Nothing is emailed. These messages are
stored as kind `note` with the reference `decision:<id>` (the inbox table's
check constraint predates them) and shown as kind `decision`.

## Answer cards under Explore answers

Every Explore answer carries a card computed in code
(`cabinet/explore/card.py`): two to four key points (highest, lowest, the
gap from the whole, the change since the same term a year earlier, the
withheld groups), each number a link to its table cell; one chart from a
fixed template registry (`CHART_TEMPLATES`; the gallery is at `/dev/charts`);
and the buttons Send to department, Show trend, Break it down, Make a plan
and See evidence. Show trend and Break it down only offer questions the rule
planner has already answered the same way. Make a plan asks the seven-day
plan follow-up for registration answers (president and admin), and otherwise
shows a short proposed list that is never saved or sent. The reviewer gets
no Make a plan.
