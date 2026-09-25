#!/usr/bin/env python3
"""Independent checker for data/fixture.json.

Recomputes M1-M4 straight from the fixture by the ROADMAP.md section 4
formulas, prints the values and the row-ID lists, and exits non-zero if any
value differs from the planted value or from the ID lists in data/VERIFY.md.
This is independent evidence for the reviewer, not the product metric code.

Stdlib only; Python 3.9. Run:

    python3 data/check_fixture.py
"""

import json
import re
import sys
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE_PATH = HERE / "fixture.json"
VERIFY_PATH = HERE / "VERIFY.md"

EXPECTED = {
    "M1": Fraction(-6, 125),  # 119/125 - 1 = -0.048 exactly
    "M2": 42,
    "M3": 18,
    "M4": 12,
}


def sid(record):
    return record["profile"]["student_id"]


def is_registered(record):
    return record["enrollment"]["registration_status"] == "registered"


def compute(fixture):
    terms = fixture["terms"]
    current_term = terms["current"]
    prior_term = terms["prior_year"]
    students = fixture["students"]
    prior_students = fixture["prior_year_students"]

    # As-of: latest registration_date among current-term rows (never the wall
    # clock). Every current-term row is in the "students" list.
    reg_dates = [s["enrollment"]["registration_date"] for s in students
                 if s["enrollment"]["registration_date"] is not None]
    as_of = max(reg_dates)

    # M1 numerator: registered continuing current-term students as of as-of.
    m1_num = sorted(sid(s) for s in students
                    if s["profile"]["continuing"]
                    and is_registered(s)
                    and s["enrollment"]["registration_date"] <= as_of)
    # M1 denominator: prior-year continuing students registered as of the
    # prior-year equivalent date.
    equiv = prior_term["prior_year_equivalent_date"]
    m1_den = sorted(sid(s) for s in prior_students
                    if s["profile"]["continuing"]
                    and is_registered(s)
                    and s["enrollment"]["registration_date"] is not None
                    and s["enrollment"]["registration_date"] <= equiv)
    m1 = Fraction(len(m1_num), len(m1_den)) - 1

    # M2: continuing students not registered (as-of).
    m2 = sorted(sid(s) for s in students
                if s["profile"]["continuing"] and not is_registered(s))
    m2_set = set(m2)

    # M3: of M2, unresolved financial hold under $1,000.
    m3 = sorted(sid(s) for s in students
                if sid(s) in m2_set
                and any(h["category"] == "financial"
                        and not h["resolved"]
                        and h["amount"] < 1000
                        for h in s["holds"]))

    # M4: of M2, no advising appointment in the term in session (last
    # appointment null or before terms.in_session.start_date; an appointment
    # exactly on the in-session start counts as this term).
    in_session_start = terms["in_session"]["start_date"]
    m4 = sorted(sid(s) for s in students
                if sid(s) in m2_set
                and (s["advising"]["last_appointment_date"] is None
                     or s["advising"]["last_appointment_date"] < in_session_start))

    # A 'last' appointment can never lie in the future relative to as-of.
    future_advising = sorted(
        sid(s) for s in students + prior_students
        if s["advising"]["last_appointment_date"] is not None
        and s["advising"]["last_appointment_date"] > as_of)
    # appointment_status mirrors the date: 'completed' with a date, 'none'
    # when null.
    bad_status = sorted(
        sid(s) for s in students + prior_students
        if (s["advising"]["last_appointment_date"] is None)
        != (s["advising"]["appointment_status"] == "none"))

    return {
        "as_of": as_of,
        "equiv": equiv,
        "term_start": current_term["start_date"],
        "in_session_start": in_session_start,
        "M1": m1,
        "M1_NUM": m1_num,
        "M1_DEN": m1_den,
        "M2": m2,
        "M3": m3,
        "M4": m4,
        "future_advising": future_advising,
        "bad_status": bad_status,
    }


def parse_verify_ids(text):
    """Parse '<!-- ids:KEY -->' markers; IDs are the following non-empty
    line(s) of space-separated STU-/PRI- tokens."""
    ids = {}
    pattern = re.compile(
        r"<!--\s*ids:([A-Z0-9_]+)\s*-->\s*\n((?:[A-Z]{3}-\d{4}(?:\s+|$))+)",
        re.MULTILINE,
    )
    for match in pattern.finditer(text):
        key = match.group(1)
        ids[key] = match.group(2).split()
    return ids


def main():
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    result = compute(fixture)
    verify_ids = parse_verify_ids(VERIFY_PATH.read_text(encoding="utf-8"))

    failures = []

    def check(label, actual, expected):
        ok = actual == expected
        print("%-28s %s" % (label, "OK" if ok else "MISMATCH"))
        if not ok:
            failures.append("%s: expected %r, got %r" % (label, expected, actual))

    print("as-of date (derived):       %s" % result["as_of"])
    print("prior-year equivalent date: %s" % result["equiv"])
    print("current term start:         %s" % result["term_start"])
    print("in-session term start:      %s" % result["in_session_start"])
    check("no advising appointment after as-of", result["future_advising"], [])
    check("appointment_status mirrors date", result["bad_status"], [])
    print()

    m1 = result["M1"]
    print("M1 = %d/%d - 1 = %s = %.1f %%" % (
        len(result["M1_NUM"]), len(result["M1_DEN"]), m1, float(m1) * 100))
    check("M1 planted -0.048", m1, EXPECTED["M1"])
    print("M2 = %d" % len(result["M2"]))
    check("M2 planted 42", len(result["M2"]), EXPECTED["M2"])
    print("M3 = %d" % len(result["M3"]))
    check("M3 planted 18", len(result["M3"]), EXPECTED["M3"])
    print("M4 = %d" % len(result["M4"]))
    check("M4 planted 12", len(result["M4"]), EXPECTED["M4"])
    print()

    for key in ("M1_NUM", "M1_DEN", "M2", "M3", "M4"):
        computed = result[key]
        print("%s (%d rows): %s%s" % (
            key, len(computed), " ".join(computed[:8]),
            " ..." if len(computed) > 8 else ""))
        if key not in verify_ids:
            failures.append("VERIFY.md has no '<!-- ids:%s -->' block" % key)
            continue
        check("%s matches VERIFY.md (%d IDs)" % (key, len(verify_ids[key])),
              sorted(verify_ids[key]), computed)
    print()

    # Decoys listed in VERIFY.md must stay out of the metric they imitate.
    decoy_rules = [
        ("D1", result["M3"], "resolved financial hold < $1,000"),
        ("D2", result["M3"], "unresolved financial hold >= $1,000"),
        ("D3", result["M3"], "unresolved non-financial hold < $1,000"),
        ("D4", result["M3"], "financial hold < $1,000 on a registered student"),
        ("D5", result["M4"], "advising appointment exactly on the in-session start"),
    ]
    for key, metric_ids, why in decoy_rules:
        if key not in verify_ids:
            failures.append("VERIFY.md has no '<!-- ids:%s -->' block" % key)
            continue
        leaked = sorted(set(verify_ids[key]) & set(metric_ids))
        check("decoy %s excluded (%s)" % (key, why), leaked, [])

    print()
    if failures:
        print("FAIL:")
        for failure in failures:
            print("  - %s" % failure)
        return 1
    print("PASS: fixture matches planted values and VERIFY.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
