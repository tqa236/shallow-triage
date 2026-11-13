"""Tools for working on a repository's issues in its workdir: start a fix
branch, run the tests, run a script, check the config, and bisect for the
commit that fixed an issue.

They all use the repository's config (workdir, venv, build, test, run).
Those that switch commits refuse to start when the workdir has uncommitted
changes, other than in the config's ``keep_local`` paths, so they never mix
with work in progress.
"""

import fnmatch
import re
import shutil
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
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
# Exit statuses of a shell that couldn't run the command at all.
_NOT_RUN = {
    126: "the run command can't be executed",
    127: "the run command wasn't found",
}
# An uncaught import error is about the environment, not the issue. A script
# whose issue is an import error catches it and exits 1 itself.
_IMPORT_ERROR_RE = re.compile(r"^(?:ModuleNotFoundError|ImportError)\b")


def _workdir(config: RepoConfig) -> Path:
    if config.work_path is None:
        raise ValueError(f"{config.name}: no 'path' in the config")
    return config.work_path


def is_kept(config: RepoConfig, path: str) -> bool:
    """Whether ``path`` is one of the config's ``keep_local`` paths (a file,
    a directory, or a glob)."""
    return any(
        path == k or path.startswith(k.rstrip("/") + "/") or fnmatch.fnmatch(path, k)
        for k in config.keep_local
    )


def ensure_clean(config: RepoConfig) -> Path:
    """The workdir, after checking it has no uncommitted changes other than
    in ``keep_local`` paths."""
    workdir = _workdir(config)
    dirty = [f for f in local.modified_files(workdir) if not is_kept(config, f)]
    if dirty:
        shown = ", ".join(dirty[:5]) + (", ..." if len(dirty) > 5 else "")
        raise ValueError(
            f"{workdir} has uncommitted changes ({shown}); commit or stash them,"
            " or add the paths to 'keep_local' in the config if they are"
            " local settings"
        )
    return workdir


@contextmanager
def checked_out(config: RepoConfig) -> Iterator[Callable[[str], None]]:
    """Check that the workdir is clean, then yield a function that switches
    it to a commit.

    Switches are forced: changes the build makes to tracked files are
    discarded, since the workdir was clean at the start. Afterwards the
    workdir goes back to its branch (or detached commit), and the
    ``keep_local`` files are put back as they were.
    """
    workdir = ensure_clean(config)
    branch = _current_branch(workdir)
    head = local.git(workdir, "rev-parse", "HEAD").strip()
    restore = [branch] if branch else ["--detach", head]

    def switch(rev: str) -> None:
        local.git(workdir, "switch", "--quiet", "--force", "--detach", rev)

    with _keeping(workdir):
        try:
            yield switch
        finally:
            local.git(workdir, "switch", "--quiet", "--force", *restore)


@contextmanager
def _keeping(workdir: Path) -> Iterator[None]:
    """Put the modified tracked files back as they are now, afterwards.

    Called on a workdir whose only changes are ``keep_local`` paths.
    """
    kept = {
        f: (workdir / f).read_bytes()
        for f in local.modified_files(workdir)
        if (workdir / f).is_file()
    }
    try:
        yield
    finally:
        for f, content in kept.items():
            (workdir / f).parent.mkdir(parents=True, exist_ok=True)
            (workdir / f).write_bytes(content)


@contextmanager
def _private_copy(script: Path) -> Iterator[Path]:
    """A copy of ``script`` outside the workdir, so checkouts can't change or
    remove it."""
    tmp = Path(tempfile.mkdtemp(prefix="shallow-triage-"))
    try:
        copy = tmp / script.name
        shutil.copyfile(script, copy)
        yield copy
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def classify(result: Result) -> tuple[str, str]:
    """FIXED, BROKEN or SKIP for a reproduction script's result, and why."""
    code = result.returncode
    if code == 0:
        return FIXED, "exit 0"
    if code == SKIP_STATUS:
        return SKIP, "the script couldn't tell (exit 125)"
    if code == TIMEOUT:
        return SKIP, "timed out"
    if code in _NOT_RUN:
        return SKIP, _NOT_RUN[code]
    last = last_line(result.output)
    if _IMPORT_ERROR_RE.match(last):
        return SKIP, f"unrelated error: {last}"
    return BROKEN, f"exit {code}" + (f": {last}" if last else "")


def last_line(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1][:200] if lines else ""


def fix_branch_name(issue: int) -> str:
    return f"triage/issue-{issue}"


def start_fix(config: RepoConfig, issue: int) -> str:
    """Create and switch to ``triage/issue-<issue>`` from the base."""
    workdir = ensure_clean(config)
    name = fix_branch_name(issue)
    if local.git(workdir, "branch", "--list", name).strip():
        raise ValueError(f"branch {name} already exists")
    with _keeping(workdir):
        local.git(
            workdir, "switch", "--quiet", "--force", "--create", name, config.base
        )
    return name


def run_tests(config: RepoConfig, *, build: bool = True) -> list[Result]:
    """Build (if configured) and test what changed since the base: the
    current branch's commits and any uncommitted or new files, other than
    ``keep_local`` paths.

    Stops after a failing build. Returns the results in order.
    """
    workdir = _workdir(config)
    changed = tuple(
        f
        for f in local.changed_files(workdir, config.base)
        if not is_kept(config, f.path)
    )
    files = local.files_to_test(changed)
    if not files:
        raise ValueError("nothing changed compared with the base, committed or not")
    results = []
    if build and config.build:
        results.append(run_command(config, config.build))
        if results[-1].returncode != 0:
            return results
    results.append(run_command(config, test_command(config, files)))
    return results


def run_script(
    config: RepoConfig,
    script: Path,
    *,
    at: str | None = None,
    build: bool | None = None,
    timeout: float | None = None,
) -> list[Result]:
    """Run ``script`` with the config's ``run`` command in the workdir.

    With ``at``, on that commit, then back on the current branch. ``build``
    defaults to building only when switching. Stops after a failing build.
    """
    if build is None:
        build = at is not None
    with _private_copy(script) as copy:
        if at is None:
            _workdir(config)
            return _build_and_run(config, copy, build, timeout)
        with checked_out(config) as switch:
            switch(at)
            return _build_and_run(config, copy, build, timeout)


def _build_and_run(
    config: RepoConfig, script: Path, build: bool, timeout: float | None
) -> list[Result]:
    results = []
    if build and config.build:
        results.append(run_command(config, config.build, timeout=timeout))
        if results[-1].returncode != 0:
            return results
    results.append(
        run_command(config, run_script_command(config, script), timeout=timeout)
    )
    return results


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


def check_config(config: RepoConfig, script: Path | None = None) -> list[Check]:
    """Check that a repository's config can be used: the clone, workdir,
    base and venv exist, and the ``run`` command runs a script (``script``,
    or an empty one) in the current checkout with exit 0."""
    checks = []

    def add(name: str, ok: bool, detail: str = "") -> bool:
        checks.append(Check(name, ok, detail))
        return ok

    if config.path is None:
        add("path", False, "no 'path' in the config")
        return checks
    places = [("path", config.path)]
    if config.workdir is not None:
        places.append(("workdir", config.workdir))
    for name, where in places:
        if not where.is_dir():
            add(name, False, f"{where} doesn't exist")
            return checks
        try:
            local.git(where, "rev-parse", "--git-dir")
        except local.LocalError as exc:
            add(name, False, str(exc))
            return checks
        add(name, True, str(where))
    try:
        sha = local.git(config.path, "rev-parse", "--short=10", config.base).strip()
        add("base", True, f"{config.base} is {sha}")
    except local.LocalError:
        add("base", False, f"{config.base} doesn't exist; fetch its remote")
    if config.venv is not None:
        python = config.venv / "bin" / "python"
        add("venv", python.exists(), str(config.venv if python.exists() else python))
    if script is None:
        with tempfile.TemporaryDirectory(prefix="shallow-triage-") as tmp:
            empty = Path(tmp) / "check.py"
            empty.write_text("")
            ran = run_command(config, run_script_command(config, empty), timeout=300)
    else:
        ran = run_script(config, script, timeout=300)[-1]
    add(
        "run",
        ran.returncode == 0,
        f"{config.run} exits 0"
        if ran.returncode == 0
        else f"exit {ran.returncode}: " + last_line(ran.output),
    )
    return checks


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
    with the config's ``run`` command, after ``build`` if configured. A
    commit also counts as "can't tell" when the build fails, the run
    command can't be found, or the script stops with an uncaught import
    error. The workdir is restored afterwards (see ``checked_out``).
    """
    new = new or config.base
    tested: list[tuple[str, str]] = []

    with _private_copy(script) as copy, checked_out(config) as switch:

        def state_of(rev: str) -> str:
            switch(rev)
            sha = local.git(workdir, "rev-parse", "--short=10", "HEAD").strip()
            results = _build_and_run(config, copy, True, timeout)
            if len(results) == 1 and config.build:
                state, why = SKIP, "build failed"
            else:
                state, why = classify(results[-1])
            tested.append((sha, state))
            log(f"  {sha}: {state} ({why})")
            return state

        workdir = _workdir(config)
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
