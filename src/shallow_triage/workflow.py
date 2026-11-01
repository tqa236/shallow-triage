"""Tools for working on a repository's issues in its workdir: start a fix
branch, run the tests, and bisect for the commit that fixed an issue.

They all use the repository's config (workdir, venv, build, test, run) and
refuse to start when the workdir has uncommitted changes, so they never
mix with work in progress.
"""

import shutil
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from shallow_triage import gh, local
from shallow_triage.config import RepoConfig
from shallow_triage.runenv import (
    TIMEOUT,
    Result,
    run_command,
    run_script_command,
    test_command,
)

FIXED, BROKEN, SKIP = "fixed", "broken", "skip"
# Exit status a reproduction script uses for "can't tell here" (as git bisect).
SKIP_STATUS = 125


def _workdir(config: RepoConfig) -> Path:
    if config.work_path is None:
        raise ValueError(f"{config.name}: no 'path' in the config")
    return config.work_path


def ensure_clean(config: RepoConfig) -> Path:
    """The workdir, after checking it has no uncommitted changes."""
    workdir = _workdir(config)
    if local.git(workdir, "status", "--porcelain", "--untracked-files=no").strip():
        raise ValueError(
            f"{workdir} has uncommitted changes; commit or stash them first"
        )
    return workdir


def fix_branch_name(issue: int) -> str:
    return f"triage/issue-{issue}"


def start_fix(config: RepoConfig, issue: int) -> str:
    """Create and switch to ``triage/issue-<issue>`` from the base."""
    workdir = ensure_clean(config)
    name = fix_branch_name(issue)
    if local.git(workdir, "branch", "--list", name).strip():
        raise ValueError(f"branch {name} already exists")
    local.git(workdir, "switch", "--quiet", "--create", name, config.base)
    return name


def run_tests(config: RepoConfig, *, build: bool = True) -> list[Result]:
    """Build (if configured) and test what the current branch changed.

    Stops after a failing build. Returns the results in order.
    """
    workdir = _workdir(config)
    files = local.files_to_test(local.changed_files(workdir, config.base, "HEAD"))
    if not files:
        raise ValueError("the current branch changes nothing compared with the base")
    results = []
    if build and config.build:
        results.append(run_command(config, config.build))
        if results[-1].returncode != 0:
            return results
    results.append(run_command(config, test_command(config, files)))
    return results


@dataclass(frozen=True)
class BisectResult:
    status: str  # "fixed", "not reproducible", "still broken", "unclear"
    first_fixed: str | None = None  # sha of the first commit where it works
    candidates: tuple[str, ...] = ()  # when unclear: one of these fixed it
    tested: tuple[tuple[str, str], ...] = field(default=())  # (sha, state)
    pr: str | None = None


def find_first_fixed(
    commits: list[str], state_of: Callable[[str], str]
) -> tuple[str | None, list[str]]:
    """Binary search for the first commit in ``commits`` that is fixed.

    The commit before ``commits[0]`` is known broken and ``commits[-1]`` is
    known fixed. ``state_of`` returns FIXED, BROKEN or SKIP. Returns the first
    fixed commit, or None and the commits among which it is (when skipped
    commits leave the answer open).
    """
    lo, hi = -1, len(commits) - 1  # lo: last known broken, hi: first known fixed
    skipped: set[int] = set()
    while hi - lo > 1:
        untested = [i for i in range(lo + 1, hi) if i not in skipped]
        if not untested:
            break
        middle = (lo + hi) / 2
        i = min(untested, key=lambda j: (abs(j - middle), j))
        state = state_of(commits[i])
        if state == FIXED:
            hi = i
        elif state == BROKEN:
            lo = i
        else:
            skipped.add(i)
    if hi - lo == 1:
        return commits[hi], []
    return None, commits[lo + 1 : hi + 1]


def bisect(
    config: RepoConfig,
    script: Path,
    *,
    old: str,
    new: str | None = None,
    timeout: float = 600,
    log: Callable[[str], None] = lambda line: print(line, file=sys.stderr),
) -> BisectResult:
    """Find the first commit between ``old`` and ``new`` (default: the base)
    where ``script`` stops showing the bug.

    ``script`` exits 0 when the issue is fixed, 125 when it can't tell (for
    example an unrelated error), anything else when the bug shows. It runs
    with the config's ``run`` command, after ``build`` if configured. The
    workdir is put back on its original branch afterwards. Changes to tracked
    files made by the build are discarded: the workdir was clean at the start,
    so they can only come from this bisect.
    """
    workdir = ensure_clean(config)
    new = new or config.base
    tested: list[tuple[str, str]] = []
    branch = _current_branch(workdir)
    head = local.git(workdir, "rev-parse", "HEAD").strip()
    restore = [branch] if branch else ["--detach", head]

    # a private copy outside the workdir, so checkouts can't change or remove it
    tmp = Path(tempfile.mkdtemp(prefix="shallow-triage-"))
    copy = tmp / script.name
    shutil.copyfile(script, copy)

    def state_of(rev: str) -> str:
        local.git(workdir, "switch", "--quiet", "--force", "--detach", rev)
        sha = local.git(workdir, "rev-parse", "--short=10", "HEAD").strip()
        if config.build:
            built = run_command(config, config.build, timeout=timeout)
            if built.returncode != 0:
                tested.append((sha, SKIP))
                log(f"  {sha}: skip (build failed)")
                return SKIP
        ran = run_command(config, run_script_command(config, copy), timeout=timeout)
        if ran.returncode == 0:
            state = FIXED
        elif ran.returncode in (SKIP_STATUS, TIMEOUT):
            state = SKIP
        else:
            state = BROKEN
        tested.append((sha, state))
        log(f"  {sha}: {state}")
        return state

    try:
        log(f"checking {old} (expected broken) and {new} (expected fixed)")
        at_old, at_new = state_of(old), state_of(new)
        if at_new == BROKEN:
            return BisectResult("still broken", tested=tuple(tested))
        if at_old == FIXED and at_new == FIXED:
            return BisectResult("not reproducible", tested=tuple(tested))
        if SKIP in (at_old, at_new):
            raise ValueError(
                "the script couldn't tell at "
                + (old if at_old == SKIP else new)
                + "; fix the build or the script first"
            )
        commits = local.git(
            workdir, "rev-list", "--first-parent", "--reverse", f"{old}..{new}"
        ).split()
        log(f"searching {len(commits)} commits on the base's main line")
        first, candidates = find_first_fixed(commits, state_of)
    finally:
        local.git(workdir, "switch", "--quiet", "--force", *restore)
        shutil.rmtree(tmp, ignore_errors=True)

    if first is None:
        return BisectResult(
            "unclear", candidates=tuple(candidates), tested=tuple(tested)
        )
    return BisectResult(
        "fixed",
        first_fixed=first,
        tested=tuple(tested),
        pr=pull_request_of(config.name, first),
    )


def _current_branch(workdir: Path) -> str | None:
    """The checked-out branch, or None when HEAD is detached."""
    try:
        return local.git(workdir, "symbolic-ref", "--quiet", "--short", "HEAD").strip()
    except local.LocalError:
        return None


def pull_request_of(repo: str, sha: str) -> str | None:
    """URL of the merged PR that ``sha`` came from, if GitHub knows one."""
    try:
        pulls = gh.api(f"repos/{repo}/commits/{sha}/pulls")
    except gh.GhError:
        return None
    for pr in pulls:
        if pr.get("merged_at") and str(pr.get("html_url", "")).startswith(
            f"https://github.com/{repo}/pull/"
        ):
            return pr["html_url"]
    return None
