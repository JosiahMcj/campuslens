# Fixture Schema

This is the shape of `data/fixture.json`, which we generate with
`data/generate_fixture.py`, deterministic with seed `20260924`, no wall-clock
values, two byte-identical runs. Every field is fictional and there is no path from
this schema to a real student, and `data/VERIFY.md` lists the row IDs behind every
planted number while `data/check_fixture.py` recomputes them.

Field groups follow ROADMAP §5, and they are `profile`, `enrollment`, `holds`,
`advising`, `comparison`, and `counseling`. ROADMAP §4 writes `hold.*`, but the group
is a **list**, so `hold.x` means "field `x` of an element of `holds`", and `terms`
contains the three term objects, with **no** top-level `term` object.

---

## Top level

```jsonc
{
  "meta":  { ... },                 // provenance block, see below
  "terms": { ... },                 // three term objects, see below
  "students":            [ ... ],   // 185 current-term records (Spring 2027 registration)
  "prior_year_students": [ ... ]    // 135 prior-year records (Spring 2026); M1/M7 denominators are real rows
}
```

The 100-200 row bound applies to `students` (185), and `prior_year_students` is a
separate list, 320 records in total.

## `meta`

| Field | Type | Value in this fixture |
|---|---|---|
| `meta.title` | string | `"Golden Eagle AI Cabinet - fictional spring registration fixture"` |
| `meta.description` | string | provenance sentence naming the planted values |
| `meta.fictional` | boolean | `true`, always |
| `meta.seed` | integer | `20260924` |
| `meta.generator` | string | `"data/generate_fixture.py"` |
| `meta.timezone` | string | `"America/Chicago"` |
| `meta.date_format` | string | `"YYYY-MM-DD"` |
| `meta.planted_metrics` | object `{M1: number, M2: int, M3: int, M4: int}` | `{M1: -0.048, M2: 42, M3: 18, M4: 12}` |

## `terms`, three objects

| Field | Type | `terms.current` | `terms.prior_year` | `terms.in_session` |
|---|---|---|---|---|
| `term` | string, term code | `"202720"` | `"202620"` | `"202710"` |
| `name` | string | `"Spring 2027"` | `"Spring 2026"` | `"Fall 2026"` |
| `start_date` | ISO date | `2027-01-11` | `2026-01-12` | `2026-08-24` |
| `registration_open_date` | ISO date | `2026-11-02` | `2025-11-03` | - (absent) |
| `registration_close_date` | ISO date | `2026-12-18` | `2025-12-19` | - (absent) |
| `prior_year_equivalent_date` | ISO date | - (absent) | `2025-11-20` | - (absent) |
| `end_date` | ISO date | - (absent) | - (absent) | `2026-12-11` |
| `as_of_rule` | string | the as-of derivation rule | the prior-year window rule | "this term" rule for M4 |
| `timezone` | string | `"America/Chicago"` | same | same |

We anchor the metrics (CONTRACTS.md §0) this way, with the as-of date as the max
`registration_date` over `students`, which is **2026-11-20**. The M1/M7 denominator
window ends at `terms.prior_year.prior_year_equivalent_date`, M4 anchors on
`terms.in_session.start_date`, and M6 uses `terms.current.registration_close_date`.

## Student record

Both lists hold the same record shape, with keys stored alphabetically and order not
meaningful, and every `prior_year_students` record has `holds: []`.

```jsonc
{
  "profile":    { ... },
  "enrollment": { ... },
  "holds":      [ ... ],   // zero or more hold objects
  "advising":   { ... },
  "comparison": { ... },
  "counseling": { ... }
}
```

### `profile`

| Field | Type | Allowed values / notes |
|---|---|---|
| `profile.student_id` | string | `"STU-"` + 4 digits in `students`; `"PRI-"` + 4 digits in `prior_year_students`. Pseudonymous. The only key joining a metric to its evidence rows. |
| `profile.program` | string, program code | one of `BA-BIBL`, `BA-COMM`, `BA-EDUC`, `BA-ENGL`, `BA-MUSC`, `BA-PSYC`, `BS-BIOL`, `BS-BUS`, `BS-CSCI`, `BS-NURS` |
| `profile.class_level` | enum | `"freshman" \| "sophomore" \| "junior" \| "senior"` (no graduate students in this fixture) |
| `profile.continuing` | boolean | `true` = enrolled in a prior term and eligible to continue. Drives the M1/M2 populations. |

### `enrollment`

| Field | Type | Allowed values / notes |
|---|---|---|
| `enrollment.term` | string, term code | `"202720"` in `students`, `"202620"` in `prior_year_students` |
| `enrollment.registration_status` | enum | `"registered" \| "not_registered"`. M2 counts everything ≠ `"registered"`. |
| `enrollment.registered_credit_hours` | integer | 0-18 in this fixture. `0` when not registered. Summed by M7. |
| `enrollment.registration_date` | ISO date or `null` | `null` when not registered. The **latest** value in `students` is the as-of date (**2026-11-20**, STU-0001). |

### `holds`, a list of hold objects (may be empty)

| Field | Type | Allowed values / notes |
|---|---|---|
| `holds[].category` | enum | `"financial" \| "academic" \| "administrative" \| "library"`. M3 tests `"financial"`. |
| `holds[].amount` | number (float), dollars | 64.75-2400.00 in this fixture. M3's boundary is strict, `< 1000`. STU-0142's exactly-`1000.00` hold is a planted decoy. |
| `holds[].responsible_office` | string | one of `"Bursar"`, `"Registrar"`, `"Library"`, `"Student Life"`. M5 grouping key. |
| `holds[].hold_date` | ISO date | 2026-08-20…2026-11-06 in this fixture. |
| `holds[].resolved` | boolean | `false` = still blocking. M3/M5 filter on this. |

No student carries more than one hold in this fixture, all 32 holds sit on
current-term students, and every `prior_year_students` record has `holds: []`.

### `advising`

| Field | Type | Allowed values / notes |
|---|---|---|
| `advising.advisor_id` | string | `"ADV-001"`…`"ADV-012"`. Pseudonymous. |
| `advising.last_appointment_date` | ISO date or `null` | Most recent appointment. `null` = never seen an advisor. Never after the as-of date. M4 compares it to `terms.in_session.start_date` (**2026-08-24**. An appointment exactly on that date counts as this term). |
| `advising.appointment_status` | enum | `"completed" \| "none"`. `"none"` exactly when `last_appointment_date` is `null`. **No `"cancelled"` exists in the fixture**. The contract rule for cancelled appointments applies only if that value ever appears (CONTRACTS.md M4). |

### `comparison`

| Field | Type | Allowed values / notes |
|---|---|---|
| `comparison.prior_year_equivalent_date` | ISO date | `"2025-11-20"` on every current-term record (prior-year records carry their own equivalent, `"2024-11-21"`). Anchors the M1/M7 denominators. |
| `comparison.prior_term_status` | enum | `"registered" \| "not_registered" \| "not_enrolled"`, the student's status at the equivalent point of the baseline term. |
| `comparison.baseline` | string, term code | `"202620"` on current-term records (`"202520"` on prior-year records). Names the baseline term for display. **Metrics never read it**. M1/M7 compute from rows (CONTRACTS.md M7). |

### `counseling`, **present only to be refused**

We put these two fields in the fixture **solely so the permission layer has something
real to refuse** (ROADMAP §5, §9). They are granted to no role, no metric, finding,
analyst, or UI element may read them, and every request is refused before any model
call and logged as `data.refused`.

| Field | Type | Allowed values / notes |
|---|---|---|
| `counseling.counseling_notes` | string or `null` | Non-null on exactly five rows (STU-0026, STU-0071, STU-0126, STU-0147, STU-0177). Gentle fictional text, so the refusal is real. **Present only to be refused.** |
| `counseling.chaplain_contact` | boolean | `true` on the same five rows, `false` elsewhere. **Present only to be refused.** |

---

## Worked example record (real row STU-0120, verbatim, in M2, M3, and M4)

```json
{
  "profile": {
    "student_id": "STU-0120",
    "program": "BA-PSYC",
    "class_level": "sophomore",
    "continuing": true
  },
  "enrollment": {
    "term": "202720",
    "registration_status": "not_registered",
    "registered_credit_hours": 0,
    "registration_date": null
  },
  "holds": [
    {
      "category": "financial",
      "amount": 160.73,
      "responsible_office": "Bursar",
      "hold_date": "2026-10-22",
      "resolved": false
    }
  ],
  "advising": {
    "advisor_id": "ADV-004",
    "last_appointment_date": null,
    "appointment_status": "none"
  },
  "comparison": {
    "prior_year_equivalent_date": "2025-11-20",
    "prior_term_status": "registered",
    "baseline": "202620"
  },
  "counseling": {
    "counseling_notes": null,
    "chaplain_contact": false
  }
}
```

See CONTRACTS.md §0.1 for the worked metric calculations on this row.

## Invariants the generator holds (checked by `data/VERIFY.md` and `data/check_fixture.py`)

1. Exactly 119 current-term records with `continuing = true` and
   `registration_status = "registered"` (STU-0001…STU-0119), and exactly 125
   prior-year records registered on or before 2025-11-20 (PRI-0001…PRI-0125), so
   M1 = −4.8 % exactly.
2. Exactly 42 records with `continuing = true` and
   `registration_status ≠ "registered"` (STU-0120…STU-0161) give M2, and the new
   unregistered students (STU-0176…STU-0185) are decoys.
3. Exactly 18 of the M2 records carry an unresolved financial hold with
   `amount < 1000` (STU-0120…STU-0137) and give M3, with decoys D1-D4 documented in
   VERIFY.md.
4. Exactly 12 of the M2 records have no appointment on or after 2026-08-24, giving
   M4, and an appointment exactly on 2026-08-24 (STU-0142, STU-0143) counts as this
   term (decoy D5).
5. Exactly five records carry non-null `counseling.counseling_notes`, which we planted
   for the refusal demo only, and the deterministic generator uses seed `20260924`,
   so two runs are byte-identical.

---

## Storage schema and dataset upload

The document shape above is also the upload shape. `POST /admin/datasets`
accepts a JSON body in exactly this shape, up to 20 MB, and we validate it
before anything is stored. We run the fixture loader's type checks plus these
upload-only rules.

- Every key at every level must be one this schema names. Extra keys are
  rejected, not ignored, so a real export's surprises surface at upload time
  instead of silently dropping data.
- A key named `email`, `phone`, `ssn`, `dob`, or `name` anywhere inside a
  student record is rejected, with an error that names the field. Student
  records stay pseudonymous.
- `profile.student_id` must be a `STU-`/`PRI-` style id or another opaque
  token with no spaces and no `@`.
- Counseling fields are allowed, and the response flags them as "present,
  will always be refused".
- We report row counts for both student lists. All problems are collected and
  reported together, and nothing is stored until the document is clean.

We keep durable state in `var/cabinet.db`. The `cabinet.migrations` module
versions the schema, and the app refuses a version it does not know. Dataset
documents live as files next to the database. We scope every row by
institution, and the client never sends an institution id.

```sql
institutions(id, name, slug UNIQUE, created_at)
users(id, email UNIQUE, password_hash, role, institution_id NOT NULL, created_at, disabled)
sessions(id, user_id, created_at, expires_at, last_seen, csrf_token)
datasets(id, institution_id, name, uploaded_by, uploaded_at, sha256,
         row_counts, is_active, deleted_at)
audit_events(institution_id, id, ts, type, actor, payload, prev_hash, hash,
             PRIMARY KEY (institution_id, id))
briefings(institution_id, dataset_id, question_id, produced_at, sections,
          dataset_sha256,
          PRIMARY KEY (institution_id, dataset_id, question_id))
decisions(institution_id, decision_id, approved_by, at, task,
          dataset_id, dataset_sha256,
          PRIMARY KEY (institution_id, decision_id, dataset_id))
recordings(institution_id, role, key, json, PRIMARY KEY (institution_id, role, key))
```

Notes on the tables.

- The bootstrap admin belongs to the bootstrap institution. `make
  bootstrap-admin` creates it and seeds it with the fictional fixture as the
  dataset "Demonstration (fictional)".
- A dataset's document is the file
  `var/data/<institution slug>/<dataset id>.json`, mode 0600, with
  institution directories at 0700. We compute the row's `sha256` from the
  stored bytes and verify it on every load, so a file changed on disk fails
  loudly.
- `row_counts` is JSON of the form `{"students": N, "prior_year_students": M}`.
- `is_active` marks the dataset the institution's findings compute from.
  Activating a dataset recomputes findings, and the previous dataset stays
  until deleted.
- Briefings and approvals are pinned to the dataset they were computed from
  through `dataset_id` and `dataset_sha256`. A newly activated dataset
  starts with no briefing and no approvals, the previous dataset's rows
  stay, and re-activating an earlier dataset serves its own briefing and
  approvals again.
- Deletion is soft through `deleted_at`. Soft-deleted datasets are kept for
  30 days, and then `make purge-deleted` removes the row and the file
  permanently. The active dataset cannot be deleted.
- `audit_events` holds one hash chain per institution, and ids restart at 1
  per institution. Scope 0 is the platform chain for pre-auth events, such
  as failed logins and anonymous refusals, and it belongs to no institution.
- `briefings.sections` holds the whole briefing document, including its
  `sections` key and `meta`, so `GET /briefing` survives restarts.
- `recordings.key` is the sha256 of the canonical received findings.
  Recordings and the in-process caches are keyed by institution, dataset
  sha, role, and question. The model endpoint key stays platform level in
  the operator's environment, never per user.
- `make backup` snapshots the database through the SQLite backup API, plus
  the dataset files and a sha256 manifest, and verifies the copy into
  `var/backups/<timestamp>/`. `make restore FROM=<dir>` runs after `make
  stop` and moves existing files aside, never deleting them.
