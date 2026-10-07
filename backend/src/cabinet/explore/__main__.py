"""Answer one question from the command line, offline (rule planner and
template answer, the same code the API runs in replay mode):

    python -m cabinet.explore "Which major has the lowest GPA?"
    python -m cabinet.explore --role staff --json "Who has taught MEEN 3310?"

It reads ``CABINET_SCHOOL_DB`` (default ``var/school/school.db``) and writes
nothing, not even audit events: it is the operator's check tool, used by
``make explore-check``. People ask through ``POST /explore``, which records
every question.
"""

from __future__ import annotations

import argparse
import json
import sys

from cabinet.explore.answer import write_answer
from cabinet.explore.catalog import SchoolDataMissing, catalog_for, connect_readonly
from cabinet.explore.execute import execute
from cabinet.explore.planner import (
    UNANSWERABLE_MESSAGE,
    forward_rule_plan_detail,
    nearest_examples,
    rule_plan,
)
from cabinet.explore.privacy import (
    FORWARD_LEAD,
    OFF_TOPIC_MESSAGE,
    is_forward_looking,
    is_off_topic,
    refusal_for,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m cabinet.explore")
    parser.add_argument("question")
    parser.add_argument(
        "--role",
        default="executive",
        choices=("executive", "admin", "staff", "reviewer"),
    )
    parser.add_argument("--json", action="store_true", help="print the full JSON")
    args = parser.parse_args(argv)
    try:
        con = connect_readonly()
    except SchoolDataMissing as exc:
        print(exc, file=sys.stderr)
        return 2
    try:
        refusal = refusal_for(args.question)
        if refusal is not None:
            # POST /explore answers these with related questions or totals
            # for students like that; this check tool only names the reason.
            print(f"Protected before planning ({refusal[0]}): {refusal[1]}")
            return 0
        catalog = catalog_for(con)
        if is_off_topic(args.question, catalog.known_names):
            print(OFF_TOPIC_MESSAGE)
            return 0
        forward = is_forward_looking(args.question)
        steps = (
            forward_rule_plan_detail(args.question, catalog)[0]
            if forward
            else rule_plan(args.question, catalog)
        )
        if steps is None:
            print(UNANSWERABLE_MESSAGE)
            for example in nearest_examples(args.question):
                print(f"  Try: {example}")
            return 0
        results = execute(steps, con, catalog, args.role)
        answer, source, _ = write_answer(results, None)
    finally:
        con.close()
    if args.json:
        print(
            json.dumps(
                {
                    "answer": [s.to_json() for s in answer],
                    "source": source,
                    "steps": [
                        {
                            "analysis_id": r.analysis.id,
                            "title": r.analysis.title,
                            "params_plain": r.params_plain,
                            "table": r.table(),
                            "notes": r.notes,
                        }
                        for r in results
                    ],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0
    if forward:
        print(FORWARD_LEAD)
    for sentence in answer:
        print(sentence.text)
    print(f"({source})")
    for result in results:
        print(f"\nStep {result.index + 1}. {result.analysis.title}")
        for line in result.params_plain:
            print(f"  {line}")
        table = result.table()
        labels = [c["label"] for c in table["columns"]]
        print("  | " + " | ".join(labels) + " |")
        for row in table["rows"]:
            print("  | " + " | ".join("" if v is None else str(v) for v in row) + " |")
        for note in result.notes:
            print(f"  Note: {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
