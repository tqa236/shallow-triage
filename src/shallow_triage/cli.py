"""Command-line entry point.

Input -> data -> output:

- ``collect`` reads the inputs (repos or ``--config``, GitHub, local
  clones) and saves one report per repository under ``--data``.
- ``render`` turns the saved reports into an HTML site; no GitHub, no git.
- ``site`` does both. ``page`` and ``candidates`` are one-repository
  shortcuts that also save their report.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from shallow_triage import gh
from shallow_triage.cache import IssueCache, default_cache_dir
from shallow_triage.config import RepoConfig, load_config
from shallow_triage.jsonout import candidates_json
from shallow_triage.local import LocalError
from shallow_triage.page import render_page
from shallow_triage.report import (
    build_report,
    load_report,
    report_paths,
    save_report,
)
from shallow_triage.site import build_site, collect, render_site
from shallow_triage.store import default_data_dir


def _add_data(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--data",
        type=Path,
        default=default_data_dir(),
        help="where reports are saved (default: %(default)s)",
    )


def _add_collect(parser: argparse.ArgumentParser) -> None:
    """Options for commands that read the inputs."""
    parser.add_argument(
        "-n", "--limit", type=int, default=10, help="candidates per repository"
    )
    parser.add_argument(
        "--include-linked",
        action="store_true",
        help="keep issues that already have an open linked PR",
    )
    _add_data(parser)
    fetching = parser.add_argument_group("fetching and cache")
    size = fetching.add_mutually_exclusive_group()
    size.add_argument(
        "--batch",
        type=int,
        default=100,
        metavar="N",
        help="fetch N more open issues this run, newest first (default: 100)",
    )
    size.add_argument(
        "--all", action="store_true", help="fetch every open issue not cached yet"
    )
    fetching.add_argument(
        "--cache-dir",
        type=Path,
        default=default_cache_dir(),
        help="where fetched issues are kept (default: %(default)s)",
    )
    fetching.add_argument(
        "--no-cache",
        action="store_true",
        help="don't read or write the cache; each run starts from scratch",
    )
    fetching.add_argument(
        "--refresh", action="store_true", help="discard the cache and start over"
    )


def _add_repos(parser: argparse.ArgumentParser, *, fetch: bool) -> None:
    parser.add_argument("repos", nargs="*", metavar="repo", help="owner/name")
    parser.add_argument(
        "-c", "--config", type=Path, help="TOML file listing repos and local clones"
    )
    if fetch:
        parser.add_argument(
            "--fetch",
            action="store_true",
            help="git fetch each clone's base remote first (changes only remote refs)",
        )


def _cache(args: argparse.Namespace) -> IssueCache | None:
    if args.no_cache:
        return None
    return IssueCache(args.cache_dir, args.refresh)


def _collect_options(args: argparse.Namespace) -> dict:
    return {
        "limit": args.limit,
        "include_linked": args.include_linked,
        "generated_at": datetime.now(UTC),
        "cache": _cache(args),
        "batch": None if args.all else args.batch,
    }


def _repos(args: argparse.Namespace, parser: argparse.ArgumentParser) -> list:
    repos = [RepoConfig(name) for name in args.repos]
    if args.config:
        repos += load_config(args.config)
    if not repos:
        parser.error(f"{args.command}: give at least one repo or --config")
    return repos


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="shallow-triage")
    sub = parser.add_subparsers(dest="command", required=True)

    site = sub.add_parser("site", help="collect, then render the site")
    _add_repos(site, fetch=True)
    _add_collect(site)
    site.add_argument("-o", "--output", type=Path, required=True, help="directory")

    coll = sub.add_parser("collect", help="build and save reports, no HTML")
    _add_repos(coll, fetch=True)
    _add_collect(coll)

    rend = sub.add_parser(
        "render", help="render saved reports as a site, without GitHub or git"
    )
    _add_repos(rend, fetch=False)
    _add_data(rend)
    rend.add_argument("-o", "--output", type=Path, required=True, help="directory")

    page = sub.add_parser("page", help="one repository's page as HTML")
    page.add_argument("repo", help="owner/name")
    _add_collect(page)
    page.add_argument(
        "-o", "--output", type=Path, help="write to this file instead of stdout"
    )

    cand = sub.add_parser("candidates", help="one repository's candidates as JSON")
    cand.add_argument("repo", help="owner/name")
    _add_collect(cand)

    args = parser.parse_args(argv)
    try:
        return _run(args, parser)
    except (gh.GhError, LocalError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.command in ("site", "collect"):
        repos = _repos(args, parser)
        options = _collect_options(args) | {"fetch": args.fetch}
        if args.command == "collect":
            _, failed = collect(repos, args.data, **options)
        else:
            failed = build_site(repos, args.output, args.data, **options)
        return 1 if failed else 0

    if args.command == "render":
        # with repos or a config: those, in that order; else every saved one
        wanted = (
            [c.name for c in _repos(args, parser)]
            if args.repos or args.config
            else None
        )
        reports, failed = [], False
        for path in report_paths(args.data):
            try:
                report = load_report(path)
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                failed = True
                continue
            if wanted is None or report.repo in wanted:
                reports.append(report)
        if wanted is not None:
            for name in dict.fromkeys(wanted):
                if name not in {r.repo for r in reports}:
                    print(
                        f"error: {name}: no saved report; run collect", file=sys.stderr
                    )
                    failed = True
            reports.sort(key=lambda r: wanted.index(r.repo))
        render_site(reports, args.output, generated_at=datetime.now(UTC))
        return 1 if failed else 0

    # page, candidates: one repository from GitHub only
    report = build_report(RepoConfig(args.repo), **_collect_options(args))
    save_report(args.data, report)
    if args.command == "page":
        html = render_page(report)
        if args.output:
            args.output.write_text(html, encoding="utf-8")
        else:
            sys.stdout.write(html)
    else:
        json.dump(candidates_json(report), sys.stdout, indent=2)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
