"""Gather everything shown on one repository's triage page."""

from dataclasses import dataclass, field

from shallow_triage import local
from shallow_triage.cache import IssueCache
from shallow_triage.config import RepoConfig
from shallow_triage.issues import Issue, fetch_issues, rank
from shallow_triage.local import Commit, FixBranch


@dataclass(frozen=True)
class FixEntry:
    """A local fix branch whose issue is still open."""

    branch: FixBranch
    issue: Issue


@dataclass(frozen=True)
class Closable:
    """An open issue that a commit or merged PR on the base claims to fix."""

    issue: Issue
    commits: tuple[Commit, ...] = ()
    merged_prs: tuple[tuple[str, str], ...] = ()  # (url, merge date)

    @property
    def latest(self) -> str:
        """Date of the newest piece of evidence."""
        dates = [c.date for c in self.commits] + [d for _, d in self.merged_prs]
        return max(dates, default="")


@dataclass(frozen=True)
class RepoReport:
    config: RepoConfig
    total_open: int  # open issues on GitHub
    candidates: list[Issue]
    fixes: list[FixEntry] = field(default_factory=list)
    closed_fixes: list[FixBranch] = field(default_factory=list)
    closable: list[Closable] = field(default_factory=list)
    base: Commit | None = None
    known_open: int | None = None  # open issues fetched so far, if not all


def build_report(
    config: RepoConfig,
    *,
    limit: int | None,
    include_linked: bool,
    cache: IssueCache | None = None,
    fetch: bool = False,
    batch: int | None = 100,
) -> RepoReport:
    """Read the local clone, if configured, then bring the issues up to date.

    With ``fetch``, the clone's base remote is fetched first. ``batch`` is how
    many more open issues (and issues cited by fixing commits) to fetch.
    """
    branches: list[FixBranch] = []
    commits: dict[int, list[Commit]] = {}
    base = None
    if config.path is not None:
        if fetch:
            local.fetch_base(config.path, config.base)
        base = local.base_commit(config.path, config.base)
        branches = local.fix_branches(config.path, config.base, config.branches)
        commits = local.closing_commits(config.path, config.base, since=config.since)

    found = fetch_issues(
        config.name,
        cache,
        batch=batch,
        wanted=tuple(b.issue for b in branches),
        extra=tuple(commits),
    )
    by_number = {i.number: i for i in found.issues}
    fixes = [FixEntry(b, by_number[b.issue]) for b in branches if b.issue in by_number]
    fixes.sort(key=lambda f: f.branch.commits[-1].date, reverse=True)
    closed = [b for b in branches if b.issue not in by_number]

    # PRs merged into forks don't fix anything upstream
    own_prs = f"https://github.com/{config.name}/pull/"
    closable = []
    for i in found.issues:
        merged = tuple(
            (url, day)
            for url, day in i.merged_prs
            if url.startswith(own_prs) and (not config.since or day >= config.since)
        )
        if i.number in commits or merged:
            closable.append(Closable(i, tuple(commits.get(i.number, ())), merged))
    closable.sort(key=lambda c: c.latest, reverse=True)
    skip = {f.issue.number for f in fixes} | {c.issue.number for c in closable}
    candidates = rank(
        [i for i in found.issues if i.number not in skip],
        limit,
        include_linked=include_linked,
    )
    return RepoReport(
        config,
        found.total_open,
        candidates,
        fixes,
        closed,
        closable,
        base,
        None if found.complete else len(found.issues),
    )
