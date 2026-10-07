"""Student support programs: transparent eligibility rules, reach, and impact.

Support is OFFERED, never imposed: each program has an eligibility rule a
person can read ("bottom 30 % of cumulative GPA entering the term"), not a
predictive risk score. This module

- applies each rule live, as reviewed SQL over the school database's core
  tables (``eligible_students``), for the eligible counts and the outreach
  list;
- reads the program's own follow-up records (``support_program_terms``,
  written by ``data/school/interventions.py``) for who took the offer and
  what followed;
- compares outcomes three ways, each labelled with its method: everyone
  eligible before the program against everyone eligible after it; those who
  took part against those who did not (the naive comparison); and the same
  within bands of similar GPA (the fairer comparison).

Everything returned by ``reach`` and ``impact`` is aggregate: no student id,
and any group under ``MINIMUM_CELL_SIZE`` students is withheld (with its
partner group, so a total cannot reveal it). The per-student list
(``eligible_students``) is only for the outreach routes, which admit the
roles allowed per-student rows; nothing here is ever sent to a model.

The rules are duplicated in ``data/school/interventions.py`` (stdlib, no
``cabinet`` import); a test checks both give the same students every term.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from typing import Any

from cabinet.counseling import MINIMUM_CELL_SIZE

PROGRAM_IDS: tuple[str, ...] = ("ai_tutoring", "theology_bridge", "fit_advising")
THEOLOGY_MAJORS: tuple[str, ...] = ("BIBL", "CHST", "MINS", "THEO", "YFMN")

# The office whose role may also read a program's outreach list (beside the
# executive and admin). Only the aid role exists as an office role today.
OFFICE_ROLE: dict[str, str] = {"theology_bridge": "aid"}

CAVEAT = (
    "This comparison isn't a randomized trial. Students chose whether to take "
    "part, so differences we cannot see, such as motivation, may remain even "
    "after matching on GPA."
)
SOURCE_NOTE = (
    "Outcomes come from the program's follow-up records; the registrar's "
    "enrollment and GPA figures are not changed by them."
)

# Built into the demonstration data (data/school/interventions.py), shown so
# a reader can see which method recovers the truth. Fictional data only.
PLANTED: dict[str, str] = {
    "ai_tutoring": (
        "Built into the demonstration data: tutoring raises a participant's term "
        "GPA by 0.15, and 15 % of participants who would have left come back. "
        "Students with higher GPAs were more likely to say yes."
    ),
    "theology_bridge": (
        "Built into the demonstration data: 35 % of participants who would have "
        "left come back, and 40 % of returning participants who would have had a "
        "financial hold do not."
    ),
    "fit_advising": (
        "Built into the demonstration data: 25 % of participants who would have "
        "left come back, and 12 % more of those who return move to a new major."
    ),
}


@dataclass(frozen=True)
class Outcome:
    key: str
    label: str
    kind: str  # pct or gpa
    better: str  # higher or lower
    among: str  # who the outcome is measured over, in words


OUTCOMES: dict[str, Outcome] = {
    "returned_next_term": Outcome(
        "returned_next_term",
        "Came back the next term",
        "pct",
        "higher",
        "eligible students who did not graduate that term",
    ),
    "term_gpa": Outcome(
        "term_gpa", "Term GPA", "gpa", "higher", "eligible students with a term GPA"
    ),
    "financial_hold_next_term": Outcome(
        "financial_hold_next_term",
        "Financial hold the next term",
        "pct",
        "lower",
        "eligible students who came back",
    ),
    "changed_major_next_term": Outcome(
        "changed_major_next_term",
        "Moved to a new major the next term",
        "pct",
        "higher",
        "eligible students who came back",
    ),
}

GPA_BAND = {"ai_tutoring": 0.25, "theology_bridge": 0.5, "fit_advising": 0.5}


class ProgramsMissing(Exception):
    """The school database has no support-program tables (regenerate it)."""


def _next_regular(col: str) -> str:
    return (
        f"CASE substr({col}, 5, 2) WHEN '10' THEN substr({col}, 1, 4) || '20' "
        f"ELSE (CAST(substr({col}, 1, 4) AS INTEGER) + 1) || '10' END"
    )


# --- the programs ------------------------------------------------------------------


def programs(con: sqlite3.Connection) -> list[dict[str, Any]]:
    try:
        rows = con.execute(
            "SELECT program_id, name, eligibility_rule, offer, owner_office, "
            "start_term, primary_outcome, secondary_outcome FROM support_programs"
        ).fetchall()
    except sqlite3.OperationalError:
        raise ProgramsMissing() from None
    terms = term_names(con)
    out = []
    for pid, name, rule, offer, office, start, primary, secondary in rows:
        out.append(
            {
                "id": pid,
                "name": name,
                "rule": rule,
                "offer": offer,
                "owner_office": office,
                "start_term": start,
                "start_term_name": terms.get(start, start),
                "outcomes": [primary, secondary],
            }
        )
    out.sort(key=lambda p: PROGRAM_IDS.index(p["id"]) if p["id"] in PROGRAM_IDS else 9)
    return out


def program(con: sqlite3.Connection, program_id: str) -> dict[str, Any]:
    for p in programs(con):
        if p["id"] == program_id:
            return p
    raise KeyError(program_id)


def term_names(con: sqlite3.Connection) -> dict[str, str]:
    return {
        str(a): str(b)
        for a, b in con.execute(
            "SELECT term_code, name FROM academic_periods ORDER BY sequence"
        )
    }


def current_term(con: sqlite3.Connection) -> str:
    """The latest fall or spring term."""
    return str(
        con.execute(
            "SELECT MAX(term_code) FROM academic_periods WHERE season != 'Summer'"
        ).fetchone()[0]
    )


# --- the eligibility rules, as reviewed SQL over the core tables ------------------

_PRIOR_GPA = """(SELECT r.cumulative_gpa FROM student_term_records r
    WHERE r.student_id = t.student_id AND r.term_code < :term
      AND r.cumulative_gpa IS NOT NULL
    ORDER BY r.term_code DESC LIMIT 1)"""

_TUTORING_SQL = f"""
WITH prior AS (
    SELECT t.student_id, {_PRIOR_GPA} AS gpa, ap.major_code AS major
    FROM student_term_records t
    JOIN academic_programs ap ON ap.program_code = t.program_code
    WHERE t.term_code = :term
),
ranked AS (
    SELECT student_id, gpa, major,
        ROW_NUMBER() OVER (ORDER BY gpa, student_id) AS rn, COUNT(*) OVER () AS n
    FROM prior WHERE gpa IS NOT NULL
)
SELECT student_id, gpa, major, NULL AS amount FROM ranked
WHERE gpa <= (SELECT gpa FROM ranked WHERE rn = (3 * n + 9) / 10)
ORDER BY student_id"""

_BRIDGE_SQL = f"""
SELECT t.student_id, {_PRIOR_GPA} AS gpa, ap.major_code AS major,
    (SELECT ROUND(SUM(h.amount), 2) FROM person_holds h
     WHERE h.student_id = t.student_id AND h.term_code = :term
       AND h.category = 'financial') AS amount
FROM student_term_records t
JOIN academic_programs ap ON ap.program_code = t.program_code
WHERE t.term_code = :term
  AND ap.major_code IN ({", ".join(f"'{m}'" for m in THEOLOGY_MAJORS)})
  AND EXISTS (SELECT 1 FROM person_holds h WHERE h.student_id = t.student_id
              AND h.term_code = :term AND h.category = 'financial')
ORDER BY t.student_id"""

_SIGNAL = """(
    COALESCE((SELECT SUM(d.n) FROM dfw d WHERE d.student_id = c.student_id
              AND d.term_code <= {upto}), 0) >= 2
    OR (SELECT COUNT(*) FROM student_academic_programs p
        WHERE p.student_id = c.student_id AND p.status = 'changed'
          AND p.end_term <= {upto}) >= 2)"""

_FIT_SQL = f"""
WITH cand AS (
    SELECT t.student_id, t.cumulative_gpa AS gpa, ap.major_code AS major,
        s.entry_term, {_next_regular("s.entry_term")} AS second_term
    FROM student_term_records t
    JOIN students s ON s.student_id = t.student_id
    JOIN academic_programs ap ON ap.program_code = t.program_code
    WHERE t.term_code = :term AND s.entry_type = 'first_time'
      AND s.entry_term IN (SELECT term_code FROM academic_periods
                           WHERE season != 'Summer')
),
c AS (SELECT * FROM cand WHERE :term IN (entry_term, second_term)),
dfw AS (
    SELECT r.student_id, r.term_code, COUNT(*) AS n
    FROM c
    JOIN section_registrations r ON r.student_id = c.student_id
        AND r.term_code IN (c.entry_term, c.second_term)
    JOIN final_grades g ON g.registration_id = r.registration_id
    JOIN sections s ON s.section_id = r.section_id
    JOIN student_term_records tr ON tr.student_id = r.student_id
        AND tr.term_code = r.term_code
    JOIN program_requirements pr ON pr.program_code = tr.program_code
        AND pr.course_id = s.course_id AND pr.requirement_type IN ('major', 'support')
    WHERE g.grade IN ('D+', 'D', 'F', 'W')
    GROUP BY r.student_id, r.term_code
)
SELECT c.student_id, c.gpa, c.major, NULL AS amount FROM c
WHERE {_SIGNAL.format(upto=":term")}
  AND NOT (:term = c.second_term AND {_SIGNAL.format(upto="c.entry_term")})
ORDER BY c.student_id"""

RULE_SQL = {
    "ai_tutoring": _TUTORING_SQL,
    "theology_bridge": _BRIDGE_SQL,
    "fit_advising": _FIT_SQL,
}

# What each outreach row carries: only the facts the rule itself used.
FACT_LABELS: dict[str, tuple[tuple[str, str], ...]] = {
    "ai_tutoring": (("gpa", "Cumulative GPA entering the term"), ("major", "Major")),
    "theology_bridge": (
        ("major", "Major"),
        ("amount", "Financial holds placed this term ($)"),
    ),
    "fit_advising": (("major", "Major"), ("gpa", "Cumulative GPA")),
}


def eligible_students(
    con: sqlite3.Connection, program_id: str, term: str
) -> list[tuple[str, dict[str, Any]]]:
    """(student id, facts) for every student the rule names in the term, in
    student id order. Per-student: only the outreach routes call this."""
    sql = RULE_SQL[program_id]
    keys = [k for k, _ in FACT_LABELS[program_id]]
    out = []
    for sid, gpa, major, amount in con.execute(sql, {"term": term}):
        values = {"gpa": gpa, "major": major, "amount": amount}
        out.append((str(sid), {k: values[k] for k in keys}))
    return out


def eligible_count(con: sqlite3.Connection, program_id: str, term: str) -> int:
    return len(eligible_students(con, program_id, term))


# --- aggregate reach -----------------------------------------------------------------


def _shown(n: int) -> int | None:
    return n if n >= MINIMUM_CELL_SIZE else None


def reach(con: sqlite3.Connection, program_id: str) -> dict[str, Any]:
    """Eligible, offered and accepted by term since the program started, and
    the live eligible count in the current term (from the rule)."""
    p = program(con, program_id)
    names = term_names(con)
    rows = con.execute(
        "SELECT term_code, COUNT(*), SUM(offered), SUM(accepted) "
        "FROM support_program_terms WHERE program_id = ? AND period = 'after' "
        "GROUP BY term_code ORDER BY term_code",
        (program_id,),
    ).fetchall()
    terms = []
    total_n = total_acc = 0
    for term, n, offered, accepted in rows:
        accepted = int(accepted or 0)
        declined = int(n) - accepted
        hide = accepted < MINIMUM_CELL_SIZE or declined < MINIMUM_CELL_SIZE
        total_n += int(n)
        total_acc += accepted
        terms.append(
            {
                "term": term,
                "term_name": names.get(term, term),
                "eligible": _shown(int(n)),
                "offered": _shown(int(offered or 0)),
                "accepted": None if hide else accepted,
                "take_up_pct": None if hide else round(100.0 * accepted / n, 1),
            }
        )
    current = current_term(con)
    now = eligible_count(con, program_id, current)
    total_hide = (
        total_acc < MINIMUM_CELL_SIZE or total_n - total_acc < MINIMUM_CELL_SIZE
    )
    return {
        "program": p,
        "current_term": current,
        "current_term_name": names.get(current, current),
        "eligible_now": _shown(now),
        "terms": terms,
        "total": {
            "eligible": _shown(total_n),
            "accepted": None if total_hide else total_acc,
            "take_up_pct": None
            if total_hide or not total_n
            else round(100.0 * total_acc / total_n, 1),
        },
    }


# --- impact ------------------------------------------------------------------------


@dataclass
class _Group:
    label: str
    values: list[float]

    @property
    def n(self) -> int:
        return len(self.values)

    @property
    def mean(self) -> float:
        return math.fsum(self.values) / len(self.values)

    @property
    def var(self) -> float:
        if len(self.values) < 2:
            return 0.0
        m = self.mean
        return math.fsum((v - m) ** 2 for v in self.values) / (len(self.values) - 1)


def _fmt(value: float, kind: str) -> float:
    return round(100.0 * value, 1) if kind == "pct" else round(value, 2)


def _band(gpa: float | None, width: float) -> str:
    if gpa is None:
        return "none"
    return str(int(gpa / width + 1e-9))


def _comparison(
    method: str,
    label: str,
    sentence: str,
    a: _Group,
    b: _Group,
    kind: str,
    *,
    diff: float | None = None,
    se: float | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "method": method,
        "label": label,
        "sentence": sentence,
        "a": {"label": a.label, "n": _shown(a.n), "value": None},
        "b": {"label": b.label, "n": _shown(b.n), "value": None},
        "difference": None,
        "low": None,
        "high": None,
        "withheld": a.n < MINIMUM_CELL_SIZE or b.n < MINIMUM_CELL_SIZE,
        # The groups that are themselves under the minimum (a partner may be
        # withheld only to protect them).
        "small": [side for side, g in (("a", a), ("b", b)) if g.n < MINIMUM_CELL_SIZE],
    }
    if extra:
        out.update(extra)
    if out["withheld"]:
        # A partner of a small group is withheld too: the total is shown
        # elsewhere, so its count would reveal the small one.
        out["a"]["n"] = out["b"]["n"] = None
        return out
    if diff is None:
        diff = a.mean - b.mean
        se = math.sqrt(a.var / a.n + b.var / b.n)
    assert se is not None
    out["a"]["value"] = _fmt(a.mean, kind)
    out["b"]["value"] = _fmt(b.mean, kind)
    out["difference"] = _fmt(diff, kind)
    out["low"] = _fmt(diff - 1.96 * se, kind)
    out["high"] = _fmt(diff + 1.96 * se, kind)
    return out


def _matched(
    rows: list[tuple[float | None, int, float]], width: float, kind: str
) -> dict[str, Any]:
    """Participants against non-participants within GPA bands, weighted by
    the participants in each band (bands without at least two of each are
    left out)."""
    bands: dict[str, tuple[list[float], list[float]]] = {}
    for gpa, accepted, value in rows:
        part, rest = bands.setdefault(_band(gpa, width), ([], []))
        (part if accepted else rest).append(value)
    used = {k: v for k, v in bands.items() if len(v[0]) >= 2 and len(v[1]) >= 2}
    a = _Group("Took part", [x for v in used.values() for x in v[0]])
    b = _Group("Similar students who did not", [x for v in used.values() for x in v[1]])
    total_part = sum(len(v[0]) for v in bands.values())
    extra = {"coverage_pct": round(100.0 * a.n / total_part, 1) if total_part else None}
    if not used or a.n < MINIMUM_CELL_SIZE or b.n < MINIMUM_CELL_SIZE:
        return {"a": a, "b": b, "diff": None, "se": None, "extra": extra}
    diff = 0.0
    var = 0.0
    for part, rest in used.values():
        w = len(part) / a.n
        ga, gb = _Group("", part), _Group("", rest)
        diff += w * (ga.mean - gb.mean)
        var += w * w * (ga.var / ga.n + gb.var / gb.n)
    # The comparison group's mean, reweighted to the participants' GPA mix.
    reweighted = math.fsum(
        (len(part) / a.n) * _Group("", rest).mean for part, rest in used.values()
    )
    extra["b_value_reweighted"] = _fmt(reweighted, kind)
    return {"a": a, "b": b, "diff": diff, "se": math.sqrt(var), "extra": extra}


def _verdict(outcome: Outcome, fair: dict[str, Any]) -> str:
    """One plain sentence from the fairer comparison's 95 % range."""
    if fair["withheld"] or fair["difference"] is None:
        return "Too few students to compare fairly yet."
    low, high = fair["low"], fair["high"]
    helped = low > 0 if outcome.better == "higher" else high < 0
    hurt = high < 0 if outcome.better == "higher" else low > 0
    if helped:
        return (
            "Likely helping: participants did better than similar students who "
            "did not take part, by more than chance alone would explain."
        )
    if hurt:
        return (
            "Worth a closer look: participants did worse than similar students "
            "who did not take part."
        )
    return (
        "Not clear yet: the difference from similar students is within what "
        "chance alone could produce."
    )


def impact(con: sqlite3.Connection, program_id: str) -> dict[str, Any]:
    """Before against after, and participants against non-participants
    (naive and within GPA bands), for the program's two outcomes."""
    p = program(con, program_id)
    names = term_names(con)
    width = GPA_BAND.get(program_id, 0.5)
    start_name = p["start_term_name"]
    outcomes_out = []
    for key in p["outcomes"]:
        outcome = OUTCOMES[key]
        rows = con.execute(
            f"SELECT period, accepted, gpa_at_eligibility, {key}, term_code "
            "FROM support_program_terms WHERE program_id = ? AND "
            f"{key} IS NOT NULL",
            (program_id,),
        ).fetchall()
        before = _Group(f"Eligible before {start_name}", [])
        after = _Group(f"Eligible since {start_name}", [])
        took = _Group("Took part", [])
        declined = _Group("Did not take part", [])
        matched_rows: list[tuple[float | None, int, float]] = []
        after_terms: set[str] = set()
        before_terms: set[str] = set()
        for period, accepted, gpa, value, term in rows:
            v = float(value)
            if period == "before":
                before.values.append(v)
                before_terms.add(term)
                continue
            after.values.append(v)
            after_terms.add(term)
            (took if accepted else declined).values.append(v)
            matched_rows.append((gpa, int(accepted), v))

        def span(terms: set[str]) -> str:
            if not terms:
                return "no terms"
            first, last = min(terms), max(terms)
            return f"{names.get(first, first)} to {names.get(last, last)}"

        comparisons = [
            _comparison(
                "before_after",
                "Before and after the program started",
                f"Everyone the rule named before the program ({span(before_terms)}) "
                f"against everyone it named since ({span(after_terms)}), whether or "
                "not they took part.",
                after,
                before,
                outcome.kind,
            ),
            _comparison(
                "naive",
                "Took part or not (naive)",
                "Eligible students who took part against those who did not, since "
                "the program started. Naive: the two groups may have differed "
                "before the program.",
                took,
                declined,
                outcome.kind,
            ),
        ]
        m = _matched(matched_rows, width, outcome.kind)
        comparisons.append(
            _comparison(
                "matched",
                "Took part or not, similar GPA (fairer)",
                f"Participants against non-participants with a similar GPA (within "
                f"{width:g} of a grade point), weighted to the participants' GPA mix.",
                m["a"],
                m["b"],
                outcome.kind,
                diff=m["diff"],
                se=m["se"],
                extra=m["extra"],
            )
        )
        if m["diff"] is None and not comparisons[-1]["withheld"]:
            comparisons[-1]["withheld"] = True
            comparisons[-1]["a"]["n"] = comparisons[-1]["b"]["n"] = None
        if not comparisons[-1]["withheld"]:
            comparisons[-1]["b"]["value"] = m["extra"].get("b_value_reweighted")
        outcomes_out.append(
            {
                "key": key,
                "label": outcome.label,
                "kind": outcome.kind,
                "better": outcome.better,
                "among": outcome.among,
                "comparisons": comparisons,
                "verdict": _verdict(outcome, comparisons[-1]),
            }
        )
    return {
        "program": p,
        "outcomes": outcomes_out,
        "caveat": CAVEAT,
        "source": SOURCE_NOTE,
        "planted": PLANTED.get(program_id),
    }


__all__ = [
    "CAVEAT",
    "FACT_LABELS",
    "OFFICE_ROLE",
    "OUTCOMES",
    "PROGRAM_IDS",
    "ProgramsMissing",
    "current_term",
    "eligible_count",
    "eligible_students",
    "impact",
    "program",
    "programs",
    "reach",
]
