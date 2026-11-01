"""Command-line entry point: ``shallow-triage candidates owner/repo``."""

import argparse
import json
import sys

from shallow_triage import gh
from shallow_triage.issues import fetch_open_issues, rank, score_issue


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="shallow-triage")
    sub = parser.add_subparsers(dest="command", required=True)
    cand = sub.add_parser("candidates", help="list the most triageable open issues")
    cand.add_argument("repo", help="owner/name")
    cand.add_argument("-n", "--limit", type=int, default=10)
    args = parser.parse_args(argv)

    try:
        issues = rank(fetch_open_issues(args.repo), args.limit)
    except gh.GhError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    out = [
        {"number": i.number, "title": i.title, "url": i.url, "score": score_issue(i)}
        for i in issues
    ]
    json.dump(out, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
