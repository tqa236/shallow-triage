"""Command-line entry point.

``shallow-triage candidates owner/repo`` prints ranked issues as JSON;
``shallow-triage page owner/repo -o triage.html`` renders them as HTML;
``shallow-triage site owner/a owner/b -o site/`` renders an index plus one
page per repository. ``site --config triage.toml`` also reads fix branches
and fixing commits from local clones (see :mod:`shallow_triage.config`).
"""

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from shallow_triage import gh
from shallow_triage.cache import IssueCache, default_cache_dir
from shallow_triage.config import RepoConfig, load_config
from shallow_triage.issues import fetch_open_issues, rank, score_issue
from shallow_triage.page import render_page
from shallow_triage.report import build_report
from shallow_triage.site import build_site


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-n", "--limit", type=int, default=10)
    parser.add_argument(
        "--include-linked",
        action="store_true",
        help="keep issues that already have an open linked PR",
    )
    cache = parser.add_argument_group("cache")
    cache.add_argument(
        "--cache-dir",
        type=Path,
        default=default_cache_dir(),
        help="where fetched issues are kept (default: %(default)s)",
    )
    cache.add_argument(
        "--no-cache", action="store_true", help="always fetch everything"
    )
    cache.add_argument(
        "--refresh", action="store_true", help="refetch everything and update the cache"
    )
    cache.add_argument(
        "--max-age",
        type=float,
        default=24,
        metavar="HOURS",
        help="refetch everything when the last full fetch is older (default: 24)",
    )


def _cache(args: argparse.Namespace) -> IssueCache | None:
    if args.no_cache:
        return None
    return IssueCache(args.cache_dir, timedelta(hours=args.max_age), args.refresh)


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
    site.add_argument("repos", nargs="*", metavar="repo", help="owner/name")
    site.add_argument(
        "-c", "--config", type=Path, help="TOML file listing repos and local clones"
    )
    _add_common(site)
    site.add_argument("-o", "--output", type=Path, required=True, help="directory")
    site.add_argument(
        "--fetch",
        action="store_true",
        help="git fetch each clone's base remote first (changes only remote refs)",
    )
    args = parser.parse_args(argv)

    if args.command == "site":
        repos = [RepoConfig(name) for name in args.repos]
        if args.config:
            try:
                repos += load_config(args.config)
            except (OSError, ValueError) as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
        if not repos:
            parser.error("site: give at least one repo or --config")
        failed = build_site(
            repos,
            args.output,
            limit=args.limit,
            include_linked=args.include_linked,
            generated_at=datetime.now(UTC),
            cache=_cache(args),
            fetch=args.fetch,
        )
        return 1 if failed else 0

    if args.command == "page":
        try:
            report = build_report(
                RepoConfig(args.repo),
                limit=args.limit,
                include_linked=args.include_linked,
                cache=_cache(args),
            )
        except (gh.GhError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        html = render_page(report, generated_at=datetime.now(UTC))
        if args.output:
            args.output.write_text(html, encoding="utf-8")
        else:
            sys.stdout.write(html)
        return 0

    try:
        issues = fetch_open_issues(args.repo, _cache(args))
    except (gh.GhError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    ranked = rank(issues, args.limit, include_linked=args.include_linked)
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
