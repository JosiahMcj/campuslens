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

We can see the data ourselves for maintenance and backups. That is the full list.

## What leaves the server

Only two things leave the server for the configured model endpoint, namely the aggregate
findings computed in code from the active dataset and the analysts' validated texts.
Student rows never leave. The counseling group never leaves.

The endpoint's own retention terms apply to what it receives, and we name the endpoint to
each institution on request. The key for the endpoint is ours as operators, never per
user, and it never appears in logs, responses, recordings, or the repository.

## Retention and deletion

Deleting a dataset is a soft delete, and the data is purged after 30 days. Backups are made with the database's own backup API and rotate on our
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
