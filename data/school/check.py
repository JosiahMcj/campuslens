#!/usr/bin/env python3
"""Check a Demonstration University database against its schema, realism rules,
and planted facts. Everything is recomputed from raw rows.

    python3 data/school/check.py                     # var/school/school.db
    python3 data/school/check.py --db /tmp/s.db --json

At the full scale (meta.scale == 1.0, the documented seed) every planted fact
must equal the value in the JSON block of VERIFY.md exactly. At any other
scale each planted pattern is checked for direction only. Exits non-zero on
any failure. Stdlib only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from statistics import median
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_DB = ROOT / "var" / "school" / "school.db"
VERIFY_MD = HERE / "VERIFY.md"
SEED = "20261005"

EXPECTED_COLUMNS: dict[str, list[str]] = {
    "meta": ["key", "value"],
    "grade_scale": ["grade", "quality_points_per_hour", "counts_in_gpa", "earns_credit",
                    "is_dfw", "description"],
    "colleges": ["college_code", "name"],
    "academic_periods": ["term_code", "name", "season", "academic_year", "sequence",
                         "start_date", "end_date", "census_date", "registration_start_date"],
    "subjects": ["subject_code", "name", "college_code"],
    "academic_programs": ["program_code", "major_code", "name", "degree", "award_level",
                          "college_code", "subject_code", "credits_required"],
    "courses": ["course_id", "subject_code", "course_number", "title", "credit_hours",
                "course_level", "grade_mode", "is_core"],
    "course_prerequisites": ["course_id", "prerequisite_course_id"],
    "program_requirements": ["program_code", "course_id", "requirement_type"],
    "instructors": ["instructor_id", "first_name", "last_name", "fictional", "academic_rank",
                    "subject_code", "college_code", "hire_date", "hire_term", "leave_term"],
    "students": ["student_id", "entry_term", "entry_type", "residency", "first_generation",
                 "pell_recipient", "transfer_credits", "opening_credits_earned",
                 "opening_gpa_hours", "opening_quality_points", "enrollment_status",
                 "exit_term", "class_level"],
    "student_academic_programs": ["record_id", "student_id", "program_code", "start_term",
                                  "end_term", "status"],
    "sections": ["section_id", "term_code", "course_id", "section_number", "crn", "modality",
                 "meeting_days", "start_time", "end_time", "capacity"],
    "section_instructors": ["section_id", "instructor_id", "role"],
    "section_registrations": ["registration_id", "student_id", "section_id", "term_code",
                              "registration_date", "status"],
    "final_grades": ["registration_id", "grade", "credit_hours", "quality_points", "gpa_hours",
                     "earned_hours"],
    "student_term_records": ["student_id", "term_code", "program_code", "class_level",
                             "attempted_hours", "earned_hours", "gpa_hours", "quality_points",
                             "term_gpa", "cumulative_earned_hours", "cumulative_gpa_hours",
                             "cumulative_quality_points", "cumulative_gpa"],
    "academic_standings": ["student_id", "term_code", "standing"],
    "person_holds": ["hold_id", "student_id", "category", "description", "amount",
                     "responsible_office", "term_code", "start_date", "end_date"],
    "student_advisor_relationships": ["relationship_id", "student_id", "advisor_id",
                                      "advisor_type", "start_term", "end_term"],
    "student_appointments": ["appointment_id", "student_id", "advisor_id", "term_code",
                             "appointment_date", "appointment_type", "status"],
    "student_profiles": ["student_id", "gender", "race_ethnicity", "age_band_at_entry",
                         "athlete", "honors"],
    "student_term_enrollment": ["student_id", "term_code", "status", "academic_load",
                                "census_hours", "housing"],
    "subsequent_enrollment": ["student_id", "found_term", "sector"],
    "first_destination": ["student_id", "graduation_term", "collected_date", "outcome",
                          "employer_sector", "starting_salary"],
    "graduate_enrollment": ["student_id", "enrollment_begin_date", "program_type",
                            "institution_control"],
    "medical_school_applications": ["student_id", "entering_year", "applied_to", "accepted"],
    "alumni_gifts": ["gift_id", "student_id", "gift_date", "fiscal_year", "amount",
                     "designation"],
}
# The tables before graduate outcomes were added. Their canonical hash is the
# whole-database hash the earlier VERIFY.md recorded, so it proves those rows
# are unchanged.
ORIGINAL_TABLES = tuple(list(EXPECTED_COLUMNS)[:24])
OUTCOME_TABLES = tuple(list(EXPECTED_COLUMNS)[24:])

POINTS10 = {"A": 40, "A-": 37, "B+": 33, "B": 30, "B-": 27, "C+": 23, "C": 20, "C-": 17,
            "D+": 13, "D": 10, "F": 0}
PASSING = {"A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D+", "D", "P"}
DFW_SQL = "('D+','D','F','W')"
GRADED_SQL = "('A','A-','B+','B','B-','C+','C','C-','D+','D','F','W')"


def gpa(qp10: int, hours: int) -> float | None:
    if hours <= 0:
        return None
    return float((Decimal(qp10) / Decimal(hours * 10)).quantize(Decimal("0.01"),
                                                                rounding=ROUND_HALF_UP))


def class_level(earned: int) -> str:
    if earned < 30:
        return "Freshman"
    if earned < 60:
        return "Sophomore"
    if earned < 90:
        return "Junior"
    return "Senior"


def standing_for(prev: str | None, cum: float | None, term_gpa: float | None) -> str:
    if cum is None or cum >= 2.0:
        return "Good Standing"
    if prev in (None, "Good Standing"):
        return "Academic Probation"
    if term_gpa is not None and term_gpa < 2.0:
        return "Academic Suspension"
    return "Continued Probation"


def median_of(values: list[int]) -> float | int | None:
    """The median, as a whole number when it is one (None for no values)."""
    if not values:
        return None
    m = median(values)
    return int(m) if m == int(m) else float(m)


def pct(n: int, d: int) -> float:
    return round(100.0 * n / d, 1) if d else 0.0


def canonical_hash(con: sqlite3.Connection, only: tuple[str, ...] | None = None) -> str:
    """sha256 over every table's rows (or the tables in ``only``) in a fixed
    order (file bytes may differ)."""
    h = hashlib.sha256()
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    for t in tables:
        if only is not None and t not in only:
            continue
        cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')]
        order = ", ".join(f'"{c}"' for c in cols)
        h.update(f"#{t}:{','.join(cols)}\n".encode())
        for row in con.execute(f'SELECT * FROM "{t}" ORDER BY {order}'):
            h.update(json.dumps(row, separators=(",", ":")).encode())
            h.update(b"\n")
    return h.hexdigest()


class Checker:
    def __init__(self, con: sqlite3.Connection) -> None:
        self.con = con
        self.results: list[dict[str, Any]] = []
        self.meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
        self.full = self.meta.get("scale") == "1.0" and self.meta.get("seed") == SEED
        self.thermo_rank_first = False

    def q(self, sql: str, args: tuple[Any, ...] = ()) -> list[Any]:
        return self.con.execute(sql, args).fetchall()

    def record(self, name: str, ok: bool, detail: str, values: Any = None) -> None:
        self.results.append({"name": name, "ok": bool(ok), "detail": detail, "values": values})

    # ------------------------------------------------------------ schema
    def check_schema(self) -> None:
        problems = []
        for table, cols in EXPECTED_COLUMNS.items():
            got = [r[1] for r in self.q(f'PRAGMA table_info("{table}")')]
            if got != cols:
                problems.append(f"{table}: {got}")
        extra = sorted({r[0] for r in self.q("SELECT name FROM sqlite_master WHERE type='table'")}
                       - set(EXPECTED_COLUMNS))
        if extra:
            problems.append(f"unexpected tables {extra}")
        self.record("schema", not problems, "; ".join(problems) or f"{len(EXPECTED_COLUMNS)} tables, columns as documented")
        fk = self.q("PRAGMA foreign_key_check")
        self.record("foreign_keys", not fk, f"{len(fk)} dangling references")

    def check_privacy(self) -> None:
        bad_ids = self.q("SELECT COUNT(*) FROM students WHERE student_id NOT GLOB 'S-[0-9]*'")[0][0]
        cols = " ".join(EXPECTED_COLUMNS["students"] + EXPECTED_COLUMNS["student_profiles"])
        personal = [w for w in ("name", "birth", "address", "email", "phone") if w in cols]
        fictional = self.q("SELECT COUNT(*) FROM instructors WHERE fictional != 1")[0][0]
        ok = bad_ids == 0 and not personal and fictional == 0 and self.meta.get("fictional") == "true"
        self.record("privacy", ok, f"non-pseudonymous ids {bad_ids}, personal columns {personal}, "
                    f"non-fictional instructors {fictional}")

    # ------------------------------------------------------------ grades and GPA
    def check_grades(self) -> None:
        rows = self.q("""SELECT g.grade, g.credit_hours, g.quality_points, g.gpa_hours,
                                g.earned_hours, c.credit_hours, c.grade_mode
                         FROM final_grades g JOIN section_registrations r USING (registration_id)
                         JOIN sections s USING (section_id) JOIN courses c USING (course_id)""")
        bad = 0
        for grade, ch, qp, gh, eh, course_ch, mode in rows:
            pts = POINTS10.get(grade)
            exp_qp = (pts or 0) * ch / 10
            exp_gh = ch if pts is not None else 0
            exp_eh = ch if grade in PASSING else 0
            mode_ok = (grade in ("P", "NP", "W", "I")) if mode == "pass_fail" else grade not in ("P", "NP")
            if (ch != course_ch or abs(qp - exp_qp) > 1e-9 or gh != exp_gh or eh != exp_eh
                    or not mode_ok):
                bad += 1
        self.record("grade_rows", bad == 0, f"{bad} of {len(rows)} grade rows inconsistent")
        status_bad = self.q("""SELECT COUNT(*) FROM section_registrations r JOIN final_grades g
            USING (registration_id) WHERE (g.grade = 'W') != (r.status = 'withdrawn')""")[0][0]
        self.record("withdrawn_status", status_bad == 0, f"{status_bad} status/grade mismatches")

    def check_gpa_recompute(self) -> None:
        students = {r[0]: r for r in self.q(
            "SELECT student_id, transfer_credits, opening_credits_earned, opening_gpa_hours, "
            "opening_quality_points FROM students")}
        per: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0, 0, 0])
        for sid, term, grade, ch in self.q(
                """SELECT r.student_id, r.term_code, g.grade, g.credit_hours FROM final_grades g
                   JOIN section_registrations r USING (registration_id)"""):
            acc = per[(sid, term)]
            if grade != "W":
                acc[0] += ch
            if grade in PASSING:
                acc[1] += ch
            if grade in POINTS10:
                acc[2] += ch
                acc[3] += POINTS10[grade] * ch
        recs = self.q("SELECT * FROM student_term_records ORDER BY student_id, term_code")
        standings = dict(((r[0], r[1]), r[2]) for r in self.q("SELECT * FROM academic_standings"))
        bad_term = bad_cum = bad_range = bad_level = bad_standing = 0
        running: dict[str, list[int]] = {}
        prev_standing: dict[str, str] = {}
        for (sid, term, _prog, level, att, earn, gh, qp, tg, cearn, cgh, cqp, cg) in recs:
            st = students[sid]
            if sid not in running:
                running[sid] = [st[1] + st[2], st[3], round(st[4] * 10)]
            acc = per.get((sid, term), [0, 0, 0, 0])
            if class_level(running[sid][0]) != level:
                bad_level += 1
            if (att, earn, gh, round(qp * 10)) != tuple(acc) or tg != gpa(acc[3], acc[2]):
                bad_term += 1
            running[sid][0] += acc[1]
            running[sid][1] += acc[2]
            running[sid][2] += acc[3]
            e, h, q10 = running[sid]
            if (cearn, cgh, round(cqp * 10)) != (e, h, q10) or cg != gpa(q10, h):
                bad_cum += 1
            for v in (tg, cg):
                if v is not None and not 0.0 <= v <= 4.0:
                    bad_range += 1
            exp = standing_for(prev_standing.get(sid), cg, tg)
            if standings.get((sid, term)) != exp:
                bad_standing += 1
            prev_standing[sid] = exp
        rec_keys = {(r[0], r[1]) for r in recs}
        missing = len(set(per) ^ rec_keys)
        self.record("gpa_range", bad_range == 0, f"{bad_range} GPAs outside 0.00-4.00")
        self.record("term_gpa_recomputed", bad_term == 0 and missing == 0,
                    f"{bad_term} of {len(recs)} term records differ from their grades; "
                    f"{missing} student-terms with grades but no record, or the reverse")
        self.record("cumulative_gpa_recomputed", bad_cum == 0,
                    f"{bad_cum} of {len(recs)} cumulative values differ from the running sums")
        self.record("class_level_recomputed", bad_level == 0, f"{bad_level} class levels differ")
        self.record("standing_recomputed", bad_standing == 0,
                    f"{bad_standing} standings differ from the standing rule")

    # ------------------------------------------------------------ operations
    def check_schedule(self) -> None:
        rows = self.q("""SELECT r.student_id, r.term_code, s.section_id, s.meeting_days,
                                s.start_time, s.end_time
                         FROM section_registrations r JOIN sections s USING (section_id)
                         WHERE s.meeting_days IS NOT NULL""")
        by: dict[tuple[str, str], list[tuple[str, int, int]]] = defaultdict(list)

        def mins(hm: str) -> int:
            h, m = hm.split(":")
            return int(h) * 60 + int(m)

        for sid, term, _sec, days, start, end in rows:
            for d in days:
                by[(sid, term)].append((d, mins(start), mins(end)))
        clashes = 0
        for meets in by.values():
            meets.sort()
            for a, b in zip(meets, meets[1:]):
                if a[0] == b[0] and b[1] < a[2]:
                    clashes += 1
        self.record("no_time_conflicts", clashes == 0,
                    f"{clashes} overlapping meetings across {len(by)} student schedules")
        over = self.q("""SELECT COUNT(*) FROM (SELECT s.section_id, s.capacity, COUNT(*) n
                         FROM sections s JOIN section_registrations r USING (section_id)
                         GROUP BY s.section_id HAVING n > s.capacity)""")[0][0]
        empty = self.q("""SELECT COUNT(*) FROM sections WHERE section_id NOT IN
                          (SELECT section_id FROM section_registrations)""")[0][0]
        self.record("enrollment_within_capacity", over == 0,
                    f"{over} sections over capacity, {empty} empty sections")
        term_mismatch = self.q("""SELECT COUNT(*) FROM section_registrations r
                                  JOIN sections s USING (section_id)
                                  WHERE r.term_code != s.term_code""")[0][0]
        self.record("registration_term_matches_section", term_mismatch == 0,
                    f"{term_mismatch} registrations whose term differs from their section's")
        cover = self.q("""SELECT tr.student_id, tr.term_code, tr.program_code,
                                 (SELECT COUNT(*) FROM student_academic_programs p
                                  WHERE p.student_id = tr.student_id AND p.start_term <= tr.term_code
                                    AND (p.end_term IS NULL OR p.end_term >= tr.term_code)),
                                 (SELECT COUNT(*) FROM student_academic_programs p
                                  WHERE p.student_id = tr.student_id AND p.start_term <= tr.term_code
                                    AND (p.end_term IS NULL OR p.end_term >= tr.term_code)
                                    AND p.program_code = tr.program_code)
                          FROM student_term_records tr""")
        bad_cover = sum(1 for r in cover if r[3] != 1 or r[4] != 1)
        self.record("term_program_matches_history", bad_cover == 0,
                    f"{bad_cover} of {len(cover)} term records not covered by exactly one "
                    "matching student_academic_programs row")
        dup = self.q("""SELECT COUNT(*) FROM (SELECT r.student_id, r.term_code, s.course_id
                        FROM section_registrations r JOIN sections s USING (section_id)
                        GROUP BY 1, 2, 3 HAVING COUNT(*) > 1)""")[0][0]
        self.record("one_section_per_course", dup == 0, f"{dup} duplicate course registrations")
        late = self.q("""SELECT COUNT(*) FROM section_registrations r JOIN academic_periods p
                         USING (term_code) WHERE r.registration_date > date(p.start_date, '+7 day')
                         OR r.registration_date < p.registration_start_date""")[0][0]
        self.record("registration_dates", late == 0, f"{late} registrations outside the window")
        not_emp = self.q("""SELECT COUNT(*) FROM section_instructors si JOIN sections s
                            USING (section_id) JOIN instructors i USING (instructor_id)
                            WHERE NOT (i.hire_term <= s.term_code AND
                                       (i.leave_term IS NULL OR s.term_code < i.leave_term))""")[0][0]
        one = self.q("""SELECT COUNT(*) FROM sections WHERE section_id NOT IN
                        (SELECT section_id FROM section_instructors)""")[0][0]
        self.record("instructors_employed", not_emp == 0 and one == 0,
                    f"{not_emp} sections taught outside employment, {one} without an instructor")
        adv = self.q("""SELECT COUNT(*) FROM student_appointments a JOIN instructors i
                        ON i.instructor_id = a.advisor_id
                        WHERE NOT (i.hire_term <= a.term_code AND
                                   (i.leave_term IS NULL OR a.term_code < i.leave_term))""")[0][0]
        self.record("advisors_employed", adv == 0, f"{adv} appointments with a departed advisor")
        holds = self.q("""SELECT COUNT(*) FROM person_holds WHERE end_date IS NOT NULL
                          AND end_date < start_date OR amount < 0""")[0][0]
        self.record("holds_dates", holds == 0, f"{holds} holds ending before they start")
        blocked = self.q("""SELECT COUNT(*) FROM section_registrations r JOIN person_holds h
                            ON h.student_id = r.student_id
                            WHERE h.category IN ('financial', 'registrar', 'advising')
                              AND h.start_date <= r.registration_date
                              AND (h.end_date IS NULL OR h.end_date > r.registration_date)""")[0][0]
        self.record("holds_block_registration", blocked == 0,
                    f"{blocked} registrations made while a financial, registrar, or advising "
                    "hold was active")
        spans = self.q("""SELECT (SELECT COUNT(*) FROM student_academic_programs
                                  WHERE end_term < start_term),
                                 (SELECT COUNT(*) FROM student_advisor_relationships
                                  WHERE end_term < start_term)""")[0]
        self.record("history_spans_ordered", spans == (0, 0),
                    f"{spans[0]} program rows and {spans[1]} advisor rows end before they start")
        adv_span = self.q("""SELECT COUNT(*) FROM student_advisor_relationships a
                             JOIN instructors i ON i.instructor_id = a.advisor_id
                             WHERE a.start_term < i.hire_term OR (i.leave_term IS NOT NULL
                               AND (a.end_term IS NULL OR a.end_term >= i.leave_term))""")[0][0]
        self.record("advisor_relationships_within_employment", adv_span == 0,
                    f"{adv_span} advisor relationships outside the advisor's employment")

    def check_prerequisites(self) -> None:
        prereqs: dict[str, list[str]] = defaultdict(list)
        for cid, pre in self.q("SELECT course_id, prerequisite_course_id FROM course_prerequisites"):
            prereqs[cid].append(pre)
        rows = self.q("""SELECT r.student_id, r.term_code, s.course_id, g.grade
                         FROM section_registrations r JOIN sections s USING (section_id)
                         JOIN final_grades g USING (registration_id)
                         JOIN students st USING (student_id)
                         WHERE st.entry_type = 'first_time' AND st.entry_term >= '202110'
                         ORDER BY r.student_id, r.term_code""")
        passed: dict[str, dict[str, str]] = defaultdict(dict)  # student -> course -> first term
        for sid, term, cid, grade in rows:
            if grade in PASSING and cid not in passed[sid]:
                passed[sid][cid] = term
        total = met = 0
        for sid, term, cid, _grade in rows:
            if cid not in prereqs:
                continue
            total += 1
            if all(passed[sid].get(p, "999999") < term for p in prereqs[cid]):
                met += 1
        share = met / total if total else 1.0
        self.record("prerequisites_mostly_respected", share >= 0.95,
                    f"{pct(met, total)} % of {total} registrations with prerequisites had them "
                    "passed in an earlier term (first-time students who entered in the window)",
                    {"share": round(share, 4)})

    def check_graduation(self) -> None:
        rows = self.q("""SELECT st.student_id, tr.cumulative_earned_hours, tr.cumulative_gpa,
                                ap.credits_required
                         FROM students st JOIN student_term_records tr
                           ON tr.student_id = st.student_id AND tr.term_code = st.exit_term
                         JOIN student_academic_programs sap ON sap.student_id = st.student_id
                           AND sap.status = 'graduated'
                         JOIN academic_programs ap ON ap.program_code = sap.program_code
                         WHERE st.enrollment_status = 'graduated'""")
        n_grad = self.q("SELECT COUNT(*) FROM students WHERE enrollment_status='graduated'")[0][0]
        bad = sum(1 for _sid, earned, cg, req in rows if earned < req or cg is None or cg < 2.0)
        self.record("graduation_after_enough_credits", bad == 0 and len(rows) == n_grad,
                    f"{bad} of {n_grad} graduates short of credits or below 2.00; "
                    f"{n_grad - len(rows)} graduates without a matching program record")
        after = self.q("""SELECT COUNT(*) FROM section_registrations r JOIN students st
                          USING (student_id) WHERE st.exit_term IS NOT NULL
                          AND r.term_code > st.exit_term""")[0][0]
        self.record("no_registration_after_exit", after == 0, f"{after} registrations after exit")

    def check_distributions(self) -> None:
        qp, gh = self.q("SELECT SUM(quality_points), SUM(gpa_hours) FROM final_grades")[0]
        mean_grade = qp / gh
        cum = self.q("""SELECT AVG(cumulative_gpa) FROM student_term_records r
                        WHERE term_code = (SELECT MAX(term_code) FROM student_term_records r2
                                           WHERE r2.student_id = r.student_id)""")[0][0]
        self.record("mean_gpa_plausible", 2.8 <= mean_grade <= 3.2 and 2.8 <= cum <= 3.25,
                    f"mean grade points {mean_grade:.3f}, mean final cumulative GPA {cum:.3f}",
                    {"mean_grade_points": round(mean_grade, 3), "mean_cumulative_gpa": round(cum, 3)})
        min_n = 30 if self.full else 10
        rows = self.q(f"""SELECT s.course_id, SUM(g.grade IN {DFW_SQL}), COUNT(*)
                          FROM final_grades g JOIN section_registrations r USING (registration_id)
                          JOIN sections s USING (section_id)
                          JOIN courses c USING (course_id)
                          WHERE g.grade IN {GRADED_SQL} AND c.grade_mode = 'standard'
                          GROUP BY s.course_id HAVING COUNT(*) >= ?""",
                       (min_n,))
        inside = sum(1 for _c, d, n in rows if 0.05 <= d / n <= 0.25)
        share = inside / len(rows) if rows else 0.0
        need = 0.70 if self.full else 0.60  # small samples are noisier at reduced scale
        self.record("dfw_mostly_5_to_25", share >= need,
                    f"{pct(inside, len(rows))} % of {len(rows)} courses (>= {min_n} graded "
                    f"registrations) have a DFW rate between 5 % and 25 % (need {need:.0%})",
                    {"share": round(share, 3)})

    # ------------------------------------------------------------ enrollment history
    def check_enrollment_history(self) -> None:
        """student_term_enrollment agrees with the records it is derived from."""
        q = self.q
        missing_profile = q("""SELECT COUNT(*) FROM students s WHERE NOT EXISTS
            (SELECT 1 FROM student_profiles p WHERE p.student_id = s.student_id)""")[0][0]
        intl = q("""SELECT COUNT(*) FROM students s JOIN student_profiles p USING (student_id)
            WHERE (s.residency = 'international') != (p.race_ethnicity = 'nonresident')""")[0][0]
        self.record("profiles_complete", missing_profile == 0 and intl == 0,
                    f"{missing_profile} students without a profile, {intl} residency/IPEDS "
                    "nonresident mismatches")
        regular = "substr(term_code, 5, 2) IN ('10', '20')"
        enrolled_mismatch = q(f"""SELECT
            (SELECT COUNT(*) FROM student_term_records r WHERE {regular} AND NOT EXISTS
                (SELECT 1 FROM student_term_enrollment e WHERE e.student_id = r.student_id
                 AND e.term_code = r.term_code AND e.status = 'enrolled'))
          + (SELECT COUNT(*) FROM student_term_enrollment e WHERE e.status = 'enrolled'
             AND NOT EXISTS (SELECT 1 FROM student_term_records r
                 WHERE r.student_id = e.student_id AND r.term_code = e.term_code))""")[0][0]
        self.record("enrolled_terms_match_records", enrolled_mismatch == 0,
                    f"{enrolled_mismatch} enrolled terms without a term record or the reverse")
        load_bad = q("""SELECT COUNT(*) FROM student_term_enrollment e WHERE
            (e.status = 'enrolled') != (e.academic_load IS NOT NULL)
            OR (e.status = 'enrolled' AND ((e.census_hours >= 12) != (e.academic_load = 'full_time')
                OR e.housing IS NULL OR e.census_hours != (
                    SELECT SUM(c.credit_hours) FROM section_registrations r
                    JOIN sections x ON x.section_id = r.section_id
                    JOIN courses c ON c.course_id = x.course_id
                    WHERE r.student_id = e.student_id AND r.term_code = e.term_code)))""")[0][0]
        self.record("load_from_census_hours", load_bad == 0,
                    f"{load_bad} terms whose load, housing, or census hours disagree with "
                    "the registrations")
        grad_bad = q("""SELECT COUNT(*) FROM student_term_enrollment e JOIN students s
            USING (student_id) WHERE e.status = 'graduated'
            AND (s.enrollment_status != 'graduated' OR e.term_code <= s.exit_term)""")[0][0]
        transfer_bad = q("""SELECT COUNT(*) FROM subsequent_enrollment x JOIN students s
            USING (student_id) WHERE s.enrollment_status NOT IN ('withdrawn', 'stopped_out')
            OR x.found_term <= s.exit_term""")[0][0]
        self.record("outcomes_consistent", grad_bad == 0 and transfer_bad == 0,
                    f"{grad_bad} graduated terms not after a degree, {transfer_bad} "
                    "transfer-out matches for students who did not leave without a degree")
        # Plausible rates: first-year retention of first-time fall entrants,
        # graduation of the first entering class within six academic years.
        ret = q("""SELECT AVG(EXISTS (SELECT 1 FROM student_term_records r
                       WHERE r.student_id = s.student_id
                         AND r.term_code = (CAST(substr(s.entry_term, 1, 4) AS INTEGER) + 1)
                             || '10'))
                   FROM students s WHERE s.entry_type = 'first_time'
                     AND s.entry_term BETWEEN '202110' AND '202510'
                     AND substr(s.entry_term, 5, 2) = '10'""")[0][0] or 0.0
        grad6 = q("""SELECT AVG(enrollment_status = 'graduated') FROM students
                     WHERE entry_type = 'first_time' AND entry_term = '202110'""")[0][0] or 0.0
        part_time = q("""SELECT AVG(academic_load = 'part_time') FROM student_term_enrollment
                         WHERE status = 'enrolled'""")[0][0] or 0.0
        ok = 0.65 <= ret <= 0.90 and 0.35 <= grad6 <= 0.75 and 0.03 <= part_time <= 0.30
        self.record("outcome_rates_plausible", ok,
                    f"first-year retention {ret:.1%}, 6-year graduation (Fall 2020 entrants) "
                    f"{grad6:.1%}, part-time terms {part_time:.1%}",
                    {"retention": round(ret, 3), "grad6": round(grad6, 3),
                     "part_time": round(part_time, 3)})

    # ------------------------------------------------------------ planted facts
    def course_dfw(self, course: str, extra: str = "", args: tuple[Any, ...] = ()) -> tuple[int, int]:
        d, n = self.q(f"""SELECT COALESCE(SUM(g.grade IN {DFW_SQL}), 0), COUNT(*)
                          FROM final_grades g JOIN section_registrations r USING (registration_id)
                          JOIN sections s USING (section_id)
                          JOIN courses c USING (course_id)
                          JOIN students st ON st.student_id = r.student_id
                          WHERE s.course_id = ? AND g.grade IN {GRADED_SQL}
                            AND c.grade_mode = 'standard' {extra}""",
                      (course, *args))[0]
        return int(d), int(n)

    def planted(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        # 1. Major with the lowest average cumulative GPA.
        min_students = 20 if self.full else 3
        rows = self.q("""WITH last AS (
                            SELECT r.student_id, r.program_code, r.cumulative_gpa
                            FROM student_term_records r
                            WHERE r.term_code = (SELECT MAX(term_code) FROM student_term_records r2
                                                 WHERE r2.student_id = r.student_id)
                              AND r.cumulative_gpa IS NOT NULL)
                         SELECT ap.major_code, ap.name, COUNT(*), AVG(cumulative_gpa)
                         FROM last JOIN academic_programs ap USING (program_code)
                         GROUP BY ap.major_code HAVING COUNT(*) >= ? ORDER BY 4, 1""",
                      (min_students,))
        lowest, runner = rows[0], rows[1]
        overall = self.q("""SELECT AVG(cumulative_gpa) FROM student_term_records r
                            WHERE term_code = (SELECT MAX(term_code) FROM student_term_records r2
                                               WHERE r2.student_id = r.student_id)""")[0][0]
        out["lowest_gpa_major"] = {
            "major": lowest[0], "name": lowest[1], "students": lowest[2],
            "avg_cumulative_gpa": round(lowest[3], 3),
            "next_lowest_major": runner[0], "next_lowest_avg": round(runner[3], 3),
            "margin": round(runner[3] - lowest[3], 3), "all_students_avg": round(overall, 3)}
        major = lowest[0]
        me_avg = next((r[3] for r in rows if r[0] == "MEEN"), None)
        # 2. Hardest required course in that major by DFW rate.
        min_secs, min_terms = (8, 4) if self.full else (1, 1)
        hard = self.q(f"""SELECT s.course_id, c.title, COUNT(DISTINCT s.section_id),
                                 COUNT(DISTINCT s.term_code), SUM(g.grade IN {DFW_SQL}), COUNT(*)
                          FROM program_requirements pr
                          JOIN academic_programs ap ON ap.program_code = pr.program_code
                          JOIN sections s ON s.course_id = pr.course_id
                          JOIN courses c ON c.course_id = s.course_id
                          JOIN section_registrations r ON r.section_id = s.section_id
                          JOIN final_grades g ON g.registration_id = r.registration_id
                          WHERE ap.major_code = ? AND g.grade IN {GRADED_SQL}
                            AND c.grade_mode = 'standard'
                          GROUP BY s.course_id
                          HAVING COUNT(DISTINCT s.section_id) >= ? AND COUNT(DISTINCT s.term_code) >= ?
                          ORDER BY 1.0 * SUM(g.grade IN {DFW_SQL}) / COUNT(*) DESC, 1""",
                      (major, min_secs, min_terms))
        top, second = hard[0], hard[1]
        out["hardest_required_course"] = {
            "major": major, "course": top[0], "title": top[1], "sections": top[2], "terms": top[3],
            "dfw": top[4], "graded": top[5], "dfw_rate_pct": pct(top[4], top[5]),
            "next_course": second[0], "next_dfw_rate_pct": pct(second[4], second[5])}
        # At full scale the ranked course is used (and must be Thermodynamics I).
        # At reduced scale the ranking is noise, so the direction checks use the
        # planted course directly.
        course = top[0] if self.full else "MEEN 3310"
        self.thermo_rank_first = top[0] == "MEEN 3310" or not self.full
        # 3. Instructors of that course.
        inst = self.q(f"""SELECT i.instructor_id, i.first_name || ' ' || i.last_name,
                                 COUNT(DISTINCT s.section_id), COUNT(DISTINCT s.term_code),
                                 SUM(g.grade IN {DFW_SQL}), COUNT(*)
                          FROM sections s JOIN section_instructors si USING (section_id)
                          JOIN instructors i USING (instructor_id)
                          JOIN section_registrations r USING (section_id)
                          JOIN final_grades g USING (registration_id)
                          WHERE s.course_id = ? AND g.grade IN {GRADED_SQL}
                          GROUP BY i.instructor_id ORDER BY 3 DESC, 1""", (course,))
        out["course_instructors"] = {
            "course": course,
            "instructors": [{"instructor_id": r[0], "name": r[1], "sections": r[2], "terms": r[3],
                             "dfw": r[4], "graded": r[5], "dfw_rate_pct": pct(r[4], r[5])}
                            for r in inst]}
        # 4. Organic Chemistry I before and after the instructor change.
        orgo = "CHEM 2323"
        change = "202410"
        before = self.course_dfw(orgo, "AND s.term_code < ?", (change,))
        after = self.course_dfw(orgo, "AND s.term_code >= ?", (change,))
        who = self.q("""SELECT s.term_code < ?, GROUP_CONCAT(DISTINCT si.instructor_id),
                               COUNT(DISTINCT s.section_id)
                        FROM sections s JOIN section_instructors si USING (section_id)
                        WHERE s.course_id = ? GROUP BY 1 ORDER BY 1 DESC""", (change, orgo))
        out["instructor_change"] = {
            "course": orgo, "change_term": change,
            "before_instructors": who[0][1], "before_sections": who[0][2],
            "before_dfw": before[0], "before_graded": before[1],
            "before_dfw_rate_pct": pct(*before),
            "after_instructors": who[1][1], "after_sections": who[1][2],
            "after_dfw": after[0], "after_graded": after[1], "after_dfw_rate_pct": pct(*after)}
        # 5. Equity gap in College Algebra.
        alg = "MATH 1314"
        fg = self.course_dfw(alg, "AND st.first_generation = 1")
        cg = self.course_dfw(alg, "AND st.first_generation = 0")
        gaps = self.q(f"""SELECT s.course_id,
                             1.0 * SUM(st.first_generation = 1 AND g.grade IN {DFW_SQL})
                                 / SUM(st.first_generation = 1)
                             - 1.0 * SUM(st.first_generation = 0 AND g.grade IN {DFW_SQL})
                                 / SUM(st.first_generation = 0) AS gap
                          FROM final_grades g JOIN section_registrations r USING (registration_id)
                          JOIN sections s USING (section_id) JOIN courses c USING (course_id)
                          JOIN students st ON st.student_id = r.student_id
                          WHERE c.course_level = 1000 AND g.grade IN {GRADED_SQL}
                            AND c.grade_mode = 'standard'
                          GROUP BY s.course_id
                          HAVING SUM(st.first_generation = 1) >= ? AND SUM(st.first_generation = 0) >= ?
                          ORDER BY gap DESC, 1""",
                       (100, 100) if self.full else (5, 5))
        out["equity_gap"] = {
            "course": alg, "first_gen_dfw": fg[0], "first_gen_graded": fg[1],
            "first_gen_dfw_rate_pct": pct(*fg), "continuing_gen_dfw": cg[0],
            "continuing_gen_graded": cg[1], "continuing_gen_dfw_rate_pct": pct(*cg),
            "gap_points": round(pct(*fg) - pct(*cg), 1),
            "largest_gap_course_1000_level": gaps[0][0] if gaps else None,
            "second_largest_gap_course": gaps[1][0] if len(gaps) > 1 else None,
            "second_largest_gap_points": round(100 * gaps[1][1], 1) if len(gaps) > 1 else None}
        # 6. Spring continuing registration, latest spring vs the spring before.
        def continuing(term: str) -> int:
            return int(self.q("""SELECT COUNT(DISTINCT r.student_id) FROM section_registrations r
                                 JOIN students st USING (student_id)
                                 WHERE r.term_code = ? AND st.entry_term < ?""", (term, term))[0][0])
        cur, prev = continuing("202620"), continuing("202520")
        out["spring_registration"] = {
            "term": "202620", "prior_term": "202520", "continuing_registered": cur,
            "prior_continuing_registered": prev,
            "change_pct": round(100.0 * (cur / prev - 1), 1) if prev else None}
        # 7. Fastest-growing major, Fall 2020 to Fall 2025 headcount.
        min_base = 40 if self.full else 2
        grow = self.q("""SELECT ap.major_code, ap.name,
                                SUM(r.term_code = '202110'), SUM(r.term_code = '202610')
                         FROM student_term_records r JOIN academic_programs ap USING (program_code)
                         WHERE r.term_code IN ('202110', '202610')
                         GROUP BY ap.major_code HAVING SUM(r.term_code = '202110') >= ?""",
                      (min_base,))
        ranked = sorted(grow, key=lambda r: (-(r[3] / r[2]), r[0]))
        out["fastest_growing_major"] = {
            "major": ranked[0][0], "name": ranked[0][1], "fall_2020": ranked[0][2],
            "fall_2025": ranked[0][3], "growth_pct": pct(ranked[0][3] - ranked[0][2], ranked[0][2]),
            "next_major": ranked[1][0], "next_growth_pct": pct(ranked[1][3] - ranked[1][2],
                                                                ranked[1][2])}
        cs = next((r for r in grow if r[0] == "CSCI"), None)
        growth_by_major = {r[0]: r[3] / r[2] for r in grow}
        # 8. Online withdrawal rate by term.
        wrows = self.q(f"""SELECT s.term_code, s.modality, SUM(g.grade = 'W'), COUNT(*)
                           FROM final_grades g JOIN section_registrations r USING (registration_id)
                           JOIN sections s USING (section_id) JOIN courses c USING (course_id)
                           JOIN academic_periods p ON p.term_code = s.term_code
                           WHERE p.season != 'Summer' AND s.modality IN ('online', 'in_person')
                             AND c.grade_mode = 'standard' AND g.grade IN {GRADED_SQL}
                           GROUP BY 1, 2""")
        by_term: dict[str, dict[str, tuple[int, int]]] = defaultdict(dict)
        for term, mod, w, n in wrows:
            by_term[term][mod] = (int(w), int(n))
        gaps_w = sorted(((pct(*v["online"]) - pct(*v["in_person"]), t)
                         for t, v in by_term.items() if "online" in v and "in_person" in v),
                        reverse=True)
        top_term = gaps_w[0][1]
        out["online_withdrawals"] = {
            "term": top_term,
            "online_w": by_term[top_term]["online"][0], "online_graded": by_term[top_term]["online"][1],
            "online_w_rate_pct": pct(*by_term[top_term]["online"]),
            "in_person_w": by_term[top_term]["in_person"][0],
            "in_person_graded": by_term[top_term]["in_person"][1],
            "in_person_w_rate_pct": pct(*by_term[top_term]["in_person"]),
            "gap_points": round(gaps_w[0][0], 1), "next_term": gaps_w[1][1],
            "next_gap_points": round(gaps_w[1][0], 1)}
        self._directional(out, me_avg, cs, growth_by_major, by_term)
        return out

    def _directional(self, out: dict[str, Any], me_avg: float | None, cs: Any,
                     growth: dict[str, float], by_term: dict[str, dict[str, tuple[int, int]]]) -> None:
        """Direction-only versions of every planted pattern (used at any scale)."""
        lo = out["lowest_gpa_major"]
        self.record("pattern_low_gpa_major", me_avg is not None and me_avg < lo["all_students_avg"] - 0.15,
                    f"Mechanical Engineering average {me_avg if me_avg is None else round(me_avg, 3)}"
                    f" vs all students {lo['all_students_avg']}")
        thermo = self.course_dfw("MEEN 3310")
        other = self.q(f"""SELECT SUM(g.grade IN {DFW_SQL}), COUNT(*)
                           FROM program_requirements pr
                           JOIN sections s ON s.course_id = pr.course_id
                           JOIN section_registrations r ON r.section_id = s.section_id
                           JOIN final_grades g ON g.registration_id = r.registration_id
                           JOIN courses c ON c.course_id = s.course_id
                           WHERE pr.program_code = 'BS-MEEN' AND pr.course_id != 'MEEN 3310'
                             AND g.grade IN {GRADED_SQL} AND c.grade_mode = 'standard'""")[0]
        self.record("pattern_hard_course",
                    self.thermo_rank_first and thermo[1] > 0
                    and pct(*thermo) > pct(other[0], other[1]) + 10,
                    f"Thermodynamics I DFW {pct(*thermo)} % vs other Mechanical Engineering "
                    f"requirements {pct(other[0], other[1])} %")
        inst = {r["instructor_id"]: r for r in out["course_instructors"]["instructors"]}
        a, b = inst.get("I-0001"), inst.get("I-0002")
        ok = a is not None and b is not None and a["sections"] > b["sections"] and \
            a["dfw_rate_pct"] > b["dfw_rate_pct"] + 5
        self.record("pattern_instructor_contrast", ok,
                    f"I-0001 {a and (a['sections'], a['dfw_rate_pct'])}, "
                    f"I-0002 {b and (b['sections'], b['dfw_rate_pct'])} (sections, DFW %)")
        ch = out["instructor_change"]
        self.record("pattern_instructor_change",
                    ch["before_instructors"] == "I-0003" and ch["after_instructors"] == "I-0004"
                    and ch["before_dfw_rate_pct"] > ch["after_dfw_rate_pct"] + 8,
                    f"Organic Chemistry I DFW {ch['before_dfw_rate_pct']} % ({ch['before_instructors']})"
                    f" -> {ch['after_dfw_rate_pct']} % ({ch['after_instructors']})")
        eq = out["equity_gap"]
        self.record("pattern_equity_gap", eq["gap_points"] > 5,
                    f"College Algebra DFW first-generation {eq['first_gen_dfw_rate_pct']} % vs "
                    f"continuing-generation {eq['continuing_gen_dfw_rate_pct']} %")
        sp = out["spring_registration"]
        self.record("pattern_spring_decline", sp["change_pct"] is not None and sp["change_pct"] < 0,
                    f"continuing spring registration {sp['prior_continuing_registered']} -> "
                    f"{sp['continuing_registered']} ({sp['change_pct']} %)")
        med = sorted(growth.values())[len(growth) // 2] if growth else 0
        cs_growth = cs[3] / cs[2] if cs else 0
        self.record("pattern_fastest_growth", cs is not None and cs_growth > 1.3 and cs_growth > med,
                    f"Computer Science Fall 2020 -> Fall 2025 ratio {round(cs_growth, 2)} vs "
                    f"median major {round(med, 2)}")
        t = by_term.get("202120", {})
        okw = "online" in t and "in_person" in t and pct(*t["online"]) > pct(*t["in_person"]) + 5
        self.record("pattern_online_withdrawals", okw,
                    f"Spring 2021 W rate online {t and pct(*t.get('online', (0, 0)))} % vs in person "
                    f"{t and pct(*t.get('in_person', (0, 0)))} %")

    # ------------------------------------------------------------ graduate outcomes
    def _graduates(self) -> list[dict[str, Any]]:
        """Every graduate once: major and award of the graduated program, the
        graduation date (last day of the term), final cumulative GPA, and the
        profile flags the outcome patterns use."""
        if getattr(self, "_grads", None) is None:
            rows = self.q("""
                SELECT st.student_id, sap.end_term, p.end_date, ap.major_code, ap.name,
                       ap.award_level, t.cumulative_gpa, pr.athlete, pr.honors
                FROM students st
                JOIN student_academic_programs sap
                    ON sap.student_id = st.student_id AND sap.status = 'graduated'
                JOIN academic_programs ap ON ap.program_code = sap.program_code
                JOIN academic_periods p ON p.term_code = sap.end_term
                JOIN student_term_records t
                    ON t.student_id = st.student_id AND t.term_code = sap.end_term
                JOIN student_profiles pr ON pr.student_id = st.student_id
                ORDER BY st.student_id""")
            self._grads = [
                {"sid": r[0], "term": r[1], "date": r[2], "major": r[3], "name": r[4],
                 "bachelor": r[5] == "Bachelor", "gpa": r[6], "athlete": r[7], "honors": r[8]}
                for r in rows]
        return self._grads

    def _data_end(self) -> str:
        return str(self.q("SELECT MAX(end_date) FROM academic_periods")[0][0])

    def check_outcomes(self) -> None:
        """The four outcome tables agree with the graduates and with each other."""
        q = self.q
        end = self._data_end()
        fd_bad = q("""SELECT COUNT(*) FROM first_destination fd
            LEFT JOIN students st ON st.student_id = fd.student_id
            LEFT JOIN student_academic_programs sap
                ON sap.student_id = fd.student_id AND sap.status = 'graduated'
            LEFT JOIN academic_programs ap ON ap.program_code = sap.program_code
            LEFT JOIN academic_periods p ON p.term_code = fd.graduation_term
            WHERE st.enrollment_status IS NOT 'graduated' OR ap.award_level IS NOT 'Bachelor'
               OR fd.graduation_term IS NOT st.exit_term
               OR fd.collected_date != date(p.end_date, '+183 days')
               OR fd.collected_date > ?""", (end,))[0][0]
        self.record("survey_only_eligible_graduates", fd_bad == 0,
                    f"{fd_bad} first-destination rows for someone who is not a bachelor's "
                    "graduate surveyed six months after graduating, by the data end")
        salary_bad = q("""SELECT COUNT(*) FROM first_destination WHERE
            (starting_salary IS NOT NULL AND (outcome != 'employed_full_time'
                OR starting_salary NOT BETWEEN 15000 AND 250000))
            OR ((employer_sector IS NOT NULL) != (outcome IN ('employed_full_time',
                'employed_part_time', 'military_service')))""")[0][0]
        self.record("salary_only_for_full_time", salary_bad == 0,
                    f"{salary_bad} salaries for someone not employed full time (or outside "
                    "$15,000 to $250,000), or sectors that do not match the outcome")
        ge_bad = q("""SELECT COUNT(*) FROM graduate_enrollment ge
            LEFT JOIN students st ON st.student_id = ge.student_id
            LEFT JOIN student_academic_programs sap
                ON sap.student_id = ge.student_id AND sap.status = 'graduated'
            LEFT JOIN academic_programs ap ON ap.program_code = sap.program_code
            LEFT JOIN academic_periods p ON p.term_code = st.exit_term
            WHERE st.enrollment_status IS NOT 'graduated' OR ap.award_level IS NOT 'Bachelor'
               OR ge.enrollment_begin_date <= p.end_date OR ge.enrollment_begin_date > ?""",
                   (end,))[0][0]
        fd_grad = q("""SELECT COUNT(*) FROM first_destination fd
            WHERE fd.outcome = 'graduate_school' AND NOT EXISTS (
                SELECT 1 FROM graduate_enrollment ge WHERE ge.student_id = fd.student_id
                  AND ge.enrollment_begin_date <= fd.collected_date)""")[0][0]
        self.record("graduate_school_consistent", ge_bad == 0 and fd_grad == 0,
                    f"{ge_bad} enrollments not after a bachelor's degree or after the data end, "
                    f"{fd_grad} survey answers of graduate school without a matching enrollment")
        med_bad = q("""SELECT COUNT(*) FROM medical_school_applications m
            LEFT JOIN students st ON st.student_id = m.student_id
            LEFT JOIN student_academic_programs sap
                ON sap.student_id = m.student_id AND sap.status = 'graduated'
            LEFT JOIN academic_programs ap ON ap.program_code = sap.program_code
            LEFT JOIN academic_periods p ON p.term_code = st.exit_term
            LEFT JOIN graduate_enrollment ge ON ge.student_id = m.student_id
            WHERE ap.award_level IS NOT 'Bachelor'
               OR m.entering_year <= CAST(substr(p.end_date, 1, 4) AS INTEGER)
               OR m.entering_year > CAST(substr(?, 1, 4) AS INTEGER)
               OR (COALESCE(ge.program_type, '') = 'medical') != (m.accepted = 1
                   AND m.entering_year || '-08-01' <= ?)""", (end, end))[0][0]
        med_orphans = q("""SELECT COUNT(*) FROM graduate_enrollment ge
            WHERE ge.program_type = 'medical' AND NOT EXISTS (
                SELECT 1 FROM medical_school_applications m
                WHERE m.student_id = ge.student_id AND m.accepted = 1
                  AND m.entering_year || '-08-01' = ge.enrollment_begin_date)""")[0][0]
        self.record("medical_school_consistent", med_bad == 0 and med_orphans == 0,
                    f"{med_bad} applications not from a bachelor's graduate, outside the "
                    f"decided cycles, or disagreeing with enrollment; {med_orphans} medical "
                    "enrollments without an acceptance")
        gift_bad = q("""SELECT COUNT(*) FROM alumni_gifts g
            LEFT JOIN students st ON st.student_id = g.student_id
            LEFT JOIN academic_periods p ON p.term_code = st.exit_term
            WHERE st.enrollment_status IS NOT 'graduated' OR g.gift_date <= p.end_date
               OR g.gift_date > ? OR g.amount <= 0
               OR g.fiscal_year != CAST(substr(g.gift_date, 1, 4) AS INTEGER)
                   + (CAST(substr(g.gift_date, 6, 2) AS INTEGER) >= 7)""", (end,))[0][0]
        self.record("gifts_from_alumni", gift_bad == 0,
                    f"{gift_bad} gifts not from a graduate after graduating, after the data "
                    "end, not positive, or in the wrong fiscal year")
        # Plausible rates (bands from NACE, Clearinghouse, AAMC and CASE
        # benchmarks; wider at reduced scale, where the counts are small).
        grads = self._graduates()
        eligible = [g for g in grads if g["bachelor"] and q(
            "SELECT date(?, '+183 days') <= ?", (g["date"], end))[0][0]]
        respondents = q("SELECT COUNT(*) FROM first_destination")[0][0]
        knowledge = respondents / len(eligible) if eligible else 0.0
        apps, accepted = q("SELECT COUNT(*), COALESCE(SUM(accepted), 0) FROM "
                           "medical_school_applications")[0]
        donors = q("SELECT COUNT(DISTINCT student_id) FROM alumni_gifts")[0][0]
        participation = donors / len(grads) if grads else 0.0
        acceptance = accepted / apps if apps else None
        wide = not self.full
        ok = ((0.50 if wide else 0.60) <= knowledge <= (0.80 if wide else 0.70)
              and (0.03 if wide else 0.05) <= participation <= (0.20 if wide else 0.12)
              and (acceptance is None or apps < 30
                   or (0.25 if wide else 0.40) <= acceptance <= (0.65 if wide else 0.50)))
        self.record("outcome_tables_plausible", ok,
                    f"survey knowledge rate {knowledge:.1%} of {len(eligible)} eligible "
                    f"graduates, medical school acceptance "
                    f"{'n/a' if acceptance is None else f'{acceptance:.1%}'} of {apps} "
                    f"applicants, alumni giving participation {participation:.1%} of "
                    f"{len(grads)} graduates",
                    {"knowledge_rate": round(knowledge, 3), "med_acceptance":
                     None if acceptance is None else round(acceptance, 3),
                     "participation": round(participation, 3)})

    @staticmethod
    def _band(gpa: float | None) -> str:
        g = gpa or 0.0
        return ("2.00-2.49" if g < 2.5 else "2.50-2.99" if g < 3.0
                else "3.00-3.49" if g < 3.5 else "3.50-4.00")

    def planted_outcomes(self) -> dict[str, Any]:
        """Planted graduate-outcome facts, with the definitions in VERIFY.md."""
        q = self.q
        end = self._data_end()
        grads = self._graduates()
        by_sid = {g["sid"]: g for g in grads}
        # Rows for someone who is not a graduate fail the integrity checks
        # above; the planted values read graduates only.
        fd = {r[0]: r for r in q("SELECT student_id, outcome, starting_salary "
                                 "FROM first_destination") if r[0] in by_sid}
        eligible = [g for g in grads if g["bachelor"] and q(
            "SELECT date(?, '+183 days') <= ?", (g["date"], end))[0][0]]
        out: dict[str, Any] = {}
        # Starting salary (full-time employed respondents who reported one).
        salaries: dict[str, list[int]] = defaultdict(list)
        all_salaries: list[int] = []
        bands: dict[str, list[int]] = defaultdict(list)
        for sid, (_s, _o, salary) in fd.items():
            if salary is None:
                continue
            g = by_sid[sid]
            salaries[g["major"]].append(salary)
            all_salaries.append(salary)
            bands[self._band(g["gpa"])].append(salary)
        floor = 30 if self.full else 3
        ranked = sorted(((median_of(v), m) for m, v in salaries.items() if len(v) >= floor),
                        key=lambda x: (-x[0], x[1]))
        names = {g["major"]: g["name"] for g in grads}
        out["starting_salary"] = {
            "eligible_graduates": len(eligible), "respondents": len(fd),
            "knowledge_rate_pct": pct(len(fd), len(eligible)),
            "salary_reporters": len(all_salaries),
            "median_all": median_of(all_salaries),
            "highest_major": ranked[0][1] if ranked else None,
            "highest_name": names.get(ranked[0][1]) if ranked else None,
            "highest_median": ranked[0][0] if ranked else None,
            "highest_reporters": len(salaries[ranked[0][1]]) if ranked else None,
            "next_major": ranked[1][1] if len(ranked) > 1 else None,
            "next_median": ranked[1][0] if len(ranked) > 1 else None,
            "lowest_major": ranked[-1][1] if ranked else None,
            "lowest_median": ranked[-1][0] if ranked else None}
        out["salary_by_gpa_band"] = {
            band: {"reporters": len(bands[band]),
                   "median": median_of(bands[band])}
            for band in ("2.00-2.49", "2.50-2.99", "3.00-3.49", "3.50-4.00")}
        employed = [s for s, r in fd.items() if r[1] in ("employed_full_time",
                                                         "employed_part_time")]
        nurs = [s for s in fd if by_sid[s]["major"] == "NURS"]
        nurs_emp = [s for s in employed if by_sid[s]["major"] == "NURS"]
        out["employment"] = {
            "respondents": len(fd), "employed": len(employed),
            "employment_rate_pct": pct(len(employed), len(fd)),
            "nursing_respondents": len(nurs), "nursing_employed": len(nurs_emp),
            "nursing_employment_rate_pct": pct(len(nurs_emp), len(nurs))}
        # Graduate or professional school within one year (Clearinghouse).
        begins = dict(q("SELECT student_id, enrollment_begin_date FROM graduate_enrollment"))
        tracked = [g for g in grads if g["bachelor"] and q(
            "SELECT date(?, '+1 year') <= ?", (g["date"], end))[0][0]]
        within = [g for g in tracked if g["sid"] in begins and q(
            "SELECT ? <= date(?, '+1 year')", (begins[g["sid"]], g["date"]))[0][0]]
        out["graduate_school"] = {
            "tracked_graduates": len(tracked), "enrolled_within_1yr": len(within),
            "rate_pct": pct(len(within), len(tracked))}
        # Medical school acceptance, of applicants.
        apps = [r for r in q("SELECT student_id, accepted FROM medical_school_applications")
                if r[0] in by_sid]

        def acc(rows: list[Any]) -> dict[str, Any]:
            n, a = len(rows), sum(r[1] for r in rows)
            return {"applicants": n, "accepted": a, "rate_pct": pct(a, n)}

        out["medical_school"] = {
            **acc(apps),
            "gpa_3_50_up": acc([r for r in apps if (by_sid[r[0]]["gpa"] or 0) >= 3.5]),
            "gpa_below_3_50": acc([r for r in apps if (by_sid[r[0]]["gpa"] or 0) < 3.5]),
            "biology": acc([r for r in apps if by_sid[r[0]]["major"] == "BIOL"])}
        # Alumni giving participation (gave at least once after graduating).
        donor_set = {r[0] for r in q("SELECT DISTINCT student_id FROM alumni_gifts")}
        gifts, dollars = q("SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM alumni_gifts")[0]
        per_major: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for g in grads:
            per_major[g["major"]][0] += 1
            per_major[g["major"]][1] += g["sid"] in donor_set
        give_floor = 100 if self.full else 10
        give_rank = sorted(((pct(d, n), m) for m, (n, d) in per_major.items()
                            if n >= give_floor), key=lambda x: (-x[0], x[1]))
        ath = [g for g in grads if g["athlete"]]
        non = [g for g in grads if not g["athlete"]]
        out["alumni_giving"] = {
            "alumni": len(grads), "donors": len(donor_set),
            "participation_pct": pct(len(donor_set), len(grads)),
            "gifts": gifts, "dollars": dollars,
            "highest_major": give_rank[0][1] if give_rank else None,
            "highest_name": names.get(give_rank[0][1]) if give_rank else None,
            "highest_alumni": per_major[give_rank[0][1]][0] if give_rank else None,
            "highest_participation_pct": give_rank[0][0] if give_rank else None,
            "next_major": give_rank[1][1] if len(give_rank) > 1 else None,
            "next_participation_pct": give_rank[1][0] if len(give_rank) > 1 else None,
            "athletes": len(ath),
            "athletes_participation_pct": pct(sum(g["sid"] in donor_set for g in ath), len(ath)),
            "non_athletes_participation_pct": pct(sum(g["sid"] in donor_set for g in non),
                                                  len(non))}
        self._directional_outcomes(out, salaries, bands, per_major)
        return out

    def _directional_outcomes(self, out: dict[str, Any], salaries: dict[str, list[int]],
                              bands: dict[str, list[int]],
                              per_major: dict[str, list[int]]) -> None:
        """Direction-only versions of the outcome patterns (used at any scale).
        A pattern over too few people at a small scale passes as too few."""
        high = [s for m in ("CSCI", "DATA", "SWDV", "ELEN", "MEEN", "CVEN", "CYBR")
                for s in salaries.get(m, [])]
        low = [s for m in ("EDEL", "EDEC", "BIBL", "THEO", "MINS", "YFMN", "MUSC", "WRSP",
                           "ARTS", "THEA") for s in salaries.get(m, [])]
        ok = bool(high) and bool(low) and median(high) > median(low) * 1.3
        self.record("pattern_salary_by_major", ok,
                    f"median starting salary, computing and engineering "
                    f"{median(high) if high else None} vs education, ministry and the arts "
                    f"{median(low) if low else None}")
        lo_band = bands.get("2.00-2.49", []) + bands.get("2.50-2.99", [])
        hi_band = bands.get("3.50-4.00", [])
        if min(len(lo_band), len(hi_band)) < 100:
            self.record("pattern_salary_rises_with_gpa", True,
                        f"too few salaries at this scale ({len(lo_band)} and {len(hi_band)})")
        else:
            self.record("pattern_salary_rises_with_gpa", median(hi_band) > median(lo_band),
                        f"median starting salary, final GPA 3.50 and up {median(hi_band)} vs "
                        f"under 3.00 {median(lo_band)}")
        med = out["medical_school"]
        hi, lo = med["gpa_3_50_up"], med["gpa_below_3_50"]
        if min(hi["applicants"], lo["applicants"]) < 20:
            self.record("pattern_med_acceptance_gpa", True,
                        f"too few applicants at this scale ({hi['applicants']} and "
                        f"{lo['applicants']})")
        else:
            self.record("pattern_med_acceptance_gpa", hi["rate_pct"] > lo["rate_pct"] + 10,
                        f"medical school acceptance, GPA 3.50 and up {hi['rate_pct']} % vs "
                        f"below {lo['rate_pct']} %")
        giving = out["alumni_giving"]
        mins = per_major.get("MINS", [0, 0])
        if mins[0] < 50:
            self.record("pattern_ministry_gives_most", True,
                        f"too few Christian Ministry alumni at this scale ({mins[0]})")
        else:
            self.record("pattern_ministry_gives_most",
                        pct(mins[1], mins[0]) > giving["participation_pct"] * 1.5,
                        f"Christian Ministry participation {pct(mins[1], mins[0])} % vs all "
                        f"alumni {giving['participation_pct']} %")
        if giving["athletes"] < 50:
            self.record("pattern_athletes_give_more", True,
                        f"too few athlete alumni at this scale ({giving['athletes']})")
        else:
            self.record("pattern_athletes_give_more",
                        giving["athletes_participation_pct"]
                        > giving["non_athletes_participation_pct"],
                        f"participation, athletes {giving['athletes_participation_pct']} % vs "
                        f"non-athletes {giving['non_athletes_participation_pct']} %")

    def check_planted_exact(self, values: dict[str, Any], digest: str, original: str) -> None:
        if not self.full:
            self.record("planted_exact", True, "reduced scale: exact planted values not compared "
                        "(direction checks above)")
            return
        text = VERIFY_MD.read_text(encoding="utf-8") if VERIFY_MD.exists() else ""
        m = re.search(r"<!-- planted-values -->\s*```json\n(.*?)```", text, re.S)
        if not m:
            self.record("planted_exact", False, "VERIFY.md has no planted-values JSON block")
            return
        expected = json.loads(m.group(1))
        diffs = []
        exp_hash = expected.pop("canonical_sha256", None)
        if exp_hash != digest:
            diffs.append(f"canonical_sha256: expected {exp_hash}, got {digest}")
        exp_original = expected.pop("original_tables_sha256", None)
        if exp_original != original:
            diffs.append(f"original_tables_sha256: expected {exp_original}, got {original}")
        for key, exp in expected.items():
            got = values.get(key)
            if got != exp:
                diffs.append(f"{key}: expected {exp}, got {got}")
        missing = sorted(set(values) - set(expected))
        if missing:
            diffs.append(f"not documented in VERIFY.md: {missing}")
        self.record("planted_exact", not diffs, "; ".join(diffs) or
                    f"all {len(expected)} planted facts and the canonical hash match VERIFY.md")

    def run(self) -> dict[str, Any]:
        self.check_schema()
        self.check_privacy()
        self.check_grades()
        self.check_gpa_recompute()
        self.check_schedule()
        self.check_prerequisites()
        self.check_graduation()
        self.check_distributions()
        self.check_enrollment_history()
        self.check_outcomes()
        values = self.planted()
        values.update(self.planted_outcomes())
        digest = canonical_hash(self.con)
        original = canonical_hash(self.con, ORIGINAL_TABLES)
        self.check_planted_exact(values, digest, original)
        counts = {t: self.q(f'SELECT COUNT(*) FROM "{t}"')[0][0] for t in EXPECTED_COLUMNS}
        return {"meta": self.meta, "counts": counts, "checks": self.results, "planted": values,
                "canonical_sha256": digest, "original_tables_sha256": original,
                "ok": all(r["ok"] for r in self.results)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check a Demonstration University database.")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = ap.parse_args(argv)
    if not args.db.exists():
        print(f"no database at {args.db}; run `make school-data` first", file=sys.stderr)
        return 2
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    con.execute("PRAGMA foreign_keys = ON")
    report = Checker(con).run()
    con.close()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for r in report["checks"]:
            print(f"{'PASS' if r['ok'] else 'FAIL'}  {r['name']:34s} {r['detail']}")
        print(f"scale {report['meta'].get('scale')}, seed {report['meta'].get('seed')}, "
              f"canonical sha256 {report['canonical_sha256']}")
        print(f"original 24 tables sha256 {report['original_tables_sha256']}")
        print("planted values:")
        print(json.dumps(report["planted"], indent=2))
    failed = [r["name"] for r in report["checks"] if not r["ok"]]
    if failed:
        print(f"FAILED: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
