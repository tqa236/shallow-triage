"""On-disk cache of each repository's issues, filled a batch at a time.

For each repository it keeps the open issues fetched so far (raw GraphQL
nodes), where the walk through the open issues stopped, and the numbers
known not to be open issues. Each run continues from there instead of
fetching the whole backlog. No token is stored, but entries for a private
repository hold its private issue content, so files are owner-only.
"""

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# Bump when the stored format or the issue fields change; older entries are
# then ignored and rebuilt.
CACHE_VERSION = 2


def default_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "shallow-triage"


@dataclass(frozen=True)
class CacheEntry:
    fetched_at: datetime | None = None  # last run; None before the first
    cursor: str | None = None  # where the walk through open issues stopped
    complete: bool = False  # the walk reached the end
    nodes: dict[int, dict] = field(default_factory=dict)  # open issues
    closed: frozenset[int] = frozenset()  # numbers known not to be open issues


@dataclass(frozen=True)
class IssueCache:
    directory: Path
    refresh: bool = False  # ignore what is cached and start over

    def _path(self, repo: str) -> Path:
        owner, name = repo.split("/")
        return self.directory / owner / f"{name}.json"

    def load(self, repo: str) -> CacheEntry | None:
        """The cached entry, or None if missing, unreadable or refreshing."""
        if self.refresh:
            return None
        try:
            data = json.loads(self._path(repo).read_text(encoding="utf-8"))
            if data["version"] != CACHE_VERSION:
                return None
            entry = CacheEntry(
                datetime.fromisoformat(data["fetched_at"]),
                data["cursor"],
                bool(data["complete"]),
                {node["number"]: node for node in data["nodes"]},
                frozenset(data["closed"]),
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if not all(_well_formed(node) for node in entry.nodes.values()):
            return None  # edited or corrupted: start over rather than trust it
        if not all(type(n) is int for n in entry.closed):
            return None
        if entry.cursor is not None and not isinstance(entry.cursor, str):
            return None
        return entry

    def save(self, repo: str, entry: CacheEntry) -> None:
        path = self._path(repo)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.parent.mkdir(mode=0o700, exist_ok=True)
        path.parent.chmod(0o700)  # also tightens folders from older versions
        data = {
            "version": CACHE_VERSION,
            "fetched_at": entry.fetched_at.isoformat() if entry.fetched_at else None,
            "cursor": entry.cursor,
            "complete": entry.complete,
            "nodes": list(entry.nodes.values()),
            "closed": sorted(entry.closed),
        }
        # mkstemp: a new owner-only (0600) file with an unpredictable name;
        # the rename is atomic, so an interrupted run leaves the old entry
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, path)
        except BaseException:
            os.unlink(tmp)
            raise


def _well_formed(node: object) -> bool:
    """The fields that end up in HTML attributes have the expected types."""
    return (
        isinstance(node, dict)
        and type(node.get("number")) is int
        and isinstance(node.get("url"), str)
        and node["url"].startswith("https://github.com/")
    )


def now_utc() -> datetime:
    return datetime.now(UTC)
