"""CLI for Orbita deterministic System Analysis CI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent import system_ci, system_ci_pr


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orbita-ci",
        description="Deterministic requirements/architecture consistency checks.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="analyse a directory and return a CI exit code")
    check.add_argument("root", nargs="?", default=".")
    check.add_argument("--baseline", default=None)
    check.add_argument(
        "--fail-on",
        choices=["blocker", "major", "minor", "none"],
        default="major",
    )
    check.add_argument("--json", action="store_true", dest="as_json")

    baseline = sub.add_parser("baseline", help="accept current deterministic findings")
    baseline.add_argument("root", nargs="?", default=".")
    baseline.add_argument("--output", default=None)

    impact = sub.add_parser("impact", help="show deterministic neighbours of an artifact")
    impact.add_argument("query")
    impact.add_argument("root", nargs="?", default=".")
    impact.add_argument("--depth", type=int, default=2)
    impact.add_argument("--json", action="store_true", dest="as_json")

    pr_review = sub.add_parser(
        "pr-review",
        help="scope deterministic analysis to files changed by a pull request",
    )
    pr_review.add_argument("root")
    pr_review.add_argument("--changed-file-list", required=True)
    pr_review.add_argument("--baseline", default=None)
    pr_review.add_argument("--depth", type=int, default=2)
    pr_review.add_argument(
        "--fail-on",
        choices=["blocker", "major", "minor", "none"],
        default="major",
    )
    pr_review.add_argument("--output")
    pr_review.add_argument("--json", action="store_true", dest="as_json")
    return parser


def _baseline_path(root: str, explicit: str | None) -> Path:
    return Path(explicit) if explicit else Path(root) / system_ci.BASELINE_FILENAME


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    if args.command == "baseline":
        result = system_ci.analyse(args.root)
        output = Path(args.output) if args.output else Path(args.root) / system_ci.BASELINE_FILENAME
        system_ci.write_baseline(output, result)
        print(f"Baseline written: {output}")
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

    if args.command == "pr-review":
        changed = [
            line.strip()
            for line in Path(args.changed_file_list).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        baseline = system_ci.load_baseline(_baseline_path(args.root, args.baseline))
        result = system_ci_pr.review(
            args.root,
            changed,
            baseline=baseline,
            depth=args.depth,
        )
        rendered = (
            json.dumps(result, ensure_ascii=False, indent=2)
            if args.as_json
            else system_ci_pr.render_markdown(result)
        )
        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(rendered + ("" if rendered.endswith("\n") else "\n"), encoding="utf-8")
        else:
            print(rendered, end="" if rendered.endswith("\n") else "\n")
        return 1 if system_ci_pr.should_fail(result, args.fail_on) else 0

    baseline = system_ci.load_baseline(_baseline_path(args.root, args.baseline))
    result = system_ci.analyse(args.root, baseline=baseline)
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(system_ci.render_markdown(result), end="")
    return 1 if system_ci.should_fail(result, args.fail_on) else 0


if __name__ == "__main__":
    raise SystemExit(main())
