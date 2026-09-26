# Ellucian Ethos connector

How the institution's student-information system (Ellucian Banner, reached
through the Ellucian Ethos Integration API) feeds the cabinet without any
identifiable student record ever entering it. The connector is
`backend/src/cabinet/ellucian.py` (standard library only: `urllib`, `hmac`,
`hashlib`, `json`), run as `make import-ethos` (RUNBOOK.md). Tests and demos
run against a recorded mock (`backend/tests/ethos_mock.py`); nothing here
touches a live Ellucian tenant on its own.

## The flow

1. `POST {base}/auth` trades the Ethos API key (sent as the bearer
   credential) for a session token.
2. `GET {base}/api/<resource>?offset=&limit=` pages through each mapped
   resource with the versioned Hedtech media type
   (`Accept: application/vnd.hedtech.integration.v<n>+json`). 429 and 5xx
   answers retry with exponential backoff (a `Retry-After` header is
   honored); a hard page cap (25 pages of 200) stops a runaway pager loudly
   instead of silently truncating the export. Requests time out at 30 s.
3. Transport rules: the base URL must be `https` (plain `http` is accepted
   only for the 127.0.0.1/localhost mock), and redirects are never
   followed, because following one would forward the bearer credential to
   whatever host the redirect names. A 401 mid-run is a one-line refusal
   with nothing written. Error text is redacted against both the API key
   and the session token.
4. The resources are mapped to the SCHEMA.md dataset shape (below).
5. Every student identifier is pseudonymised: `student_id = "S-" +
   hmac_sha256(key, ethos_person_id)[:12]`; advisor ids get the `A-` prefix.
   The key comes from `CABINET_PSEUDONYM_KEY_FILE`, a file and never an env
   value, never stored, never printed. A group- or world-readable key file
   is refused (the file must be 0600). The mapping is one way and no
   reverse table is written anywhere.
6. The document is validated with the same `validate_upload` the admin UI
   upload goes through, written to
   `var/exports/<slug>-<term>-<timestamp>-<suffix>.json` (0600), and,
   unless `DRY_RUN=1`, stored via `store.add_dataset`, inactive until an
   admin activates it, audited as `dataset.uploaded` with
   `importer: "ethos"`. After a successful import the export file is
   removed: the stored dataset is the retained copy (purged 30 days after
   deletion), and the export directory must not become a second,
   unpurged store. Export files are kept only on a dry run.

`meta.fictional` is `false` on a real export. `meta.source` records the
Ethos host, the resource versions, the extraction time, and the row counts.
`meta.title` names the institution and the term.

## Resource mapping (Ethos Education Data Model)

Assumed resources and versions; both are overridable per tenant with
`CABINET_ETHOS_RESOURCES` (JSON, keyed by the connector's resource name,
each value with optional `path` and `version`). Overrides are validated
before the first request: a path must match `^[a-z][a-z0-9-]*$` (so case
tricks, trailing slashes, query strings, and path traversal are all
refused) and must survive the deny lists below; a version must be
an integer.

| Connector name | Ethos resource (assumed version) | Becomes |
|---|---|---|
| `students` | `students` (v6) | `person.id` is the pseudonym source; `programs[0].code` → `profile.program`, `academicLevel.code` (lowercased) → `profile.class_level`, `continuing` (boolean) → `profile.continuing` |
| `academic_periods` | `academic-periods` (v3) | the three `terms.*` objects (`code`, `title`, `startOn`, `endOn`, `registration.openOn`/`closeOn`) |
| `student_academic_periods` | `student-academic-periods` (v4) | `enrollment.*` (`registrationStatus`, `creditHours`, `registeredOn`) and `comparison.prior_term_status` |
| `person_holds` | `person-holds` (v4) | `holds[]` (`type.category`, `amount`, `organization.name`, `placedOn`, `releasedOn`) |
| `advisor_relationships` | `student-advisor-relationships` (v2) | `advising.advisor_id` (pseudonymised) |
| `student_appointments` | `student-appointments` (v1) | `advising.last_appointment_date` / `appointment_status` (latest appointment with `status == "completed"`; a cancelled appointment never counts, CONTRACTS.md M4) |

There is deliberately no `persons` entry. The tenant-wide `persons`
resource carries every name, email, credential, and phone number in the
system, and every id the connector needs already arrives as
`students[].person.id`, so the resource is never requested. Fields outside
the mapped list are never read into the export document, never logged, and
never stored. The mock serves a person record planted with a name, an
email, an SSN, and a phone number, and the test suite asserts both that
`/api/persons` is never requested and that none of the planted strings
reach the export.

Field-level rules:

- Registration status: only an explicit `registered` maps to
  `"registered"`; every other status (or none) is `"not_registered"`,
  matching M2's `≠ "registered"` test.
- Dates: date or datetime strings (any offset, or `Z`) are normalised to
  calendar dates. Anything else, and any missing required field, is a
  one-line refusal naming the resource and the field, never the value.
- Holds: `releasedOn` null means unresolved. `type.category` maps through an
  explicit table (`FINANCIAL` → `financial`, `ACADEMIC` → `academic`,
  `ADMINISTRATIVE` → `administrative`, `LIBRARY` → `library`) and any other
  category becomes `"other"` with a warning in the import report, so a
  tenant's local hold types surface instead of silently vanishing.
- Terms: `terms.current` is the term named on the command line.
  `terms.prior_year` is the Banner-style prior-year code, `int(term) - 100`
  (202720 → 202620). `terms.in_session` is the period with the latest
  `startOn` on or before the derived as-of date. The as-of date is the
  latest current-term `registeredOn`, never the wall clock (CONTRACTS.md
  §0), and `prior_year_equivalent_date` is the as-of date shifted back one
  year. `comparison.prior_term_status` looks the baseline term up by code,
  then by period id, on both current-term and prior-year rows.
- A person with records in both the current and the prior-year period
  appears once, in `students`, with the prior-year record feeding
  `comparison.prior_term_status`. A person with only a prior-year record
  goes to `prior_year_students` (prior-year rows never carry holds).

## The id-join assumption to confirm per tenant

The connector assumes the `student`/`person` reference ids in
`student-academic-periods`, `person-holds`, `student-advisor-relationships`,
and `student-appointments` are the same person ids that
`students[].person.id` carries. Confirm this against the tenant before the
first import. When the assumption does not hold, no records join, the
current-term list comes out empty, and the run refuses loudly with "the
export would be empty", so a wrong join can never produce a quiet export of
zeros.

## What is never requested

Before the first request, every configured resource path is checked two
ways. It must not be an exact-name deny, because `persons` is on that list (a
substring would catch the legitimate `person-holds`), so the tenant-wide
persons resource is never requested, however the configuration is edited.
And it is matched, after lowercasing, against these deny substrings:
`counsel`, `chaplain`, `spiritual`, `financial-aid`, `financialaid`,
`finaid`, `medical`, `disciplin`. Substring matching, on purpose: a
tenant's local variant (say `student-finaid-records` or
`counseling-notes-v2`) cannot slip past an exact-name list. Counseling and
spiritual care are out of scope by policy (ROADMAP §5/§9); financial-aid
decisions, discipline, and medical records have no place in a registration
briefing.

## Configuration

| Variable | Meaning |
|---|---|
| `CABINET_ETHOS_BASE_URL` | the institution's Ethos Integration base URL (https) |
| `CABINET_ETHOS_API_KEY_FILE` | file holding the Ethos API key (0600, outside the repo) |
| `CABINET_PSEUDONYM_KEY_FILE` | file holding the pseudonym key (0600); never leaves the institution |
| `CABINET_ETHOS_RESOURCES` | optional JSON overrides for resource names/versions, validated as above |
| `CABINET_ETHOS_TIMEZONE` | date-semantics timezone (default `America/Chicago`) |
| `CABINET_EXPORTS_DIR` | export directory (default `var/exports/`) |

Both keys come from files so they cannot leak through a process listing or a
dumped environment. A missing, unreadable, or group/world-readable key file
is a one-line refusal before any network access, and nothing is written.

## What to change per tenant

- **Resource names/versions.** Set `CABINET_ETHOS_RESOURCES`, e.g.
  `{"person_holds": {"path": "person-holds", "version": 5}}`. Unknown keys
  are an error, so a typo cannot silently keep fetching the wrong resource.
- **Advising resources.** If the tenant exposes advising differently (some
  expose appointments but not relationships, or a single combined resource),
  point `advisor_relationships` and `student_appointments` at what the
  tenant has; a missing advisor relationship exports `A-unassigned`.
- **The id join.** See "The id-join assumption" above.
- **Term-code scheme.** The prior-year derivation `int(term) - 100` assumes
  Banner-style numeric codes; a tenant with a different scheme needs the
  mapping adjusted in `cabinet/ellucian.py` (`_prior_term_code`).
- **Hold categories.** Extend `HOLD_CATEGORY_MAP` with the tenant's local
  `person-holds` type categories; unmapped categories land in `"other"` with
  a warning.
- **Class levels and programs.** `academicLevel.code` is lowercased
  verbatim and `programs[0].code` is taken as-is; check the tenant's codes
  against what the briefing should show.
- **Timezone.** Set `CABINET_ETHOS_TIMEZONE`.

## Verifying an import

`--dry-run` (`make import-ethos ... DRY_RUN=1`) writes the export and prints
the validation report (row counts, counseling note, fictional flag) and
every mapping warning, without storing anything; the dry run is the only
case where the export file is kept. The export file can be hand-checked
against SCHEMA.md before the real run; once imported, the dataset sits
inactive in `GET /admin/datasets` until an admin activates it.
