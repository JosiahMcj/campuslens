# Privacy posture

**Draft for legal review. Not yet in force.**

Draft date 2026-09-25.

We operate this service for institutions that want executive briefings built from student
information system data. This page states, in our own words as the operators, what the
service holds and what it never holds. It also covers where the data lives, who can see
it, and what happens when you ask us to delete it. It describes only what the software
does or is designed to do.

## What the service holds

For each institution we hold the following.

- The institution's name.
- The email addresses and roles of the institution's users, with password hashes.
- The student datasets the institution uploads, in the shape defined in
  [SCHEMA.md](../SCHEMA.md). These rows carry pseudonymous student identifiers, program
  codes, enrollment and registration fields, holds, advising indicators, and comparison
  fields.
- The audit log, which records who asked what, what data was granted, what was refused,
  and what was approved.
- The briefings the service produces, the decisions an executive approves, and recordings
  of validated analyst replies used for replay.
- The office mailboxes the institution's administrator configures for follow-up
  messages, and the dispatch records of messages sent to those offices.
- The Financial Aid review queue, once someone prepares it for an authorized
  emergency-aid review. Each row holds one pseudonymous student id from the
  briefing's M3 finding and the facts the aid office needs to start its own
  review, namely the qualifying hold's amount, date, and responsible office, the
  registration status, and the advising status. It never holds a counseling
  field. Each row also holds the status and the free-text note a person in the
  Financial Aid office sets, with who set them and when. The software computes
  nothing about any student in the queue.

Every new institution also starts with a fictional demonstration dataset, which is
labelled fictional in every response and contains no real students.

## What the service never holds

We never hold student names. We never hold contact details. We never hold counseling
content.

The upload validator checks each dataset against [SCHEMA.md](../SCHEMA.md) before the
dataset is accepted. It rejects columns that look like personal identifiers, including
names, email addresses, phone numbers, social security numbers, and dates of birth. The
counseling fields exist in the schema only so the permission layer has something real to
refuse. No role is granted them, and every request for them is refused before any model
call, with the refusal recorded in the audit log.

## Where the data lives

All of it lives on our server, where each institution is a separate tenant. Storage and
every route isolate one institution's data, audit events, briefings, decisions, and
recordings from every other institution, so a user in one institution cannot read another
institution's data.

## Who can see it

The institution's own users see the service by role. Administrators manage users and
datasets, and executives ask questions and approve decisions. Staff read briefings and
findings. Reviewers read everything, including the audit log, and change nothing.
Financial Aid staff read the briefing like staff and work the Financial Aid review
queue. The queue's rows are visible to Financial Aid staff, administrators,
executives, and reviewers. Only Financial Aid staff and administrators can change a
row, and staff cannot read the rows at all.

The briefing's support indicators are named rules computed in code, not models. Each
rule is a simple test, for example an unresolved financial hold under a defined amount
or no advising appointment this term, and each rule carries a plain-language reason
that names the fields it read. Only the aggregate count and the per-rule counts may
leave the server for the configured model endpoint, like the other findings. The
per-student list of which rules fired never goes to a model. It appears only in the
evidence drawer, and only for executives and administrators, and it carries
pseudonymous identifiers only.

The student ids behind every figure work the same way. Executives and administrators
see the list of pseudonymous records behind a figure, because their work acts on those
records. Staff, reviewers, and Financial Aid staff see the figure, the per-office and
per-indicator counts, and how many records sit behind it, and the evidence says the
list is shown to executives and administrators only. The server removes the ids before
the response leaves it, so they never reach those roles' browsers.

Support programs (the Support programs page) work the same way. Every role sees each
program's rule, reach and impact as totals, with groups under 10 withheld. The list of
students a program's rule names is prepared only by an executive, an administrator,
or the program's own office (Financial Aid staff, for the theology and ministry
funding bridge). It waits for an executive or administrator to approve it; preparing,
approving, opening and updating it are each recorded in the audit log without any
student id; and nothing is sent to a student. For the demonstration, the person who
prepared a list may also approve it (a president working alone can show the whole
flow); the audit log names who prepared and who approved, so an institution that wants
two people can check it there. The eligibility rules are plain tests
anyone can read, not risk scores, and no program row ever goes to a model.

We can see the data ourselves for maintenance and backups. That is the full list.

## Explore and the demonstration university

Explore answers questions over Demonstration University, a fictional university that
exists only as a generated database on our server. No institution's data is in it. The
same rules we apply to real data hold there.

- **Aggregates only.** Every answer is built from tables of counts, rates, and averages.
  No answer, table, or audit event carries a student id, and a question about one student
  is refused before anything runs.
- **Small groups are withheld.** Any figure about fewer than 10 students reads "fewer than
  10" and is left out of every ranking.
- **Counseling is refused.** The database holds no counseling or spiritual-care data, and
  a question about either is refused before any planning or model call and recorded.
- **Instructor rows are narrower.** Results about individual instructors go to the
  executive and admin roles only. Staff and reviewers see the course as a whole. Every
  instructor name is generated and fictional, and answers say so.
- **What a model sees.** When a model is configured, it receives the list of approved
  analyses and the question (to plan), and the finished aggregate tables (to reword the
  answer). It never receives a student row and never computes a number.

## The counseling figure

Per-student counseling and spiritual-care data is refused to every role and every AI
employee. That does not change, and it is the default for every institution.

An institution may allow one aggregate figure, and only in writing. When the
institution's counseling director authorizes it, an administrator records the
authorization in Institution settings with the director's name and title and a
reference to the document. The record keeps who entered it and when, and an
administrator can revoke it the same way. Each change is recorded in the audit log.

While the authorization is recorded, the briefing shows one count, the number of
continuing students not yet registered who have had any counseling contact this term.
The count is computed in code. Its source label names the person who authorized it.

- **Minimum group size.** A count under 10 is never shown. The briefing says "fewer
  than 10" and explains that the count is withheld so no one can be identified.
- **No rows.** The evidence for the figure shows the authorization and the fields it
  read, never a student, an identifier, or a note. There is no drill-down.
- **Never shown, to anyone.** Counseling notes, chaplain contacts, and which students
  had contact. No role can request those fields, with or without the authorization,
  and every such request is refused and recorded as before.

## What leaves the server

For briefings, only two things leave the server for the configured model endpoint,
namely the aggregate findings computed in code from the active dataset and the analysts'
validated texts. For Explore, only the analysis catalog, the question, and the computed
aggregate tables leave, as described above. Student rows never leave. The counseling
group never leaves. While a counseling authorization is recorded, the Chief of Staff may receive the one counseling count,
and only when it is 10 or more. A smaller count reaches the model as a marker with
no number in it. The Financial Aid
review queue never leaves either. Its rows, its facts, and the office's notes are never
sent to a model.

The endpoint's own retention terms apply to what it receives, and we name the endpoint to
each institution on request. The key for the endpoint is ours as operators, never per
user, and it never appears in logs, responses, recordings, or the repository.

One more thing can leave, and only through a person's click. When an executive approves a
leadership decision, a staff member may send the responsible office a message about the
follow-up. The software writes the message from the verified findings, never the model,
and it names offices, decisions, and aggregate numbers only. It never contains a student
identifier or a student row. The recipient is an office mailbox the institution's
administrator configured, never a student address. By default the message does not even
leave the machine. The built-in outbox writes it to a file on the server, and a person
delivers it. Real email delivery over SMTP stays off unless we turn it on.

A dispatch record keeps the subject and body that were sent, the office it went to, who
composed it, who sent it and when, and the delivery provider's reference. We keep these
records because they are the proof that a person, not the software, sent each message.

## Retention and deletion

Deleting a dataset is a soft delete, and the data is purged after 30 days. The
Financial Aid review queue belongs to the dataset it was read from, so its rows and
notes are purged with that dataset. The audit log keeps that a queue row changed, by
its row number, with the status before and after. It never keeps the student id or
the note text, so nothing in it points back to a student once the queue is purged. Backups are made with the database's own backup API and rotate on our
schedule. You may ask us for full deletion of your institution's data at any time, and
the audit log then retains only the fact of deletion.

## FERPA posture

The institution remains the owner of its education records and decides what it uploads.
The service is designed for pseudonymous exports, and the validator refuses obvious
identifiers. Any school official agreement under FERPA is the institution's
responsibility to arrange, and we will sign a data processing agreement on request.

## Security

The threat model, the controls in place, and what is not covered yet are documented in
[SECURITY.md](SECURITY.md).

## Contact

Contact the team listed in the README. For security reports, see
[SECURITY.md](SECURITY.md).
