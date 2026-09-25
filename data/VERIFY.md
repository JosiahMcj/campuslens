# Fixture verification by hand count

I generated `data/fixture.json` with `data/generate_fixture.py` (seed `20260924`,
deterministic, no wall-clock values). I wrote this file so a reviewer can recount
every planted demo value (M1 = −4.8 %, M2 = 42, M3 = 18, M4 = 12) straight from the
JSON. The script `data/check_fixture.py` recomputes the same numbers and the same
ID lists, and it exits non-zero if anything here disagrees.

## Shape of fixture.json

```json
{
  "meta":  {"title", "description", "fictional", "seed", "generator", "timezone",
            "date_format", "planted_metrics"},
  "terms": {
    "current":    {"term", "name", "start_date", "registration_open_date",
                   "registration_close_date", "as_of_rule", "timezone"},
    "prior_year": {"term", "name", "start_date", "registration_open_date",
                   "registration_close_date", "prior_year_equivalent_date",
                   "as_of_rule", "timezone"},
    "in_session": {"term", "name", "start_date", "end_date", "as_of_rule",
                   "timezone"}
  },
  "students":            [ <185 current-term records> ],
  "prior_year_students": [ <135 prior-year records>   ]
}
```

Each student record (both lists) has exactly these nested groups (names per
ROADMAP.md §4/§5).

```
profile:     {student_id, program, class_level, continuing}
enrollment:  {term, registration_status, registered_credit_hours, registration_date}
holds:       [ {category, amount, responsible_office, hold_date, resolved}, ... ]
advising:    {advisor_id, last_appointment_date, appointment_status}
comparison:  {prior_year_equivalent_date, prior_term_status, baseline}
counseling:  {counseling_notes, chaplain_contact}
```

All dates are ISO `YYYY-MM-DD`, timezone America/Chicago, and `registration_date`
and `last_appointment_date` are `null` when they never happened. Every
`last_appointment_date` is on or before the as-of date, because a *last* appointment
never lies in the future. `appointment_status` is `"completed"` when a date is
present and `"none"` when it is null. The 100-200 row bound applies to current-term
students (`students`, 185 records), and the prior-year population lives in its own
list, 135 records, 320 rows total.

## Fixed dates used below

I derived the fixed dates below from the data, never the wall clock.

| Name | Value | Where it lives |
|---|---|---|
| Current term | `202720` (Spring 2027) | `terms.current.term` |
| Current term start | `2027-01-11` | `terms.current.start_date` |
| As-of date | `2026-11-20` | **derived**. Max `enrollment.registration_date` over `students` (STU-0001 carries it). Never the wall clock |
| Prior term | `202620` (Spring 2026) | `terms.prior_year.term` |
| Prior-year equivalent date | `2025-11-20` | `terms.prior_year.prior_year_equivalent_date` |
| Term in session at as-of | `202710` (Fall 2026) | `terms.in_session.term` |
| In-session term start | `2026-08-24` | `terms.in_session.start_date` (end `2026-12-11`) |

## M1. Spring registration vs. same point last year = −4.8 %

The formula is `registered_continuing(as_of) / registered_continuing(equivalent date) − 1`.

**Numerator = 119.** Count rows in `students` where `profile.continuing` is
`true`, `enrollment.registration_status` is `"registered"`, and
`enrollment.registration_date` ≤ as-of (every registered row satisfies the
last clause), and these are the rows.

<!-- ids:M1_NUM -->
STU-0001 STU-0002 STU-0003 STU-0004 STU-0005 STU-0006 STU-0007 STU-0008 STU-0009 STU-0010 STU-0011 STU-0012 STU-0013 STU-0014 STU-0015 STU-0016 STU-0017 STU-0018 STU-0019 STU-0020 STU-0021 STU-0022 STU-0023 STU-0024 STU-0025 STU-0026 STU-0027 STU-0028 STU-0029 STU-0030 STU-0031 STU-0032 STU-0033 STU-0034 STU-0035 STU-0036 STU-0037 STU-0038 STU-0039 STU-0040 STU-0041 STU-0042 STU-0043 STU-0044 STU-0045 STU-0046 STU-0047 STU-0048 STU-0049 STU-0050 STU-0051 STU-0052 STU-0053 STU-0054 STU-0055 STU-0056 STU-0057 STU-0058 STU-0059 STU-0060 STU-0061 STU-0062 STU-0063 STU-0064 STU-0065 STU-0066 STU-0067 STU-0068 STU-0069 STU-0070 STU-0071 STU-0072 STU-0073 STU-0074 STU-0075 STU-0076 STU-0077 STU-0078 STU-0079 STU-0080 STU-0081 STU-0082 STU-0083 STU-0084 STU-0085 STU-0086 STU-0087 STU-0088 STU-0089 STU-0090 STU-0091 STU-0092 STU-0093 STU-0094 STU-0095 STU-0096 STU-0097 STU-0098 STU-0099 STU-0100 STU-0101 STU-0102 STU-0103 STU-0104 STU-0105 STU-0106 STU-0107 STU-0108 STU-0109 STU-0110 STU-0111 STU-0112 STU-0113 STU-0114 STU-0115 STU-0116 STU-0117 STU-0118 STU-0119

**Denominator = 125.** Count rows in `prior_year_students` where
`profile.continuing` is `true`, `enrollment.registration_status` is
`"registered"`, and `enrollment.registration_date` ≤ `2025-11-20`.

<!-- ids:M1_DEN -->
PRI-0001 PRI-0002 PRI-0003 PRI-0004 PRI-0005 PRI-0006 PRI-0007 PRI-0008 PRI-0009 PRI-0010 PRI-0011 PRI-0012 PRI-0013 PRI-0014 PRI-0015 PRI-0016 PRI-0017 PRI-0018 PRI-0019 PRI-0020 PRI-0021 PRI-0022 PRI-0023 PRI-0024 PRI-0025 PRI-0026 PRI-0027 PRI-0028 PRI-0029 PRI-0030 PRI-0031 PRI-0032 PRI-0033 PRI-0034 PRI-0035 PRI-0036 PRI-0037 PRI-0038 PRI-0039 PRI-0040 PRI-0041 PRI-0042 PRI-0043 PRI-0044 PRI-0045 PRI-0046 PRI-0047 PRI-0048 PRI-0049 PRI-0050 PRI-0051 PRI-0052 PRI-0053 PRI-0054 PRI-0055 PRI-0056 PRI-0057 PRI-0058 PRI-0059 PRI-0060 PRI-0061 PRI-0062 PRI-0063 PRI-0064 PRI-0065 PRI-0066 PRI-0067 PRI-0068 PRI-0069 PRI-0070 PRI-0071 PRI-0072 PRI-0073 PRI-0074 PRI-0075 PRI-0076 PRI-0077 PRI-0078 PRI-0079 PRI-0080 PRI-0081 PRI-0082 PRI-0083 PRI-0084 PRI-0085 PRI-0086 PRI-0087 PRI-0088 PRI-0089 PRI-0090 PRI-0091 PRI-0092 PRI-0093 PRI-0094 PRI-0095 PRI-0096 PRI-0097 PRI-0098 PRI-0099 PRI-0100 PRI-0101 PRI-0102 PRI-0103 PRI-0104 PRI-0105 PRI-0106 PRI-0107 PRI-0108 PRI-0109 PRI-0110 PRI-0111 PRI-0112 PRI-0113 PRI-0114 PRI-0115 PRI-0116 PRI-0117 PRI-0118 PRI-0119 PRI-0120 PRI-0121 PRI-0122 PRI-0123 PRI-0124 PRI-0125

My hand count gives 119 / 125 − 1 = −0.048 = **−4.8 %** exactly.

I planted prior-year decoys, excluded from the denominator. PRI-0126 … PRI-0131
registered *after* the equivalent date (registration_date between 2025-11-21
and 2025-12-19), and PRI-0132 … PRI-0135 never registered at all.

## M2. Continuing students not yet registered = 42

Count rows in `students` where `profile.continuing` is `true` and
`enrollment.registration_status` ≠ `"registered"`.

<!-- ids:M2 -->
STU-0120 STU-0121 STU-0122 STU-0123 STU-0124 STU-0125 STU-0126 STU-0127 STU-0128 STU-0129 STU-0130 STU-0131 STU-0132 STU-0133 STU-0134 STU-0135 STU-0136 STU-0137 STU-0138 STU-0139 STU-0140 STU-0141 STU-0142 STU-0143 STU-0144 STU-0145 STU-0146 STU-0147 STU-0148 STU-0149 STU-0150 STU-0151 STU-0152 STU-0153 STU-0154 STU-0155 STU-0156 STU-0157 STU-0158 STU-0159 STU-0160 STU-0161

I planted decoys here too, excluded by the `continuing` filter. STU-0176 …
STU-0185 are new students (`profile.continuing` is `false`) who are also not
registered, and they do not count toward M2. (STU-0162 … STU-0175 are new and
registered.)

## M3. Of M2, unresolved financial hold under $1,000 = 18

Of the 42 M2 rows, count those with a hold where `category` = `"financial"`,
`resolved` = `false`, and `amount` < `1000`.

<!-- ids:M3 -->
STU-0120 STU-0121 STU-0122 STU-0123 STU-0124 STU-0125 STU-0126 STU-0127 STU-0128 STU-0129 STU-0130 STU-0131 STU-0132 STU-0133 STU-0134 STU-0135 STU-0136 STU-0137

I planted M3 decoys. Each is in M2 unless noted, and each fails exactly one
clause.

<!-- ids:D1 -->
STU-0138 STU-0139 STU-0140 STU-0141

- **D1** (4 rows). A financial hold under $1,000 but `resolved` = `true`,
  excluded because the hold is resolved.

<!-- ids:D2 -->
STU-0142 STU-0143 STU-0144

- **D2** (3 rows). An unresolved financial hold, but the amount is $1,000.00
  exactly (STU-0142), $1,250.00 (STU-0143), or $2,400.00 (STU-0144), excluded
  because the amount is not under $1,000. The boundary $1,000.00 itself does
  not count.

<!-- ids:D3 -->
STU-0145 STU-0146 STU-0147 STU-0148

- **D3** (4 rows). An unresolved hold under $1,000 but `category` is
  `academic`, `library`, or `administrative`, not `financial`, excluded for
  the wrong category.

<!-- ids:D4 -->
STU-0007 STU-0034 STU-0088

- **D4** (3 rows). An unresolved financial hold under $1,000, but the student
  is **registered**, so the row is not in M2, excluded because M3 counts only
  *of M2*.

STU-0149 … STU-0161 (13 rows) are in M2 with no holds at all.

## M4. Of M2, no advising appointment this term = 12

"This term" means the **term in session at the as-of date**, Fall 2026
(`terms.in_session`, start `2026-08-24`), not the spring term being
registered for. Of the 42 M2 rows, count those where
`advising.last_appointment_date` is `null` **or** earlier than `2026-08-24`.

<!-- ids:M4 -->
STU-0120 STU-0121 STU-0122 STU-0123 STU-0124 STU-0125 STU-0126 STU-0127 STU-0138 STU-0139 STU-0140 STU-0141

(STU-0120 … STU-0124 have `null`, and STU-0125 … STU-0127 and STU-0138 … STU-0141
have completed appointments in spring 2026. An old appointment still counts
as "no appointment this term", which is exactly what M4 is meant to surface.)

My M4 decoy.

<!-- ids:D5 -->
STU-0142 STU-0143

- **D5** (2 rows). `last_appointment_date` = `2026-08-24`, exactly the
  in-session term start. An appointment *on* the first day of the term counts
  as this term, so these are excluded. The other 28 M2 rows have completed
  appointments inside Fall 2026 (on or before the as-of date) and are likewise
  excluded.

## Counseling fields (refusal demo only)

Five rows carry gentle, fictional `counseling.counseling_notes` (STU-0026,
STU-0071, STU-0126, STU-0147, STU-0177), with `chaplain_contact: true`. These
fields exist only so the permission layer's refusal is real (ROADMAP.md §5).
No metric reads them. Students are people, not risk scores. I wrote the notes
accordingly.
