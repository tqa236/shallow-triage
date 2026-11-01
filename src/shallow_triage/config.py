"""Load the repositories to triage from a TOML file.

Example::

    [[repo]]
    name = "owner/project"
    path = "~/src/project"        # local clone; enables fixes, closable, bisect
    workdir = "~/src/project-wt"  # where fixes, tests and bisect run (default: path)
    base = "upstream/main"
    venv = "~/venv/project"       # activated for every command run in workdir
    build = "pip install -e ."    # run after each checkout, if needed
    test = "python -m pytest {files}"
    run = "python {script}"       # how to run a reproduction script
    since = 2020-01-01            # ignore older commits when looking for closable

Only ``name`` is required. The config is trusted: ``build``, ``test`` and
``run`` are shell commands run as written.

Keys are only ever added, each with a default, so a config keeps working
after an update; see docs/architecture.md.
"""

import os
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

DEFAULT_BRANCHES = ("triage/issue-*", "fix-*")
DEFAULT_TEST = "python -m pytest {files}"
DEFAULT_RUN = "python {script}"
_PATH_KEYS = {"path", "workdir", "venv"}
_KEYS = {"name", "base", "test", "run", "build", "branches", "since"} | _PATH_KEYS
_PLACEHOLDERS = {"test": "{files}", "run": "{script}"}


@dataclass(frozen=True)
class RepoConfig:
    name: str
    path: Path | None = None
    base: str = "upstream/main"
    test: str = DEFAULT_TEST  # {files} is replaced by the files to test
    branches: tuple[str, ...] = DEFAULT_BRANCHES  # globs under refs/heads/
    since: str | None = None  # ISO date
    workdir: Path | None = None  # defaults to path
    venv: Path | None = None
    build: str | None = None
    run: str = DEFAULT_RUN  # {script} is replaced by the script's path

    @property
    def work_path(self) -> Path | None:
        """Where fixes, tests and bisect run."""
        return self.workdir or self.path


def default_config_path() -> Path:
    """``$XDG_CONFIG_HOME/shallow-triage/triage.toml``."""
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "shallow-triage" / "triage.toml"


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
        for key, placeholder in _PLACEHOLDERS.items():
            if key in raw and placeholder not in raw[key]:
                raise ValueError(f"{where}: '{key}' needs {placeholder}")
        since = raw.get("since")
        paths = {k: Path(raw[k]).expanduser() for k in _PATH_KEYS if k in raw}
        repos.append(
            RepoConfig(
                name=raw["name"],
                path=paths.get("path"),
                base=raw.get("base", RepoConfig.base),
                test=raw.get("test", DEFAULT_TEST),
                branches=tuple(raw.get("branches", DEFAULT_BRANCHES)),
                since=since.isoformat() if isinstance(since, date) else since,
                workdir=paths.get("workdir"),
                venv=paths.get("venv"),
                build=raw.get("build"),
                run=raw.get("run", DEFAULT_RUN),
            )
        )
    return repos


def find_repo(repos: list[RepoConfig], name: str) -> RepoConfig:
    """The config of ``name``; ValueError if it isn't configured."""
    for repo in repos:
        if repo.name == name:
            return repo
    raise ValueError(f"{name} is not in the config")
