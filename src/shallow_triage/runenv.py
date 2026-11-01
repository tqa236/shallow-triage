"""Run a repository's configured commands (build, test, run) in its workdir.

Commands run with the configured venv activated and without credentials in
the environment: reproductions and tests execute project code (and possibly
code from issues), which should not be able to read tokens.
"""

import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from shallow_triage.config import RepoConfig

# Environment variables that look like they hold a credential, matched on
# whole "_"-separated parts: GH_TOKEN, AWS_SECRET_ACCESS_KEY, OPENAI_API_KEY,
# SSH_AUTH_SOCK, but not GIT_AUTHOR_NAME.
_SECRET_NAME_RE = re.compile(
    r"(?:^|_)(?:TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?|API_?KEY|PRIVATE_KEY|AUTH)"
    r"(?:_|$)",
    re.I,
)
# Exit status of a command that ran too long.
TIMEOUT = 124


def clean_env(venv: Path | None, base: dict[str, str] | None = None) -> dict[str, str]:
    """``base`` (default: this process's environment) without credentials,
    with ``venv`` activated if given."""
    env = {
        k: v
        for k, v in (os.environ if base is None else base).items()
        if not _SECRET_NAME_RE.search(k)
    }
    if venv is not None:
        env["VIRTUAL_ENV"] = str(venv)
        env["PATH"] = os.pathsep.join([str(venv / "bin"), env.get("PATH", "")])
        env.pop("PYTHONHOME", None)
    return env


@dataclass(frozen=True)
class Result:
    command: str
    returncode: int
    output: str  # stdout and stderr, interleaved


def run_command(
    config: RepoConfig, command: str, *, timeout: float | None = None
) -> Result:
    """Run a configured shell ``command`` in the workdir."""
    workdir = config.work_path
    if workdir is None:
        raise ValueError(f"{config.name}: no 'path' in the config")
    try:
        proc = subprocess.run(
            ["bash", "-c", command],
            cwd=workdir,
            env=clean_env(config.venv),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        return Result(command, TIMEOUT, out + f"\n[timed out after {timeout} s]")
    return Result(command, proc.returncode, proc.stdout)


def test_command(config: RepoConfig, files: tuple[str, ...]) -> str:
    return config.test.replace("{files}", " ".join(shlex.quote(f) for f in files))


def run_script_command(config: RepoConfig, script: Path) -> str:
    return config.run.replace("{script}", shlex.quote(str(script)))
