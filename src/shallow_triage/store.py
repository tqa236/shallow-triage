"""Owner-only, atomic JSON files under a per-repository layout.

Both the issue cache and the saved reports can hold private content (issues
of private repositories, unpushed diffs), so their files are readable by the
current user only.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def repo_path(root: Path, repo: str) -> Path:
    """``root/<owner>/<name>.json``; ``repo`` must already be validated."""
    owner, name = repo.split("/")
    return root / owner / f"{name}.json"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_private_json(root: Path, path: Path, data: Any) -> None:
    """Write ``data`` to ``path`` (under ``root``) as an owner-only file."""
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)  # also tightens folders made by older versions
    # mkstemp: a new owner-only (0600) file with an unpredictable name;
    # the rename is atomic, so an interrupted run leaves the old file
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def default_data_dir() -> Path:
    """Where reports are saved: ``$XDG_DATA_HOME/shallow-triage/reports``."""
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "shallow-triage" / "reports"
