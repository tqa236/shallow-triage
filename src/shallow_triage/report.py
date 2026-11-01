"""Gather everything shown on one repository's triage page."""

from dataclasses import dataclass, field

from shallow_triage import local
from shallow_triage.cache import IssueCache
from shallow_triage.config import RepoConfig
from shallow_triage.issues import Issue, fetch_open_issues, rank
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
    total_open: int
    candidates: list[Issue]
    fixes: list[FixEntry] = field(default_factory=list)
    closed_fixes: list[FixBranch] = field(default_factory=list)
    closable: list[Closable] = field(default_factory=list)
    base: Commit | None = None


def build_report(
    config: RepoConfig,
    *,
    limit: int | None,
    include_linked: bool,
    cache: IssueCache | None = None,
    fetch: bool = False,
) -> RepoReport:
    """Fetch issues from GitHub and, if configured, read the local clone.

    With ``fetch``, the clone's base remote is fetched first.
    """
    if fetch and config.path is not None:
        local.fetch_base(config.path, config.base)
    issues = fetch_open_issues(config.name, cache)
    by_number = {i.number: i for i in issues}
    fixes: list[FixEntry] = []
    closed: list[FixBranch] = []
    commits: dict[int, list[Commit]] = {}
    base = None
    if config.path is not None:
        base = local.base_commit(config.path, config.base)
        for branch in local.fix_branches(config.path, config.base, config.branches):
            if branch.issue in by_number:
                fixes.append(FixEntry(branch, by_number[branch.issue]))
            else:
                closed.append(branch)
        fixes.sort(key=lambda f: f.branch.commits[-1].date, reverse=True)
        commits = local.closing_commits(
            config.path, config.base, set(by_number), since=config.since
        )

    # PRs merged into forks don't fix anything upstream
    own_prs = f"https://github.com/{config.name}/pull/"
    closable = []
    for i in issues:
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
        [i for i in issues if i.number not in skip],
        limit,
        include_linked=include_linked,
    )
    return RepoReport(config, len(issues), candidates, fixes, closed, closable, base)
