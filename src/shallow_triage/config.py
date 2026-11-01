"""Load the repositories to triage from a TOML file.

Example::

    [[repo]]
    name = "owner/project"
    path = "~/src/project"        # local clone; enables fix branches + closable
    base = "upstream/main"
    test = "python -m pytest {files}"
    since = 2020-01-01            # ignore older commits when looking for closable

Only ``name`` is required.
"""

import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

DEFAULT_BRANCHES = ("triage/issue-*", "fix-*")
DEFAULT_TEST = "python -m pytest {files}"
_KEYS = {"name", "path", "base", "test", "branches", "since"}


@dataclass(frozen=True)
class RepoConfig:
    name: str
    path: Path | None = None
    base: str = "upstream/main"
    test: str = DEFAULT_TEST  # {files} is replaced by the files to test
    branches: tuple[str, ...] = DEFAULT_BRANCHES  # globs under refs/heads/
    since: str | None = None  # ISO date


def load_config(path: Path) -> list[RepoConfig]:
    """Parse ``[[repo]]`` tables from ``path``."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    repos = []
    for i, raw in enumerate(data.get("repo", []), start=1):
        where = f"{path}: repo #{i}"
        if unknown := raw.keys() - _KEYS:
            raise ValueError(f"{where}: unknown keys {sorted(unknown)}")
        if "name" not in raw:
            raise ValueError(f"{where}: missing 'name'")
        since = raw.get("since")
        repos.append(
            RepoConfig(
                name=raw["name"],
                path=Path(raw["path"]).expanduser() if "path" in raw else None,
                base=raw.get("base", RepoConfig.base),
                test=raw.get("test", DEFAULT_TEST),
                branches=tuple(raw.get("branches", DEFAULT_BRANCHES)),
                since=since.isoformat() if isinstance(since, date) else since,
            )
        )
    return repos
