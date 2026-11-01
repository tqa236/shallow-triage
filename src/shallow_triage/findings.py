"""Verified closable findings: issues checked to be fixed, or not to
reproduce, with how that was established.

Findings come from ``bisect`` or are added by hand (``closable add``). They
are an input, like the config: kept between runs in
``$XDG_DATA_HOME/shallow-triage/findings/<owner>/<name>.json`` and shown
under "Possibly closable" as verified.
"""

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from shallow_triage import serialize
from shallow_triage.store import read_json, repo_path, write_private_json

FINDINGS_VERSION = 1
FIXED = "fixed"
NOT_REPRODUCIBLE = "not reproducible"
STATUSES = (FIXED, NOT_REPRODUCIBLE)


def default_findings_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "shallow-triage" / "findings"


@dataclass(frozen=True)
class Finding:
    issue: int
    status: str  # FIXED or NOT_REPRODUCIBLE
    recorded_at: datetime
    commit: str | None = None  # the commit that fixed it
    pr: str | None = None  # URL of the PR that commit came from
    method: str = ""  # how it was established, e.g. "bisect v1.0..upstream/main"
    note: str = ""


class Findings:
    """The findings of one repository, one per issue (the latest wins)."""

    def __init__(self, directory: Path, repo: str):
        self.directory = directory
        self.path = repo_path(directory, repo)
        self.items: dict[int, Finding] = {}
        if self.path.exists():
            data = read_json(self.path)
            if data.get("version") != FINDINGS_VERSION:
                raise ValueError(f"{self.path}: unknown format {data.get('version')}")
            for raw in data["findings"]:
                finding = serialize.from_data(Finding, raw)
                self.items[finding.issue] = finding

    def add(self, finding: Finding) -> None:
        if finding.status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        self.items[finding.issue] = finding

    def remove(self, issue: int) -> bool:
        return self.items.pop(issue, None) is not None

    def save(self) -> None:
        data = {
            "version": FINDINGS_VERSION,
            "findings": [serialize.to_data(f) for _, f in sorted(self.items.items())],
        }
        write_private_json(self.directory, self.path, data)
