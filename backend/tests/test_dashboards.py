"""The Data page (cabinet.dashboards): time series through the governed
general analysis.

Against the generator at --scale 0.01, whose many small groups exercise the
withholding: a withheld point is null with status "withheld", never a zero,
and no visible point covers fewer than 10 students.
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cabinet import dashboards as d
from cabinet.api import create_app
from cabinet.data_roles import DATA_ROLES, ROLE_DASHBOARDS, dashboards_for
from cabinet.explore import general
from cabinet.explore.catalog import Vocab, catalog_for, connect_readonly
from conftest import make_authenticated_client

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATE = REPO_ROOT / "data" / "school" / "generate.py"
STUDENT_ID = re.compile(r"\bS-\d+")


@pytest.fixture(scope="module")
def school_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("school-data-page") / "school.db"
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
    d.clear_cache()


@pytest.fixture
def con(school_db: Path) -> Iterator[sqlite3.Connection]:
    connection = connect_readonly(school_db)
    yield connection
    connection.close()


@pytest.fixture
def vocab(con: sqlite3.Connection, school_db: Path) -> Vocab:
    return catalog_for(con, school_db).vocab


def _client(role: str) -> TestClient:
    return make_authenticated_client(create_app(), role=role)


def _points(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [p for s in result["series"] for p in s["points"]]


# --- roles -----------------------------------------------------------------------


def test_the_role_table_gives_the_president_everything_and_admin_nothing() -> None:
    assert set(dashboards_for("executive")) == set(d.DASHBOARDS)
    assert dashboards_for("aid") == ("finances",)
    assert dashboards_for("staff") == ("students",)
    assert dashboards_for("reviewer") == ("students",)
    assert dashboards_for("admin") == ()
    assert dashboards_for("someone-new") == ()
    assert "admin" not in DATA_ROLES
    for boards in ROLE_DASHBOARDS.values():
        assert set(boards) <= set(d.DASHBOARDS)
    for chart in d.CHARTS:
        assert chart.dashboard in d.DASHBOARDS


@pytest.mark.parametrize(
    ("role", "boards"),
    [
        ("executive", ["students", "finances", "budget", "campus"]),
        ("aid", ["finances"]),
        ("staff", ["students"]),
        ("reviewer", ["students"]),
    ],
)
def test_the_catalog_lists_only_the_roles_dashboards(
    role: str, boards: list[str]
) -> None:
    body = _client(role).get("/data/dashboards").json()
    assert [b["id"] for b in body["dashboards"]] == boards
    assert body["years"][0] == "2020-2021" and len(body["years"]) == 6
    filters = {f["key"]: f for f in body["filters"]}
    # Pell status is a financial attribute: the executive and aid only.
    pell = role in ("executive", "aid")
    expected = [k for k in d.FILTER_KEYS if pell or k != "pell"]
    assert list(filters) == expected
    if pell:
        assert {"value": "pell", "label": "Pell recipients"} in filters["pell"][
            "options"
        ]
    # Summer's "not recorded" is never offered as a choice.
    assert all(o["value"] != "not_recorded" for o in filters["housing"]["options"])
    assert [c["key"] for c in body["compare"]] == [
        k for k in d.COMPARE_KEYS if pell or k != "pell"
    ]
    assert body["minimum_cell_size"] == 10


def test_admin_has_no_data_page() -> None:
    client = _client("admin")
    assert client.get("/data/dashboards").status_code == 403
    assert client.get("/data/series?chart=headcount").status_code == 403


@pytest.mark.parametrize(
    ("role", "chart", "status"),
    [
        ("aid", "financial_hold_rate", 200),
        ("aid", "headcount", 403),
        ("aid", "dfw", 403),
        ("staff", "headcount", 200),
        ("staff", "financial_balance_total", 403),
        ("reviewer", "on_campus", 403),
        ("executive", "on_campus", 200),
        ("executive", "financial_balance_median", 200),
    ],
)
def test_each_chart_is_limited_to_its_dashboards_roles(
    role: str, chart: str, status: int
) -> None:
    response = _client(role).get(f"/data/series?chart={chart}")
    assert response.status_code == status, response.text


# --- shapes ------------------------------------------------------------------------


def test_headcount_is_one_point_per_fall_and_spring_term() -> None:
    body = _client("executive").get("/data/series?chart=headcount").json()
    assert [x["label"] for x in body["x"]][:3] == [
        "Fall 2020",
        "Spring 2021",
        "Fall 2021",
    ]
    assert len(body["x"]) == 12  # six years, no summers
    assert body["x"][0]["year"] == "2020-2021"
    (series,) = body["series"]
    assert series["label"] == "All students"
    assert [p["x"] for p in series["points"]] == [x["key"] for x in body["x"]]
    assert all(p["status"] == "ok" and p["value"] >= 10 for p in series["points"])
    assert body["kind"] == "count" and body["form"] == "line"


def test_cohort_charts_use_the_classes_each_measure_follows() -> None:
    client = _client("executive")
    labels = {
        chart: [
            x["label"] for x in client.get(f"/data/series?chart={chart}").json()["x"]
        ]
        for chart in ("retention", "grad4", "grad6", "new_students", "dropout")
    }
    assert labels["retention"] == [f"Fall {y}" for y in range(2020, 2025)]
    assert labels["grad4"] == ["Fall 2020", "Fall 2021", "Fall 2022"]
    assert labels["grad6"] == ["Fall 2020"]
    assert len(labels["new_students"]) == 6
    assert labels["dropout"] == [f"Fall {y}" for y in range(2020, 2024)]


def test_new_students_split_first_time_and_transfer() -> None:
    body = _client("executive").get("/data/series?chart=new_students").json()
    # At this small scale each fall brings fewer than 10 transfers, so both
    # groups are withheld (the first-time count would reveal the transfers)
    # and the chart says so by name.
    shown = [s["label"] for s in body["series"]]
    said = " ".join(body["notes"])
    for label in ("First-time students", "Transfer students"):
        assert label in shown or label in said
    assert body["split"] == {"key": "admit_type", "label": "Entry type"}


def test_compare_by_splits_rates_and_adds_everyone() -> None:
    body = (
        _client("executive").get("/data/series?chart=on_campus&compare=gender").json()
    )
    labels = [s["label"] for s in body["series"]]
    assert labels == ["Women", "Men", "All students"]
    # Colours follow the group, not its position: Men keep slot 1.
    assert [s["slot"] for s in body["series"]] == [0, 1, None]
    pct = [p for p in _points(body) if p["status"] == "ok"]
    assert pct and all(
        p["value"] == round(100 * p["numerator"] / p["denominator"], 1) for p in pct
    )


def test_counts_split_without_a_total_series() -> None:
    body = _client("executive").get("/data/series?chart=headcount&compare=pell").json()
    assert [s["label"] for s in body["series"]] == [
        "Pell recipients",
        "Students without Pell",
    ]


def test_a_fixed_split_ignores_compare_and_a_filter_collapses_it() -> None:
    client = _client("executive")
    body = client.get("/data/series?chart=headcount_by_college&compare=gender").json()
    assert body["split"]["key"] == "college"
    body = client.get("/data/series?chart=headcount_by_college&college=COE").json()
    assert body["split"] is None
    assert [s["label"] for s in body["series"]] == ["College of Education"]
    # Narrowed by another attribute, the chart is not also split by college.
    body = client.get("/data/series?chart=headcount_by_college&gender=female").json()
    assert body["split"] is None
    assert [s["label"] for s in body["series"]] == ["Women"]
    assert any("narrows or splits, never both" in n for n in body["notes"])


def test_filters_narrow_every_point() -> None:
    client = _client("executive")
    whole = client.get("/data/series?chart=headcount").json()
    part = client.get("/data/series?chart=headcount&first_generation=first_generation")
    narrowed = part.json()
    for a, b in zip(
        whole["series"][0]["points"], narrowed["series"][0]["points"], strict=True
    ):
        if b["status"] == "ok":
            assert b["value"] < a["value"]


def test_finance_charts_read_financial_holds(
    vocab: Vocab, con: sqlite3.Connection
) -> None:
    client = _client("aid")
    count = client.get("/data/series?chart=financial_hold_students").json()
    total = client.get("/data/series?chart=financial_balance_total").json()
    median = client.get("/data/series?chart=financial_balance_median").json()
    term = count["x"][0]["key"]
    expected = con.execute(
        "SELECT COUNT(DISTINCT h.student_id), SUM(h.amount) FROM person_holds h "
        "JOIN student_term_records t ON t.student_id = h.student_id "
        "AND t.term_code = h.term_code "
        "WHERE h.category = 'financial' AND h.term_code = ?",
        (term,),
    ).fetchone()
    first = count["series"][0]["points"][0]
    if first["status"] == "ok":
        assert first["value"] == expected[0]
        assert total["series"][0]["points"][0]["value"] == pytest.approx(expected[1])
    for p in _points(median):
        if p["status"] == "ok":
            # Never one student's exact balance: rounded to $10.
            assert p["value"] > 0 and p["value"] % 10 == 0
    assert total["kind"] == "dollars"


def test_a_share_is_not_shown_for_its_own_attribute() -> None:
    client = _client("executive")
    body = client.get("/data/series?chart=pell_share&pell=pell").json()
    assert body["not_applicable"] == {"key": "pell", "label": "Pell grant"}
    body = client.get("/data/series?chart=on_campus&compare=housing").json()
    assert body["not_applicable"]["key"] == "housing"


def test_a_filter_the_chart_cannot_take_is_said_not_ignored() -> None:
    body = _client("executive").get("/data/series?chart=retention&class_level=Junior")
    assert body.status_code == 200
    assert body.json()["not_applicable"] == {
        "key": "class_level",
        "label": "Class level",
    }


@pytest.mark.parametrize(
    "query",
    [
        "chart=headcount&color=red",
        "chart=headcount&gender=other",
        "chart=headcount&compare=major",
        "chart=headcount&compare=race_ethnicity",
        "chart=headcount&college=NOPE",
    ],
)
def test_unknown_filters_and_values_are_refused(query: str) -> None:
    assert _client("executive").get(f"/data/series?{query}").status_code == 422


def test_an_unknown_chart_is_not_found() -> None:
    assert _client("executive").get("/data/series?chart=nope").status_code == 404


# --- privacy -----------------------------------------------------------------------


SMALL_QUERIES = [
    "chart=headcount&race_ethnicity=pacific_islander",
    "chart=headcount_by_college",
    "chart=retention&compare=age_band",
    "chart=financial_hold_students&compare=residency",
    "chart=dfw&major=MEEN",
    "chart=headcount&compare=college",
    "chart=stop_out&compare=honors",
]


@pytest.mark.parametrize("query", SMALL_QUERIES)
def test_small_groups_are_gaps_never_zero(query: str) -> None:
    body = _client("executive").get(f"/data/series?{query}").json()
    for point in _points(body):
        if point["status"] == "withheld":
            assert point["value"] is None
            assert set(point) == {"x", "value", "status"}
        elif point["status"] == "ok":
            assert point["students"] >= 10


def test_at_least_one_small_query_withholds_a_point() -> None:
    client = _client("executive")
    withheld = [
        p
        for q in SMALL_QUERIES
        for p in _points(client.get(f"/data/series?{q}").json())
        if p["status"] == "withheld"
    ]
    assert withheld


def test_withheld_parts_cannot_be_recovered_from_the_total(
    con: sqlite3.Connection, vocab: Vocab
) -> None:
    """Counts by college per term: whatever is withheld in a term adds up to
    at least 10 students, so subtracting the visible colleges from the
    published term total never reveals a small college."""
    split = d.compute(con, d.CHARTS_BY_ID["headcount"], {}, "college", vocab)
    whole = d.compute(con, d.CHARTS_BY_ID["headcount"], {}, None, vocab)
    totals = {p["x"]: p for p in whole["series"][0]["points"]}
    for i, x in enumerate(split["x"]):
        total = totals[x["key"]]
        if total["status"] != "ok":
            continue
        visible = sum(
            s["points"][i]["value"]
            for s in split["series"]
            if s["points"][i]["status"] == "ok"
        )
        gap = total["value"] - visible
        assert gap == 0 or gap >= 10, (x, gap)


@pytest.mark.parametrize(
    ("chart", "filters", "compare"),
    [
        ("headcount", {}, "college"),
        ("headcount", {"race_ethnicity": "asian"}, "class_level"),
        ("retention", {"college": "COE"}, "gender"),
        ("dfw", {"pell": "pell"}, "load"),
        ("financial_hold_rate", {"gender": "female"}, "residency"),
        ("dropout", {}, "first_generation"),
        ("new_students", {"honors": "honors"}, None),
    ],
)
def test_the_grouped_reading_matches_the_general_analysis(
    con: sqlite3.Connection,
    vocab: Vocab,
    chart: str,
    filters: dict[str, str],
    compare: str | None,
) -> None:
    """The Data page reads sums per group instead of per student: the cells
    and the withheld set are exactly the general analysis's."""
    c = d.CHARTS_BY_ID[chart]
    split = c.split or compare
    keys = [c.x, *([split] if split else [])]
    req = general.Request(c.measure, tuple(keys), filters, None, None)
    fast = d._SeriesRunner(con, req, vocab, c.x)
    slow = general._Runner(con, req, vocab)
    if c.all_entrants:
        fast.params["admit_default"] = slow.params["admit_default"] = 0
    fast_cells = fast.cells(keys, filters)
    slow_cells = slow.cells(keys, filters)
    assert fast_cells.keys() == slow_cells.keys()
    for key, cell in slow_cells.items():
        other = fast_cells[key]
        assert other.students == cell.students
        assert other.num == pytest.approx(cell.num)
        assert other.den == pytest.approx(cell.den)
    assert general._hidden(fast, keys, filters, fast_cells, vocab) == general._hidden(
        slow, keys, filters, slow_cells, vocab
    )


def test_no_student_rows_or_ids_and_the_read_is_logged() -> None:
    app = create_app()
    client = make_authenticated_client(app, role="executive")
    queries = [f"chart={c.id}" for c in d.CHARTS] + SMALL_QUERIES
    for q in queries:
        body = client.get(f"/data/series?{q}").text
        assert not STUDENT_ID.search(body), q
    events = app.state.auth.audit_for(1).events()
    granted = [
        e
        for e in events
        if e["type"] == "data.granted" and e["payload"].get("route") == "/data/series"
    ]
    assert len(granted) == len(queries)
    payload = granted[-1]["payload"]
    assert payload["aggregate_only"] is True
    assert payload["fields_read"]
    assert granted[0]["actor"] == "executive@test.example"
    assert not STUDENT_ID.search(json.dumps(granted))


def test_repeat_requests_are_served_from_the_cache(
    con: sqlite3.Connection, vocab: Vocab
) -> None:
    chart = d.CHARTS_BY_ID["stop_out"]
    first = d.cached_series(chart, {}, "pell")
    assert d.cached_series(chart, {}, "pell") is first
    assert first == d.compute(con, chart, {}, "pell", vocab)


# --- one attribute per request (review 2026-10-07) ---------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "chart=financial_balance_total&admit_type=first_time&compare=athlete",
        "chart=headcount&gender=female&pell=pell",
        "chart=headcount&chart=dfw",
        "chart=headcount&compare=gender&compare=pell",
    ],
)
def test_a_request_narrows_or_splits_never_both_and_is_logged(query: str) -> None:
    """Narrowing by one attribute and splitting by another would publish a
    two-way table whose margins (each one-way chart) solve the withheld
    cells; such a request is refused and the refusal is recorded."""
    app = create_app()
    client = make_authenticated_client(app, role="executive")
    response = client.get(f"/data/series?{query}")
    assert response.status_code == 422
    refused = app.state.auth.audit_for(1).events("data.refused")
    assert refused and refused[-1]["payload"]["route"] == "/data/series"


def _one_way(client: TestClient, chart: str, key: str) -> dict[str, Any]:
    return dict(client.get(f"/data/series?chart={chart}&compare={key}").json())


ONE_WAY = [
    ("headcount", "college"),
    ("headcount", "race_ethnicity"),
    ("headcount", "age_band"),
    ("financial_balance_total", "residency"),
    ("financial_hold_students", "honors"),
    ("on_campus", "gender"),
    ("stop_out", "honors"),
    ("dfw", "admit_type"),
    ("retention", "age_band"),
    ("financial_hold_rate", "class_level"),
]


@pytest.mark.parametrize(("chart", "key"), ONE_WAY)
def test_a_narrowed_chart_is_the_same_group_of_the_split_chart(
    chart: str, key: str, con: sqlite3.Connection, vocab: Vocab
) -> None:
    """Narrowing to a group shows exactly that group's points (values and
    withholding) as the chart split by the same attribute."""
    c = d.CHARTS_BY_ID[chart]
    # (Race and ethnicity is a filter only on the page, but the same table.)
    split = d.compute(con, c, {}, key, vocab)
    groups = d.domain(key, vocab)
    by_group = {s["key"]: s["points"] for s in split["series"]}
    for value in groups:
        narrowed = d.compute(con, c, {key: value}, None, vocab)
        (series,) = narrowed["series"]
        if value in by_group:
            assert series["points"] == by_group[value]
        else:  # dropped from the split: withheld (or empty) in every year
            assert all(p["status"] != "ok" for p in series["points"])


@pytest.mark.parametrize(
    ("chart", "key"), [p for p in ONE_WAY if p[1] != "race_ethnicity"]
)
def test_withheld_groups_cannot_be_worked_out_from_the_total(
    chart: str, key: str, con: sqlite3.Connection, vocab: Vocab
) -> None:
    """In every term (or class), the published total minus the shown groups
    is either nothing or at least 10: students, and for a rate also the
    count it rests on either way (who did and who did not)."""
    c = d.CHARTS_BY_ID[chart]
    split = d.compute(con, c, {}, key, vocab)
    whole = d.compute(con, c, {}, None, vocab)["series"][0]["points"]
    groups = [s for s in split["series"] if s["slot"] is not None]
    # Groups withheld in every year are dropped from the series: count them
    # as withheld, which only makes this check stricter.
    for i, total in enumerate(whole):
        if total["status"] != "ok":
            continue
        shown = [s["points"][i] for s in groups if s["points"][i]["status"] == "ok"]
        names = ["students"]
        if split["kind"] == "pct":
            names += ["numerator", "rest"]

        def get(p: dict[str, Any], name: str) -> int:
            if name == "rest":
                return int(p["denominator"]) - int(p["numerator"])
            return int(p[name])

        for name in names:
            gap = get(total, name) - sum(get(p, name) for p in shown)
            assert gap == 0 or gap >= 10, (chart, key, total["x"], name, gap)


def test_no_shown_rate_rests_on_fewer_than_10_either_way(
    con: sqlite3.Connection, vocab: Vocab
) -> None:
    for c in d.CHARTS:
        if c.measure.kind != "pct":
            continue
        for key in [None, *[k for k in d.COMPARE_KEYS if d.applies(c, k)]]:
            for s in d.compute(con, c, {}, key, vocab)["series"]:
                for p in s["points"]:
                    if p["status"] == "ok":
                        assert p["numerator"] >= 10, (c.id, key, p)
                        assert p["denominator"] - p["numerator"] >= 10, (c.id, key, p)
                    else:
                        assert "numerator" not in p


def test_medians_need_20_students_and_are_rounded_to_100(
    con: sqlite3.Connection, vocab: Vocab
) -> None:
    c = d.CHARTS_BY_ID["financial_balance_median"]
    for key in [None, "gender", "residency"]:
        for s in d.compute(con, c, {}, key, vocab)["series"]:
            for p in s["points"]:
                if p["status"] == "ok":
                    assert p["students"] >= 20 and p["value"] % 100 == 0
    notes = " ".join(d.compute(con, c, {}, None, vocab)["notes"])
    assert "latest term in the records and is not complete" in notes


def test_notes_name_the_terms_from_the_data(
    con: sqlite3.Connection, vocab: Vocab
) -> None:
    grad4 = d.compute(con, d.CHARTS_BY_ID["grad4"], {}, None, vocab)
    assert any("Fall 2020 to Fall 2022" in n for n in grad4["notes"])
    stop = d.compute(con, d.CHARTS_BY_ID["stop_out"], {}, None, vocab)
    assert any(n.startswith("Spring 2026 has no next term") for n in stop["notes"])


@pytest.mark.parametrize(
    ("role", "query", "status"),
    [
        ("staff", "chart=retention&compare=pell", 403),
        ("staff", "chart=headcount&pell=pell", 403),
        ("reviewer", "chart=stop_out&compare=pell", 403),
        ("staff", "chart=retention&compare=gender", 200),
        ("executive", "chart=retention&compare=pell", 200),
        ("aid", "chart=financial_hold_rate&pell=no_pell", 200),
    ],
)
def test_pell_status_is_for_the_finance_roles_only(
    role: str, query: str, status: int
) -> None:
    app = create_app()
    client = make_authenticated_client(app, role=role)
    assert client.get(f"/data/series?{query}").status_code == status
    if status == 403:
        (event,) = app.state.auth.audit_for(1).events("data.refused")
        assert event["payload"]["category"] == "role_attribute"


def test_a_chart_that_cannot_take_the_choice_is_logged_as_refused() -> None:
    app = create_app()
    client = make_authenticated_client(app, role="executive")
    body = client.get("/data/series?chart=retention&class_level=Junior").json()
    assert body["not_applicable"]["key"] == "class_level"
    (event,) = app.state.auth.audit_for(1).events("data.refused")
    assert event["payload"]["category"] == "not_applicable"


def test_the_department_roles_are_mapped() -> None:
    # Finance is the university budget only; Student Accounts is student money only.
    assert dashboards_for("finance") == ("budget",)
    assert dashboards_for("studentaccounts") == ("finances",)
    assert dashboards_for("registrar") == ("students",)
    assert dashboards_for("studentlife") == ("students",)
    assert dashboards_for("it") == ()
    assert "it" not in DATA_ROLES and {"finance", "studentaccounts"} <= set(DATA_ROLES)
