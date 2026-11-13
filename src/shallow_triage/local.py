"""Read fix branches and fixing commits from a local clone with plain git."""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from shallow_triage.gh import redact

# a whole "-" or "/" separated part: fix-123-x, triage/issue-7, but not py3
_ISSUE_IN_NAME_RE = re.compile(r"(?:^|[/-])(\d+)(?=$|[/-])")
_CLOSES_RE = re.compile(
    r"\b(?:fix(?:e[sd])?|close[sd]?|resolve[sd]?)\b[:\s]+(?:issue\s+)?(?:#|gh-)(\d+)\b",
    re.I,
)
# Plain output whatever the user's git config says (color, external diff
# tools, textconv filters); no rename detection so numstat has one path each.
_DIFF_OPTS = ("--no-color", "--no-ext-diff", "--no-textconv", "--no-renames")
_TEST_FILE_RE = re.compile(r"(^|/)(tests?/|test_[^/]*$|[^/]*_test\.py$)")


class LocalError(RuntimeError):
    """Raised when a git command fails."""


@dataclass(frozen=True)
class Commit:
    sha: str
    date: str  # committer date, YYYY-MM-DD
    subject: str
    body: str = ""


@dataclass(frozen=True)
class FileChange:
    path: str
    added: int | None  # None for binary files
    deleted: int | None


@dataclass(frozen=True)
class FixBranch:
    name: str
    issue: int
    commits: tuple[Commit, ...]  # oldest first
    files: tuple[FileChange, ...]
    diff: str

    @property
    def files_to_test(self) -> tuple[str, ...]:
        return files_to_test(self.files)


def files_to_test(files: tuple[FileChange, ...]) -> tuple[str, ...]:
    """Changed test files, or every changed file if none (e.g. doctests)."""
    tests = tuple(f.path for f in files if _TEST_FILE_RE.search(f.path))
    return tests or tuple(f.path for f in files)


def _git(path: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, check=False
    )
    if proc.returncode != 0:
        err = redact(proc.stderr.decode("utf-8", "replace").strip())
        raise LocalError(f"git {args[0]} in {path}: {err}")
    return proc.stdout.decode("utf-8", "replace")


def git(path: Path, *args: str) -> str:
    """Run ``git -C path args`` and return its output; LocalError on failure."""
    return _git(path, *args)


def _log(path: Path, *args: str) -> list[Commit]:
    out = _git(path, "log", "--format=%H%x00%cs%x00%s%x00%b%x00", *args)
    parts = out.split("\x00")
    return [
        Commit(parts[i].strip(), parts[i + 1], parts[i + 2], parts[i + 3].strip())
        for i in range(0, len(parts) - 3, 4)
    ]


def closing_refs(text: str) -> list[int]:
    """Issue numbers referenced with a closing keyword, e.g. ``Fixes #12``."""
    return [int(n) for n in _CLOSES_RE.findall(text)]


def fetch_base(path: Path, base: str) -> None:
    """``git fetch`` the remote that ``base`` (e.g. ``upstream/main``) is on.

    Only remote-tracking refs change. A local branch as base is left alone.
    """
    remote = base.split("/", 1)[0]
    if "/" in base and remote in _git(path, "remote").split():
        _git(path, "fetch", "--quiet", remote)


def changed_files(
    path: Path, base: str, rev: str | None = None
) -> tuple[FileChange, ...]:
    """Files changed on ``rev`` since it left ``base``.

    Without ``rev``: in the working tree, committed or not, including
    untracked files that aren't ignored.
    """
    files = []
    if rev is None:
        fork = _git(path, "merge-base", base, "HEAD").strip()
        numstat = _git(path, "diff", *_DIFF_OPTS, "--numstat", "-z", fork)
    else:
        numstat = _git(path, "diff", *_DIFF_OPTS, "--numstat", "-z", f"{base}...{rev}")
    for record in filter(None, numstat.split("\0")):
        added, deleted, file = record.split("\t", 2)
        files.append(
            FileChange(
                file,
                None if added == "-" else int(added),
                None if deleted == "-" else int(deleted),
            )
        )
    if rev is None:
        untracked = _git(path, "ls-files", "-z", "--others", "--exclude-standard")
        files += [
            FileChange(f, None, None) for f in filter(None, untracked.split("\0"))
        ]
    return tuple(files)


def modified_files(path: Path) -> list[str]:
    """Tracked files with uncommitted changes, staged or not."""
    out = _git(path, "status", "--porcelain=v1", "-z", "--untracked-files=no")
    paths, records = [], iter(out.split("\0"))
    for record in records:
        if not record:
            continue
        paths.append(record[3:])
        if record[0] in "RC":  # renames and copies: the source path follows
            next(records, None)
    return paths


def base_commit(path: Path, base: str) -> Commit:
    return _log(path, "-1", base)[0]


def fix_branches(path: Path, base: str, patterns: tuple[str, ...]) -> list[FixBranch]:
    """Local branches matching ``patterns`` that are not merged into ``base``.

    The issue number comes from the branch name, or else from a closing
    keyword in its commit messages; branches with neither are skipped.
    """
    refs = _git(
        path,
        "for-each-ref",
        f"--no-merged={base}",
        "--format=%(refname:short)",
        *(f"refs/heads/{p}" for p in patterns),
    ).split()
    branches = []
    for name in refs:
        commits = _log(path, "--reverse", f"{base}..{name}")
        if m := _ISSUE_IN_NAME_RE.search(name):
            issue = int(m.group(1))
        elif refs_ := closing_refs("\n".join(c.body for c in commits)):
            issue = refs_[0]
        else:
            continue
        files = changed_files(path, base, name)
        diff = _git(path, "diff", *_DIFF_OPTS, f"{base}...{name}")
        branches.append(FixBranch(name, issue, tuple(commits), files, diff))
    return branches


def closing_commits(
    path: Path, base: str, *, since: str | None = None
) -> dict[int, list[Commit]]:
    """Commits on ``base`` that close an issue by keyword, by issue number.

    Numbers are in history order of their latest such commit, most recent
    first. Whether each issue is still open is for the caller to find out.
    """
    found: dict[int, list[Commit]] = {}
    for commit in _log(path, "-i", "-E", "--grep=(fix|close|resolve)", base):
        # filtered here: git's --since stops at the first older commit, so it
        # misses newer commits behind one with a skewed date
        if since and commit.date < since:
            continue
        for n in closing_refs(f"{commit.subject}\n{commit.body}"):
            if commit not in found.get(n, []):
                found.setdefault(n, []).append(commit)
    return found
