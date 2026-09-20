"""CLI for Orbita deterministic System Analysis CI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent import system_ci


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orbita-ci",
        description="Deterministic requirements/architecture consistency checks.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="analyse a directory and return a CI exit code")
    check.add_argument("root", nargs="?", default=".")
    check.add_argument("--baseline", default=system_ci.BASELINE_FILENAME)
    check.add_argument(
        "--fail-on",
        choices=["blocker", "major", "minor", "none"],
        default="major",
    )
    check.add_argument("--json", action="store_true", dest="as_json")

    baseline = sub.add_parser("baseline", help="accept current deterministic findings")
    baseline.add_argument("root", nargs="?", default=".")
    baseline.add_argument("--output", default=system_ci.BASELINE_FILENAME)

    impact = sub.add_parser("impact", help="show deterministic neighbours of an artifact")
    impact.add_argument("query")
    impact.add_argument("root", nargs="?", default=".")
    impact.add_argument("--depth", type=int, default=2)
    impact.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    if args.command == "baseline":
        result = system_ci.analyse(args.root)
        system_ci.write_baseline(args.output, result)
        print(f"Baseline written: {Path(args.output)}")
        print(f"Accepted findings: {len(result['report'].get('findings') or [])}")
        return 0

    if args.command == "impact":
        result = system_ci.analyse(args.root)
        impacted = system_ci.impact(result, args.query, depth=args.depth)
        if args.as_json:
            print(json.dumps(impacted, ensure_ascii=False, indent=2))
        else:
            print(system_ci.render_impact(impacted), end="")
        return 0

    baseline = system_ci.load_baseline(args.baseline)
    result = system_ci.analyse(args.root, baseline=baseline)
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(system_ci.render_markdown(result), end="")
    return 1 if system_ci.should_fail(result, args.fail_on) else 0


if __name__ == "__main__":
    raise SystemExit(main())
