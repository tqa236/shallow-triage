"""Command-line entry point.

Input -> data -> output:

- ``collect`` reads the inputs (repos or a config, GitHub, local clones,
  recorded findings and safe marks) and saves one report per repository.
- ``render`` turns the saved reports into an HTML site; no GitHub, no git.
- ``site`` does both. ``page`` and ``candidates`` are one-repository
  shortcuts that also save their report (``candidates`` only when unfiltered).

A repository named on the command line uses its config entry, if it has one.

Working on issues, in a repository's workdir with its venv (from the config):

- ``check`` checks that a repository's config works;
- ``fix`` starts a fix branch, ``test`` runs the tests for it;
- ``run`` runs a reproduction script, optionally on another commit;
- ``bisect`` finds the commit that fixed an issue and records it;
- ``closable`` adds, removes or lists recorded findings by hand;
- ``safe`` marks issues as reviewed, and ``repro`` takes code from those.

Without ``--config`` or repositories, the config at
``~/.config/shallow-triage/triage.toml`` is used if it exists.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from shallow_triage import gh
from shallow_triage.cache import IssueCache, default_cache_dir
from shallow_triage.config import (
    RepoConfig,
    default_config_path,
    find_repo,
    load_config,
    lookup_repo,
)
from shallow_triage.findings import (
    FIXED,
    STATUSES,
    Finding,
    Findings,
    default_findings_dir,
)
from shallow_triage.issues import Issue, fetch_issue, valid_repo
from shallow_triage.jsonout import candidates_json
from shallow_triage.local import LocalError
from shallow_triage.marks import SAFE, Marks, code_blocks, default_marks_dir
from shallow_triage.page import render_page
from shallow_triage.report import (
    build_report,
    load_report,
    report_paths,
    save_report,
)
from shallow_triage.site import build_site, collect, render_site
from shallow_triage.store import default_data_dir
from shallow_triage.workflow import (
    bisect,
    check_config,
    classify,
    run_script,
    run_tests,
    start_fix,
)

COMMIT_TEMPLATE = """\
<area>: <what the fix does, imperative, under 72 characters>

Problem: what goes wrong, with the smallest input that shows it.
Solution: what changed and why. Name any approach you rejected and why.
Test: what the new test checks.

Fixes #{issue}
"""


# -- arguments ---------------------------------------------------------------


def _add_data(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--data",
        type=Path,
        default=default_data_dir(),
        help="where reports are saved (default: %(default)s)",
    )


def _add_config(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        help=f"TOML file of repos and local clones (default: {default_config_path()})",
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
    _add_config(parser)
    if fetch:
        parser.add_argument(
            "--fetch",
            action="store_true",
            help="git fetch each clone's base remote first (changes only remote refs)",
        )


def _add_repo(parser: argparse.ArgumentParser, *, issue: bool = True) -> None:
    parser.add_argument("repo", help="owner/name")
    if issue:
        parser.add_argument("issue", type=int, help="issue number")


def _parser() -> argparse.ArgumentParser:
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
    _add_repo(page, issue=False)
    _add_config(page)
    _add_collect(page)
    page.add_argument(
        "-o", "--output", type=Path, help="write to this file instead of stdout"
    )

    cand = sub.add_parser("candidates", help="one repository's candidates as JSON")
    _add_repo(cand, issue=False)
    _add_config(cand)
    _add_collect(cand)
    filters = cand.add_argument_group(
        "filters (applied before -n; a filtered list isn't saved)"
    )
    filters.add_argument(
        "--label",
        action="append",
        default=[],
        metavar="NAME",
        help="only issues with this label (repeat: all of them)",
    )
    filters.add_argument(
        "--exclude-label",
        action="append",
        default=[],
        metavar="NAME",
        help="leave out issues with this label (repeatable)",
    )
    filters.add_argument(
        "--max-comments", type=int, metavar="N", help="at most N comments"
    )
    filters.add_argument(
        "--unassigned", action="store_true", help="only issues nobody is assigned to"
    )

    check = sub.add_parser(
        "check", help="check that repositories' configs work (clone, base, venv, run)"
    )
    _add_repos(check, fetch=False)
    check.add_argument(
        "--script",
        type=Path,
        help="run this script (e.g. one that imports the project) instead of an"
        " empty one; it must exit 0",
    )

    fix = sub.add_parser(
        "fix", help="start triage/issue-N from the base in the workdir"
    )
    _add_repo(fix)
    _add_config(fix)

    test = sub.add_parser(
        "test", help="build and run the tests the current branch changed"
    )
    _add_repo(test, issue=False)
    _add_config(test)
    test.add_argument("--no-build", action="store_true", help="skip the build step")

    run = sub.add_parser(
        "run", help="run a reproduction script in the workdir with the venv"
    )
    _add_repo(run, issue=False)
    _add_config(run)
    run.add_argument("script", type=Path, help="the script, outside the clone")
    run.add_argument(
        "--at",
        metavar="REV",
        help="on this commit (e.g. the base), then back to the current branch",
    )
    build = run.add_mutually_exclusive_group()
    build.add_argument(
        "--build",
        action="store_true",
        default=None,
        help="build first (the default with --at)",
    )
    build.add_argument(
        "--no-build",
        dest="build",
        action="store_false",
        default=None,
        help="don't build first",
    )
    run.add_argument("--timeout", type=float, help="seconds per command")

    bis = sub.add_parser(
        "bisect", help="find the commit that fixed an issue, and record it"
    )
    _add_repo(bis)
    _add_config(bis)
    bis.add_argument(
        "--old", required=True, help="a commit or tag where the issue shows"
    )
    bis.add_argument("--new", help="where it is fixed (default: the config's base)")
    bis.add_argument(
        "--script",
        type=Path,
        required=True,
        help="exits 0 when fixed, 125 when it can't tell, else when broken",
    )
    bis.add_argument(
        "--timeout", type=float, default=600, help="seconds per step (default: 600)"
    )
    bis.add_argument(
        "--no-record", action="store_true", help="don't record the finding"
    )

    clo = sub.add_parser("closable", help="recorded findings: add, remove, list")
    clo_sub = clo.add_subparsers(dest="action", required=True)
    add = clo_sub.add_parser("add", help="record an issue as fixed or not reproducible")
    _add_repo(add)
    add.add_argument("--status", choices=STATUSES, default=FIXED)
    add.add_argument("--commit", help="the commit that fixed it")
    add.add_argument("--pr", help="URL of the PR that fixed it")
    add.add_argument("--method", default="", help="how this was checked")
    add.add_argument("--note", default="")
    _add_repo(clo_sub.add_parser("remove", help="forget a finding"))
    _add_repo(clo_sub.add_parser("list", help="list findings"), issue=False)

    safe = sub.add_parser("safe", help="issues reviewed as safe to take code from")
    safe_sub = safe.add_subparsers(dest="action", required=True)
    for action, text in [
        ("mark", "mark issues as reviewed and safe, as they are now"),
        ("unmark", "remove the marks"),
    ]:
        p = safe_sub.add_parser(action, help=text)
        p.add_argument("repo", help="owner/name")
        p.add_argument("issues", type=int, nargs="+", metavar="issue")
    _add_repo(
        safe_sub.add_parser("list", help="list marks and whether they still hold"),
        issue=False,
    )

    repro = sub.add_parser(
        "repro", help="write the code blocks of an issue marked safe to a script"
    )
    _add_repo(repro)
    repro.add_argument(
        "-o", "--output", type=Path, help="write to this file instead of stdout"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return COMMANDS[args.command](args, parser)
    except (gh.GhError, LocalError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


# -- helpers -----------------------------------------------------------------


def _cache(args: argparse.Namespace) -> IssueCache | None:
    if args.no_cache:
        return None
    return IssueCache(args.cache_dir, args.refresh)


def _collect_options(args: argparse.Namespace) -> dict:
    return {
        "limit": args.limit,
        "include_linked": args.include_linked,
        "generated_at": _now(),
        "cache": _cache(args),
        "batch": None if args.all else args.batch,
    }


def _issue_filter(args: argparse.Namespace):
    """The candidates filter from the options, or None if there is none."""
    wanted = {name.lower() for name in getattr(args, "label", [])}
    unwanted = {name.lower() for name in getattr(args, "exclude_label", [])}
    max_comments = getattr(args, "max_comments", None)
    unassigned = getattr(args, "unassigned", False)
    if not (wanted or unwanted or max_comments is not None or unassigned):
        return None

    def keep(issue: Issue) -> bool:
        return (
            wanted <= issue.labels
            and not unwanted & issue.labels
            and (max_comments is None or issue.comments <= max_comments)
            and not (unassigned and issue.assigned)
        )

    return keep


def _configured(args: argparse.Namespace) -> list[RepoConfig]:
    """The repos in ``--config``, or in the default config if it exists."""
    path = args.config
    if path is None and default_config_path().exists():
        path = default_config_path()
    return load_config(path) if path else []


def _repos(args: argparse.Namespace, parser: argparse.ArgumentParser) -> list:
    """The repos named, each with its config entry if it has one; with
    ``--config`` or no names, also every configured repo."""
    configured = _configured(args)
    repos = [lookup_repo(configured, n) or RepoConfig(n) for n in args.repos]
    if args.config or not repos:
        repos += [c for c in configured if c.name not in args.repos]
    if not repos:
        parser.error(f"{args.command}: give at least one repo or --config")
    return repos


def _one(args: argparse.Namespace) -> RepoConfig:
    return find_repo(_configured(args), args.repo)


def _one_or_bare(args: argparse.Namespace) -> RepoConfig:
    """The repo's config entry, or just its name if it has none."""
    name = _checked_repo(args.repo)
    return lookup_repo(_configured(args), name) or RepoConfig(name)


def _print_results(results: list) -> None:
    for result in results:
        print(f"$ {result.command}")
        print(result.output, end="" if result.output.endswith("\n") else "\n")


def _checked_repo(name: str) -> str:
    if not valid_repo(name):
        raise ValueError(f"expected owner/name, got {name!r}")
    return name


def _now() -> datetime:
    return datetime.now(UTC)


# -- commands ----------------------------------------------------------------


def _site(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    repos = _repos(args, parser)
    options = _collect_options(args) | {
        "fetch": args.fetch,
        "findings_dir": default_findings_dir(),
        "marks_dir": default_marks_dir(),
    }
    if args.command == "collect":
        _, failed = collect(repos, args.data, **options)
    else:
        failed = build_site(repos, args.output, args.data, **options)
    return 1 if failed else 0


def _render(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    # with repos or a config: those, in that order; else every saved one
    wanted = (
        [c.name for c in _repos(args, parser)] if args.repos or args.config else None
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
                print(f"error: {name}: no saved report; run collect", file=sys.stderr)
                failed = True
        reports.sort(key=lambda r: wanted.index(r.repo))
    render_site(reports, args.output, generated_at=_now())
    return 1 if failed else 0


def _single(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """page, candidates: one repository, with its local clone if configured."""
    config = _one_or_bare(args)
    keep = _issue_filter(args)
    report = build_report(
        config,
        **_collect_options(args),
        findings=Findings(default_findings_dir(), config.name),
        marks=Marks(default_marks_dir(), config.name),
        keep=keep,
    )
    if keep is None:
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


def _fix(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    config = _one(args)
    branch = start_fix(config, args.issue)
    print(f"On {branch} in {config.work_path}, from {config.base}.")
    print("Fix it, add a test, run `shallow-triage test`, then commit once with:\n")
    print(COMMIT_TEMPLATE.format(issue=args.issue))
    return 0


def _test(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    results = run_tests(_one(args), build=not args.no_build)
    _print_results(results)
    return results[-1].returncode


def _run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    config = _one(args)
    results = run_script(
        config, args.script, at=args.at, build=args.build, timeout=args.timeout
    )
    _print_results(results)
    sys.stdout.flush()  # the verdict on stderr comes after the output
    last = results[-1]
    if config.build and last.command == config.build:
        print("build failed", file=sys.stderr)
    else:
        state, why = classify(last)
        where = args.at or "the current checkout"
        print(f"{state} at {where} ({why})", file=sys.stderr)
    return last.returncode


def _check(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    failed = False
    for config in _repos(args, parser):
        print(config.name)
        for check in check_config(config, args.script):
            mark = "ok  " if check.ok else "FAIL"
            print(f"  {mark} {check.name}: {check.detail}".rstrip(": "))
            failed |= not check.ok
    return 1 if failed else 0


def _bisect(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    config = _one(args)
    result = bisect(
        config, args.script, old=args.old, new=args.new, timeout=args.timeout
    )
    new = args.new or config.base
    if result.status == "still broken":
        print(f"#{args.issue} still shows on {new}: not closable.")
        return 1
    if result.status == "unclear":
        print("Some commits couldn't be tested; the fix is one of:")
        print("\n".join(f"  {sha}" for sha in result.candidates))
        return 1
    if result.status == "not reproducible":
        print(f"#{args.issue} doesn't show at {args.old} either: try an older --old.")
        return 1
    print(f"#{args.issue} was fixed by {result.first_fixed}")
    if result.pr:
        print(f"from {result.pr}")
    if not args.no_record:
        findings = Findings(default_findings_dir(), config.name)
        findings.add(
            Finding(
                issue=args.issue,
                status=FIXED,
                recorded_at=_now(),
                commit=result.first_fixed,
                pr=result.pr,
                method=f"bisect {args.old}..{new}, {len(result.tested)} commits tested",
            )
        )
        findings.save()
        print("Recorded; it shows as verified closable after the next collect.")
    return 0


def _closable(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    findings = Findings(default_findings_dir(), _checked_repo(args.repo))
    if args.action == "add":
        if args.pr and not args.pr.startswith(f"https://github.com/{args.repo}/pull/"):
            raise ValueError(f"--pr must be a PR of {args.repo}")
        findings.add(
            Finding(
                issue=args.issue,
                status=args.status,
                recorded_at=_now(),
                commit=args.commit,
                pr=args.pr,
                method=args.method,
                note=args.note,
            )
        )
        findings.save()
    elif args.action == "remove":
        if not findings.remove(args.issue):
            raise ValueError(f"no finding for #{args.issue}")
        findings.save()
    else:
        for f in findings.items.values():
            by = f" by {f.commit[:10]}" if f.commit else ""
            print(f"#{f.issue}: {f.status}{by} ({f.recorded_at.date()}) {f.method}")
    return 0


def _safe(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    marks = Marks(default_marks_dir(), _checked_repo(args.repo))
    if args.action == "list":
        for number, mark in sorted(marks.marks.items()):
            issue = fetch_issue(args.repo, number)
            state = (
                "gone"
                if issue is None
                else marks.state(number, issue.title, issue.body)
            )
            print(f"#{number}: {state} (marked {mark.marked_at.date()}) {mark.title}")
        return 0
    for number in args.issues:
        if args.action == "unmark":
            if not marks.unmark(number):
                print(f"#{number} wasn't marked", file=sys.stderr)
            continue
        issue = fetch_issue(args.repo, number)
        if issue is None:
            raise ValueError(f"{args.repo} has no issue #{number}")
        marks.mark(number, issue.title, issue.body, _now())
        print(f"#{number} marked safe as it is now: {issue.title}")
    marks.save()
    return 0


def _repro(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    marks = Marks(default_marks_dir(), _checked_repo(args.repo))
    issue = fetch_issue(args.repo, args.issue)
    if issue is None:
        raise ValueError(f"{args.repo} has no issue #{args.issue}")
    state = marks.state(issue.number, issue.title, issue.body)
    if state != SAFE:
        reason = "isn't marked safe" if state is None else "changed since marked safe"
        raise ValueError(
            f"#{issue.number} {reason}; review it, then `shallow-triage safe mark`"
        )
    blocks = code_blocks(issue.body)
    if not blocks:
        raise ValueError(f"#{issue.number} has no Python code blocks")
    script = f"# From {issue.url}, marked safe.\n" + "\n".join(
        f"# --- block {i} ---\n{code}" for i, code in enumerate(blocks, 1)
    )
    if args.output:
        args.output.write_text(script, encoding="utf-8")
    else:
        sys.stdout.write(script)
    return 0


COMMANDS = {
    "site": _site,
    "collect": _site,
    "render": _render,
    "page": _single,
    "candidates": _single,
    "fix": _fix,
    "test": _test,
    "run": _run,
    "check": _check,
    "bisect": _bisect,
    "closable": _closable,
    "safe": _safe,
    "repro": _repro,
}


if __name__ == "__main__":
    raise SystemExit(main())
