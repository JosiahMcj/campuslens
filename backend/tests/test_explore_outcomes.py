"""Graduate outcomes in Explore: the outcome measures of the general analysis
(salary, employment, graduate and medical school, alumni giving), the final
GPA band grouping, their privacy rules, and the planner routes for the
owner's questions.

Against the generator at --scale 0.01 in a tmp directory, like the other
explore tests: the reduced scale has many small groups, which is what the
suppression tests need. Every expected figure is recomputed here from the
raw outcome tables with the definitions in data/school/VERIFY.md.
"""

from __future__ import annotations

import math
import re
import shutil
import sqlite3
import statistics
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from cabinet.counseling import SUPPRESSED_DISPLAY
from cabinet.explore import general
from cabinet.explore.answer import check_sentence, template_answer
from cabinet.explore.catalog import Catalog, catalog_for, connect_readonly
from cabinet.explore.compact import compact_catalog
from cabinet.explore.execute import StepResult, execute
from cabinet.explore.planner import Step, rule_plan
from cabinet.explore.privacy import mask_names, refusal_for

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATE = REPO_ROOT / "data" / "school" / "generate.py"
STUDENT_ID = re.compile(r"\bS-\d+")
MIN = 10
OUTCOMES = tuple(m.id for m in general.OUTCOME_MEASURES)

# The owner's questions (2026-10-07) and the plan each must get from the rules.
OWNER_QUESTIONS: list[tuple[str, dict[str, Any]]] = [
    (
        "What do various majors make after graduation?",
        {"measure": "median_salary", "group_by": "major"},
    ),
    (
        "Do grades matter for earning potential?",
        {"measure": "median_salary", "group_by": "gpa_band"},
    ),
    (
        "What % of biology students got into med school?",
        {"measure": "med_acceptance_rate", "major": "BIOL"},
    ),
    ("What % of graduates went to grad school?", {"measure": "grad_school_rate"}),
    ("What % of alumni have given back?", {"measure": "giving_rate"}),
    (
        "Which majors give back the most?",
        {"measure": "giving_rate", "group_by": "major", "order": "highest_first"},
    ),
    ("Do athletes give back more?", {"measure": "giving_rate", "group_by": "athlete"}),
    (
        "How many nursing grads are employed?",
        {"measure": "employment_rate", "major": "NURS"},
    ),
]


@pytest.fixture(scope="module")
def school_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("school-outcomes") / "school.db"
    proc = subprocess.run(
        [sys.executable, str(GENERATE), "--scale", "0.01", "--out", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return out


@pytest.fixture(autouse=True)
def _school_env(school_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CABINET_SCHOOL_DB", str(school_db))
    monkeypatch.setenv("CABINET_PROVIDER", "replay")
    monkeypatch.delenv("CABINET_RECORD", raising=False)
    monkeypatch.delenv("CABINET_EXPLORE_PLANNER", raising=False)


@pytest.fixture
def con(school_db: Path) -> Iterator[sqlite3.Connection]:
    connection = connect_readonly(school_db)
    yield connection
    connection.close()


@pytest.fixture
def catalog(con: sqlite3.Connection, school_db: Path) -> Catalog:
    return catalog_for(con, school_db)


def _step(con: sqlite3.Connection, catalog: Catalog, **params: Any) -> StepResult:
    result = execute([Step(general.ANALYSIS_ID, params)], con, catalog, "executive")[0]
    assert result.error is None, (params, result.error)
    return result


def _is_total(row: dict[str, Any]) -> bool:
    return "All graduates" in (
        row.get("group"),
        row.get("major_name"),
        row.get("college_name"),
    )


def _total(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return next(r for r in rows if _is_total(r))


def _graduates(con: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Every graduate with the attributes the definitions use."""
    rows = con.execute(
        """SELECT st.student_id, ap.major_code, ap.college_code, ap.award_level,
                  p.end_date, t.cumulative_gpa, pr.athlete
           FROM students st
           JOIN student_academic_programs sap
               ON sap.student_id = st.student_id AND sap.status = 'graduated'
           JOIN academic_programs ap ON ap.program_code = sap.program_code
           JOIN academic_periods p ON p.term_code = sap.end_term
           JOIN student_term_records t
               ON t.student_id = st.student_id AND t.term_code = sap.end_term
           JOIN student_profiles pr ON pr.student_id = st.student_id"""
    ).fetchall()
    return {
        r[0]: {
            "major": r[1],
            "college": r[2],
            "bachelor": r[3] == "Bachelor",
            "date": r[4],
            "gpa": r[5],
            "athlete": r[6],
        }
        for r in rows
    }


def _half_up(value: float, step: int) -> int:
    return int(math.floor(value / step + 0.5) * step)


# --- the measures ---------------------------------------------------------------


def test_outcome_measures_match_the_definitions(
    con: sqlite3.Connection, catalog: Catalog
) -> None:
    """Each measure, overall, equals a recount from the raw rows."""
    grads = _graduates(con)
    end = con.execute("SELECT MAX(end_date) FROM academic_periods").fetchone()[0]

    def before(sql_date: str, shift: str) -> bool:
        return bool(
            con.execute("SELECT date(?, ?) <= ?", (sql_date, shift, end)).fetchone()[0]
        )

    surveyed = [
        s for s, g in grads.items() if g["bachelor"] and before(g["date"], "+183 days")
    ]
    fd = dict(
        con.execute("SELECT student_id, outcome FROM first_destination").fetchall()
    )
    knowledge = _total(
        _step(con, catalog, measure="knowledge_rate", group_by="college").rows
    )
    assert (knowledge["students"], knowledge["numerator"]) == (len(surveyed), len(fd))

    employed = [s for s, o in fd.items() if o.startswith("employed")]
    employment = _step(con, catalog, measure="employment_rate").rows[0]
    assert (employment["students"], employment["numerator"]) == (len(fd), len(employed))

    salaries = [
        r[0]
        for r in con.execute(
            "SELECT starting_salary FROM first_destination WHERE starting_salary "
            "IS NOT NULL"
        )
    ]
    assert len(salaries) >= MIN
    salary = _step(con, catalog, measure="median_salary").rows[0]
    assert salary == {
        "students": len(salaries),
        "value": _half_up(statistics.median(salaries), 500),
    }
    assert salary["value"] % 500 == 0

    tracked = [
        s for s, g in grads.items() if g["bachelor"] and before(g["date"], "+1 year")
    ]
    begins = dict(
        con.execute(
            "SELECT student_id, enrollment_begin_date FROM graduate_enrollment"
        ).fetchall()
    )
    within = [
        s
        for s in tracked
        if s in begins
        and con.execute(
            "SELECT ? <= date(?, '+1 year')", (begins[s], grads[s]["date"])
        ).fetchone()[0]
    ]
    grad_school = _step(con, catalog, measure="grad_school_rate").rows[0]
    assert (grad_school["students"], grad_school["numerator"]) == (
        len(tracked),
        len(within),
    )

    donors = {r[0] for r in con.execute("SELECT student_id FROM alumni_gifts")}
    giving = _step(con, catalog, measure="giving_rate").rows[0]
    assert (giving["students"], giving["numerator"]) == (len(grads), len(donors))
    gifts, dollars = con.execute(
        "SELECT COUNT(*), SUM(amount) FROM alumni_gifts"
    ).fetchone()
    assert _step(con, catalog, measure="avg_gift").rows[0] == {
        "students": len(donors),
        "value": _half_up(dollars / gifts, 5),
    }
    assert _step(con, catalog, measure="total_giving").rows[0] == {
        "students": len(donors),
        "value": _half_up(dollars, 100),
    }


def test_medical_school_rate_is_of_applicants(
    con: sqlite3.Connection, catalog: Catalog
) -> None:
    applicants, accepted = con.execute(
        "SELECT COUNT(*), SUM(accepted) FROM medical_school_applications"
    ).fetchone()
    result = _step(con, catalog, measure="med_acceptance_rate")
    if applicants < MIN:
        assert result.rows == []  # the one figure is withheld
    else:
        row = result.rows[0]
        assert (row["students"], row["numerator"]) == (applicants, accepted)
    assert any("over graduates who applied" in note for note in result.notes)
    assert [c.label for c in result.columns if c.key == "students"] == [
        "Medical school applicants"
    ]


def test_every_outcome_measure_by_every_grouping_is_aggregate_and_safe(
    con: sqlite3.Connection, catalog: Catalog
) -> None:
    """No student id anywhere, and every shown group has at least 10 people."""
    for measure_id in OUTCOMES:
        measure = general.MEASURES[measure_id]
        for grouping in general.allowed_groupings(measure):
            result = _step(con, catalog, measure=measure_id, group_by=grouping)
            text = repr(result.rows) + repr(result.notes)
            assert not STUDENT_ID.search(text), (measure_id, grouping)
            for row in result.rows:
                if row["value"] != SUPPRESSED_DISPLAY:
                    assert row["students"] >= MIN, (measure_id, grouping, row)


def test_load_and_housing_do_not_apply_to_graduates() -> None:
    allowed = set(general.allowed_groupings(general.MEASURES["median_salary"]))
    assert {"major", "college", "gpa_band", "athlete", "term"} <= allowed
    assert not allowed & {"load", "housing", "class_level", "hold", "modality"}
    with pytest.raises(general.GeneralError):
        general.check_request("median_salary", ["housing"], {})
    # The final GPA band belongs to graduates only.
    with pytest.raises(general.GeneralError):
        general.check_request("headcount", ["gpa_band"], {})


def test_salary_by_gpa_band_reads_in_band_order(
    con: sqlite3.Connection, catalog: Catalog
) -> None:
    rows = _step(con, catalog, measure="median_salary", group_by="gpa_band").rows
    bands = [r["group"] for r in rows if not _is_total(r)]
    assert bands == sorted(bands)
    assert all(b.startswith("Graduates with a final GPA of ") for b in bands)
    assert _total(rows)["group"] == "All graduates"


# --- privacy ---------------------------------------------------------------------


def test_small_groups_are_withheld_with_their_complements(
    con: sqlite3.Connection, catalog: Catalog
) -> None:
    """Giving by major: a major under 10 alumni is withheld, and within its
    college (whose total is published) the withheld majors add up to 10 or
    more, so no small major can be recovered by subtraction."""
    grads = _graduates(con)
    sizes: dict[str, int] = {}
    for g in grads.values():
        sizes[g["major"]] = sizes.get(g["major"], 0) + 1
    assert any(n < MIN for n in sizes.values()), "the scale should have small majors"
    rows = _step(con, catalog, measure="giving_rate", group_by="major").rows
    shown = {
        r["major"]
        for r in rows
        if not _is_total(r) and r["value"] != SUPPRESSED_DISPLAY
    }
    for major, n in sizes.items():
        if n < MIN:
            assert major not in shown, major
    colleges = {g["major"]: g["college"] for g in grads.values()}
    for college in set(colleges.values()):
        hidden = [
            sizes[m] for m, c in colleges.items() if c == college and m not in shown
        ]
        assert not hidden or sum(hidden) >= MIN, (college, hidden)


def test_salary_cells_count_salary_reporters(
    con: sqlite3.Connection, catalog: Catalog
) -> None:
    """A median is withheld when fewer than 10 salaries stand behind it, even
    in a group with many graduates."""
    rows = _step(con, catalog, measure="median_salary", group_by="major").rows
    counts = dict(
        con.execute(
            """SELECT ap.major_code, COUNT(*) FROM first_destination fd
               JOIN student_academic_programs sap
                   ON sap.student_id = fd.student_id AND sap.status = 'graduated'
               JOIN academic_programs ap ON ap.program_code = sap.program_code
               WHERE fd.starting_salary IS NOT NULL GROUP BY 1"""
        ).fetchall()
    )
    for row in rows:
        if _is_total(row) or row["value"] == SUPPRESSED_DISPLAY:
            continue
        assert row["students"] == counts[row["major"]] >= MIN


def test_outcome_measures_say_so_without_the_tables(
    school_db: Path, tmp_path: Path, catalog: Catalog
) -> None:
    """A school database generated before outcomes were added: the outcome
    measures explain how to add them; every other measure still answers."""
    old = tmp_path / "old.db"
    shutil.copy(school_db, old)
    writable = sqlite3.connect(old)
    for table in general.OUTCOME_TABLES:
        writable.execute(f"DROP TABLE {table}")
    writable.commit()
    writable.close()
    con = connect_readonly(old)
    try:
        result = execute(
            [Step(general.ANALYSIS_ID, {"measure": "median_salary"})],
            con,
            catalog,
            "executive",
        )[0]
        assert result.error is not None and "make school-data" in result.error
        assert _step(con, catalog, measure="graduates").rows
    finally:
        con.close()


# --- answers ---------------------------------------------------------------------


def test_answers_name_who_is_counted(con: sqlite3.Connection, catalog: Catalog) -> None:
    salary = execute(
        [
            Step(
                general.ANALYSIS_ID,
                {"measure": "median_salary", "group_by": "gpa_band"},
            )
        ],
        con,
        catalog,
        "executive",
    )
    text = " ".join(s.text for s in template_answer(salary))
    assert "$" in text
    assert not re.search(r"\$[\d,]+\.\d", text)  # whole dollars, no cents
    assert "first-destination survey" in text
    assert any("knowledge rate" in note for note in salary[0].notes)

    giving = execute(
        [Step(general.ANALYSIS_ID, {"measure": "giving_rate"})],
        con,
        catalog,
        "executive",
    )
    sentence = template_answer(giving)[0].text
    assert sentence.startswith("Graduates have an alumni giving participation rate of")
    assert "alumni)" in sentence

    def first(measure: str) -> str:
        steps = execute(
            [Step(general.ANALYSIS_ID, {"measure": measure})], con, catalog, "executive"
        )
        return template_answer(steps)[0].text

    assert re.fullmatch(
        r"Graduates gave a total of \$[\d,]+00 after graduating \([\d,]+ donors\)\.",
        first("total_giving"),
    )
    assert re.fullmatch(
        r"The average gift from graduates is \$[\d,]+ \([\d,]+ donors\)\.",
        first("avg_gift"),
    )


def test_definition_sentences_pass_the_numeral_check(
    con: sqlite3.Connection, catalog: Catalog
) -> None:
    """A definition sentence is checked like any sentence: a number word in it
    ("six months") would be rejected and the sentence dropped."""
    for measure_id in OUTCOMES:
        short = general.MEASURES[measure_id].short
        if not short:
            continue
        steps = execute(
            [Step(general.ANALYSIS_ID, {"measure": measure_id})],
            con,
            catalog,
            "executive",
        )
        check_sentence(short, steps)


# --- planning --------------------------------------------------------------------


@pytest.mark.parametrize(("question", "expected"), OWNER_QUESTIONS)
def test_rules_plan_the_owners_outcome_questions(
    catalog: Catalog, question: str, expected: dict[str, Any]
) -> None:
    steps = rule_plan(question, catalog)
    assert steps is not None and len(steps) == 1, question
    assert steps[0].analysis_id == general.ANALYSIS_ID
    params = steps[0].params
    for key, value in expected.items():
        assert params.get(key) == value, (question, params)
    for key in ("group_by", "major"):
        if key not in expected:
            assert key not in params, (question, params)


@pytest.mark.parametrize(
    "question",
    [
        "which companies hire the most of our graduates",
        "what is the average student loan debt of our graduates",
    ],
)
def test_rules_do_not_stretch_to_records_we_lack(
    catalog: Catalog, question: str
) -> None:
    assert rule_plan(question, catalog) is None


@pytest.mark.parametrize(
    ("question", "measure"),
    [
        ("graduation rate by race", "grad_rate_6yr"),
        ("what is the credit completion rate", "credit_completion_rate"),
        ("do athletes have lower gpas than everyone else", "avg_gpa"),
    ],
)
def test_outcome_words_leave_older_questions_alone(
    catalog: Catalog, question: str, measure: str
) -> None:
    steps = rule_plan(question, catalog)
    assert steps is not None and steps[0].params.get("measure") == measure


def test_compact_catalog_teaches_the_outcome_measures(catalog: Catalog) -> None:
    text = compact_catalog(catalog)
    assert len(text) <= 12_000, len(text)
    for measure_id in OUTCOMES:
        assert f"- {measure_id}:" in text
    assert "- gpa_band: graduates' final GPA:" in text
    assert "med school = medical school" in text


@pytest.mark.parametrize(
    "question",
    [
        "What Do Various Majors Make After Graduation?",
        "Do Grades Matter For Earning Potential?",
        "What % of Biology students got into Med School?",
        "Median Starting Salary by Final GPA band",
        "Alumni Giving Participation by College",
        "Knowledge Rate of the First-Destination Survey",
        "MD or DO acceptance for Biomedical Sciences graduates",
    ],
)
def test_outcome_words_are_not_masked_as_names(catalog: Catalog, question: str) -> None:
    masked, hidden = mask_names(question, catalog.title_names)
    assert hidden == [] and masked == question
    assert refusal_for(question, catalog.title_names) is None


def test_a_person_asked_about_outcomes_is_still_masked(catalog: Catalog) -> None:
    for question in (
        "Did Kim Lee donate?",
        "What did Ravi Patel make after graduating?",
    ):
        _masked, hidden = mask_names(question, catalog.title_names)
        assert hidden, question
