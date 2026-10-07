#!/usr/bin/env python3
"""Deterministic generator for Demonstration University, a whole synthetic school.

Writes an Ellucian-shaped SQLite database (tables documented in SCHEMA.md)
covering six academic years, Fall 2020 (202110) to Spring 2026 (202620):
programs, courses, sections, fictional instructors, pseudonymous students,
registrations, final grades, GPAs, standings, holds, advising, appointments.

Everything is simulated from one seed. The same seed and scale always give the
same rows (check.py's canonical hash proves it). No wall-clock value is
written anywhere. Planted facts (VERIFY.md) are produced by the grade model
below, not written in afterwards, so every one of them is recomputable from
the raw rows.

Usage:
    python3 data/school/generate.py                 # scale 1.0 -> var/school/school.db
    python3 data/school/generate.py --scale 0.05 --out /tmp/school.db

Stdlib only (sqlite3, random, datetime, hashlib, json).
"""

from __future__ import annotations

import argparse
import math
import os
import random
import sqlite3
import sys
import time
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import catalog as C  # noqa: E402

SEED = 20261005
GENERATOR_VERSION = "1"
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "var" / "school" / "school.db"

# --------------------------------------------------------------------------
# Grade scale. Points are stored in tenths so quality points stay exact.
# --------------------------------------------------------------------------
LETTER_POINTS10: dict[str, int] = {
    "A": 40, "A-": 37, "B+": 33, "B": 30, "B-": 27, "C+": 23, "C": 20, "C-": 17,
    "D+": 13, "D": 10, "F": 0,
}
GRADE_CUTS: list[tuple[float, str]] = [
    (0.85, "A"), (0.50, "A-"), (0.20, "B+"), (-0.10, "B"), (-0.40, "B-"), (-0.70, "C+"),
    (-1.00, "C"), (-1.25, "C-"), (-1.50, "D+"), (-1.75, "D"),
]
BLOCKING_HOLDS = {"financial", "registrar", "advising"}
PASSING = {"A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D+", "D", "P"}
DFW = {"D+", "D", "F", "W"}

# --------------------------------------------------------------------------
# Planted facts (VERIFY.md). The generator produces them through the grade
# model; check.py recomputes them from raw rows.
# --------------------------------------------------------------------------
PLANT_LOW_GPA_PROGRAM = "MEEN"
THERMO = "MEEN 3310"
STATICS = "EGR 2301"
ORGO = "CHEM 2323"
GATEWAY_ALGEBRA = "MATH 1314"
GROWTH_PROGRAM = "CSCI"
SPRING_DECLINE = -0.048
SPRING_DECLINE_TERM = "202620"
SPRING_DECLINE_BASE = "202520"
ONLINE_W_TERM = "202120"
# Thermodynamics I: the harsh long-time instructor teaches every section
# except in these terms, where the lenient one does.
THERMO_SECOND_TERMS = {"202220", "202420", "202520"}
# Organic Chemistry I: one instructor through Spring 2023, a new hire from Fall 2023.
ORGO_CHANGE_TERM = "202410"

# The planted courses are taught in fall and spring only.
NO_SUMMER = {THERMO, ORGO}

INTERCEPT = 0.30
COURSE_EXTRA_DIFFICULTY = {THERMO: 0.65, STATICS: 0.0, ORGO: 0.35, GATEWAY_ALGEBRA: 0.10}
SUBJECT_DIFFICULTY = {
    "MATH": 0.20, "CHEM": 0.25, "PHYS": 0.30, "EGR": 0.30, "MEEN": 0.35, "ELEN": 0.30,
    "CVEN": 0.25, "CSCI": 0.15, "NURS": 0.10, "ACCT": 0.10, "BIOL": 0.10,
    "EDUC": -0.25, "EDEL": -0.25, "EDSE": -0.25, "EDSP": -0.25, "KINE": -0.15,
    "MUSC": -0.15, "ARTS": -0.20, "THEA": -0.20, "WRSP": -0.20, "MINS": -0.15,
    "BIBL": -0.05, "THEO": -0.05, "SOWK": -0.10, "UNIV": -0.30,
}
LEVEL_DIFFICULTY = {1000: 0.10, 2000: 0.05, 3000: 0.03, 4000: -0.07}
PROGRAM_SHIFT = {"MEEN": -0.40, "ELEN": -0.10, "CVEN": -0.10, "NURS": 0.10, "GNST": -0.08}
FIRST_GEN_ALGEBRA_PENALTY = -0.75
GROWTH_TREND = {"CSCI": 1.13, "NURS": 1.05, "CYBR": 1.03}
# Minimum entering first-time students per program each fall, so the planted
# patterns still have students when the generator runs at a reduced scale.
PROGRAM_FLOOR = {"MEEN": 10, "BIOL": 6, "CHEM": 3}

TERM_ROMAN = {"Fall": "10", "Spring": "20", "Summer": "30"}

MWF = [480, 545, 610, 675, 740, 805, 870]
TR = [480, 570, 660, 750, 840, 930]


def gpa(qp10: int, hours: int) -> float | None:
    """GPA from quality points (tenths) and GPA hours, half-up to 2 decimals."""
    if hours <= 0:
        return None
    value = (Decimal(qp10) / Decimal(hours * 10)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float(value)


def class_level(earned: int) -> str:
    if earned < 30:
        return "Freshman"
    if earned < 60:
        return "Sophomore"
    if earned < 90:
        return "Junior"
    return "Senior"


def standing_for(prev: str | None, cum: float | None, term_gpa: float | None) -> str:
    """The academic standing rule (also in SCHEMA.md and check.py)."""
    if cum is None or cum >= 2.0:
        return "Good Standing"
    if prev in (None, "Good Standing"):
        return "Academic Probation"
    if term_gpa is not None and term_gpa < 2.0:
        return "Academic Suspension"
    return "Continued Probation"


def monday_on_or_after(d: date) -> date:
    return d + timedelta(days=(7 - d.weekday()) % 7)


def hhmm(minutes: int) -> str:
    return "%02d:%02d" % (minutes // 60, minutes % 60)


# --------------------------------------------------------------------------
# Terms
# --------------------------------------------------------------------------
class Term:
    __slots__ = ("code", "name", "season", "year", "academic_year", "start", "end", "census",
                 "reg_start", "index")

    def __init__(self, season: str, year: int, ay: int, index: int) -> None:
        self.code = f"{ay}{TERM_ROMAN[season]}"
        self.season = season
        self.year = year
        self.name = f"{season} {year}"
        self.academic_year = f"{ay - 1}-{ay}"
        self.index = index
        if season == "Fall":
            self.start = monday_on_or_after(date(year, 8, 22))
            self.end = self.start + timedelta(days=109)
            self.census = self.start + timedelta(days=11)
            self.reg_start = date(year, 4, 1)
        elif season == "Spring":
            self.start = monday_on_or_after(date(year, 1, 9))
            self.end = self.start + timedelta(days=116)
            self.census = self.start + timedelta(days=11)
            self.reg_start = date(year - 1, 11, 1)
        else:
            self.start = monday_on_or_after(date(year, 5, 15))
            self.end = self.start + timedelta(days=81)
            self.census = self.start + timedelta(days=4)
            self.reg_start = date(year, 3, 15)


def build_terms() -> list[Term]:
    terms: list[Term] = []
    for ay in range(2021, 2027):
        terms.append(Term("Fall", ay - 1, ay, len(terms)))
        terms.append(Term("Spring", ay, ay, len(terms)))
        if ay < 2026:
            terms.append(Term("Summer", ay, ay, len(terms)))
    return terms


# --------------------------------------------------------------------------
# Catalog
# --------------------------------------------------------------------------
class Course:
    __slots__ = ("cid", "subject", "number", "title", "credits", "level", "pass_fail",
                 "prereqs", "difficulty", "is_core", "is_lab")

    def __init__(self, subject: str, number: str, title: str) -> None:
        self.cid = f"{subject} {number}"
        self.subject = subject
        self.number = number
        self.title = title
        self.credits = int(number[1])
        self.level = int(number[0]) * 1000
        self.pass_fail = False
        self.prereqs: list[str] = []
        self.difficulty = 0.0
        self.is_core = False
        self.is_lab = self.credits == 4 and subject in {"BIOL", "CHEM", "PHYS", "ENVS", "SPAN"}


class Program:
    __slots__ = ("major", "code", "name", "degree", "award", "college", "subject", "weight",
                 "track", "reqs", "req_types", "credits_required", "priority", "trend")


def build_catalog(rng: random.Random) -> tuple[dict[str, Course], dict[str, Program]]:
    courses: dict[str, Course] = {}
    for subject in sorted(C.SUBJECTS):
        name, _college, entries = C.SUBJECTS[subject]
        numbers: set[str] = set()
        for entry in entries:
            number, title = entry.split(" ", 1)
            if number in numbers:
                raise SystemExit(f"duplicate course number {subject} {number}")
            numbers.add(number)
            courses[f"{subject} {number}"] = Course(subject, number, title)
        if subject != "UNIV":
            for number, template in C.GENERIC_COURSES:
                if number in numbers:
                    raise SystemExit(f"generic course collides with {subject} {number}")
                c = Course(subject, number, template.format(name=name))
                c.pass_fail = number in C.PASS_FAIL_GENERIC_NUMBERS
                courses[c.cid] = c
    for cid in C.PASS_FAIL:
        courses[cid].pass_fail = True
    for cid, pre in C.PREREQS.items():
        for p in pre:
            if p not in courses:
                raise SystemExit(f"unknown prerequisite {p} for {cid}")
        courses[cid].prereqs = list(pre)
    for cid in sorted(courses):
        c = courses[cid]
        if c.level >= 3000 and cid not in C.PREREQS:
            gate = C.GATEWAY.get(c.subject)
            if gate and gate != cid:
                c.prereqs = [gate]
        c.difficulty = round(
            SUBJECT_DIFFICULTY.get(c.subject, 0.0) + LEVEL_DIFFICULTY[c.level]
            + COURSE_EXTRA_DIFFICULTY.get(cid, 0.0) + rng.gauss(0.0, 0.12), 4)

    programs: dict[str, Program] = {}
    core_ids: set[str] = set()
    for (major, name, degree, award, college, subject, weight, track, reqs) in C.PROGRAMS:
        p = Program()
        p.major = major
        p.code = f"{degree}-{major}"
        p.name = name
        p.degree = degree
        p.award = award
        p.college = college
        p.subject = subject
        p.weight = weight
        p.track = track
        p.credits_required = C.CREDITS_REQUIRED.get(major, C.DEFAULT_CREDITS_REQUIRED)
        p.trend = GROWTH_TREND.get(major, round(rng.uniform(0.96, 1.035), 4))
        tokens = reqs.split()
        major_list = [f"{a} {b}" for a, b in zip(tokens[::2], tokens[1::2])]
        core = list(C.CORE_COMMON) + [C.CORE_MATH[track]]
        if not any(courses[c].is_lab for c in major_list):
            core.append(C.CORE_SCIENCE)
        ordered: list[str] = []
        types: dict[str, str] = {}
        for cid in core:
            if cid not in types:
                ordered.append(cid)
                types[cid] = "core"
                core_ids.add(cid)
        for cid in major_list:
            if cid not in courses:
                raise SystemExit(f"unknown required course {cid} in {major}")
            if cid in types:
                continue
            ordered.append(cid)
            own = courses[cid].subject == (subject or "")
            types[cid] = "major" if own else "support"
        p.reqs = ordered
        p.req_types = types
        # Priority: courses that unlock long chains first, then by number.
        req_set = set(ordered)
        depth: dict[str, int] = {}

        def chain(cid: str, seen: tuple[str, ...] = ()) -> int:
            if cid in depth:
                return depth[cid]
            best = 0
            for other in ordered:
                if other not in seen and cid in courses[other].prereqs and other in req_set:
                    best = max(best, 1 + chain(other, seen + (cid,)))
            depth[cid] = best
            return best

        for cid in ordered:
            chain(cid)
        p.priority = sorted(ordered, key=lambda c: (-depth[c], courses[c].level, c))
        programs[major] = p
    for cid in core_ids:
        courses[cid].is_core = True
    return courses, programs


# --------------------------------------------------------------------------
# Instructors
# --------------------------------------------------------------------------
RANK_LOAD = {"Professor": 4, "Associate Professor": 4, "Assistant Professor": 4,
             "Instructor": 5, "Lecturer": 5, "Adjunct Instructor": 2}


class Instructor:
    __slots__ = ("iid", "first", "last", "rank", "subject", "college", "hire_date", "hire_term",
                 "leave_term", "effect")


def term_code_for(season: str, year: int) -> str:
    ay = year + 1 if season == "Fall" else year
    return f"{ay}{TERM_ROMAN[season]}"


def build_instructors(rng: random.Random, scale: float, courses: dict[str, Course],
                      programs: dict[str, Program], terms: list[Term],
                      demand: dict[str, float] | None) -> list[Instructor]:
    # Department sizes follow the teaching demand of each subject: the
    # sections per subject measured by a first simulation pass, or, for that
    # first pass, an estimate from the program requirements.
    weight: dict[str, float] = {s: 0.5 for s in C.SUBJECTS}
    if demand is not None:
        for subject, n in demand.items():
            weight[subject] += n
    else:
        total_w = sum(p.weight for p in programs.values())
        for p in programs.values():
            for cid in p.reqs:
                weight[courses[cid].subject] += p.weight / total_w * courses[cid].credits * 6
    n_total = max(48, round(220 * scale))
    names: list[tuple[str, str]] = []
    used: set[tuple[str, str]] = set()
    while len(names) < n_total:
        pair = (rng.choice(C.FIRST_NAMES), rng.choice(C.LAST_NAMES))
        if pair not in used:
            used.add(pair)
            names.append(pair)
    planted = [
        # (subject, rank, hire year or None for the Fall 2023 hire, leave term)
        ("MEEN", "Professor", 2008, None),
        ("MEEN", "Associate Professor", 2015, None),
        ("CHEM", "Associate Professor", 2006, "202330"),
        ("CHEM", "Assistant Professor", None, None),
    ]
    subjects = sorted(C.SUBJECTS)
    n_rest = n_total - len(planted)
    wsum = sum(weight.values())
    quota = {s: max(1, int(weight[s] / wsum * n_rest)) for s in subjects}
    while sum(quota.values()) < n_rest:
        best = max(subjects, key=lambda s: (weight[s] / wsum * n_rest - quota[s], s))
        quota[best] += 1
    while sum(quota.values()) > n_rest:
        best = max(subjects, key=lambda s: (quota[s], s))
        quota[best] -= 1
    window_terms = [t.code for t in terms if t.season != "Summer"]
    ranks = ["Professor", "Associate Professor", "Assistant Professor", "Instructor",
             "Lecturer", "Adjunct Instructor"]
    rank_w = [0.2, 0.22, 0.24, 0.12, 0.08, 0.14]
    out: list[Instructor] = []
    idx = 0
    for subject, rank, hire_year, leave in planted:
        ins = Instructor()
        ins.first, ins.last = names[idx]
        idx += 1
        ins.rank = rank
        ins.subject = subject
        ins.college = C.SUBJECTS[subject][1]
        if hire_year is None:
            ins.hire_term = ORGO_CHANGE_TERM
            ins.hire_date = "2023-08-01"
        else:
            ins.hire_term = term_code_for("Fall", hire_year)
            ins.hire_date = f"{hire_year}-08-01"
        ins.leave_term = leave
        ins.effect = 0.0
        ins.iid = ""
        out.append(ins)
    for subject in subjects:
        for j in range(quota[subject]):
            ins = Instructor()
            ins.first, ins.last = names[idx]
            idx += 1
            ins.subject = subject
            ins.college = C.SUBJECTS[subject][1]
            ins.rank = rng.choices(ranks, rank_w)[0]
            ins.effect = round(rng.gauss(0.0, 0.14), 4)
            # The first instructor of every department is a long-time,
            # permanent member so no department is ever empty.
            if j == 0 or rng.random() < 0.80:
                year = rng.randint(1996, 2019)
                ins.hire_term = term_code_for("Fall", year)
                ins.hire_date = f"{year}-08-{rng.randint(1, 20):02d}"
            else:
                code = rng.choice(window_terms[1:])
                ins.hire_term = code
                t = next(t for t in terms if t.code == code)
                ins.hire_date = (t.start - timedelta(days=rng.randint(10, 40))).isoformat()
            ins.leave_term = None
            if j > 0 and rng.random() < 0.17:
                later = [c for c in window_terms if c > ins.hire_term]
                if len(later) > 1:
                    ins.leave_term = rng.choice(later[1:])
            ins.iid = ""
            out.append(ins)
    for n, ins in enumerate(out, start=1):
        ins.iid = f"I-{n:04d}"
    return out


def employed(ins: Instructor, term: str) -> bool:
    return ins.hire_term <= term and (ins.leave_term is None or term < ins.leave_term)


# --------------------------------------------------------------------------
# Students
# --------------------------------------------------------------------------
class Student:
    __slots__ = ("sid", "entry_term", "entry_type", "residency", "first_gen", "pell", "ability",
                 "program", "completed", "transfer_credits", "opening_earned", "opening_gpa_hours",
                 "opening_qp10", "earned", "gpa_hours", "qp10", "status", "sit_out_done",
                 "stop_terms", "standing", "load", "advisor", "advisor_rel", "program_rel",
                 "last_term", "exit_term", "level", "suspensions")

    def total_earned(self) -> int:
        return self.transfer_credits + self.opening_earned + self.earned

    def cum_gpa(self) -> float | None:
        return gpa(self.qp10, self.gpa_hours)


class Section:
    __slots__ = ("sid", "term", "course", "number", "crn", "modality", "days", "start", "end",
                 "cap", "instructor", "enrolled", "meets")


class Generator:
    def __init__(self, scale: float, demand: dict[str, float] | None = None) -> None:
        self.scale = scale
        self.rng_cat = random.Random(f"{SEED}:catalog")
        self.terms = build_terms()
        self.term_by_code = {t.code: t for t in self.terms}
        self.courses, self.programs = build_catalog(self.rng_cat)
        self.instructors = build_instructors(self.rng_cat, scale, self.courses, self.programs,
                                             self.terms, demand)
        self.ins_by_id = {i.iid: i for i in self.instructors}
        self.students: list[Student] = []
        self.sections: list[Section] = []
        self.section_by_id: dict[str, Section] = {}
        self.registrations: list[tuple] = []   # (rid, sid, section_id, term, date, status)
        self.grades: list[tuple] = []          # (rid, grade, credits, qp, gpa_hours, earned)
        self.term_records: list[tuple] = []
        self.standings: list[tuple] = []
        self.holds: list[list] = []
        self.holds_by_student: dict[str, list[list]] = {}
        self.program_rows: list[list] = []     # [rid, sid, program, start, end, status]
        self.advisor_rows: list[list] = []     # [rid, sid, advisor, start, end]
        self.appointments: list[tuple] = []
        self.course_regulars: dict[str, list[str]] = {}
        self.next_sid = 100001
        self.thermo_first = self.instructors[0].iid
        self.thermo_second = self.instructors[1].iid
        self.orgo_before = self.instructors[2].iid
        self.orgo_after = self.instructors[3].iid
        self.pair_effect = {
            (THERMO, self.thermo_first): -0.30,
            (THERMO, self.thermo_second): 0.60,
            (ORGO, self.orgo_before): -0.60,
            (ORGO, self.orgo_after): 0.45,
        }
        self.elective_pool = sorted(
            c.cid for c in self.courses.values()
            if c.level <= 2000 and not c.prereqs and not c.pass_fail and c.subject != "UNIV"
            and c.number not in {"2190", "2390"})
        self.program_electives: dict[str, list[str]] = {}
        for p in self.programs.values():
            reqs = set(p.reqs)
            self.program_electives[p.major] = sorted(
                c.cid for c in self.courses.values()
                if p.subject and c.subject == p.subject and c.cid not in reqs)
        self.spring_counts: dict[str, int] = {}

    # ---------------------------------------------------------------- helpers
    def pick_program(self, rng: random.Random, year: int) -> str:
        majors = sorted(self.programs)
        weights = [self.programs[m].weight * self.programs[m].trend ** (year - 2020) for m in majors]
        return rng.choices(majors, weights)[0]

    def new_student(self, rng: random.Random, entry_term: str, entry_type: str, program: str,
                    year_in: int) -> Student:
        s = Student()
        s.sid = f"S-{self.next_sid}"
        self.next_sid += 1
        s.entry_term = entry_term
        s.entry_type = entry_type
        s.residency = rng.choices(["in_state", "out_of_state", "international"],
                                  [0.68, 0.29, 0.03])[0]
        s.first_gen = 1 if rng.random() < (0.30 if entry_type == "first_time" else 0.38) else 0
        s.pell = 1 if rng.random() < (0.30 + 0.15 * s.first_gen) else 0
        s.ability = rng.gauss(0.0, 0.62) - 0.05 * s.first_gen - 0.05 * s.pell
        s.program = program
        s.completed = set()
        s.transfer_credits = 0
        s.opening_earned = 0
        s.opening_gpa_hours = 0
        s.opening_qp10 = 0
        s.earned = 0
        s.status = "active"
        s.sit_out_done = False
        s.stop_terms = 0
        s.standing = None
        s.load = "FT" if rng.random() < 0.9 else "PT"
        s.advisor = None
        s.advisor_rel = None
        s.program_rel = None
        s.last_term = None
        s.exit_term = None
        s.level = "Freshman"
        s.suspensions = 0
        p = self.programs[program]
        if entry_type == "transfer":
            s.transfer_credits = rng.randint(12, 60)
            self._mark_completed(s, p, int(s.transfer_credits * 0.7))
        elif rng.random() < 0.28:
            s.transfer_credits = rng.choice([3, 6, 6, 9, 12, 15])  # dual credit, unmapped
        if year_in > 0:  # already enrolled before Fall 2020: an opening balance
            earned = max(0, round(rng.gauss(29.0 * year_in, 5.0)))
            s.opening_earned = earned
            self._mark_completed(s, p, int(earned * 0.75))
            g = min(4.0, max(2.05, rng.gauss(3.05 + 0.45 * s.ability, 0.25)))
            s.opening_gpa_hours = earned + rng.choice([0, 0, 0, 3, 3, 6])
            s.opening_qp10 = round(g * s.opening_gpa_hours * 10)
        s.gpa_hours = s.opening_gpa_hours
        s.qp10 = s.opening_qp10
        self.students.append(s)
        return s

    def _mark_completed(self, s: Student, p: Program, credits: int) -> None:
        got = 0
        for cid in p.priority:
            if got >= credits:
                break
            c = self.courses[cid]
            if all(pre in s.completed for pre in c.prereqs):
                s.completed.add(cid)
                got += c.credits

    def open_program(self, s: Student, term: str) -> None:
        row = [len(self.program_rows) + 1, s.sid, self.programs[s.program].code, term, None,
               "active"]
        self.program_rows.append(row)
        s.program_rel = row

    def assign_advisor(self, rng: random.Random, s: Student, term: str) -> None:
        p = self.programs[s.program]
        pool = [i for i in self.instructors if employed(i, term) and i.rank != "Adjunct Instructor"
                and p.subject is not None and i.subject == p.subject]
        if not pool:
            pool = [i for i in self.instructors if employed(i, term)
                    and i.rank != "Adjunct Instructor" and i.college == p.college]
        if not pool:
            pool = [i for i in self.instructors if employed(i, term)]
        ins = rng.choice(pool)
        if s.advisor_rel is not None:
            s.advisor_rel[4] = self.prev_term(term)
            self.cap_advisor_row(s.advisor_rel)
        row = [len(self.advisor_rows) + 1, s.sid, ins.iid, term, None]
        self.advisor_rows.append(row)
        s.advisor = ins.iid
        s.advisor_rel = row

    def cap_advisor_row(self, row: list) -> None:
        """A relationship never runs past the advisor's employment or before its start."""
        leave = self.ins_by_id[row[2]].leave_term
        if leave is not None and (row[4] is None or row[4] >= leave):
            row[4] = self.prev_term(leave)
        if row[4] is not None and row[4] < row[3]:
            row[4] = row[3]

    def add_hold(self, hold: list) -> None:
        self.holds.append(hold)
        self.holds_by_student.setdefault(hold[0], []).append(hold)

    def release_blocking_holds(self, sid: str, day: date) -> None:
        """A financial, registrar, or advising hold blocks registration, so any
        such hold still active on the registration day was released that day."""
        d = day.isoformat()
        for h in self.holds_by_student.get(sid, []):
            if h[1] in BLOCKING_HOLDS and h[6] <= d and (h[7] is None or h[7] > d):
                h[7] = max(h[6], d)

    def prev_term(self, code: str) -> str:
        t = self.term_by_code[code]
        return self.terms[t.index - 1].code if t.index > 0 else code

    # ---------------------------------------------------------------- cohorts
    def seed_population(self) -> None:
        rng = random.Random(f"{SEED}:population")
        first = self.terms[0].code
        # Students already enrolled at Fall 2020 (entered Fall 2016 to Fall 2019).
        for year, size in ((2016, 70), (2017, 410), (2018, 450), (2019, 520)):
            n = max(1, round(size * self.scale))
            for _ in range(n):
                prog = self.pick_program(rng, year)
                s = self.new_student(rng, term_code_for("Fall", year), "first_time", prog,
                                     2020 - year)
                self._start(rng, s, first)
            nt = max(1, round(60 * self.scale))
            for _ in range(nt):
                prog = self.pick_program(rng, year)
                s = self.new_student(rng, term_code_for("Fall", year), "transfer", prog,
                                     2020 - year)
                self._start(rng, s, first)

    def _start(self, rng: random.Random, s: Student, term: str) -> None:
        s.level = class_level(s.total_earned())
        self.open_program(s, term)
        self.assign_advisor(rng, s, term)

    def add_entrants(self, rng: random.Random, t: Term) -> None:
        if t.season == "Fall":
            k = t.year - 2020
            factor = [1.0, 1.03, 0.98, 1.01, 0.97, 0.95][k]
            n_ftic = max(1, round(620 * self.scale * factor))
            entrants = [self.pick_program(rng, t.year) for _ in range(n_ftic)]
            for major, floor in sorted(PROGRAM_FLOOR.items()):
                have = entrants.count(major)
                entrants.extend([major] * max(0, floor - have))
            for prog in entrants:
                s = self.new_student(rng, t.code, "first_time", prog, 0)
                self._start(rng, s, t.code)
            for _ in range(max(1, round(110 * self.scale * factor))):
                s = self.new_student(rng, t.code, "transfer", self.pick_program(rng, t.year), 0)
                self._start(rng, s, t.code)
        elif t.season == "Spring":
            for _ in range(max(1, round(60 * self.scale))):
                etype = "transfer" if rng.random() < 0.7 else "first_time"
                s = self.new_student(rng, t.code, etype, self.pick_program(rng, t.year), 0)
                self._start(rng, s, t.code)

    # ---------------------------------------------------------------- persistence
    def persistence(self, rng: random.Random, t: Term) -> list[Student]:
        """Who registers in a fall or spring term."""
        candidates: list[tuple[float, Student]] = []
        new: list[Student] = []
        leaving: list[Student] = []
        for s in self.students:
            if s.entry_term == t.code:
                new.append(s)
                continue
            if s.status == "active":
                cum = s.cum_gpa()
                base = 0.945 if t.season == "Spring" else (0.86 if s.level == "Freshman" else 0.92)
                p = base + 0.03 * s.ability - 0.02 * s.pell - 0.02 * s.first_gen
                if cum is not None and cum < 2.0:
                    p -= 0.15
                if s.standing in ("Academic Probation", "Continued Probation"):
                    p -= 0.05
            elif s.status == "stopped" and s.stop_terms <= 2:
                p = 0.10
            elif s.status == "suspended":
                if not s.sit_out_done:
                    s.sit_out_done = True
                    continue
                p = 0.55
            else:
                continue
            u = rng.random()
            candidates.append((u - p, s))
        if t.code == SPRING_DECLINE_TERM and SPRING_DECLINE_BASE in self.spring_counts:
            target = round(self.spring_counts[SPRING_DECLINE_BASE] * (1 + SPRING_DECLINE))
            ranked = sorted(candidates, key=lambda x: (x[0], x[1].sid))
            stay = [s for _, s in ranked[:target]]
            go = [s for _, s in ranked[target:]]
        else:
            stay = [s for m, s in candidates if m < 0]
            go = [s for m, s in candidates if m >= 0]
        for s in go:
            if s.status == "active":
                leaving.append(s)
            elif s.status == "stopped":
                s.stop_terms += 1
            elif s.status == "suspended":
                s.status = "suspension_exit"
        for s in leaving:
            if rng.random() < 0.35:
                s.status = "withdrawn"
            else:
                s.status = "stopped"
                s.stop_terms = 1
            if rng.random() < 0.40:
                last = self.term_by_code[s.last_term] if s.last_term else t
                start = last.end - timedelta(days=rng.randint(0, 25))
                amount = round(min(9000.0, max(120.0, rng.lognormvariate(7.0, 0.8))), 2)
                self.add_hold([s.sid, "financial", "Unpaid account balance", amount,
                               "Student Accounts", last.code, start.isoformat(), None])
        for s in stay:
            if s.status != "active":
                s.status = "active"
                s.stop_terms = 0
                s.sit_out_done = False
        return sorted(stay + new, key=lambda s: s.sid)

    # ---------------------------------------------------------------- planning
    def plan(self, rng: random.Random, s: Student, t: Term) -> tuple[list[str], list[str], int]:
        p = self.programs[s.program]
        level = class_level(s.total_earned())
        max_level = {"Freshman": 2000, "Sophomore": 3000}.get(level, 4000)
        if t.season == "Summer":
            target = rng.choice([3, 3, 4, 6, 6, 7])
            max_level = min(max_level, 2000)
        elif s.load == "FT":
            target = rng.choice([12, 13, 14, 15, 15, 15, 16, 16, 17])
        else:
            target = rng.choice([6, 7, 9])
        primary: list[str] = []
        credits = 0
        override_used = False
        for cid in p.priority:
            if credits >= target:
                break
            if cid in s.completed:
                continue
            c = self.courses[cid]
            if c.level > max_level or (t.season == "Summer" and (c.is_lab or cid in NO_SUMMER)):
                continue
            if not all(pre in s.completed for pre in c.prereqs):
                if override_used or rng.random() >= 0.012:
                    continue
                override_used = True
            primary.append(cid)
            credits += c.credits
        alternates: list[str] = []
        pool = self.program_electives[p.major]
        tries = 0
        while (credits < target or len(alternates) < 4) and tries < 30:
            tries += 1
            if pool and rng.random() < 0.45:
                cid = rng.choice(pool)
            else:
                cid = rng.choice(self.elective_pool)
            c = self.courses[cid]
            if (cid in s.completed or cid in primary or cid in alternates or c.level > max_level
                    or (t.season == "Summer" and (c.is_lab or cid in NO_SUMMER))
                    or not all(pre in s.completed for pre in c.prereqs)):
                continue
            if credits < target:
                primary.append(cid)
                credits += c.credits
            else:
                alternates.append(cid)
        return primary, alternates, target

    # ---------------------------------------------------------------- sections
    def modality_for(self, rng: random.Random, t: Term, c: Course) -> str:
        if c.is_lab or c.cid in (THERMO, ORGO):
            return "in_person"
        if t.code in ("202110", "202120"):
            online, hybrid = 0.40, 0.22
        elif t.season == "Summer":
            online, hybrid = 0.50, 0.05
        else:
            online, hybrid = 0.12 + 0.012 * (t.year - 2021), 0.07
        r = rng.random()
        if r < online:
            return "online"
        if r < online + hybrid:
            return "hybrid"
        return "in_person"

    def make_section(self, rng: random.Random, t: Term, c: Course, n: int,
                     modality: str | None = None) -> Section:
        sec = Section()
        sec.term = t.code
        sec.course = c.cid
        sec.modality = modality or self.modality_for(rng, t, c)
        prefix = {"in_person": "", "online": "W", "hybrid": "H"}[sec.modality]
        sec.number = f"{prefix}{n:02d}"
        sec.crn = str(10001 + self._crn_counter[t.code])
        self._crn_counter[t.code] += 1
        sec.sid = f"{t.code}-{sec.crn}"
        if c.is_lab or c.credits == 1:
            sec.cap = 24
        elif c.level == 1000 and c.is_core:
            sec.cap = 35
        elif c.level >= 3000:
            sec.cap = 28
        else:
            sec.cap = 32
        sec.enrolled = 0
        sec.meets = []
        sec.days = None
        sec.start = None
        sec.end = None
        if sec.modality == "in_person":
            r = rng.random()
            if r < 0.48:
                st = rng.choice(MWF)
                sec.days, sec.start, sec.end = "MWF", st, st + 50
            elif r < 0.92:
                st = rng.choice(TR)
                sec.days, sec.start, sec.end = "TR", st, st + 75
            else:
                sec.days = rng.choice(["M", "T", "W", "R"])
                sec.start, sec.end = 1080, 1245
        elif sec.modality == "hybrid":
            if rng.random() < 0.5:
                st = rng.choice(MWF)
                sec.days, sec.start, sec.end = rng.choice(["M", "W", "F"]), st, st + 50
            else:
                st = rng.choice(TR)
                sec.days, sec.start, sec.end = rng.choice(["T", "R"]), st, st + 75
        if sec.days:
            sec.meets = [(d, sec.start) for d in sec.days]
        sec.instructor = None
        self.sections.append(sec)
        self.section_by_id[sec.sid] = sec
        return sec

    def assign_instructor(self, t: Term, sec: Section, load: dict[str, int]) -> None:
        c = self.courses[sec.course]
        chosen: str | None = None
        if c.cid == THERMO:
            chosen = self.thermo_second if t.code in THERMO_SECOND_TERMS else self.thermo_first
        elif c.cid == ORGO:
            chosen = self.orgo_after if t.code >= ORGO_CHANGE_TERM else self.orgo_before
        if chosen is None:
            summer = t.season == "Summer"

            def cap(i: Instructor) -> int:
                return min(2, RANK_LOAD[i.rank]) if summer else RANK_LOAD[i.rank]

            def ok(i: Instructor, extra: int = 0) -> bool:
                return employed(i, t.code) and load.get(i.iid, 0) < cap(i) + extra

            regulars = [self.ins_by_id[i] for i in self.course_regulars.get(c.cid, [])
                        if self.ins_by_id[i].subject == c.subject]
            dept = [i for i in self.instructors if i.subject == c.subject]
            college = [i for i in self.instructors if i.college == C.SUBJECTS[c.subject][1]]
            for group, extra in ((regulars, 0), (dept, 0), (dept, 1), (college, 0),
                                 (self.instructors, 0), (college, 1), (self.instructors, 1),
                                 (self.instructors, 99)):
                pool = [i for i in group if ok(i, extra)]
                if pool:
                    best = min(pool, key=lambda i: (load.get(i.iid, 0), i.iid))
                    chosen = best.iid
                    break
        assert chosen is not None and employed(self.ins_by_id[chosen], t.code)
        sec.instructor = chosen
        load[chosen] = load.get(chosen, 0) + 1
        regs = self.course_regulars.setdefault(c.cid, [])
        if chosen not in regs:
            regs.append(chosen)

    # ---------------------------------------------------------------- grading
    def grade(self, rng: random.Random, s: Student, sec: Section, t: Term) -> str:
        c = self.courses[sec.course]
        ins = self.ins_by_id[sec.instructor]
        latent = (INTERCEPT + s.ability + PROGRAM_SHIFT.get(s.program, 0.0) - c.difficulty
                  + ins.effect + self.pair_effect.get((c.cid, ins.iid), 0.0)
                  + rng.gauss(0.0, 0.85))
        if c.cid == GATEWAY_ALGEBRA and s.first_gen:
            latent += FIRST_GEN_ALGEBRA_PENALTY
        w_base = 0.012
        if sec.modality == "online":
            w_base += 0.02
            if t.code == ONLINE_W_TERM:
                w_base += 0.10
        u = rng.random()
        if u < w_base or (latent < -1.25 and u < w_base + 0.33):
            return "W"
        if rng.random() < 0.003:
            return "I"
        if c.pass_fail:
            return "P" if latent > -1.6 else "NP"
        for cut, letter in GRADE_CUTS:
            if latent >= cut:
                return letter
        return "F"

    # ---------------------------------------------------------------- run
    def run(self) -> None:
        self._crn_counter = {t.code: 0 for t in self.terms}
        self.seed_population()
        for t in self.terms:
            rng = random.Random(f"{SEED}:term:{t.code}")
            self.add_entrants(rng, t)
            if t.season == "Summer":
                regs = [s for s in self.students if s.status == "active"
                        and s.last_term is not None
                        and rng.random() < (0.24 if (s.cum_gpa() or 4.0) < 2.3 else 0.15)]
            else:
                regs = self.persistence(rng, t)
            self.run_term(rng, t, regs)
        self.finish()

    def run_term(self, rng: random.Random, t: Term, regs: list[Student]) -> None:
        plans: list[tuple[Student, list[str], list[str], int]] = []
        demand: dict[str, int] = {}
        required: set[str] = set()
        for s in regs:
            primary, alternates, target = self.plan(rng, s, t)
            plans.append((s, primary, alternates, target))
            preqs = self.programs[s.program].req_types
            for cid in primary:
                demand[cid] = demand.get(cid, 0) + 1
                if cid in preqs:
                    required.add(cid)
        min_elective = 5 if self.scale >= 0.5 else 2
        by_course: dict[str, list[Section]] = {}
        load: dict[str, int] = {}
        for cid in sorted(demand):
            if demand[cid] < min_elective and cid not in required:
                continue
            c = self.courses[cid]
            cap_guess = 24 if (c.is_lab or c.credits == 1) else 30
            n = max(1, math.ceil(demand[cid] * 1.1 / cap_guess))
            secs = [self.make_section(rng, t, c, k + 1) for k in range(n)]
            for sec in secs:
                self.assign_instructor(t, sec, load)
            by_course[cid] = secs
        order = list(range(len(plans)))
        rng.shuffle(order)
        enrolled: dict[str, list[Section]] = {}
        for k in order:
            s, primary, alternates, target = plans[k]
            got: list[Section] = []
            busy: set[tuple[str, int]] = set()
            credits = 0
            for cid in primary + alternates:
                if credits >= target:
                    break
                secs = by_course.get(cid)
                if not secs:
                    continue
                for sec in sorted(secs, key=lambda x: (x.enrolled, x.crn)):
                    if sec.enrolled >= sec.cap or any(m in busy for m in sec.meets):
                        continue
                    sec.enrolled += 1
                    busy.update(sec.meets)
                    got.append(sec)
                    credits += self.courses[cid].credits
                    break
            if not got:
                cid = primary[0] if primary else self.elective_pool[0]
                c = self.courses[cid]
                n = len(by_course.get(cid, [])) + 1
                sec = self.make_section(rng, t, c, n, modality="online")
                self.assign_instructor(t, sec, load)
                by_course.setdefault(cid, []).append(sec)
                sec.enrolled += 1
                got.append(sec)
            enrolled[s.sid] = got
        # Drop sections nobody enrolled in (cancelled offerings).
        dead = {sec.sid for secs in by_course.values() for sec in secs if sec.enrolled == 0}
        if dead:
            self.sections = [x for x in self.sections if x.sid not in dead]
            for sid in dead:
                del self.section_by_id[sid]
        # Registration dates, grades, term records.
        for s in regs:
            got = enrolled[s.sid]
            if s.entry_term == t.code:
                lo = max(t.reg_start, t.start - timedelta(days=75))
            else:
                lo = t.reg_start
            hi = t.start + timedelta(days=4)
            reg_date = lo + timedelta(days=rng.randint(0, (hi - lo).days))
            self.release_blocking_holds(s.sid, reg_date)
            attempted = earned = gh = qp10 = 0
            level_before = class_level(s.total_earned())
            passed: list[str] = []
            for sec in sorted(got, key=lambda x: x.crn):
                c = self.courses[sec.course]
                g = self.grade(rng, s, sec, t)
                rid = len(self.registrations) + 1
                self.registrations.append((rid, s.sid, sec.sid, t.code, reg_date.isoformat(),
                                           "withdrawn" if g == "W" else "registered"))
                pts = LETTER_POINTS10.get(g)
                g_hours = c.credits if pts is not None else 0
                e_hours = c.credits if g in PASSING else 0
                q = (pts or 0) * c.credits
                self.grades.append((rid, g, c.credits, q / 10, g_hours, e_hours))
                if g != "W":
                    attempted += c.credits
                earned += e_hours
                gh += g_hours
                qp10 += q
                if g in PASSING:
                    passed.append(c.cid)
            s.earned += earned
            s.gpa_hours += gh
            s.qp10 += qp10
            s.completed.update(passed)
            tg = gpa(qp10, gh)
            cg = s.cum_gpa()
            st = standing_for(s.standing, cg, tg)
            s.standing = st
            s.last_term = t.code
            s.level = level_before
            self.term_records.append((
                s.sid, t.code, self.programs[s.program].code, level_before, attempted, earned, gh,
                qp10 / 10, tg, s.total_earned(), s.gpa_hours, s.qp10 / 10, cg))
            self.standings.append((s.sid, t.code, st))
            if st == "Academic Suspension":
                s.suspensions += 1
                # A first suspension sits out a term, a second one dismisses.
                s.status = "suspended" if s.suspensions == 1 else "dismissed"
                s.sit_out_done = False
            self.after_term(rng, s, t)
        if t.season == "Spring":
            self.spring_counts[t.code] = sum(1 for s in regs if s.entry_term < t.code)

    def after_term(self, rng: random.Random, s: Student, t: Term) -> None:
        p = self.programs[s.program]
        # Graduation.
        if (s.status == "active" and s.total_earned() >= p.credits_required
                and all(cid in s.completed for cid in p.reqs)
                and (s.cum_gpa() or 0.0) >= 2.0):
            s.status = "graduated"
            s.exit_term = t.code
            s.program_rel[4] = t.code
            s.program_rel[5] = "graduated"
            if s.advisor_rel is not None:
                s.advisor_rel[4] = t.code
            return
        if t.season == "Summer":
            return
        # Holds during the term.
        for cat, prob, desc, office in (
                ("financial", 0.06 + 0.04 * s.pell, "Account balance past due", "Student Accounts"),
                ("registrar", 0.02 + (0.08 if s.entry_term == t.code and s.entry_type == "transfer"
                                      else 0.0), "Missing final transcript", "Registrar"),
                ("advising", 0.07, "Advising required before registration", "Academic Advising"),
                ("library", 0.025, "Overdue library materials", "Library"),
                ("student_life", 0.008, "Student conduct follow-up", "Student Life")):
            if rng.random() < prob:
                start = t.start + timedelta(days=rng.randint(5, (t.end - t.start).days - 10))
                if cat == "financial":
                    amount = round(min(9000.0, max(40.0, rng.lognormvariate(6.4, 0.9))), 2)
                elif cat == "library":
                    amount = round(rng.uniform(5, 150), 2)
                else:
                    amount = 0.0
                last_term = t.code == self.terms[-1].code
                if last_term and rng.random() < 0.45:
                    end = None
                else:
                    end = (start + timedelta(days=rng.randint(2, 45))).isoformat()
                self.add_hold([s.sid, cat, desc, amount, office, t.code, start.isoformat(), end])
        # Advising appointments.
        if s.advisor is None or not employed(self.ins_by_id[s.advisor], t.code):
            self.assign_advisor(rng, s, t.code)
        n = rng.choices([0, 1, 2], [0.2, 0.62, 0.18])[0]
        for _ in range(n):
            d = t.start + timedelta(days=rng.randint(10, (t.end - t.start).days - 5))
            if s.standing in ("Academic Probation", "Continued Probation"):
                kind = "academic_recovery"
            elif class_level(s.total_earned()) == "Senior":
                kind = "degree_audit"
            else:
                kind = rng.choice(["registration_advising", "registration_advising",
                                   "academic_planning"])
            status = rng.choices(["completed", "no_show", "cancelled"], [0.85, 0.1, 0.05])[0]
            self.appointments.append((s.sid, s.advisor, t.code, d.isoformat(), kind, status))
        # Change of major (effective next term).
        level = class_level(s.total_earned())
        if s.status == "active" and level in ("Freshman", "Sophomore") and t.index + 1 < len(
                self.terms):
            cum = s.cum_gpa() or 4.0
            hard = s.program in ("MEEN", "ELEN", "CVEN", "NURS", "CHEM", "BIOL")
            prob = 0.25 if (hard and cum < 2.3) else 0.035
            if rng.random() < prob:
                if hard and cum < 2.3:
                    new = rng.choice(["BUAD", "PSYC", "COMM", "HLSC", "GNST", "INFT", "KINE"])
                else:
                    new = self.pick_program(rng, t.year)
                if new != s.program:
                    nxt = self.terms[t.index + 1].code
                    s.program_rel[4] = t.code
                    s.program_rel[5] = "changed"
                    s.program = new
                    self.open_program(s, nxt)
                    self.assign_advisor(rng, s, nxt)

    def finish(self) -> None:
        for s in self.students:
            if s.status == "graduated":
                continue
            if s.status in ("active", "suspended") and s.last_term is not None:
                continue
            status = {"withdrawn": "withdrawn", "stopped": "stopped_out",
                      "suspension_exit": "inactive", "suspended": "inactive",
                      "dismissed": "dismissed"}.get(s.status)
            if status is None or s.last_term is None:
                continue
            s.exit_term = s.last_term
            # A change of major declared after the last enrolled term closes
            # on its own start term; it never covers an enrolled term.
            s.program_rel[4] = max(s.last_term, s.program_rel[3])
            s.program_rel[5] = status
            if s.advisor_rel is not None and s.advisor_rel[4] is None:
                s.advisor_rel[4] = s.last_term
        for row in self.advisor_rows:
            self.cap_advisor_row(row)

    # ---------------------------------------------------------------- write
    def student_status(self, s: Student) -> str:
        return {"active": "active", "graduated": "graduated", "withdrawn": "withdrawn",
                "stopped": "stopped_out", "suspended": "academic_suspension",
                "suspension_exit": "academic_suspension",
                "dismissed": "academic_dismissal"}[s.status]


SCHEMA_SQL = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE grade_scale (
    grade TEXT PRIMARY KEY, quality_points_per_hour REAL, counts_in_gpa INTEGER NOT NULL,
    earns_credit INTEGER NOT NULL, is_dfw INTEGER NOT NULL, description TEXT NOT NULL);
CREATE TABLE colleges (college_code TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE academic_periods (
    term_code TEXT PRIMARY KEY, name TEXT NOT NULL,
    season TEXT NOT NULL CHECK (season IN ('Fall','Spring','Summer')),
    academic_year TEXT NOT NULL, sequence INTEGER NOT NULL UNIQUE,
    start_date TEXT NOT NULL, end_date TEXT NOT NULL, census_date TEXT NOT NULL,
    registration_start_date TEXT NOT NULL);
CREATE TABLE subjects (
    subject_code TEXT PRIMARY KEY, name TEXT NOT NULL,
    college_code TEXT NOT NULL REFERENCES colleges(college_code));
CREATE TABLE academic_programs (
    program_code TEXT PRIMARY KEY, major_code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
    degree TEXT NOT NULL, award_level TEXT NOT NULL CHECK (award_level IN ('Associate','Bachelor')),
    college_code TEXT NOT NULL REFERENCES colleges(college_code),
    subject_code TEXT REFERENCES subjects(subject_code),
    credits_required INTEGER NOT NULL);
CREATE TABLE courses (
    course_id TEXT PRIMARY KEY, subject_code TEXT NOT NULL REFERENCES subjects(subject_code),
    course_number TEXT NOT NULL, title TEXT NOT NULL, credit_hours INTEGER NOT NULL,
    course_level INTEGER NOT NULL, grade_mode TEXT NOT NULL CHECK (grade_mode IN ('standard','pass_fail')),
    is_core INTEGER NOT NULL, UNIQUE (subject_code, course_number));
CREATE TABLE course_prerequisites (
    course_id TEXT NOT NULL REFERENCES courses(course_id),
    prerequisite_course_id TEXT NOT NULL REFERENCES courses(course_id),
    PRIMARY KEY (course_id, prerequisite_course_id));
CREATE TABLE program_requirements (
    program_code TEXT NOT NULL REFERENCES academic_programs(program_code),
    course_id TEXT NOT NULL REFERENCES courses(course_id),
    requirement_type TEXT NOT NULL CHECK (requirement_type IN ('core','major','support')),
    PRIMARY KEY (program_code, course_id));
CREATE TABLE instructors (
    instructor_id TEXT PRIMARY KEY, first_name TEXT NOT NULL, last_name TEXT NOT NULL,
    fictional INTEGER NOT NULL CHECK (fictional = 1), academic_rank TEXT NOT NULL,
    subject_code TEXT NOT NULL REFERENCES subjects(subject_code),
    college_code TEXT NOT NULL REFERENCES colleges(college_code),
    hire_date TEXT NOT NULL, hire_term TEXT NOT NULL, leave_term TEXT);
CREATE TABLE students (
    student_id TEXT PRIMARY KEY, entry_term TEXT NOT NULL,
    entry_type TEXT NOT NULL CHECK (entry_type IN ('first_time','transfer')),
    residency TEXT NOT NULL, first_generation INTEGER NOT NULL, pell_recipient INTEGER NOT NULL,
    transfer_credits INTEGER NOT NULL, opening_credits_earned INTEGER NOT NULL,
    opening_gpa_hours INTEGER NOT NULL, opening_quality_points REAL NOT NULL,
    enrollment_status TEXT NOT NULL, exit_term TEXT, class_level TEXT NOT NULL);
CREATE TABLE student_academic_programs (
    record_id INTEGER PRIMARY KEY, student_id TEXT NOT NULL REFERENCES students(student_id),
    program_code TEXT NOT NULL REFERENCES academic_programs(program_code),
    start_term TEXT NOT NULL, end_term TEXT, status TEXT NOT NULL);
CREATE TABLE sections (
    section_id TEXT PRIMARY KEY, term_code TEXT NOT NULL REFERENCES academic_periods(term_code),
    course_id TEXT NOT NULL REFERENCES courses(course_id), section_number TEXT NOT NULL,
    crn TEXT NOT NULL, modality TEXT NOT NULL CHECK (modality IN ('in_person','online','hybrid')),
    meeting_days TEXT, start_time TEXT, end_time TEXT, capacity INTEGER NOT NULL,
    UNIQUE (term_code, crn));
CREATE TABLE section_instructors (
    section_id TEXT NOT NULL REFERENCES sections(section_id),
    instructor_id TEXT NOT NULL REFERENCES instructors(instructor_id),
    role TEXT NOT NULL, PRIMARY KEY (section_id, instructor_id));
CREATE TABLE section_registrations (
    registration_id INTEGER PRIMARY KEY, student_id TEXT NOT NULL REFERENCES students(student_id),
    section_id TEXT NOT NULL REFERENCES sections(section_id),
    term_code TEXT NOT NULL REFERENCES academic_periods(term_code),
    registration_date TEXT NOT NULL, status TEXT NOT NULL CHECK (status IN ('registered','withdrawn')),
    UNIQUE (student_id, section_id));
CREATE TABLE final_grades (
    registration_id INTEGER PRIMARY KEY REFERENCES section_registrations(registration_id),
    grade TEXT NOT NULL REFERENCES grade_scale(grade), credit_hours INTEGER NOT NULL,
    quality_points REAL NOT NULL, gpa_hours INTEGER NOT NULL, earned_hours INTEGER NOT NULL);
CREATE TABLE student_term_records (
    student_id TEXT NOT NULL REFERENCES students(student_id),
    term_code TEXT NOT NULL REFERENCES academic_periods(term_code),
    program_code TEXT NOT NULL REFERENCES academic_programs(program_code),
    class_level TEXT NOT NULL, attempted_hours INTEGER NOT NULL, earned_hours INTEGER NOT NULL,
    gpa_hours INTEGER NOT NULL, quality_points REAL NOT NULL, term_gpa REAL,
    cumulative_earned_hours INTEGER NOT NULL, cumulative_gpa_hours INTEGER NOT NULL,
    cumulative_quality_points REAL NOT NULL, cumulative_gpa REAL,
    PRIMARY KEY (student_id, term_code));
CREATE TABLE academic_standings (
    student_id TEXT NOT NULL REFERENCES students(student_id),
    term_code TEXT NOT NULL REFERENCES academic_periods(term_code),
    standing TEXT NOT NULL, PRIMARY KEY (student_id, term_code));
CREATE TABLE person_holds (
    hold_id INTEGER PRIMARY KEY, student_id TEXT NOT NULL REFERENCES students(student_id),
    category TEXT NOT NULL, description TEXT NOT NULL, amount REAL NOT NULL,
    responsible_office TEXT NOT NULL, term_code TEXT NOT NULL REFERENCES academic_periods(term_code),
    start_date TEXT NOT NULL, end_date TEXT);
CREATE TABLE student_advisor_relationships (
    relationship_id INTEGER PRIMARY KEY, student_id TEXT NOT NULL REFERENCES students(student_id),
    advisor_id TEXT NOT NULL REFERENCES instructors(instructor_id),
    advisor_type TEXT NOT NULL, start_term TEXT NOT NULL, end_term TEXT);
CREATE TABLE student_appointments (
    appointment_id INTEGER PRIMARY KEY, student_id TEXT NOT NULL REFERENCES students(student_id),
    advisor_id TEXT NOT NULL REFERENCES instructors(instructor_id),
    term_code TEXT NOT NULL REFERENCES academic_periods(term_code),
    appointment_date TEXT NOT NULL, appointment_type TEXT NOT NULL, status TEXT NOT NULL);
CREATE INDEX idx_sections_course ON sections(course_id, term_code);
CREATE INDEX idx_regs_student ON section_registrations(student_id, term_code);
CREATE INDEX idx_regs_section ON section_registrations(section_id);
CREATE INDEX idx_terms_term ON student_term_records(term_code);
CREATE INDEX idx_sap_student ON student_academic_programs(student_id);
CREATE INDEX idx_holds_student ON person_holds(student_id);
CREATE INDEX idx_appts_student ON student_appointments(student_id);
"""

GRADE_SCALE_ROWS = [
    ("A", 4.0, 1, 1, 0, "Excellent"), ("A-", 3.7, 1, 1, 0, "Excellent"),
    ("B+", 3.3, 1, 1, 0, "Good"), ("B", 3.0, 1, 1, 0, "Good"), ("B-", 2.7, 1, 1, 0, "Good"),
    ("C+", 2.3, 1, 1, 0, "Satisfactory"), ("C", 2.0, 1, 1, 0, "Satisfactory"),
    ("C-", 1.7, 1, 1, 0, "Satisfactory"), ("D+", 1.3, 1, 1, 1, "Passing, below satisfactory"),
    ("D", 1.0, 1, 1, 1, "Passing, below satisfactory"), ("F", 0.0, 1, 0, 1, "Failing"),
    ("W", None, 0, 0, 1, "Withdrawn after census"), ("I", None, 0, 0, 0, "Incomplete"),
    ("P", None, 0, 1, 0, "Pass (pass/no pass course)"),
    ("NP", None, 0, 0, 0, "No pass (pass/no pass course)"),
]


def write_db(g: Generator, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(str(tmp))
    con.executescript(SCHEMA_SQL)
    meta = {
        "institution": "Demonstration University (fictional)",
        "fictional": "true",
        "seed": str(SEED),
        "scale": repr(g.scale),
        "generator_version": GENERATOR_VERSION,
        "first_term": g.terms[0].code,
        "last_term": g.terms[-1].code,
        "notice": ("Synthetic data. Students are pseudonymous ids with no names, birth dates, "
                   "or addresses. Instructor names are generated and fictional. Not modelled on "
                   "any real institution's data."),
    }
    con.executemany("INSERT INTO meta VALUES (?,?)", sorted(meta.items()))
    con.executemany("INSERT INTO grade_scale VALUES (?,?,?,?,?,?)", GRADE_SCALE_ROWS)
    con.executemany("INSERT INTO colleges VALUES (?,?)", C.COLLEGES)
    con.executemany("INSERT INTO academic_periods VALUES (?,?,?,?,?,?,?,?,?)", [
        (t.code, t.name, t.season, t.academic_year, t.index + 1, t.start.isoformat(),
         t.end.isoformat(), t.census.isoformat(), t.reg_start.isoformat()) for t in g.terms])
    con.executemany("INSERT INTO subjects VALUES (?,?,?)", [
        (code, name, college) for code, (name, college, _) in sorted(C.SUBJECTS.items())])
    con.executemany("INSERT INTO academic_programs VALUES (?,?,?,?,?,?,?,?)", [
        (p.code, p.major, p.name, p.degree, p.award, p.college, p.subject, p.credits_required)
        for p in g.programs.values()])
    con.executemany("INSERT INTO courses VALUES (?,?,?,?,?,?,?,?)", [
        (c.cid, c.subject, c.number, c.title, c.credits, c.level,
         "pass_fail" if c.pass_fail else "standard", int(c.is_core))
        for c in sorted(g.courses.values(), key=lambda c: c.cid)])
    con.executemany("INSERT INTO course_prerequisites VALUES (?,?)", [
        (c.cid, pre) for c in sorted(g.courses.values(), key=lambda c: c.cid) for pre in c.prereqs])
    con.executemany("INSERT INTO program_requirements VALUES (?,?,?)", [
        (p.code, cid, p.req_types[cid]) for p in g.programs.values() for cid in p.reqs])
    con.executemany("INSERT INTO instructors VALUES (?,?,?,1,?,?,?,?,?,?)", [
        (i.iid, i.first, i.last, i.rank, i.subject, i.college, i.hire_date, i.hire_term,
         i.leave_term) for i in g.instructors])
    con.executemany("INSERT INTO students VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        (s.sid, s.entry_term, s.entry_type, s.residency, s.first_gen, s.pell, s.transfer_credits,
         s.opening_earned, s.opening_gpa_hours, s.opening_qp10 / 10, g.student_status(s),
         s.exit_term, s.level) for s in g.students if s.last_term is not None])
    enrolled_ids = {s.sid for s in g.students if s.last_term is not None}
    con.executemany("INSERT INTO student_academic_programs VALUES (?,?,?,?,?,?)", [
        tuple(r) for r in g.program_rows if r[1] in enrolled_ids])
    con.executemany("INSERT INTO sections VALUES (?,?,?,?,?,?,?,?,?,?)", [
        (x.sid, x.term, x.course, x.number, x.crn, x.modality, x.days,
         hhmm(x.start) if x.start is not None else None,
         hhmm(x.end) if x.end is not None else None, x.cap) for x in g.sections])
    con.executemany("INSERT INTO section_instructors VALUES (?,?,'primary')", [
        (x.sid, x.instructor) for x in g.sections])
    con.executemany("INSERT INTO section_registrations VALUES (?,?,?,?,?,?)", g.registrations)
    con.executemany("INSERT INTO final_grades VALUES (?,?,?,?,?,?)", g.grades)
    con.executemany("INSERT INTO student_term_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    g.term_records)
    con.executemany("INSERT INTO academic_standings VALUES (?,?,?)", g.standings)
    con.executemany("INSERT INTO person_holds VALUES (?,?,?,?,?,?,?,?,?)", [
        (n, *h) for n, h in enumerate(
            sorted(g.holds, key=lambda h: (h[0], h[6], h[1], h[5])), start=1)
        if h[0] in enrolled_ids])
    con.executemany("INSERT INTO student_advisor_relationships VALUES (?,?,?,'primary',?,?)", [
        tuple(r) for r in g.advisor_rows if r[1] in enrolled_ids])
    con.executemany("INSERT INTO student_appointments VALUES (?,?,?,?,?,?,?)", [
        (n, *a) for n, a in enumerate(g.appointments, start=1)])
    con.commit()
    con.execute("VACUUM")
    con.close()
    os.replace(tmp, out)


def build(scale: float) -> Generator:
    """Two passes: the first measures sections per subject to size departments."""
    first = Generator(scale)
    first.run()
    demand: dict[str, float] = {}
    for sec in first.sections:
        subject = first.courses[sec.course].subject
        demand[subject] = demand.get(subject, 0.0) + 1.0
    g = Generator(scale, demand)
    g.run()
    return g


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)
    if not 0.05 <= args.scale <= 4.0:
        ap.error("--scale must be between 0.05 and 4.0")
    t0 = time.perf_counter()
    g = build(args.scale)
    write_db(g, args.out)
    elapsed = time.perf_counter() - t0
    con = sqlite3.connect(str(args.out))
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    print(f"wrote {args.out} (scale {args.scale}, seed {SEED}) in {elapsed:.1f} s, "
          f"{args.out.stat().st_size / 1e6:.1f} MB")
    for name in tables:
        n = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        print(f"  {name:32s} {n:>9,d}")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
