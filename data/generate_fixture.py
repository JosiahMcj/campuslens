#!/usr/bin/env python3
"""Seeded generator for data/fixture.json.

Produces fictional Ellucian-style student records for the Golden Eagle AI
Cabinet demo. Field names and nesting follow ROADMAP.md sections 4 and 5
(SCHEMA.md is being written in parallel and will be reconciled against the
real fixture). Stdlib only; runs on Python 3.9.

Planted values (ROADMAP.md section 2/4), exact by construction:
  M1 = -4.8 %  (119 registered continuing as of as-of / 125 registered
                continuing as of the prior-year equivalent date - 1)
  M2 = 42      (continuing students not registered)
  M3 = 18      (of M2, unresolved financial hold under $1,000)
  M4 = 12      (of M2, no advising appointment in the term in session:
                last appointment null or before terms.in_session.start_date)

Deterministic: fixed seed, no wall clock anywhere in the output. Run:

    python3 data/generate_fixture.py

Running it twice must produce byte-identical data/fixture.json.
"""

import json
import random
from datetime import date, timedelta
from pathlib import Path

SEED = 20260924
HERE = Path(__file__).resolve().parent
OUT_PATH = HERE / "fixture.json"

TIMEZONE = "America/Chicago"

# Current term: Spring 2027. Registration opened 2026-11-02.
CURRENT_TERM = "202720"
CURRENT_TERM_NAME = "Spring 2027"
CURRENT_START = "2027-01-11"
REG_OPEN = "2026-11-02"
REG_CLOSE = "2026-12-18"

# Prior year term: Spring 2026.
PRIOR_TERM = "202620"
PRIOR_TERM_NAME = "Spring 2026"
PRIOR_START = "2026-01-12"
PRIOR_REG_OPEN = "2025-11-03"
PRIOR_REG_CLOSE = "2025-12-19"
PRIOR_EQUIV = "2025-11-20"

# Term in session at the as-of date: Fall 2026. M4's "this term" means this
# term, not the spring term being registered for.
IN_SESSION_TERM = "202710"
IN_SESSION_NAME = "Fall 2026"
IN_SESSION_START = "2026-08-24"
IN_SESSION_END = "2026-12-11"

# As-of date. Per ROADMAP section 4 this is never stored in the fixture; it is
# derived as max(enrollment.registration_date) over current-term rows. The
# generator guarantees the maximum lands exactly here (STU-0001).
AS_OF = "2026-11-20"

PROGRAMS = [
    "BA-BIBL", "BS-BUS", "BA-PSYC", "BS-CSCI", "BA-EDUC",
    "BS-NURS", "BA-ENGL", "BS-BIOL", "BA-MUSC", "BA-COMM",
]
CLASS_LEVELS = ["freshman", "sophomore", "junior", "senior"]
ADVISORS = ["ADV-%03d" % n for n in range(1, 13)]

OFFICE_BY_CATEGORY = {
    "financial": "Bursar",
    "academic": "Registrar",
    "administrative": "Student Life",
    "library": "Library",
}

# Gentle, fictional counseling notes. Present only so the permission layer has
# something real to refuse (ROADMAP section 5); no metric reads them.
COUNSELING_NOTES = {
    "STU-0026": "Chatted with the campus chaplain after chapel; settling into the semester well.",
    "STU-0071": "Asked about grief support after a family loss; connected with the care team and doing okay.",
    "STU-0126": "Feeling homesick this fall; encouraged to join a small group and check in next month.",
    "STU-0147": "Appreciated prayer before midterms; reports things are looking up.",
    "STU-0177": "New to campus and looking for community; invited to the weekly dinner group.",
}


def d(iso):
    return date.fromisoformat(iso)


def rand_date(rng, start_iso, end_iso):
    span = (d(end_iso) - d(start_iso)).days
    return (d(start_iso) + timedelta(days=rng.randrange(span + 1))).isoformat()


def money(rng, low, high):
    return round(rng.uniform(low, high), 2)


def profile(rng, sid, continuing):
    return {
        "student_id": sid,
        "program": rng.choice(PROGRAMS),
        "class_level": rng.choice(CLASS_LEVELS),
        "continuing": continuing,
    }


def enrollment(term, registered, registration_date, credit_hours):
    return {
        "term": term,
        "registration_status": "registered" if registered else "not_registered",
        "registered_credit_hours": credit_hours,
        "registration_date": registration_date,
    }


def advising(advisor_id, last_appointment_date, appointment_status):
    return {
        "advisor_id": advisor_id,
        "last_appointment_date": last_appointment_date,
        "appointment_status": appointment_status,
    }


def comparison(prior_year_equivalent_date, prior_term_status, baseline):
    return {
        "prior_year_equivalent_date": prior_year_equivalent_date,
        "prior_term_status": prior_term_status,
        "baseline": baseline,
    }


def counseling(sid):
    note = COUNSELING_NOTES.get(sid)
    return {
        "counseling_notes": note,
        "chaplain_contact": note is not None,
    }


def make_hold(category, amount, hold_date, resolved):
    return {
        "category": category,
        "amount": amount,
        "responsible_office": OFFICE_BY_CATEGORY[category],
        "hold_date": hold_date,
        "resolved": resolved,
    }


def student(sid, prof, enr, holds, adv, comp):
    return {
        "profile": prof,
        "enrollment": enr,
        "holds": holds,
        "advising": adv,
        "comparison": comp,
        "counseling": counseling(sid),
    }


def completed_in_session_advising(rng):
    # An advising appointment inside the term in session (Fall 2026), never
    # after the as-of date.
    return advising(
        rng.choice(ADVISORS),
        rand_date(rng, IN_SESSION_START, AS_OF),
        "completed",
    )


def completed_spring_advising(rng):
    # A completed appointment in spring 2026, before the in-session term
    # started: "no advising appointment this term".
    return advising(
        rng.choice(ADVISORS),
        rand_date(rng, "2026-01-12", "2026-05-01"),
        "completed",
    )


def mixed_advising(rng):
    # For students whose advising record does not affect any metric. Every
    # date is on or before the as-of date; 'last' appointments never lie in
    # the future.
    roll = rng.random()
    if roll < 0.70:
        return completed_in_session_advising(rng)
    if roll < 0.95:
        return completed_spring_advising(rng)
    return advising(rng.choice(ADVISORS), None, "none")


def build_current_students(rng):
    students = []

    # --- 119 registered continuing students: STU-0001..STU-0119 -------------
    # D4 decoys (financial hold < $1,000 on a *registered* student): 0007, 0034, 0088.
    d4_ids = {"STU-0007", "STU-0034", "STU-0088"}
    for n in range(1, 120):
        sid = "STU-%04d" % n
        if n == 1:
            reg_date = AS_OF  # forces the as-of date
        else:
            reg_date = rand_date(rng, REG_OPEN, AS_OF)
        holds = []
        if sid in d4_ids:
            holds.append(make_hold("financial", money(rng, 60.0, 950.0),
                                   rand_date(rng, "2026-09-01", "2026-11-10"), False))
        students.append(student(
            sid,
            profile(rng, sid, True),
            enrollment(CURRENT_TERM, True, reg_date, rng.randint(12, 18)),
            holds,
            mixed_advising(rng),
            comparison(PRIOR_EQUIV, "registered" if rng.random() < 0.85 else "not_registered", PRIOR_TERM),
        ))

    # --- 42 unregistered continuing students: STU-0120..STU-0161 ------------
    # Holds:   M3 = 0120..0137 (18, unresolved financial < $1,000)
    #          D1 = 0138..0141 (4, resolved financial < $1,000)
    #          D2 = 0142..0144 (3, unresolved financial >= $1,000)
    #          D3 = 0145..0148 (4, unresolved non-financial < $1,000)
    #          0149..0161      (13, no holds)
    # Advising: "this term" = the term in session (Fall 2026, start
    # 2026-08-24). M4 = 12 -> 0120..0124 null (5), 0125..0127 + 0138..0141
    # last appointment in spring 2026, before the in-session start (7);
    # D5 = 0142, 0143 exactly on the in-session start (decoys: on-start
    # counts as this term, so NOT in M4); the remaining 28 have completed
    # appointments inside the in-session term.
    for n in range(120, 162):
        sid = "STU-%04d" % n
        holds = []
        if 120 <= n <= 137:
            holds.append(make_hold("financial", money(rng, 35.0, 995.0),
                                   rand_date(rng, "2026-09-01", "2026-11-10"), False))
        elif 138 <= n <= 141:
            holds.append(make_hold("financial", money(rng, 50.0, 900.0),
                                   rand_date(rng, "2026-08-15", "2026-10-01"), True))
        elif 142 <= n <= 144:
            amount = {142: 1000.00, 143: 1250.00, 144: 2400.00}[n]
            holds.append(make_hold("financial", amount,
                                   rand_date(rng, "2026-09-01", "2026-11-10"), False))
        elif 145 <= n <= 148:
            category = {145: "academic", 146: "library", 147: "administrative", 148: "academic"}[n]
            holds.append(make_hold(category, money(rng, 20.0, 400.0),
                                   rand_date(rng, "2026-09-01", "2026-11-10"), False))

        if 120 <= n <= 124:
            adv = advising(rng.choice(ADVISORS), None, "none")
        elif 125 <= n <= 127 or 138 <= n <= 141:
            adv = completed_spring_advising(rng)
        elif 142 <= n <= 143:
            adv = advising(rng.choice(ADVISORS), IN_SESSION_START, "completed")  # D5 decoy
        else:
            adv = completed_in_session_advising(rng)

        students.append(student(
            sid,
            profile(rng, sid, True),
            enrollment(CURRENT_TERM, False, None, 0),
            holds,
            adv,
            comparison(PRIOR_EQUIV, "registered" if rng.random() < 0.75 else "not_registered", PRIOR_TERM),
        ))

    # --- 24 new (non-continuing) students: STU-0162..STU-0185 ---------------
    # 14 registered (0162..0175), 10 not (0176..0185). Excluded from M1/M2 by
    # the continuing filter; they make that filter matter.
    for n in range(162, 186):
        sid = "STU-%04d" % n
        registered = n <= 175
        reg_date = rand_date(rng, REG_OPEN, AS_OF) if registered else None
        students.append(student(
            sid,
            profile(rng, sid, False),
            enrollment(CURRENT_TERM, registered, reg_date, rng.randint(12, 18) if registered else 0),
            [],
            mixed_advising(rng),
            comparison(PRIOR_EQUIV, "not_enrolled", PRIOR_TERM),
        ))

    return students


def build_prior_year_students(rng):
    students = []
    # 125 registered continuing as of the prior-year equivalent date:
    # PRI-0001..PRI-0125, registration_date within [2025-11-03, 2025-11-20].
    # Decoys: PRI-0126..PRI-0131 registered *after* the equivalent date
    # (2025-11-21..2025-12-19); PRI-0132..PRI-0135 never registered.
    for n in range(1, 136):
        sid = "PRI-%04d" % n
        if n <= 125:
            registered = True
            reg_date = rand_date(rng, PRIOR_REG_OPEN, PRIOR_EQUIV)
        elif n <= 131:
            registered = True
            reg_date = rand_date(rng, "2025-11-21", PRIOR_REG_CLOSE)
        else:
            registered = False
            reg_date = None
        students.append(student(
            sid,
            profile(rng, sid, True),
            enrollment(PRIOR_TERM, registered, reg_date, rng.randint(12, 18) if registered else 0),
            [],
            advising(rng.choice(ADVISORS), rand_date(rng, "2026-01-12", "2026-04-20"), "completed"),
            comparison("2024-11-21", "registered" if rng.random() < 0.8 else "not_registered", "202520"),
        ))
    return students


def build_fixture():
    rng = random.Random(SEED)
    fixture = {
        "meta": {
            "title": "Golden Eagle AI Cabinet - fictional spring registration fixture",
            "description": (
                "Fictional Ellucian-style student records for the President Weekly "
                "Student Success Briefing demo. No real student data. Field groups "
                "follow ROADMAP.md sections 4 and 5. Planted demo values: "
                "M1 = -4.8 %, M2 = 42, M3 = 18, M4 = 12; row-ID lists in data/VERIFY.md."
            ),
            "fictional": True,
            "seed": SEED,
            "generator": "data/generate_fixture.py",
            "timezone": TIMEZONE,
            "date_format": "YYYY-MM-DD",
            "planted_metrics": {"M1": -0.048, "M2": 42, "M3": 18, "M4": 12},
        },
        "terms": {
            "current": {
                "term": CURRENT_TERM,
                "name": CURRENT_TERM_NAME,
                "start_date": CURRENT_START,
                "registration_open_date": REG_OPEN,
                "registration_close_date": REG_CLOSE,
                "as_of_rule": (
                    "as_of = max(enrollment.registration_date) over current-term rows "
                    "(the 'students' list); never the wall clock"
                ),
                "timezone": TIMEZONE,
            },
            "prior_year": {
                "term": PRIOR_TERM,
                "name": PRIOR_TERM_NAME,
                "start_date": PRIOR_START,
                "registration_open_date": PRIOR_REG_OPEN,
                "registration_close_date": PRIOR_REG_CLOSE,
                "prior_year_equivalent_date": PRIOR_EQUIV,
                "as_of_rule": (
                    "prior-year window anchored to prior_year_equivalent_date: a "
                    "prior-year row counts as registered when registration_date <= "
                    "prior_year_equivalent_date"
                ),
                "timezone": TIMEZONE,
            },
            "in_session": {
                "term": IN_SESSION_TERM,
                "name": IN_SESSION_NAME,
                "start_date": IN_SESSION_START,
                "end_date": IN_SESSION_END,
                "as_of_rule": (
                    "the term in session at the as-of date; M4's 'this term' "
                    "means this term, not the spring term being registered for"
                ),
                "timezone": TIMEZONE,
            },
        },
        "students": build_current_students(rng),
        "prior_year_students": build_prior_year_students(rng),
    }
    return fixture


def main():
    fixture = build_fixture()
    text = json.dumps(fixture, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    with open(OUT_PATH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    current = fixture["students"]
    prior = fixture["prior_year_students"]
    print("wrote %s" % OUT_PATH)
    print("current-term students: %d (continuing %d, new %d)" % (
        len(current),
        sum(1 for s in current if s["profile"]["continuing"]),
        sum(1 for s in current if not s["profile"]["continuing"]),
    ))
    print("prior-year students: %d" % len(prior))
    print("planted: M1 = -4.8 %, M2 = 42, M3 = 18, M4 = 12 (verify with data/check_fixture.py)")


if __name__ == "__main__":
    main()
