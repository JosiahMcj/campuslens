"""CLI: print the ROADMAP §3 findings object (metrics M1-M8) as JSON.

    .venv/bin/python -m cabinet.findings [--fixture PATH]

Defaults to data/fixture.json at the repository root.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cabinet.fixture import load_fixture
from cabinet.metrics import findings

DEFAULT_FIXTURE = Path(__file__).resolve().parents[3] / "data" / "fixture.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cabinet.findings",
        description="Compute metrics M1-M8 (CONTRACTS.md) from the fixture and print "
        "the ROADMAP §3 findings object as JSON.",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=DEFAULT_FIXTURE,
        help=f"fixture JSON path (default: {DEFAULT_FIXTURE})",
    )
    args = parser.parse_args(argv)
    fixture = load_fixture(args.fixture)
    out = findings(fixture, fixture_path=args.fixture)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
