"""Support programs: the synthetic program records, the eligibility rules,
the impact comparisons, the Interventions routes and the outreach lists.

The school database is generated at --scale 0.05 in a tmp directory (large
enough that the programs have participants and non-participants to compare,
small enough to build in a few seconds).
"""

from __future__ import annotations

import importlib.util
import re
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi import FastAPI

from cabinet import interventions as iv
from cabinet.api import create_app
from cabinet.auth import AuthStore
from cabinet.counseling import MINIMUM_CELL_SIZE
from cabinet.explore.catalog import Catalog, catalog_for, connect_readonly
from cabinet.explore.planner import rule_plan
from conftest import make_authenticated_client

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHOOL = REPO_ROOT / "data" / "school"
GENERATE = SCHOOL / "generate.py"
STUDENT_ID = re.compile(r"\bS-\d+")


def _load(name: str) -> ModuleType:
    """A module from data/school (they import each other by bare name)."""
    if str(SCHOOL) not in sys.path:
        sys.path.insert(0, str(SCHOOL))
    spec = importlib.util.spec_from_file_location(
        f"school_{name}", SCHOOL / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def school_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("school") / "school.db"
    proc = subprocess.run(
        [sys.executable, str(GENERATE), "--scale", "0.05", "--out", str(out)],
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


@pytest.fixture
def con(school_db: Path) -> Iterator[sqlite3.Connection]:
    connection = connect_readonly(school_db)
    yield connection
    connection.close()


@pytest.fixture
def catalog(con: sqlite3.Connection, school_db: Path) -> Catalog:
    return catalog_for(con, school_db)


@pytest.fixture
def app() -> FastAPI:
    return create_app()


def _events(app: FastAPI, event_type: str) -> list[dict[str, Any]]:
    store: AuthStore = app.state.auth
    institution = store.institution_by_slug("bootstrap")
    assert institution is not None
    return store.audit_events(int(institution["id"]), event_type)


def _regular_terms(con: sqlite3.Connection) -> list[str]:
    return [
        str(r[0])
        for r in con.execute(
            "SELECT term_code FROM academic_periods WHERE season != 'Summer' "
            "ORDER BY term_code"
        )
    ]


# --- the data: the core tables are untouched -----------------------------------


def test_the_program_tables_leave_every_core_table_unchanged(tmp_path: Path) -> None:
    generate = _load("generate")
    check = _load("check")
    g = generate.build(0.02)
    with_programs = tmp_path / "with.db"
    generate.write_db(g, with_programs)
    original = generate.IV.write_tables
    generate.IV.write_tables = lambda con, seed: None
    try:
        without = tmp_path / "without.db"
        generate.write_db(g, without)
    finally:
        generate.IV.write_tables = original
    a = sqlite3.connect(with_programs)
    b = sqlite3.connect(without)
    try:
        for table in check.EXPECTED_COLUMNS:  # every core table, one at a time
            assert check.canonical_hash(a, [table]) == check.canonical_hash(
                b, [table]
            ), table
        assert check.canonical_hash(a) == check.canonical_hash(b)
        names = {r[0] for r in b.execute("SELECT name FROM sqlite_master")}
        assert "support_program_terms" not in names
        assert a.execute("SELECT COUNT(*) FROM support_program_terms").fetchone()[0]
        # Deterministic: a second write gives the same program rows.
        again = tmp_path / "again.db"
        generate.write_db(g, again)
        c = sqlite3.connect(again)
        tables = list(check.PROGRAM_COLUMNS)
        assert check.canonical_hash(a, tables) == check.canonical_hash(c, tables)
        c.close()
    finally:
        a.close()
        b.close()


def test_the_checker_passes_the_program_records(school_db: Path) -> None:
    check = _load("check")
    con = sqlite3.connect(school_db)
    try:
        report = check.Checker(con).run()
    finally:
        con.close()
    result = {r["name"]: r for r in report["checks"]}
    assert result["support_programs"]["ok"], result["support_programs"]["detail"]
    assert result["schema"]["ok"], result["schema"]["detail"]
    assert re.fullmatch(r"[0-9a-f]{64}", report["programs_sha256"])


def test_offers_start_with_the_program_and_take_up_is_realistic(
    con: sqlite3.Connection,
) -> None:
    rows = con.execute(
        "SELECT program_id, period, COUNT(*), SUM(offered), AVG(accepted) "
        "FROM support_program_terms GROUP BY 1, 2"
    ).fetchall()
    seen = {(r[0], r[1]): r for r in rows}
    for program in iv.PROGRAM_IDS:
        before, after = seen[(program, "before")], seen[(program, "after")]
        assert before[3] == 0  # nothing offered before Fall 2024
        assert after[3] == after[2]  # everyone eligible is offered
    tutoring = seen[("ai_tutoring", "after")][4]
    assert 0.5 <= tutoring <= 0.7  # about 55 to 65 % say yes


# --- the eligibility rules -----------------------------------------------------------


def test_the_live_rules_name_exactly_the_recorded_students(
    con: sqlite3.Connection,
) -> None:
    """The reviewed SQL (backend) and the generator's rule code agree, for
    every program and every fall and spring term."""
    for program in iv.PROGRAM_IDS:
        for term in _regular_terms(con):
            live = {sid for sid, _ in iv.eligible_students(con, program, term)}
            recorded = {
                str(r[0])
                for r in con.execute(
                    "SELECT student_id FROM support_program_terms "
                    "WHERE program_id = ? AND term_code = ?",
                    (program, term),
                )
            }
            assert live == recorded, (program, term)


def test_tutoring_is_the_bottom_thirty_percent_by_gpa(con: sqlite3.Connection) -> None:
    term = "202520"
    eligible = dict(
        (sid, facts["gpa"])
        for sid, facts in iv.eligible_students(con, "ai_tutoring", term)
    )
    prior = {
        str(sid): gpa
        for sid, gpa in con.execute(
            """SELECT t.student_id, (SELECT r.cumulative_gpa FROM student_term_records r
                WHERE r.student_id = t.student_id AND r.term_code < ?
                  AND r.cumulative_gpa IS NOT NULL ORDER BY r.term_code DESC LIMIT 1)
            FROM student_term_records t WHERE t.term_code = ?""",
            (term, term),
        )
        if gpa is not None
    }
    assert set(eligible) <= set(prior)
    cutoff = max(eligible.values())
    assert all(g > cutoff for sid, g in prior.items() if sid not in eligible)
    assert len(eligible) >= (3 * len(prior) + 9) // 10
    assert len(eligible) < 0.4 * len(prior)


def test_the_bridge_names_theology_majors_with_a_financial_hold(
    con: sqlite3.Connection,
) -> None:
    for term in ("202510", "202620"):
        for sid, facts in iv.eligible_students(con, "theology_bridge", term):
            assert facts["major"] in iv.THEOLOGY_MAJORS
            assert facts["amount"] is not None and facts["amount"] >= 0
            hold = con.execute(
                "SELECT COUNT(*) FROM person_holds WHERE student_id = ? AND "
                "term_code = ? AND category = 'financial'",
                (sid, term),
            ).fetchone()[0]
            assert hold >= 1


def test_fit_advising_names_first_year_first_time_students_once(
    con: sqlite3.Connection,
) -> None:
    rows = con.execute(
        "SELECT p.student_id, p.term_code, s.entry_term, s.entry_type "
        "FROM support_program_terms p JOIN students s USING (student_id) "
        "WHERE p.program_id = 'fit_advising'"
    ).fetchall()
    assert rows
    seen: set[str] = set()
    for sid, term, entry, entry_type in rows:
        assert entry_type == "first_time"
        assert term in (entry, iv_next(entry))
        assert sid not in seen  # counted once, in the term the rule is first met
        seen.add(sid)


def iv_next(code: str) -> str:
    return code[:4] + "20" if code.endswith("10") else f"{int(code[:4]) + 1}10"


# --- the impact comparisons -----------------------------------------------------------


def test_a_small_group_is_withheld_with_its_partner() -> None:
    small = iv._Group("Took part", [1.0] * 5)
    large = iv._Group("Did not", [0.0, 1.0] * 20)
    c = iv._comparison("naive", "x", "y", small, large, "pct")
    assert c["withheld"] is True
    assert c["a"]["n"] is None and c["b"]["n"] is None  # the partner too
    assert c["small"] == ["a"]
    assert c["difference"] is None and c["a"]["value"] is None


def test_matching_on_gpa_removes_selection_the_naive_comparison_keeps() -> None:
    """Higher-GPA students take part more; the outcome follows GPA plus a
    true effect of 0.1. The naive gap overstates it; within GPA bands it is
    recovered."""
    rows: list[tuple[float | None, int, float]] = []
    for band in range(10):
        gpa = 1.0 + 0.2 * band
        takers = 2 + band * 2  # selection: more takers at higher GPA
        for i in range(30):
            accepted = 1 if i < takers else 0
            rows.append((gpa, accepted, gpa + (0.1 if accepted else 0.0)))
    took = iv._Group("a", [v for _, a, v in rows if a])
    rest = iv._Group("b", [v for _, a, v in rows if not a])
    naive = took.mean - rest.mean
    m = iv._matched(rows, 0.2, "gpa")
    assert naive > 0.3
    assert m["diff"] == pytest.approx(0.1, abs=1e-9)
    assert m["extra"]["coverage_pct"] == 100.0


def test_impact_labels_every_method_and_carries_the_caveat(
    con: sqlite3.Connection,
) -> None:
    report = iv.impact(con, "ai_tutoring")
    assert report["caveat"].startswith("This comparison isn't a randomized trial")
    assert "follow-up records" in report["source"]
    gpa = report["outcomes"][0]
    assert gpa["key"] == "term_gpa"
    methods = [c["method"] for c in gpa["comparisons"]]
    assert methods == ["before_after", "naive", "matched"]
    assert all(c["sentence"] for c in gpa["comparisons"])
    naive, fair = gpa["comparisons"][1], gpa["comparisons"][2]
    assert not naive["withheld"] and not fair["withheld"]
    # The planted selection: the naive gap is larger than the fair one,
    # and the fair one is near the planted 0.15.
    assert naive["difference"] > fair["difference"]
    assert 0.05 <= fair["difference"] <= 0.25
    assert fair["low"] <= fair["difference"] <= fair["high"]
    assert gpa["verdict"]
    assert not STUDENT_ID.search(repr(report))


def test_reach_withholds_small_counts(con: sqlite3.Connection) -> None:
    for program in iv.PROGRAM_IDS:
        report = iv.reach(con, program)
        for term in report["terms"]:
            for key in ("eligible", "offered", "accepted"):
                value = term[key]
                assert value is None or value >= MINIMUM_CELL_SIZE
            if term["accepted"] is not None and term["eligible"] is not None:
                assert term["eligible"] - term["accepted"] >= MINIMUM_CELL_SIZE


# --- Explore: the owner's questions ----------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        (
            "What % of students change majors, by major?",
            [("measure_by_group", {"measure": "major_change_out_rate"})],
        ),
        (
            "What is the attrition rate for each major?",
            [("measure_by_group", {"measure": "major_attrition_rate"})],
        ),
        (
            "Did the AI tutoring program work?",
            [
                ("program_impact", {"program": "ai_tutoring"}),
                ("program_impact", {"outcome": "returned_next_term"}),
            ],
        ),
        (
            "Is the theology funding bridge helping?",
            [
                ("program_impact", {"program": "theology_bridge"}),
                ("program_impact", {"outcome": "financial_hold_next_term"}),
            ],
        ),
        (
            "How many students are eligible for tutoring this term?",
            [("program_reach", {"program": "ai_tutoring", "term": "202620"})],
        ),
        (
            "Which majors have the most students who aren't a good fit in their "
            "first year?",
            [
                (
                    "measure_by_group",
                    {"measure": "fit_flag_rate", "group_by": "major"},
                )
            ],
        ),
    ],
)
def test_the_owner_questions_plan(
    catalog: Catalog, question: str, expected: list[tuple[str, dict[str, Any]]]
) -> None:
    steps = rule_plan(question, catalog)
    assert steps is not None, question
    assert [s.analysis_id for s in steps] == [a for a, _ in expected]
    for step, (_, params) in zip(steps, expected, strict=True):
        for key, value in params.items():
            assert step.params.get(key) == value, (question, key)


def test_the_owner_questions_answer_over_the_api(app: FastAPI) -> None:
    client = make_authenticated_client(app, role="staff")
    for question in (
        "Did the AI tutoring program work?",
        "How many students are eligible for tutoring this term?",
        "What is the attrition rate for each major?",
        "Which majors have the most students who aren't a good fit in their "
        "first year?",
    ):
        response = client.post("/explore", json={"question": question})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["refused"] is False, question
        assert not STUDENT_ID.search(response.text), question
        assert all(s["aggregate_only"] for s in body["steps"])
        assert all(not s.get("error") for s in body["steps"]), question
    impact = client.post(
        "/explore", json={"question": "Did the AI tutoring program work?"}
    )
    text = impact.text
    assert "isn't a randomized trial" in text


# --- the routes: roles, approval, audit -----------------------------------------------


def test_every_role_reads_the_aggregate_page(app: FastAPI) -> None:
    for role in ("staff", "reviewer", "aid", "executive"):
        client = make_authenticated_client(app, role=role)
        response = client.get("/interventions")
        assert response.status_code == 200, (role, response.text)
        assert not STUDENT_ID.search(response.text)
        body = response.json()
        assert [p["id"] for p in body["programs"]] == list(iv.PROGRAM_IDS)
        for p in body["programs"]:
            assert p["rule"] and p["offer"] and p["owner_office"]
            assert p["impact"]["caveat"]
    staff = make_authenticated_client(app, role="staff").get("/interventions").json()
    assert not any(p["can_prepare"] for p in staff["programs"])
    aid = make_authenticated_client(app, role="aid").get("/interventions").json()
    assert [p["id"] for p in aid["programs"] if p["can_prepare"]] == ["theology_bridge"]


def test_only_row_roles_and_the_owning_office_prepare_a_list(app: FastAPI) -> None:
    staff = make_authenticated_client(app, role="staff")
    assert staff.post("/interventions/ai_tutoring/outreach").status_code == 403
    reviewer = make_authenticated_client(app, role="reviewer")
    assert reviewer.post("/interventions/theology_bridge/outreach").status_code == 403
    aid = make_authenticated_client(app, role="aid")
    assert aid.post("/interventions/ai_tutoring/outreach").status_code == 403
    response = aid.post("/interventions/theology_bridge/outreach")
    assert response.status_code == 200, response.text
    assert response.json()["list"]["status"] == "pending_approval"
    refusals = _events(app, "data.refused")
    assert any(
        "/interventions/ai_tutoring/outreach" in str(e["payload"]) for e in refusals
    )
    assert (
        make_authenticated_client(app, role="executive")
        .post("/interventions/nope/outreach")
        .status_code
        == 404
    )


def test_an_outreach_list_waits_for_approval_and_is_audited(app: FastAPI) -> None:
    executive = make_authenticated_client(app, role="executive")
    first = executive.post("/interventions/ai_tutoring/outreach")
    assert first.status_code == 200, first.text
    listed = first.json()["list"]
    assert first.json()["created"] is True
    assert listed["status"] == "pending_approval" and listed["count"] > 0
    again = executive.post("/interventions/ai_tutoring/outreach").json()
    assert again["created"] is False and again["list"]["id"] == listed["id"]
    list_id = listed["id"]

    # The rows: the executive may open them; the reviewer and staff may not.
    rows = executive.get(f"/outreach/{list_id}")
    assert rows.status_code == 200
    body = rows.json()
    assert len(body["rows"]) == listed["count"]
    assert all(STUDENT_ID.fullmatch(r["student_id"]) for r in body["rows"])
    assert set(body["rows"][0]["facts"]) == {"gpa", "major"}
    assert (
        make_authenticated_client(app, role="reviewer")
        .get(f"/outreach/{list_id}")
        .status_code
        == 403
    )
    assert (
        make_authenticated_client(app, role="aid")
        .get(f"/outreach/{list_id}")
        .status_code
        == 403
    )  # tutoring is not the aid office's program

    # Nothing is recorded before approval; the aid role cannot approve.
    row_id = body["rows"][0]["id"]
    early = executive.patch(
        f"/outreach/{list_id}/rows/{row_id}", json={"status": "offered"}
    )
    assert early.status_code == 409
    aid = make_authenticated_client(app, role="aid")
    assert (
        aid.post(
            f"/outreach/{list_id}/decision", json={"decision": "approve"}
        ).status_code
        == 403
    )
    bad = executive.post(f"/outreach/{list_id}/decision", json={"decision": "maybe"})
    assert bad.status_code == 422

    approved = executive.post(
        f"/outreach/{list_id}/decision", json={"decision": "approve"}
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["list"]["status"] == "approved"
    assert approved.json()["list"]["decided_by"] == "executive@test.example"
    twice = executive.post(
        f"/outreach/{list_id}/decision", json={"decision": "decline"}
    )
    assert twice.status_code == 409

    done = executive.patch(
        f"/outreach/{list_id}/rows/{row_id}", json={"status": "offered"}
    )
    assert done.status_code == 200, done.text
    assert (
        executive.patch(
            f"/outreach/{list_id}/rows/{row_id}", json={"status": "required"}
        ).status_code
        == 422
    )

    for event_type in (
        "outreach.prepared",
        "outreach.decided",
        "outreach.viewed",
        "outreach.updated",
    ):
        events = _events(app, event_type)
        assert events, event_type
        assert not STUDENT_ID.search(repr([e["payload"] for e in events])), event_type
    decided = _events(app, "outreach.decided")[-1]["payload"]
    assert decided["decision"] == "approved" and decided["list_id"] == list_id
    page = executive.get("/interventions").json()
    tutoring = next(p for p in page["programs"] if p["id"] == "ai_tutoring")
    assert tutoring["outreach"]["status"] == "approved"


def test_an_older_school_database_says_to_rebuild(
    school_db: Path, tmp_path: Path
) -> None:
    """A school database built before the program tables answers with a
    plain sentence, never a traceback."""
    old = tmp_path / "old.db"
    src = sqlite3.connect(school_db)
    dst = sqlite3.connect(old)
    src.backup(dst)
    src.close()
    dst.execute("DROP TABLE support_program_terms")
    dst.execute("DROP TABLE support_programs")
    dst.commit()
    dst.close()
    con = connect_readonly(old)
    try:
        from cabinet.explore.catalog import ANALYSIS_BY_ID, AnalysisError

        catalog = catalog_for(con, old)
        run = ANALYSIS_BY_ID["measure_by_group"].run
        with pytest.raises(AnalysisError, match="make school-data"):
            run(con, {"measure": "fit_flag_rate", "group_by": "major"}, catalog.vocab)
        with pytest.raises(AnalysisError, match="make school-data"):
            ANALYSIS_BY_ID["program_impact"].run(
                con, {"program": "ai_tutoring"}, catalog.vocab
            )
    finally:
        con.close()


# --- review fixes: no subtraction leak, concurrent decisions, wording --------------


def _reach_db(path: Path, counts: list[tuple[str, int, int]]) -> sqlite3.Connection:
    """A school database with only what ``reach`` reads: per term, (term,
    eligible, took part)."""
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE academic_periods (term_code TEXT, name TEXT, season TEXT,
            sequence INTEGER);
        CREATE TABLE support_programs (program_id TEXT, name TEXT,
            eligibility_rule TEXT, offer TEXT, owner_office TEXT, start_term TEXT,
            primary_outcome TEXT, secondary_outcome TEXT);
        CREATE TABLE support_program_terms (program_id TEXT, student_id TEXT,
            term_code TEXT, period TEXT, offered INTEGER, accepted INTEGER);
        """
    )
    for i, (term, _, _) in enumerate(counts):
        con.execute(
            "INSERT INTO academic_periods VALUES (?, ?, ?, ?)",
            (term, term, "Fall" if term.endswith("10") else "Spring", i),
        )
    con.execute(
        "INSERT INTO support_programs VALUES ('theology_bridge', 'Bridge', 'r', 'o', "
        "'Financial Aid', ?, 'returned_next_term', 'financial_hold_next_term')",
        (counts[0][0],),
    )
    for term, eligible, took in counts:
        for k in range(eligible):
            con.execute(
                "INSERT INTO support_program_terms VALUES "
                "('theology_bridge', ?, ?, 'after', 1, ?)",
                (f"S-{term}{k:03d}", term, 1 if k < took else 0),
            )
    con.commit()
    return con


def _recoverable(report: dict[str, Any], key: str) -> list[str]:
    """Terms whose withheld ``key`` the shown terms and total give away."""
    terms = report["terms"]
    total = report["total"][key]
    hidden = [t for t in terms if t[key] is None]
    if total is None or not hidden:
        return []
    shown = sum(t[key] for t in terms if t[key] is not None)
    # One hidden term is the total minus the rest; several hidden terms
    # reveal their sum, which is also a leak when it is under the minimum.
    if len(hidden) == 1 or total - shown < MINIMUM_CELL_SIZE:
        return [t["term"] for t in hidden]
    return []


def test_a_withheld_term_is_never_recoverable_from_the_total(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The review's case: Spring 2026 'took part' withheld (8 declined) while
    the other terms and the total were shown, so 39 = 181 - 58 - 40 - 44."""
    monkeypatch.setattr(iv, "eligible_count", lambda con, program, term: 47)
    con = _reach_db(
        tmp_path / "reach.db",
        [
            ("202510", 73, 58),
            ("202520", 52, 40),
            ("202610", 60, 44),
            ("202620", 47, 39),
        ],
    )
    report = iv.reach(con, "theology_bridge")
    assert report["terms"][-1]["accepted"] is None  # 8 declined
    assert report["total"]["accepted"] is None
    assert report["total"]["take_up_pct"] is None
    assert _recoverable(report, "accepted") == []
    # A term with fewer than 10 eligible withholds the total eligible too.
    con2 = _reach_db(tmp_path / "small.db", [("202510", 60, 30), ("202520", 6, 3)])
    small = iv.reach(con2, "theology_bridge")
    assert small["terms"][-1]["eligible"] is None
    assert small["total"]["eligible"] is None
    assert _recoverable(small, "eligible") == []


def test_no_withheld_reach_cell_is_recoverable_in_the_generated_data(
    con: sqlite3.Connection,
) -> None:
    for program in iv.PROGRAM_IDS:
        report = iv.reach(con, program)
        assert _recoverable(report, "accepted") == [], program
        assert _recoverable(report, "eligible") == [], program


def test_verdicts_compare_without_claiming_cause(con: sqlite3.Connection) -> None:
    for program in iv.PROGRAM_IDS:
        for o in iv.impact(con, program)["outcomes"]:
            assert o["tone"] in ("better", "worse", "unclear", "withheld")
            assert "helping" not in o["verdict"].lower()
            assert "working" not in o["verdict"].lower()


def test_a_concurrent_decision_is_a_409_with_no_second_event(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cabinet.outreach as outreach

    executive = make_authenticated_client(app, role="executive")
    listed = executive.post("/interventions/fit_advising/outreach").json()["list"]
    stale = outreach._list_row(app.state.auth, _institution_id(app), int(listed["id"]))
    assert (
        executive.post(
            f"/outreach/{listed['id']}/decision", json={"decision": "approve"}
        ).status_code
        == 200
    )
    before = len(_events(app, "outreach.decided"))
    # The second request read the list while it was still pending.
    real = outreach._list_row
    calls = {"n": 0}

    def first_stale(store: Any, institution: int, list_id: int) -> Any:
        calls["n"] += 1
        return stale if calls["n"] == 1 else real(store, institution, list_id)

    monkeypatch.setattr(outreach, "_list_row", first_stale)
    raced = executive.post(
        f"/outreach/{listed['id']}/decision", json={"decision": "decline"}
    )
    assert raced.status_code == 409
    assert raced.json()["list"]["status"] == "approved"
    assert len(_events(app, "outreach.decided")) == before


def _institution_id(app: FastAPI) -> int:
    store: AuthStore = app.state.auth
    institution = store.institution_by_slug("bootstrap")
    assert institution is not None
    return int(institution["id"])


def test_a_school_database_without_the_program_says_to_rebuild(
    app: FastAPI, school_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = tmp_path / "partial.db"
    src = sqlite3.connect(school_db)
    dst = sqlite3.connect(old)
    src.backup(dst)
    src.close()
    dst.execute("DELETE FROM support_programs WHERE program_id = 'fit_advising'")
    dst.commit()
    dst.close()
    monkeypatch.setenv("CABINET_SCHOOL_DB", str(old))
    response = make_authenticated_client(app, role="executive").post(
        "/interventions/fit_advising/outreach"
    )
    assert response.status_code == 503
    assert "make school-data" in response.json()["detail"]


def test_the_page_payload_carries_no_small_side_marker(app: FastAPI) -> None:
    body = make_authenticated_client(app, role="executive").get("/interventions").json()
    for p in body["programs"]:
        for o in p["impact"]["outcomes"]:
            for c in o["comparisons"]:
                assert "small" not in c
