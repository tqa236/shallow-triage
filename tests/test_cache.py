from datetime import UTC, datetime, timedelta

import pytest

from shallow_triage import cache as cache_mod
from shallow_triage import issues
from shallow_triage.cache import CACHE_VERSION, IssueCache

T0 = datetime(2026, 11, 1, 12, tzinfo=UTC)


def _node(n, state="OPEN", title=None):
    return {
        "number": n,
        "state": state,
        "title": title or f"t{n}",
        "url": f"u{n}",
        "body": "",
        "comments": {"totalCount": 0},
        "assignees": {"totalCount": 0},
        "labels": {"nodes": []},
        "closedByPullRequestsReferences": {"nodes": []},
    }


class FakeGitHub:
    """Stands in for _fetch_nodes and records each call's ``since``."""

    def __init__(self):
        self.calls = []
        self.full = []
        self.updated = []

    def __call__(self, repo, since=None):
        self.calls.append(since)
        return self.full if since is None else self.updated


@pytest.fixture
def github(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr(issues, "_fetch_nodes", fake)
    return fake


@pytest.fixture
def clock(monkeypatch):
    now = [T0]
    monkeypatch.setattr(issues, "now_utc", lambda: now[0])
    return now


def _titles(got):
    return sorted((i.number, i.title) for i in got)


def test_incremental_refresh(tmp_path, github, clock):
    c = IssueCache(tmp_path)
    github.full = [_node(1), _node(2), _node(3)]
    assert _titles(issues.fetch_open_issues("o/r", c)) == [
        (1, "t1"),
        (2, "t2"),
        (3, "t3"),
    ]
    assert github.calls == [None]

    # next run: 2 was edited, 3 closed, 4 opened
    clock[0] = T0 + timedelta(hours=1)
    github.updated = [_node(2, title="new"), _node(3, "CLOSED"), _node(4)]
    got = issues.fetch_open_issues("o/r", c)
    assert _titles(got) == [(1, "t1"), (2, "new"), (4, "t4")]
    assert github.calls == [None, T0]  # asked only for changes since the last run

    # the run after that asks since the second run
    clock[0] = T0 + timedelta(hours=2)
    github.updated = []
    assert len(issues.fetch_open_issues("o/r", c)) == 3
    assert github.calls[-1] == T0 + timedelta(hours=1)


def test_full_fetch_when_old_or_refreshing(tmp_path, github, clock):
    github.full = [_node(1)]
    issues.fetch_open_issues("o/r", IssueCache(tmp_path))

    clock[0] = T0 + timedelta(hours=25)  # older than the default 24h
    issues.fetch_open_issues("o/r", IssueCache(tmp_path))
    assert github.calls == [None, None]

    issues.fetch_open_issues("o/r", IssueCache(tmp_path, refresh=True))
    assert github.calls == [None, None, None]

    issues.fetch_open_issues("o/r", IssueCache(tmp_path))
    assert github.calls[-1] == T0 + timedelta(hours=25)


@pytest.mark.parametrize(
    "content", ["not json", '{"version": 0}', f'{{"version": {CACHE_VERSION}}}']
)
def test_unusable_cache_file_means_full_fetch(tmp_path, github, clock, content):
    path = tmp_path / "o" / "r.json"
    path.parent.mkdir()
    path.write_text(content)
    github.full = [_node(1)]
    assert len(issues.fetch_open_issues("o/r", IssueCache(tmp_path))) == 1
    assert github.calls == [None]


def test_no_cache_does_not_write(tmp_path, github, clock):
    github.full = [_node(1)]
    issues.fetch_open_issues("o/r")
    assert not list(tmp_path.iterdir())


def test_since_query_is_sent_with_skew(monkeypatch):
    sent = {}

    def fake_graphql(query, **variables):
        sent.update(variables, query=query)
        return []

    monkeypatch.setattr(issues.gh, "graphql", fake_graphql)
    issues._fetch_nodes("o/r", since=T0)
    assert sent["since"] == "2026-11-01T11:55:00Z"
    assert "filterBy: {since: $since}" in sent["query"]
    assert "[OPEN, CLOSED]" in sent["query"]


def test_default_cache_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert cache_mod.default_cache_dir() == tmp_path / "shallow-triage"
