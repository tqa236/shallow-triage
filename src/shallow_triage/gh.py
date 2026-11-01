"""Thin wrapper around the GitHub CLI.

Authentication is delegated entirely to ``gh`` (keyring or ``GH_TOKEN``), so
this module never reads, stores or passes a token. Anything that may surface
to the user (error messages) is passed through :func:`redact` first.
"""

import json
import re
import shutil
import subprocess
from typing import Any

_TOKEN_RE = re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")


class GhError(RuntimeError):
    """Raised when a ``gh`` invocation fails."""


def redact(text: str) -> str:
    """Replace anything that looks like a GitHub token with a placeholder."""
    return _TOKEN_RE.sub("[REDACTED]", text)


def api(path: str, *, paginate: bool = False) -> Any:
    """Run ``gh api <path>`` (GET only) and return the decoded JSON."""
    if shutil.which("gh") is None:
        raise GhError("GitHub CLI 'gh' not found on PATH")
    cmd = ["gh", "api", "--method", "GET", path]
    if paginate:
        cmd += ["--paginate", "--slurp"]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise GhError(redact(proc.stderr.strip() or f"gh exited {proc.returncode}"))
    data = json.loads(proc.stdout)
    if paginate:
        # --slurp wraps each page in an outer list.
        return [item for page in data for item in page]
    return data
