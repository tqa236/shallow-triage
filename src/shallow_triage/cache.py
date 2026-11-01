"""On-disk cache of each repository's open issues.

The cache stores the raw GraphQL issue nodes. A run with a fresh enough cache
only asks GitHub for issues updated since the previous run (open or closed),
then adds, updates or drops them, so a large backlog is fetched in full only
once per ``max_age``. No token is stored, but entries for a private
repository hold its private issue content, so files are owner-only.
"""

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Bump when the issue query's fields change, so old entries are refetched.
CACHE_VERSION = 1


def default_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "shallow-triage"


@dataclass(frozen=True)
class CacheEntry:
    full_fetch_at: datetime  # last time every open issue was fetched
    fetched_at: datetime  # last time the entry was brought up to date
    nodes: dict[int, dict]  # issue number -> GraphQL node


@dataclass(frozen=True)
class IssueCache:
    directory: Path
    max_age: timedelta = timedelta(hours=24)  # between full fetches
    refresh: bool = False  # ignore what is cached and fetch everything

    def _path(self, repo: str) -> Path:
        owner, name = repo.split("/")
        return self.directory / owner / f"{name}.json"

    def load(self, repo: str, now: datetime) -> CacheEntry | None:
        """The cached entry, or None if missing, unreadable, stale or refreshing."""
        if self.refresh:
            return None
        try:
            data = json.loads(self._path(repo).read_text(encoding="utf-8"))
            if data["version"] != CACHE_VERSION:
                return None
            entry = CacheEntry(
                datetime.fromisoformat(data["full_fetch_at"]),
                datetime.fromisoformat(data["fetched_at"]),
                {node["number"]: node for node in data["nodes"]},
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if not all(_well_formed(node) for node in entry.nodes.values()):
            return None  # edited or corrupted: refetch rather than trust it
        if now - entry.full_fetch_at > self.max_age:
            return None
        return entry

    def save(self, repo: str, entry: CacheEntry) -> None:
        path = self._path(repo)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.parent.mkdir(mode=0o700, exist_ok=True)
        path.parent.chmod(0o700)  # also tightens folders from older versions
        data = {
            "version": CACHE_VERSION,
            "full_fetch_at": entry.full_fetch_at.isoformat(),
            "fetched_at": entry.fetched_at.isoformat(),
            "nodes": list(entry.nodes.values()),
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
