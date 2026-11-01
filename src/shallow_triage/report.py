"""The data layer: one repository's triage report.

:func:`build_report` turns the inputs (config, GitHub issues, local clone)
into a :class:`RepoReport`. A report holds everything the outputs show,
already computed (scores, the base, the test command), so an output only
formats it, and a saved report can be rendered again without GitHub or git.

Reports are saved as JSON with :func:`save_report` and read back with
:func:`load_report`. To keep saved reports loadable, give every new field a
default and don't rename or retype existing ones; see
:mod:`shallow_triage.serialize`.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from shallow_triage import local, serialize
from shallow_triage.cache import IssueCache
from shallow_triage.config import RepoConfig
from shallow_triage.issues import Issue, fetch_issues, rank, score_reasons
from shallow_triage.local import Commit, FixBranch
from shallow_triage.store import read_json, repo_path, write_private_json

# Bump only for a change that old files can't follow by the rules above.
REPORT_VERSION = 1


@dataclass(frozen=True)
class Candidate:
    """An open issue without a fix, with the score that ranked it."""

    issue: Issue
    score: int
    reasons: tuple[tuple[int, str], ...] = ()  # (points, reason)


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
    repo: str  # owner/name
    generated_at: datetime
    total_open: int  # open issues on GitHub
    candidates: list[Candidate] = field(default_factory=list)
    fixes: list[FixEntry] = field(default_factory=list)
    closed_fixes: list[FixBranch] = field(default_factory=list)
    closable: list[Closable] = field(default_factory=list)
    known_open: int | None = None  # open issues fetched so far, if not all
    # From the config, for the commands shown with each fix. The clone's
    # location is deliberately not part of a report: reports may be shared.
    base: str | None = None  # e.g. upstream/main; None without a local clone
    base_commit: Commit | None = None
    test: str | None = None  # test command template with {files}


def build_report(
    config: RepoConfig,
    *,
    limit: int | None,
    include_linked: bool,
    generated_at: datetime,
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
    base_commit = None
    if config.path is not None:
        if fetch:
            local.fetch_base(config.path, config.base)
        base_commit = local.base_commit(config.path, config.base)
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
    ranked = rank(
        [i for i in found.issues if i.number not in skip],
        limit,
        include_linked=include_linked,
    )
    candidates = []
    for issue in ranked:
        reasons = tuple(score_reasons(issue))
        candidates.append(Candidate(issue, sum(p for p, _ in reasons), reasons))
    local_clone = config.path is not None
    return RepoReport(
        repo=config.name,
        generated_at=generated_at,
        total_open=found.total_open,
        candidates=candidates,
        fixes=fixes,
        closed_fixes=closed,
        closable=closable,
        known_open=None if found.complete else len(found.issues),
        base=config.base if local_clone else None,
        base_commit=base_commit,
        test=config.test if local_clone else None,
    )


def save_report(data_dir: Path, report: RepoReport) -> Path:
    """Write ``report`` to ``data_dir/<owner>/<name>.json`` (owner-only)."""
    path = repo_path(data_dir, report.repo)
    data = {"version": REPORT_VERSION, "report": serialize.to_data(report)}
    write_private_json(data_dir, path, data)
    return path


def load_report(path: Path) -> RepoReport:
    """Read a report saved by :func:`save_report`.

    Raises ValueError, naming the file, if it isn't a report this version
    can read.
    """
    try:
        data = read_json(path)
        if data["version"] != REPORT_VERSION:
            raise ValueError(f"format {data['version']}, expected {REPORT_VERSION}")
        return serialize.from_data(RepoReport, data["report"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"{path}: not a readable report ({exc})") from exc


def report_paths(data_dir: Path) -> list[Path]:
    """The saved report files under ``data_dir``, sorted by repository."""
    return sorted(data_dir.glob("*/*.json"))
