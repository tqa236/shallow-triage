"""Command-line entry point.

``shallow-triage candidates owner/repo`` prints ranked issues as JSON;
``shallow-triage page owner/repo -o triage.html`` renders them as HTML;
``shallow-triage site owner/a owner/b -o site/`` renders an index plus one
page per repository.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from shallow_triage import gh
from shallow_triage.issues import fetch_open_issues, rank, score_issue
from shallow_triage.page import render_page
from shallow_triage.site import build_site


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-n", "--limit", type=int, default=10)
    parser.add_argument(
        "--include-linked",
        action="store_true",
        help="keep issues that already have an open linked PR",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="shallow-triage")
    sub = parser.add_subparsers(dest="command", required=True)
    cand = sub.add_parser("candidates", help="list the most triageable open issues")
    cand.add_argument("repo", help="owner/name")
    _add_common(cand)
    page = sub.add_parser("page", help="render the triage page as HTML")
    page.add_argument("repo", help="owner/name")
    _add_common(page)
    page.add_argument(
        "-o", "--output", type=Path, help="write to this file instead of stdout"
    )
    site = sub.add_parser("site", help="render an index plus one page per repo")
    site.add_argument("repos", nargs="+", metavar="repo", help="owner/name")
    _add_common(site)
    site.add_argument("-o", "--output", type=Path, required=True, help="directory")
    args = parser.parse_args(argv)

    if args.command == "site":
        failed = build_site(
            args.repos,
            args.output,
            limit=args.limit,
            include_linked=args.include_linked,
            generated_at=datetime.now(UTC),
        )
        return 1 if failed else 0

    try:
        issues = fetch_open_issues(args.repo)
    except (gh.GhError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    ranked = rank(issues, args.limit, include_linked=args.include_linked)

    if args.command == "page":
        html = render_page(
            args.repo,
            ranked,
            total_open=len(issues),
            generated_at=datetime.now(UTC),
        )
        if args.output:
            args.output.write_text(html, encoding="utf-8")
        else:
            sys.stdout.write(html)
        return 0

    out = [
        {
            "number": i.number,
            "title": i.title,
            "url": i.url,
            "score": score_issue(i),
            "linked_prs": list(i.linked_prs),
        }
        for i in ranked
    ]
    json.dump(out, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
