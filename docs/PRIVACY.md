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
evidence drawer, which every signed-in role may read, and it carries pseudonymous
identifiers only.

We can see the data ourselves for maintenance and backups. That is the full list.

## What leaves the server

Only two things leave the server for the configured model endpoint, namely the aggregate
findings computed in code from the active dataset and the analysts' validated texts.
Student rows never leave. The counseling group never leaves. The Financial Aid
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

Email us at contact@example.edu, a placeholder mailbox that will be replaced with our real
mailbox before the service goes live. For security reports, use the address given in
[SECURITY.md](SECURITY.md).
