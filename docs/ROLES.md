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
| Finance — Student Accounts (`finance@`) | `finance` | Inbox, Finance overview | yes, aggregate only | no |
| Financial Aid (`aid@`) | `aid` | Inbox, the briefing pages (read only), Financial Aid review | no (unchanged) | no |
| Registrar (`registrar@`) | `registrar` | Inbox, Registrar overview | yes, aggregate only | no |
| Student Life (`studentlife@`) | `studentlife` | Inbox, Student Life overview | yes, aggregate only | no |
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
  department and staff accounts (`IT_MANAGED_ROLES`: finance, aid,
  registrar, studentlife, staff, reviewer). Any action on an admin,
  executive or IT account, or any grant of those roles, is a logged 403.
  IT also reads the audit log, the outside connections and sign-in
  activity.
- **The president** sees every page and every panel. Two things still need
  another person, as before: Institution settings (the admin manages the
  datasets and the office mailboxes) and the Send step of an office message
  (staff or the admin send; the president decides).

## Department overviews

`GET /departments/overview?department=finance|registrar|studentlife`
(`cabinet/departments.py`). The overview is computed in code from the same
read-only school data Explore uses (`CABINET_SCHOOL_DB`). Each overview has
four headline figures and two tables for the current term:

- **Finance — Student Accounts.** Students with an open account hold, the
  open balance, the average balance per student, holds placed this term,
  open holds by office, and students by balance owed.
- **Registrar.** Students enrolled this term, the share enrolled full time,
  open Registrar holds, students not in good standing, enrollment by class
  level, and academic standing.
- **Student Life.** Students living on campus, the share who are first
  generation, students who stopped out this term, the advising no-show rate,
  advising appointments by type, and housing by load.

Every figure is a count, a share or a sum. Any group of fewer than 10
students reads "Fewer than 10" (the same minimum group size as the
counseling aggregate). A department account reads its own overview. Naming
another department is a logged 403. The president and the admin read every
overview. Without the school data the answer is 503 with the same sentence
Explore uses.

## The inbox and Send alert

Every account has an inbox (`cabinet/inbox.py`, table `inbox_messages`,
migration 10).

- **Send alert** is offered under every Explore answer, in the evidence
  panel of every briefing figure, and on every overview figure. The sender
  picks a person (any enabled account of the institution except themselves),
  writes a short note (required, at most 1,000 characters) and can add a
  review-by date.
- **What travels with it is built by the server, never by the browser.**
  - A briefing figure carries its title, value and definition from the
    active dataset. It never carries the student ids, even though the
    president's own view of the figure has them.
  - An overview figure is recomputed on the server.
  - An Explore answer carries the question (with student-id-shaped tokens
    redacted) and at most six answer sentences. These are aggregate by
    construction. If the answer names an instructor and the recipient may
    not see instructor-level results, only the question travels, and the
    recipient can ask it under their own role.
  - A role that cannot read a source cannot attach it. For example, IT can
    send a plain note but cannot attach a figure.
- **The recipient** sees the alert with an unread badge in the sidebar.
  Opening it marks it read. **Mark reviewed** records that they looked. The
  **sender** sees each alert under Sent as "Not opened yet", "Read" or
  "Reviewed".
- **Audit.** Each send, read and review is one event (`inbox.sent`,
  `inbox.read`, `inbox.reviewed`). The events carry ids, roles and the source
  kind and ref, never the note text and never a student id. In the audit log
  they are under "Inbox alerts".
- **Limits.** CSRF is checked like on every POST. `POST /inbox` shares the
  tighter per-session and per-IP bucket with Ask and Send (default 5 a
  minute). Read and review answer 404 to anyone but the recipient, so
  message ids do not leak. A recipient from another institution is a 404.

`GET /admin/sessions` (IT, the admin and the president) lists, for each
account, how many sessions are signed in now and when the account was last
seen. It never shows a session id or a token.

## New routes and their roles

| Route | Roles |
|---|---|
| `GET /departments/overview` | admin, executive, finance, registrar, studentlife (a department account reads its own only) |
| `GET /inbox` | every role |
| `GET /inbox/recipients` | every role |
| `POST /inbox` | every role (what can be attached depends on the sender's role) |
| `POST /inbox/{id}/read`, `POST /inbox/{id}/reviewed` | every role, recipient only (404 otherwise) |
| `GET /admin/sessions` | admin, it, executive |
| `GET/POST /admin/users`, `POST /admin/users/{id}/disable\|enable`, `PATCH /admin/users/{id}` | admin, and now it (department and staff accounts only) |
| `GET /admin/connections` | admin, and now it |
| `GET /events` (audit log) | admin, executive, reviewer, and now it |
| `POST /explore`, `/explore/stream`, `GET /explore/catalog` | admin, executive, staff, reviewer, and now finance, registrar, studentlife |
| `GET /findings`, `/briefing`, `/decisions`, `/questions`, `/staff-actions` | every role except it |

## Creating the demonstration sign-ins

```bash
make demo-accounts OUT=$HOME/campuslens-demo-accounts.txt
```

This creates `president@`, `it@`, `finance@`, `aid@`, `registrar@`,
`studentlife@`, `staff@` and `reviewer@demo.test` in the bootstrap
institution (`INSTITUTION=<slug>` for another). Each gets a generated
password, appended to `OUT` (created with mode 600). Keep that file
**outside the repository**. Without `OUT`, the passwords print once.

- An account that already exists is left alone. Its password is never reset.
- The command refuses unless the institution's active dataset is the
  fictional demonstration data.
- It uses `CABINET_DB` like every other command. With the API stopped or
  running, the new accounts can sign in at once.

Add a single account in any role with
`make user EMAIL=… ROLE=finance|registrar|studentlife|it|…`, or from
**Accounts** (IT) or **Institution settings** (admin).
