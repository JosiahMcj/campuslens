# Data access request: pseudonymised enrollment extract for the weekly briefing

Draft date 2026-09-26. Addressed to the registrar, the data steward, and IT
security. Please route to whoever owns Ethos Integration API access.

## Who we are and what we are asking for

We operate the Golden Eagle AI Cabinet, a small service that prepares the
president's weekly student success briefing. The briefing answers one
question: how is spring registration going, compared with the same point
last year, and who needs outreach before registration closes. The full data
shape is defined in the schema document we attach to this request.

We ask for read-only API access to a small set of Ellucian Ethos Integration
resources, listed below. We run the export ourselves, at the institution's
edge, and we pseudonymise every student identifier before any record leaves
the institution's network. No identifiable student record ever enters our
service.

## The exact resources and fields requested

We request the following Ethos resources (Education Data Model), nothing
else:

| Ethos resource | Fields used | Used for |
|---|---|---|
| `students` | `person.id`, `programs[0].code`, `academicLevel.code`, `continuing` | the id that feeds the pseudonym hash, plus program, class level, continuing flag |
| `academic-periods` | `code`, `title`, `startOn`, `endOn`, registration open and close dates | the term calendar |
| `student-academic-periods` | `registrationStatus`, `creditHours`, `registeredOn` | registration status, credit hours, registration date |
| `person-holds` | `type.category`, `amount`, `organization.name`, `placedOn`, `releasedOn` | holds that block registration |
| `student-advisor-relationships` | advisor person id | a pseudonymous advisor id |
| `student-appointments` | appointment date and completion status | whether the student has seen an advisor this term |

These fields are exactly the list in the attached schema, nothing else. We
do not request the tenant-wide persons resource at all, because the id we
need already arrives on each students record. We never request names, email
addresses, phone numbers, postal addresses, birth dates, social security
numbers, photos, or free text, and fields outside the list above are never
read into the export document, never logged, and never stored.

## Pseudonymisation at the edge

The export runs on infrastructure the institution controls. Each student id
is replaced with `"S-"` plus the first twelve hex characters of
HMAC-SHA256(key, ethos person id). Advisor ids get the same treatment with
an `"A-"` prefix. The key is generated at the institution, stored in a file
at the institution, and never leaves it. Our service never sees the key, and
no reverse mapping table is written anywhere. Without the key the exported
ids cannot be linked back to a person. Rotating or destroying the key makes
future exports unlinkable to past ones.

## What we never request

We never request counseling records, spiritual care or chaplaincy records,
financial-aid applications, awards, or eligibility decisions, disciplinary
records, medical records, or any free-text notes field. Before every run
the connector checks each configured resource name against this list
(counseling, chaplaincy, spiritual care, financial aid, medical, and
discipline, matched by substring after lowercasing) and refuses to start on
a match.

## What the service holds, where, and who can see it

The export lives on our server as a dataset scoped to the institution's
tenant. Each institution is isolated from every other institution in storage
and in every route. Access inside the institution is by role:

| Role | Can do |
|---|---|
| admin | manage users and datasets, everything else below |
| executive | ask questions, read briefings and findings, approve decisions, read the audit log |
| staff | read briefings and findings |
| reviewer | read everything including the audit log, change nothing |

As operator we can see the data for maintenance and backups. That is the
full list. Only aggregate findings ever leave the server for the model
endpoint that drafts the briefing text. Student rows never leave.

## Retention and deletion

The export file produced at the institution's edge is a staging copy. It is
removed as soon as the dataset is stored, and it is kept only when a dry
run is requested for review. Inside the service, deleting a dataset is a
soft delete: the dataset row and file are kept for 30 days and then purged
permanently by a scheduled command. The audit log keeps the fact of each
upload and each deletion, with the dataset's hash, indefinitely, and it
holds no student rows. The institution may ask for full deletion of its
data at any time, and the audit log then retains only the fact of deletion.

## Security posture

The threat model, the controls in place, and what is not covered yet are
documented in docs/SECURITY.md. In short: scrypt password hashing,
server-side sessions with signed HttpOnly cookies, per-route role
allow-lists, CSRF tokens on every write, rate limits, a hash-chained audit
log, dataset files at 0600 verified against their recorded sha256 on every
load, and dependency audits. The API binds localhost behind a
TLS-terminating proxy.

## FERPA basis, to be confirmed by the institution

The institution remains the owner of its education records. We ask the
institution to confirm the legal basis for this extract. Our understanding
is that the appropriate basis is a school official designation for the
operators under FERPA, with legitimate educational interest, supported by a
data processing agreement that we will sign on request. Because the extract
is pseudonymised at the edge and the key stays with the institution, the
service holds no directly identifiable education records. We ask the
registrar and counsel to confirm this reading.

## Sign-off

Registrar: name ______________________  signature ______________________  date __________

Data steward: name ______________________  signature ______________________  date __________

IT security: name ______________________  signature ______________________  date __________

FERPA basis confirmed by counsel: name ______________________  date __________
