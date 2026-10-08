"""Finance: the university's budget (data/school/budget.py, Explore's three
budget analyses, the University budget dashboard, the Finance overview's
University budget section) and the student-account measures over the
billing tables (Explore, the Student finances dashboard, the Student
accounts section).

One small school (scale 0.02) is generated once for the module.
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cabinet import dashboards, departments
from cabinet.api import create_app
from cabinet.counseling import MINIMUM_CELL_SIZE
from cabinet.explore import finance as fin
from cabinet.explore import general
from cabinet.explore.answer import write_answer
from cabinet.explore.catalog import Catalog, catalog_for, connect_readonly
from cabinet.explore.execute import execute
from cabinet.explore.planner import rule_plan
from conftest import make_authenticated_client

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHOOL = REPO_ROOT / "data" / "school"
GENERATE = SCHOOL / "generate.py"


@pytest.fixture(scope="module")
def school_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("school-finance") / "school.db"
    proc = subprocess.run(
        [sys.executable, str(GENERATE), "--scale", "0.02", "--out", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return out


@pytest.fixture(scope="module")
def no_billing_db(school_db: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The same school without the billing and budget tables (an older
    database)."""
    out = tmp_path_factory.mktemp("school-old") / "school.db"
    shutil.copyfile(school_db, out)
    con = sqlite3.connect(out)
    for table in (*general.BILLING_TABLES, *fin.BUDGET_TABLES):
        con.execute(f'DROP TABLE "{table}"')
    con.commit()
    con.close()
    return out


@pytest.fixture
def con(school_db: Path) -> Any:
    connection = connect_readonly(school_db)
    yield connection
    connection.close()


@pytest.fixture
def catalog(con: sqlite3.Connection, school_db: Path) -> Catalog:
    return catalog_for(con, school_db)


def q(con: sqlite3.Connection, sql: str, *args: Any) -> list[Any]:
    return con.execute(sql, args).fetchall()


# --- the data -----------------------------------------------------------------


def test_net_tuition_is_gross_less_aid_and_agrees_with_revenue(
    con: sqlite3.Connection,
) -> None:
    rows = q(con, "SELECT * FROM tuition_revenue ORDER BY fiscal_year")
    assert [r[0] for r in rows] == list(fin.fiscal_years(con))
    assert len(rows) == 6
    for fy, _terms, _hours, gross, aid, net, rate in rows:
        assert net == gross - aid
        assert rate == round(aid / gross, 4)
        revenue = dict(
            q(
                con,
                "SELECT source, actual_amount FROM revenue_lines"
                " WHERE fiscal_year = ? AND source IN"
                " ('gross_tuition', 'institutional_aid')",
                fy,
            )
        )
        assert revenue == {"gross_tuition": gross, "institutional_aid": aid}


def test_tuition_and_housing_tie_to_billing_and_enrollment(
    con: sqlite3.Connection,
) -> None:
    for fy, ay in q(con, "SELECT fiscal_year, academic_year FROM fiscal_years"):
        billed = dict(
            q(
                con,
                "SELECT c.category, SUM(c.amount) FROM student_charges c"
                " JOIN academic_periods p USING (term_code)"
                " WHERE p.academic_year = ? GROUP BY c.category",
                ay,
            )
        )
        gross, terms = q(
            con,
            "SELECT gross_tuition, student_terms FROM tuition_revenue"
            " WHERE fiscal_year = ?",
            fy,
        )[0]
        # Billed fall and spring tuition plus summer hours (never less).
        assert gross >= round(billed["tuition"]) - 1
        assert gross - billed["tuition"] < 0.2 * billed["tuition"]
        enrolled = q(
            con,
            "SELECT COUNT(*) FROM student_term_enrollment e"
            " JOIN academic_periods p USING (term_code)"
            " WHERE e.status = 'enrolled' AND p.academic_year = ?",
            ay,
        )[0][0]
        assert terms == enrolled
        housing = q(
            con,
            "SELECT actual_amount FROM revenue_lines WHERE fiscal_year = ?"
            " AND source = 'housing'",
            fy,
        )[0][0]
        assert housing == round(billed["housing"])


def test_budget_lines_add_up_and_amounts_are_whole_dollars(
    con: sqlite3.Connection,
) -> None:
    bad = q(
        con,
        "SELECT COUNT(*) FROM budget_lines WHERE budget_amount < 0"
        " OR actual_amount < 0 OR budget_amount % 1000 != 0"
        " OR typeof(actual_amount) != 'integer'",
    )[0][0]
    assert bad == 0
    for fy in fin.fiscal_years(con):
        rows, _ = fin.budget_vs_actual(con, {"fiscal_year": fy})
        total = rows[-1]
        assert total["group"] == "All expenses"
        assert total["budget"] == sum(r["budget"] for r in rows[:-1])
        assert total["actual"] == sum(r["actual"] for r in rows[:-1])
        assert (total["budget"], total["actual"]) == tuple(
            q(
                con,
                "SELECT SUM(budget_amount), SUM(actual_amount) FROM budget_lines"
                " WHERE fiscal_year = ?",
                fy,
            )[0]
        )


def test_planted_stories(con: sqlite3.Connection) -> None:
    rates = [r[0] for r in q(con, "SELECT discount_rate FROM tuition_revenue")]
    assert rates[-1] > rates[0]
    athletics = {
        fy: a > b
        for fy, b, a in q(
            con,
            "SELECT b.fiscal_year, SUM(budget_amount), SUM(actual_amount)"
            " FROM budget_lines b JOIN cost_centers c USING (cost_center_id)"
            " WHERE c.division = 'Athletics' GROUP BY 1",
        )
    }
    assert athletics["FY2025"] and athletics["FY2026"]
    over, _ = fin.budget_vs_actual(con, {"fiscal_year": "FY2026", "over_budget": "yes"})
    assert over[0]["group"] == "Athletics"


def test_budget_rows_are_deterministic(con: sqlite3.Connection) -> None:
    sys.path.insert(0, str(SCHOOL))
    import budget  # type: ignore[import-not-found]

    first = budget.budget_rows(con, 20261005)
    assert first == budget.budget_rows(con, 20261005)
    stored = q(con, "SELECT * FROM budget_lines ORDER BY 1, 2, 3, 4")
    assert sorted(first["budget_lines"]) == [tuple(r) for r in stored]


# --- student-account measures --------------------------------------------------


def _run(con: sqlite3.Connection, catalog: Catalog, **p: Any) -> list[dict[str, Any]]:
    return general.run(con, p, catalog.vocab)[0]


def test_past_due_measures_agree(con: sqlite3.Connection, catalog: Catalog) -> None:
    (balance,) = _run(con, catalog, measure="past_due_balance")
    (students,) = _run(con, catalog, measure="past_due_students")
    (late,) = _run(con, catalog, measure="past_due_90_students")
    assert balance["students"] == students["value"] >= late["value"] > 0
    assert balance["value"] % 100 == 0
    (average,) = _run(con, catalog, measure="avg_balance_owed")
    assert average["value"] % 10 == 0
    assert abs(average["numerator"] - balance["value"]) <= 100
    # The raw rows: unpaid, past the due date, not on a payment plan.
    asof = q(con, "SELECT MAX(end_date) FROM academic_periods")[0][0]
    raw = q(
        con,
        """SELECT COUNT(DISTINCT c.student_id) FROM (
               SELECT student_id, term_code, SUM(amount) AS billed,
                      MIN(due_date) AS due FROM student_charges GROUP BY 1, 2) c
           LEFT JOIN (SELECT student_id, term_code, SUM(amount) AS paid
                      FROM student_payments WHERE paid_on <= ? GROUP BY 1, 2) p
               USING (student_id, term_code)
           LEFT JOIN payment_plans pl USING (student_id, term_code)
           WHERE c.billed - COALESCE(p.paid, 0) > 0.005 AND c.due < ?
             AND pl.student_id IS NULL""",
        asof,
        asof,
    )[0][0]
    assert students["value"] == raw


def test_student_measures_withhold_small_groups(
    con: sqlite3.Connection, catalog: Catalog
) -> None:
    for measure in ("past_due_students", "on_time_payment_rate", "past_due_balance"):
        rows, notes, _ = general.run(
            con, {"measure": measure, "group_by": "major"}, catalog.vocab
        )
        shown = [r for r in rows if r.get("major") != "All"]
        for row in shown:
            n = row["value"] if measure == "past_due_students" else row["students"]
            assert n >= MINIMUM_CELL_SIZE, (measure, row)
        # At this scale most majors are small: some are withheld, and said so.
        assert len(shown) < len(catalog.vocab.majors)
        assert any("withheld" in note for note in notes)


def test_rates_by_group(con: sqlite3.Connection, catalog: Catalog) -> None:
    # Over every term (one term at this small scale is noisy).
    rows = _run(
        con,
        catalog,
        measure="on_time_payment_rate",
        group_by="pell",
        term_from="202110",
        term_to="202620",
    )
    by = {r["group"]: r for r in rows}
    for name in ("Pell recipients", "Students without Pell"):
        assert 70 < by[name]["value"] <= 100
    (share,) = _run(con, catalog, measure="payment_plan_share")
    assert 10 < share["value"] < 35
    (collected,) = _run(con, catalog, measure="collection_rate")
    assert 90 < collected["value"] <= 100


def test_aging_groups_never_add_a_misleading_total(
    con: sqlite3.Connection, catalog: Catalog
) -> None:
    rows, notes, _ = general.run(
        con, {"measure": "past_due_students", "group_by": "aging"}, catalog.vocab
    )
    assert rows and all(not r.get("_total") for r in rows)
    assert any("counted in each group" in n for n in notes)


def test_without_billing_or_budget_the_measures_say_so(no_billing_db: Path) -> None:
    con = connect_readonly(no_billing_db)
    try:
        v = catalog_for(con, no_billing_db).vocab
        assert v.fiscal_years == ()
        with pytest.raises(general.GeneralError, match="not in this school database"):
            general.run(con, {"measure": "past_due_balance"}, v)
        with pytest.raises(fin.BudgetMissing):
            fin.budget_vs_actual(con, {})
        # Every other measure still answers.
        assert general.run(con, {"measure": "headcount"}, v)[0]
    finally:
        con.close()
    body = departments.overview("finance", no_billing_db)
    assert body["sections"] == []


# --- Explore: routing and answers -----------------------------------------------

QUESTIONS: tuple[tuple[str, str, dict[str, Any]], ...] = (
    ("What is our budget vs actual this year?", "budget_vs_actual", {}),
    (
        "Which departments are over budget?",
        "budget_vs_actual",
        {"by": "department", "over_budget": "yes"},
    ),
    ("What is our tuition discount rate trend?", "tuition_discount", {}),
    (
        "How much net tuition revenue did we make last year?",
        "tuition_discount",
        {"fiscal_year": "FY2025"},
    ),
    ("How much is past due?", "measure_by_group", {"measure": "past_due_balance"}),
    (
        "How many students are more than 90 days past due?",
        "measure_by_group",
        {"measure": "past_due_90_students"},
    ),
    (
        "What is the on-time payment rate by college?",
        "measure_by_group",
        {"measure": "on_time_payment_rate", "group_by": "college"},
    ),
    (
        "How many students are on payment plans?",
        "measure_by_group",
        {"measure": "payment_plan_share"},
    ),
    ("What is our revenue by source in FY2024?", "revenue_by_source", {}),
    (
        "Past-due balance by aging",
        "measure_by_group",
        {"measure": "past_due_balance", "group_by": "aging"},
    ),
)


@pytest.mark.parametrize(("question", "analysis", "params"), QUESTIONS)
def test_rule_planner_routes_finance_questions(
    catalog: Catalog,
    con: sqlite3.Connection,
    question: str,
    analysis: str,
    params: dict[str, Any],
) -> None:
    steps = rule_plan(question, catalog)
    assert steps is not None and len(steps) == 1
    assert steps[0].analysis_id == analysis
    for key, value in params.items():
        assert steps[0].params.get(key) == value
    results = execute(steps, con, catalog, "finance")
    assert results[0].error is None and results[0].rows
    answer, _, fallback = write_answer(results, None)
    text = " ".join(s.text for s in answer)
    assert fallback is None
    assert "in its table" not in text  # every template sentence passed its check
    assert "$" in text or "%" in text or any(ch.isdigit() for ch in text)


def test_old_holds_question_still_goes_to_holds(catalog: Catalog) -> None:
    steps = rule_plan("How much do students owe on financial holds?", catalog)
    assert steps is not None and steps[0].analysis_id == "holds_by_office"


def test_budget_steps_are_refused_in_the_executor_for_other_roles(
    catalog: Catalog, con: sqlite3.Connection
) -> None:
    steps = rule_plan("What is our budget vs actual this year?", catalog)
    assert steps is not None
    (refused,) = execute(steps, con, catalog, "staff")
    assert refused.error and not refused.rows


# --- the API: roles, the overview and the dashboards -----------------------------


@pytest.fixture
def app(school_db: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("CABINET_SCHOOL_DB", str(school_db))
    monkeypatch.setenv("CABINET_PROVIDER", "fake")
    monkeypatch.setenv("CABINET_EXPLORE_PLANNER", "rules-only")
    dashboards.clear_cache()
    return create_app()


def _client(app: FastAPI, role: str) -> TestClient:
    return make_authenticated_client(app, role=role)


def _refusals(app: FastAPI) -> list[dict[str, Any]]:
    return list(app.state.auth.audit_for(1).events("data.refused"))


@pytest.mark.parametrize("role", ["finance", "executive", "admin"])
def test_budget_roles_read_the_budget_in_explore(app: FastAPI, role: str) -> None:
    body = (
        _client(app, role)
        .post("/explore", json={"question": "Which departments are over budget?"})
        .json()
    )
    assert body["refused"] is False and body["steps"][0]["analysis_id"] == (
        "budget_vs_actual"
    )


@pytest.mark.parametrize("role", ["staff", "reviewer", "registrar", "studentlife"])
def test_other_roles_are_refused_the_budget(app: FastAPI, role: str) -> None:
    client = _client(app, role)
    response = client.post(
        "/explore", json={"question": "What is our tuition discount rate trend?"}
    )
    assert response.status_code == 403
    assert response.json()["refused"] is True
    assert any(
        e["payload"].get("category") == "institutional_budget" for e in _refusals(app)
    )
    # Student-account totals are aggregate student data: any Explore role.
    ok = client.post("/explore", json={"question": "How much is past due?"})
    assert ok.status_code == 200 and ok.json()["answer"]


def test_finance_overview_has_both_sections(app: FastAPI) -> None:
    body = _client(app, "finance").get("/departments/overview").json()
    titles = [s["title"] for s in body["sections"]]
    assert titles == ["University budget", "Student accounts"]
    accounts = body["sections"][1]
    aging = next(t for t in accounts["tables"] if t["key"] == "accounts_aging")
    shown = [r["students"] for r in aging["rows"]]
    assert all(
        s in ("None", departments.WITHHELD) or int(s.replace(",", "")) >= 10
        for s in shown
    )
    # Other departments get no sections, and registrar may not read Finance.
    registrar = _client(app, "registrar")
    assert "sections" not in registrar.get("/departments/overview").json()
    assert registrar.get("/departments/overview?department=finance").status_code == 403


def test_budget_dashboard_is_finance_and_executive_only(app: FastAPI) -> None:
    for role in ("finance", "executive"):
        client = _client(app, role)
        boards = {
            d["id"]: d for d in client.get("/data/dashboards").json()["dashboards"]
        }
        assert boards["budget"]["students"] is False
        assert len(boards["budget"]["charts"]) == len(dashboards.BUDGET_CHARTS)
        assert any(c["id"] == "accounts_on_time" for c in boards["finances"]["charts"])
        for chart in dashboards.BUDGET_CHARTS:
            data = client.get(f"/data/series?chart={chart.id}").json()
            assert [x["key"] for x in data["x"]] == list(
                f"FY{y}" for y in range(2021, 2027)
            )
            assert data["series"]
        assert (
            client.get("/data/series?chart=budget_spending&pell=pell").status_code
            == 422
        )
    for role in ("aid", "staff", "registrar"):
        client = _client(app, role)
        ids = [d["id"] for d in client.get("/data/dashboards").json()["dashboards"]]
        assert "budget" not in ids
        assert client.get("/data/series?chart=budget_spending").status_code == 403
    assert any(e["payload"].get("chart") == "budget_spending" for e in _refusals(app))


def test_account_chart_rounds_and_withholds(app: FastAPI) -> None:
    client = _client(app, "finance")
    data = client.get("/data/series?chart=accounts_past_due").json()
    values = [p["value"] for p in data["series"][0]["points"] if p["status"] == "ok"]
    assert values and all(v % 100 == 0 for v in values)
    split = client.get("/data/series?chart=accounts_on_time&compare=college").json()
    for series in split["series"]:
        for point in series["points"]:
            if point["status"] == "ok":
                assert point["students"] >= MINIMUM_CELL_SIZE
