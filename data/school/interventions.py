"""Student support programs for Demonstration University: who was eligible,
who took the offer, and what the program's follow-up records show.

Three programs start in Fall 2024 (``202510``). Each has an eligibility rule
a person can read, applied in code to the 24 core tables:

- ``theology_bridge``: theology and ministry majors with a financial hold
  placed in the term get an emergency grant, a payment plan and a check-in
  with the Bursar.
- ``ai_tutoring``: students whose cumulative GPA entering the term is at or
  below the 30th percentile of that term's enrolled students are offered
  free AI tutoring and weekly coaching.
- ``fit_advising``: first-time students who, in their first year, earn a D,
  F or W in two or more courses their major requires, or change major
  twice, are offered major-exploration advising.

This module runs after every core table is written and only ADDS two
tables (``support_programs``, ``support_program_terms``). It reads the core
tables through SQL and never writes to them, so every existing row, every
planted fact in VERIFY.md and the canonical hash of the core tables are
unchanged. Its randomness comes from its own seeded stream per row.

How the planted effects are recorded, honestly. The core tables are the
university's registrar records and must stay byte-identical, so a program
cannot change them. ``support_program_terms`` is the program's own
follow-up record: one row per eligible student per term, before and after
the program started. For every row before Fall 2024 and every eligible
student who did not take part, the outcome columns are copied exactly from
the core tables. Only after-period participants differ, by these planted
amounts (with noise):

- AI tutoring: term GPA +0.15; 15 % of participants who would not have come
  back the next term do come back. Students with a higher GPA entering the
  term are more likely to accept, so a naive participant-versus-non-
  participant comparison overstates the effect; comparing within GPA bands
  recovers it.
- Theology bridge: 35 % of participants who would not have come back do;
  40 % of returning participants who would have had a financial hold the
  next term do not.
- Fit advising: 25 % of participants who would not have come back do;
  12 % of returning participants who would have stayed in an ill-fitting
  major change major the next term.

Stdlib only, Python 3.9.
"""

from __future__ import annotations

import random
import sqlite3
from typing import Dict, List, Optional, Tuple

START_TERM = "202510"  # Fall 2024

THEOLOGY_MAJORS = ("BIBL", "CHST", "MINS", "THEO", "YFMN")

# (program_id, name, eligibility rule, offer, owner office, primary outcome,
# secondary outcome). The rule text is what the Interventions page shows.
PROGRAMS: Tuple[Tuple[str, str, str, str, str, str, str], ...] = (
    (
        "theology_bridge",
        "Theology and ministry funding bridge",
        "Theology and ministry majors (Biblical Studies, Christian Studies, Christian "
        "Ministry, Theology, Youth Ministry) with a financial hold placed on their "
        "account in the term.",
        "An emergency grant toward the balance, a payment plan, and a check-in with the "
        "Bursar's office.",
        "Financial Aid",
        "returned_next_term",
        "financial_hold_next_term",
    ),
    (
        "ai_tutoring",
        "AI tutoring and coaching",
        "Students whose cumulative GPA entering the term is in the bottom 30 % of the "
        "students enrolled that term (at or below the 30th percentile). Students in "
        "their first term have no GPA yet and are not included.",
        "Free AI tutoring for their courses and a weekly coaching check-in for the term.",
        "Student Success",
        "term_gpa",
        "returned_next_term",
    ),
    (
        "fit_advising",
        "Early major-fit advising",
        "First-time students in their first year who have earned a D, F or W in two or "
        "more courses their major requires, or who have changed major twice. Each "
        "student is counted in the term the rule is first met.",
        "A major-exploration advising meeting and, where it helps, a planned move to a "
        "major that fits better.",
        "Academic Advising",
        "returned_next_term",
        "changed_major_next_term",
    ),
)
PROGRAM_IDS = tuple(p[0] for p in PROGRAMS)

# Planted effects (see the module notes).
TUTORING_GPA_LIFT = 0.15
FLIP_RETURN = {"ai_tutoring": 0.15, "theology_bridge": 0.35, "fit_advising": 0.25}
BRIDGE_HOLD_CLEARED = 0.40
FIT_CHANGE_LIFT = 0.12

SCHEMA_SQL = """
CREATE TABLE support_programs (
    program_id TEXT PRIMARY KEY, name TEXT NOT NULL, eligibility_rule TEXT NOT NULL,
    offer TEXT NOT NULL, owner_office TEXT NOT NULL,
    start_term TEXT NOT NULL REFERENCES academic_periods(term_code),
    primary_outcome TEXT NOT NULL, secondary_outcome TEXT NOT NULL);
CREATE TABLE support_program_terms (
    program_id TEXT NOT NULL REFERENCES support_programs(program_id),
    student_id TEXT NOT NULL REFERENCES students(student_id),
    term_code TEXT NOT NULL REFERENCES academic_periods(term_code),
    period TEXT NOT NULL CHECK (period IN ('before','after')),
    gpa_at_eligibility REAL,
    offered INTEGER NOT NULL CHECK (offered IN (0, 1)),
    accepted INTEGER CHECK (accepted IN (0, 1)),
    term_gpa REAL,
    returned_next_term INTEGER CHECK (returned_next_term IN (0, 1)),
    financial_hold_next_term INTEGER CHECK (financial_hold_next_term IN (0, 1)),
    changed_major_next_term INTEGER CHECK (changed_major_next_term IN (0, 1)),
    PRIMARY KEY (program_id, student_id, term_code));
CREATE INDEX idx_spt_term ON support_program_terms(program_id, term_code);
"""

COLUMNS: Dict[str, List[str]] = {
    "support_programs": ["program_id", "name", "eligibility_rule", "offer", "owner_office",
                         "start_term", "primary_outcome", "secondary_outcome"],
    "support_program_terms": ["program_id", "student_id", "term_code", "period",
                              "gpa_at_eligibility", "offered", "accepted", "term_gpa",
                              "returned_next_term", "financial_hold_next_term",
                              "changed_major_next_term"],
}


def next_regular(code: str) -> str:
    """The next fall or spring term after a fall or spring term."""
    if code.endswith("10"):
        return code[:4] + "20"
    return str(int(code[:4]) + 1) + "10"


def bottom_share_count(n: int) -> int:
    """How many of n students are the bottom 30 % (rounded up, in integers)."""
    return (3 * n + 9) // 10


class Records:
    """The core rows the rules and outcomes read, loaded once."""

    def __init__(self, con: sqlite3.Connection) -> None:
        self.terms = [r[0] for r in con.execute(
            "SELECT term_code FROM academic_periods ORDER BY sequence")]
        self.regular = [t for t in self.terms if t[4:] in ("10", "20")]
        self.major_of = dict(con.execute(
            "SELECT program_code, major_code FROM academic_programs"))
        # student -> [(term, program, term_gpa, cumulative_gpa)] in term order
        self.recs: Dict[str, List[Tuple[str, str, Optional[float], Optional[float]]]] = {}
        self.rec_at: Dict[Tuple[str, str], Tuple[str, Optional[float], Optional[float]]] = {}
        self.by_term: Dict[str, List[str]] = {}
        for sid, term, prog, tg, cg in con.execute(
                "SELECT student_id, term_code, program_code, term_gpa, cumulative_gpa "
                "FROM student_term_records ORDER BY student_id, term_code"):
            self.recs.setdefault(sid, []).append((term, prog, tg, cg))
            self.rec_at[(sid, term)] = (prog, tg, cg)
            self.by_term.setdefault(term, []).append(sid)
        self.status = {(sid, term): st for sid, term, st in con.execute(
            "SELECT student_id, term_code, status FROM student_term_enrollment")}
        self.fin_hold = {(sid, term) for sid, term in con.execute(
            "SELECT DISTINCT student_id, term_code FROM person_holds "
            "WHERE category = 'financial'")}
        self.students = {sid: (entry, etype) for sid, entry, etype in con.execute(
            "SELECT student_id, entry_term, entry_type FROM students")}
        self.changed_end: Dict[str, List[str]] = {}
        for sid, end in con.execute(
                "SELECT student_id, end_term FROM student_academic_programs "
                "WHERE status = 'changed'"):
            self.changed_end.setdefault(sid, []).append(end)
        # D, F or W grades in a course the student's program (that term) requires
        # as a major or support course, per student and term.
        self.req_dfw: Dict[Tuple[str, str], int] = {}
        for sid, term, n in con.execute(
                "SELECT r.student_id, r.term_code, COUNT(*) FROM section_registrations r "
                "JOIN final_grades g ON g.registration_id = r.registration_id "
                "JOIN sections s ON s.section_id = r.section_id "
                "JOIN student_term_records t ON t.student_id = r.student_id "
                "  AND t.term_code = r.term_code "
                "JOIN program_requirements pr ON pr.program_code = t.program_code "
                "  AND pr.course_id = s.course_id "
                "  AND pr.requirement_type IN ('major', 'support') "
                "WHERE g.grade IN ('D+','D','F','W') GROUP BY 1, 2"):
            self.req_dfw[(sid, term)] = n

    def prior_gpa(self, sid: str, term: str) -> Optional[float]:
        """Cumulative GPA at the end of the student's latest term before ``term``."""
        best = None
        for t, _p, _tg, cg in self.recs.get(sid, ()):
            if t >= term:
                break
            if cg is not None:
                best = cg
        return best


def tutoring_eligible(rec: Records, term: str) -> Dict[str, float]:
    """student -> GPA entering the term, for the bottom 30 % of the term."""
    gpas = {}
    for sid in rec.by_term.get(term, ()):
        g = rec.prior_gpa(sid, term)
        if g is not None:
            gpas[sid] = g
    if not gpas:
        return {}
    ordered = sorted(gpas.values())
    cutoff = ordered[bottom_share_count(len(ordered)) - 1]
    return {sid: g for sid, g in gpas.items() if g <= cutoff}


def bridge_eligible(rec: Records, term: str) -> Dict[str, Optional[float]]:
    out: Dict[str, Optional[float]] = {}
    for sid in rec.by_term.get(term, ()):
        prog = rec.rec_at[(sid, term)][0]
        if rec.major_of[prog] in THEOLOGY_MAJORS and (sid, term) in rec.fin_hold:
            out[sid] = rec.prior_gpa(sid, term)
    return out


def _first_year(rec: Records, sid: str) -> Tuple[str, ...]:
    entry, etype = rec.students[sid]
    if etype != "first_time" or entry not in rec.regular:
        return ()
    return (entry, next_regular(entry))


def _fit_signal(rec: Records, sid: str, term: str, year: Tuple[str, ...]) -> bool:
    dfw = sum(rec.req_dfw.get((sid, t), 0) for t in year if t <= term)
    changes = sum(1 for end in rec.changed_end.get(sid, ()) if end <= term)
    return dfw >= 2 or changes >= 2


def fit_eligible(rec: Records, term: str) -> Dict[str, Optional[float]]:
    """student -> cumulative GPA at the end of the term, for first-year students
    who first meet the fit rule in this term."""
    out: Dict[str, Optional[float]] = {}
    for sid in rec.by_term.get(term, ()):
        cg = rec.rec_at[(sid, term)][2]
        year = _first_year(rec, sid)
        if term not in year:
            continue
        if not _fit_signal(rec, sid, term, year):
            continue
        if term == year[1] and _fit_signal(rec, sid, year[0], year):
            continue  # already counted in the first term
        out[sid] = cg
    return out


def eligible(rec: Records, program: str, term: str) -> Dict[str, Optional[float]]:
    if program == "ai_tutoring":
        return dict(tutoring_eligible(rec, term))
    if program == "theology_bridge":
        return bridge_eligible(rec, term)
    return fit_eligible(rec, term)


def _accept_probability(program: str, gpa: Optional[float]) -> float:
    if program == "theology_bridge":
        return 0.78
    g = 2.0 if gpa is None else gpa
    if program == "ai_tutoring":
        # Students doing better already are more likely to say yes: the
        # selection a naive comparison mistakes for an effect.
        return min(0.85, max(0.15, 0.25 + 0.22 * (g - 1.0)))
    return min(0.85, max(0.20, 0.50 + 0.15 * (g - 1.5)))


def _clamp_gpa(x: float) -> float:
    return round(min(4.0, max(0.0, x)), 2)


def program_rows(rec: Records, seed: int) -> List[tuple]:
    rows: List[tuple] = []
    for program in PROGRAM_IDS:
        for term in rec.regular:
            who = eligible(rec, program, term)
            nxt = next_regular(term)
            has_next = nxt in rec.terms
            for sid in sorted(who):
                gpa = who[sid]
                prog, term_gpa, _cg = rec.rec_at[(sid, term)]
                returned: Optional[int] = None
                hold_next: Optional[int] = None
                changed_next: Optional[int] = None
                if has_next:
                    st = rec.status.get((sid, nxt))
                    if st is not None and st != "graduated":
                        returned = 1 if st == "enrolled" else 0
                if returned == 1:
                    hold_next = 1 if (sid, nxt) in rec.fin_hold else 0
                    changed_next = 1 if rec.rec_at[(sid, nxt)][0] != prog else 0
                after = term >= START_TERM
                accepted: Optional[int] = None
                if after:
                    rng = random.Random(f"{seed}:interventions:{program}:{sid}:{term}")
                    draws = [rng.random() for _ in range(4)]
                    noise = rng.gauss(0.0, 0.08)
                    accepted = 1 if draws[0] < _accept_probability(program, gpa) else 0
                    if accepted:
                        flipped = returned == 0 and draws[1] < FLIP_RETURN[program]
                        if flipped:
                            returned = 1
                        if program == "ai_tutoring" and term_gpa is not None:
                            term_gpa = _clamp_gpa(term_gpa + TUTORING_GPA_LIFT + noise)
                        if flipped:
                            hold_next = 1 if draws[2] < 0.15 else 0
                            changed_next = 1 if draws[3] < 0.40 else 0
                        elif returned == 1:
                            if hold_next == 1 and draws[2] < BRIDGE_HOLD_CLEARED:
                                hold_next = 0
                            if changed_next == 0 and draws[3] < FIT_CHANGE_LIFT:
                                changed_next = 1
                if program != "ai_tutoring":
                    term_gpa = None
                if program != "theology_bridge":
                    hold_next = None
                if program != "fit_advising":
                    changed_next = None
                rows.append((program, sid, term, "after" if after else "before", gpa,
                             1 if after else 0, accepted, term_gpa, returned, hold_next,
                             changed_next))
    return rows


def write_tables(con: sqlite3.Connection, seed: int) -> None:
    """Add the two program tables to an open database whose core tables are
    already written (called from generate.write_db before the commit)."""
    con.executescript(SCHEMA_SQL)
    con.executemany("INSERT INTO support_programs VALUES (?,?,?,?,?,?,?,?)", [
        (pid, name, rule, offer, office, START_TERM, primary, secondary)
        for pid, name, rule, offer, office, primary, secondary in PROGRAMS])
    rec = Records(con)
    con.executemany("INSERT INTO support_program_terms VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    program_rows(rec, seed))
