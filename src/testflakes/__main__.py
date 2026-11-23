"""Command line entry point: ``testflakes`` or ``python -m testflakes``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import flakiness, newly_failing, parse_run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="testflakes",
        description="Rank flaky tests from a history of JUnit XML reports.",
    )
    parser.add_argument("reports", nargs="+", type=Path, metavar="REPORT")
    parser.add_argument("--top", type=int, default=10, help="how many tests to show")
    parser.add_argument("--min-runs", type=int, default=2, help="ignore tests seen fewer times")
    parser.add_argument("--json", action="store_true", help="print JSON")
    parser.add_argument(
        "--new",
        metavar="BEFORE",
        help="take the last report as AFTER and list tests that newly fail since BEFORE",
    )
    args = parser.parse_args(argv)

    missing = [str(path) for path in args.reports if not path.is_file()]
    if missing:
        print(f"no such file: {', '.join(missing)}", file=sys.stderr)
        return 2
    try:
        runs = [parse_run(path) for path in args.reports]
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.new:
        if len(runs) != 2:
            print(
                "--new takes the history report and the new report: BEFORE AFTER", file=sys.stderr
            )
            return 2
        fresh = newly_failing(runs[0], runs[1])
        if args.json:
            print(json.dumps({"newly_failing": fresh}, indent=2))
        else:
            for node_id in fresh:
                print(node_id)
            print(f"{len(fresh)} newly failing")
        return 1 if fresh else 0

    ranked = flakiness(runs, min_runs=args.min_runs)[: args.top]
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "node_id": stat.node_id,
                        "runs": stat.runs,
                        "passes": stat.passes,
                        "failures": stat.failures,
                        "errors": stat.errors,
                        "skips": stat.skips,
                        "flips": stat.flips,
                        "score": stat.score,
                    }
                    for stat in ranked
                ],
                indent=2,
            )
        )
        return 0
    if not ranked:
        print(f"no test appears in {args.min_runs} reports")
        return 0
    width = max(len(stat.node_id) for stat in ranked)
    print(f"{'test'.ljust(width)}  score  runs  fail  flip")
    for stat in ranked:
        print(
            f"{stat.node_id.ljust(width)}  {stat.score:5.2f}  {stat.runs:4d}  "
            f"{stat.failures + stat.errors:4d}  {stat.flips:4d}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
