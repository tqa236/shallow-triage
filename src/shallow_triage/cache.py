"""On-disk cache of each repository's open issues.

The cache stores the raw GraphQL issue nodes. A run with a fresh enough cache
only asks GitHub for issues updated since the previous run (open or closed),
then adds, updates or drops them, so a large backlog is fetched in full only
once per ``max_age``. The data is public issue content; no token is stored.
"""

import json
import os
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
        if now - entry.full_fetch_at > self.max_age:
            return None
        return entry

    def save(self, repo: str, entry: CacheEntry) -> None:
        path = self._path(repo)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": CACHE_VERSION,
            "full_fetch_at": entry.full_fetch_at.isoformat(),
            "fetched_at": entry.fetched_at.isoformat(),
            "nodes": list(entry.nodes.values()),
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)  # atomic, so an interrupted run leaves the old entry


def now_utc() -> datetime:
    return datetime.now(UTC)
