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
# user:password@ (or token@) in a URL, e.g. a remote URL in a git error
_URL_USERINFO_RE = re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^/@\s]+@", re.I)


class GhError(RuntimeError):
    """Raised when a ``gh`` invocation fails."""


def redact(text: str) -> str:
    """Replace anything that looks like a token or URL credentials."""
    return _TOKEN_RE.sub("[REDACTED]", _URL_USERINFO_RE.sub(r"\1[REDACTED]@", text))


def _run(cmd: list[str]) -> Any:
    if shutil.which("gh") is None:
        raise GhError("GitHub CLI 'gh' not found on PATH")
    proc = subprocess.run(["gh", *cmd], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise GhError(redact(proc.stderr.strip() or f"gh exited {proc.returncode}"))
    return json.loads(proc.stdout)


def api(path: str, *, paginate: bool = False) -> Any:
    """Run ``gh api <path>`` (GET only) and return the decoded JSON."""
    cmd = ["api", "--method", "GET", path]
    if paginate:
        cmd += ["--paginate", "--slurp"]
    data = _run(cmd)
    if paginate:
        # --slurp wraps each page in an outer list.
        return [item for page in data for item in page]
    return data


def graphql(query: str, **variables: str) -> list[dict]:
    """Run a paginated GraphQL query and return the ``data`` of every page.

    The query must take ``$endCursor: String`` and select
    ``pageInfo { hasNextPage endCursor }`` on the paginated connection.
    """
    cmd = ["api", "graphql", "--paginate", "--slurp", "-f", f"query={query}"]
    for key, value in variables.items():
        cmd += ["-f", f"{key}={value}"]
    return [page["data"] for page in _run(cmd)]


def graphql_once(
    query: str, *, allow_not_found: bool = False, **variables: str | int | None
) -> dict:
    """Run one GraphQL query and return its ``data``.

    Ints are sent as numbers, None values are left out. With
    ``allow_not_found``, lookups of numbers that don't exist come back as
    null instead of failing the whole query.
    """
    if shutil.which("gh") is None:
        raise GhError("GitHub CLI 'gh' not found on PATH")
    cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        if isinstance(value, int):
            cmd += ["-F", f"{key}={value}"]  # -F: typed; only ever an int here
        elif value is not None:
            cmd += ["-f", f"{key}={value}"]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        out = {}
    errors = out.get("errors") or []
    if proc.returncode == 0 or (
        allow_not_found
        and out.get("data")
        and all(e.get("type") == "NOT_FOUND" for e in errors)
    ):
        return out["data"]
    raise GhError(redact(proc.stderr.strip() or f"gh exited {proc.returncode}"))
