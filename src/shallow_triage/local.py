"""Read fix branches and fixing commits from a local clone with plain git."""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# a whole "-" or "/" separated part: fix-123-x, triage/issue-7, but not py3
_ISSUE_IN_NAME_RE = re.compile(r"(?:^|[/-])(\d+)(?=$|[/-])")
_CLOSES_RE = re.compile(
    r"\b(?:fix(?:e[sd])?|close[sd]?|resolve[sd]?)\b[:\s]+(?:issue\s+)?(?:#|gh-)(\d+)\b",
    re.I,
)
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
        """Changed test files, or every changed file if none (e.g. doctests)."""
        tests = tuple(f.path for f in self.files if _TEST_FILE_RE.search(f.path))
        return tests or tuple(f.path for f in self.files)


def _git(path: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, check=False
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise LocalError(f"git {args[0]} in {path}: {err}")
    return proc.stdout.decode("utf-8", "replace")


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
        files = []
        for line in _git(path, "diff", "--numstat", f"{base}...{name}").splitlines():
            added, deleted, file = line.split("\t", 2)
            files.append(
                FileChange(
                    file,
                    None if added == "-" else int(added),
                    None if deleted == "-" else int(deleted),
                )
            )
        diff = _git(path, "diff", f"{base}...{name}")
        branches.append(FixBranch(name, issue, tuple(commits), tuple(files), diff))
    return branches


def closing_commits(
    path: Path, base: str, issues: set[int], *, since: str | None = None
) -> dict[int, list[Commit]]:
    """Commits on ``base`` that close one of ``issues`` by keyword, newest first."""
    found: dict[int, list[Commit]] = {}
    for commit in _log(path, "-i", "-E", "--grep=(fix|close|resolve)", base):
        # filtered here: git's --since stops at the first older commit, so it
        # misses newer commits behind one with a skewed date
        if since and commit.date < since:
            continue
        for n in closing_refs(f"{commit.subject}\n{commit.body}"):
            if n in issues and commit not in found.get(n, []):
                found.setdefault(n, []).append(commit)
    return found
