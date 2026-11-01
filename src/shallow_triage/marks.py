"""Issues the user has reviewed and marked safe to take code from.

Issue content is written by anyone, so code from an issue is only used (for
example by ``repro``) when the user has read the issue and marked it safe.
A mark records a hash of the title and body as they were when marked: once
either changes, the issue is no longer safe until it is marked again.

Marks are an input (the user's decisions) and are kept apart from fetched
data, in ``$XDG_DATA_HOME/shallow-triage/safe/<owner>/<name>.json``.
"""

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from shallow_triage import serialize
from shallow_triage.store import read_json, repo_path, write_private_json

MARKS_VERSION = 1
SAFE = "safe"
CHANGED = "changed since marked safe"

_CODE_RE = re.compile(r"^```[ \t]*([\w+-]*)[^\n]*\n(.*?)^```", re.S | re.M)
_PYTHON_TAGS = {"", "python", "py", "python3", "pycon", "ipython"}


def default_marks_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "shallow-triage" / "safe"


def content_hash(title: str, body: str) -> str:
    return hashlib.sha256(f"{title}\n\0\n{body}".encode()).hexdigest()


@dataclass(frozen=True)
class Mark:
    number: int
    sha256: str  # of the title and body when marked
    marked_at: datetime
    title: str = ""  # for listing


class Marks:
    """The safe marks of one repository."""

    def __init__(self, directory: Path, repo: str):
        self.directory = directory
        self.path = repo_path(directory, repo)
        self.marks: dict[int, Mark] = {}
        if self.path.exists():
            data = read_json(self.path)
            if data.get("version") != MARKS_VERSION:
                raise ValueError(f"{self.path}: unknown format {data.get('version')}")
            for raw in data["marks"]:
                mark = serialize.from_data(Mark, raw)
                self.marks[mark.number] = mark

    def save(self) -> None:
        data = {
            "version": MARKS_VERSION,
            "marks": [
                serialize.to_data(m)
                for m in sorted(self.marks.values(), key=lambda m: m.number)
            ],
        }
        write_private_json(self.directory, self.path, data)

    def mark(self, number: int, title: str, body: str, at: datetime) -> None:
        self.marks[number] = Mark(number, content_hash(title, body), at, title)

    def unmark(self, number: int) -> bool:
        return self.marks.pop(number, None) is not None

    def state(self, number: int, title: str, body: str) -> str | None:
        """SAFE, CHANGED, or None if the issue was never marked."""
        mark = self.marks.get(number)
        if mark is None:
            return None
        return SAFE if mark.sha256 == content_hash(title, body) else CHANGED


def code_blocks(body: str) -> list[str]:
    """The Python (or untagged) fenced code blocks in an issue body.

    Interactive sessions (``>>>`` prompts) are turned into plain code: the
    prompts are removed and the printed output is dropped.
    """
    blocks = []
    for tag, code in _CODE_RE.findall(body.replace("\r\n", "\n")):
        if tag.lower() not in _PYTHON_TAGS:
            continue
        lines = code.splitlines()
        if any(line.startswith(">>>") for line in lines):
            lines = [
                line[4:]
                for line in lines
                if line.startswith((">>> ", "... ")) or line in (">>>", "...")
            ]
        blocks.append("\n".join(lines).rstrip() + "\n")
    return blocks
