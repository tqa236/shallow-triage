import json
from datetime import UTC, datetime, timedelta

import pytest

from shallow_triage import cache as cache_mod
from shallow_triage import issues
from shallow_triage.cache import IssueCache

T0 = datetime(2026, 11, 1, 12, tzinfo=UTC)


def _node(n, state="OPEN", title=None):
    return {
        "number": n,
        "state": state,
        "title": title or f"t{n}",
        "url": f"https://github.com/o/r/issues/{n}",
        "body": "",
        "comments": {"totalCount": 0},
        "assignees": {"totalCount": 0},
        "labels": {"nodes": []},
        "closedByPullRequestsReferences": {"nodes": []},
    }


class FakeGitHub:
    """Issues 1..n in memory, standing in for the GraphQL calls.

    The walk goes newest first (highest number first); its cursor is the
    number of issues already returned. ``calls`` records what was asked.
    """

    def __init__(self, n):
        # created and last updated a day before the first run
        self.issues = {i: (_node(i), T0 - timedelta(days=1)) for i in range(1, n + 1)}
        self.prs = set()
        self.calls = []

    def update(self, n, at, **changes):
        node = dict(self.issues.get(n, (_node(n), at))[0], **changes)
        self.issues[n] = (node, at)

    def open_newest_first(self):
        return [
            node
            for _, (node, _) in sorted(self.issues.items(), reverse=True)
            if node["state"] == "OPEN"
        ]

    def walk(self, owner, name, after, count):
        self.calls.append(("walk", after, count))
        start = int(after or 0)
        nodes = self.open_newest_first()
        end = len(nodes) if count is None else start + count
        page = nodes[start:end]
        done = end >= len(nodes)
        return page, None if done else str(end), done

    def sync(self, owner, name, since):
        self.calls.append(("sync", since))
        return [node for node, at in self.issues.values() if at >= since]

    def lookup(self, owner, name, numbers):
        self.calls.append(("lookup", tuple(numbers)))
        return {
            n: None if n in self.prs or n not in self.issues else self.issues[n][0]
            for n in numbers
        }

    def total(self, owner, name):
        return len(self.open_newest_first())


@pytest.fixture
def github(monkeypatch):
    fake = FakeGitHub(250)
    monkeypatch.setattr(issues, "_walk", fake.walk)
    monkeypatch.setattr(issues, "_sync", fake.sync)
    monkeypatch.setattr(issues, "_lookup", fake.lookup)
    monkeypatch.setattr(issues, "_open_total", fake.total)
    return fake


@pytest.fixture
def clock(monkeypatch):
    now = [T0]
    monkeypatch.setattr(issues, "now_utc", lambda: now[0])
    return now


def _numbers(found):
    return sorted(i.number for i in found.issues)


def test_each_run_adds_a_batch(tmp_path, github, clock):
    c = IssueCache(tmp_path)
    first = issues.fetch_issues("o/r", c, batch=100)
    assert _numbers(first) == list(range(151, 251))  # newest first
    assert (first.total_open, first.complete) == (250, False)
    assert github.calls == [("walk", None, 100), ("lookup", ())]

    clock[0] = T0 + timedelta(hours=1)
    second = issues.fetch_issues("o/r", c, batch=100)
    assert _numbers(second) == list(range(51, 251))
    assert github.calls[-3:] == [("sync", T0), ("walk", "100", 100), ("lookup", ())]

    clock[0] = T0 + timedelta(hours=2)
    third = issues.fetch_issues("o/r", c, batch=100)
    assert _numbers(third) == list(range(1, 251)) and third.complete

    # nothing left to walk: later runs only sync
    clock[0] = T0 + timedelta(hours=3)
    github.calls.clear()
    issues.fetch_issues("o/r", c, batch=100)
    assert [call[0] for call in github.calls] == ["sync", "lookup"]


def test_sync_drops_closed_and_refreshes_changed(tmp_path, github, clock):
    c = IssueCache(tmp_path)
    issues.fetch_issues("o/r", c, batch=10)  # 241..250

    clock[0] = T0 + timedelta(hours=1)
    github.update(250, clock[0], state="CLOSED")
    github.update(249, clock[0], title="edited")
    github.update(5, clock[0], title="old but active")  # not walked yet
    found = issues.fetch_issues("o/r", c, batch=0)  # sync only

    titles = {i.number: i.title for i in found.issues}
    assert 250 not in titles
    assert titles[249] == "edited"
    assert titles[5] == "old but active"  # recently active issues join early


def test_lookups_for_fix_branches_and_closing_commits(tmp_path, github, clock):
    c = IssueCache(tmp_path)
    github.update(7, T0, state="CLOSED")
    github.prs.add(8)
    found = issues.fetch_issues("o/r", c, batch=2, wanted=(3, 7), extra=(9, 8, 4, 2, 1))
    # wanted: always; extra: at most `batch` of the unknown ones
    assert github.calls[-1] == ("lookup", (3, 7, 9, 8))
    assert {3, 9} <= set(_numbers(found)) and 7 not in _numbers(found)

    # known states are remembered: only the rest are looked up next time
    clock[0] = T0 + timedelta(hours=1)
    issues.fetch_issues("o/r", c, batch=2, wanted=(3, 7), extra=(9, 8, 4, 2, 1))
    assert github.calls[-1] == ("lookup", (4, 2))


def test_all_fetches_everything(tmp_path, github, clock):
    found = issues.fetch_issues("o/r", IssueCache(tmp_path), batch=None)
    assert len(found.issues) == 250 and found.complete


def test_refresh_starts_over(tmp_path, github, clock):
    issues.fetch_issues("o/r", IssueCache(tmp_path), batch=10)
    found = issues.fetch_issues("o/r", IssueCache(tmp_path, refresh=True), batch=10)
    assert github.calls[-2] == ("walk", None, 10)
    assert len(found.issues) == 10


def test_no_cache_does_not_write(tmp_path, github, clock):
    issues.fetch_issues("o/r", batch=10)
    issues.fetch_issues("o/r", batch=10)
    assert github.calls[-2] == ("walk", None, 10)  # started from scratch again
    assert not list(tmp_path.iterdir())


def test_cache_files_are_owner_only(tmp_path, github, clock):
    issues.fetch_issues("o/r", IssueCache(tmp_path / "c"), batch=1)
    path = tmp_path / "c" / "o" / "r.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert [p.name for p in path.parent.iterdir()] == ["r.json"]  # no temp left


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["nodes"][0].update(number='1"><b>x</b>'),
        lambda d: d["nodes"][0].update(url="javascript:alert(1)"),
        lambda d: d["nodes"][0].update(url="https://evil.example/"),
        lambda d: d.update(closed=["1"]),
        lambda d: d.update(cursor=5),
        lambda d: d.update(version=1),
    ],
)
def test_unusable_cache_starts_over(tmp_path, github, clock, change):
    c = IssueCache(tmp_path)
    issues.fetch_issues("o/r", c, batch=1)
    path = tmp_path / "o" / "r.json"
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))

    issues.fetch_issues("o/r", c, batch=1)
    assert github.calls[-2] == ("walk", None, 1)  # from the start, no sync


@pytest.mark.parametrize("content", ["not json", "{}"])
def test_broken_cache_file_starts_over(tmp_path, github, clock, content):
    path = tmp_path / "o" / "r.json"
    path.parent.mkdir()
    path.write_text(content)
    issues.fetch_issues("o/r", IssueCache(tmp_path), batch=1)
    assert github.calls == [("walk", None, 1), ("lookup", ())]


def test_sync_query_is_sent_with_skew(monkeypatch):
    sent = {}

    def fake_graphql(query, **variables):
        sent.update(variables, query=query)
        return []

    monkeypatch.setattr(issues.gh, "graphql", fake_graphql)
    issues._sync("o", "r", T0)
    assert sent["since"] == "2026-11-01T11:55:00Z"
    assert "filterBy: {since: $since}" in sent["query"]
    assert "[OPEN, CLOSED]" in sent["query"]


def test_walk_pages_and_cursor(monkeypatch):
    pages = {
        None: (["a", "b"], True, "c1"),
        "c1": (["c"], False, None),
    }
    asked = []

    def fake_once(query, **v):
        asked.append((v["first"], v["after"]))
        nodes, more, end = pages[v["after"]]
        return {
            "repository": {
                "issues": {
                    "nodes": nodes,
                    "pageInfo": {"hasNextPage": more, "endCursor": end},
                }
            }
        }

    monkeypatch.setattr(issues.gh, "graphql_once", fake_once)
    assert issues._walk("o", "r", None, 2) == (["a", "b"], "c1", False)
    assert issues._walk("o", "r", None, None) == (["a", "b", "c"], None, True)
    assert asked == [(2, None), (100, None), (100, "c1")]


def test_lookup_treats_prs_and_missing_as_not_open(monkeypatch):
    def fake_once(query, *, allow_not_found, **v):
        assert allow_not_found
        assert "i1: issueOrPullRequest(number: 1)" in query
        return {
            "repository": {
                "i1": dict(_node(1), __typename="Issue"),
                "i2": {"__typename": "PullRequest"},
                "i3": None,
            }
        }

    monkeypatch.setattr(issues.gh, "graphql_once", fake_once)
    found = issues._lookup("o", "r", [1, 2, 3])
    assert found[1]["number"] == 1 and found[2] is None and found[3] is None


def test_default_cache_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert cache_mod.default_cache_dir() == tmp_path / "shallow-triage"


def test_field_change_keeps_the_cache(tmp_path, github, clock, monkeypatch):
    c = IssueCache(tmp_path)
    issues.fetch_issues("o/r", c, batch=10)  # 241..250 with field set 1
    github.calls.clear()

    # a new version fetches more fields: old nodes are still used...
    monkeypatch.setattr(issues, "FIELDS_VERSION", issues.FIELDS_VERSION + 1)
    clock[0] = T0 + timedelta(hours=1)
    found = issues.fetch_issues("o/r", c, batch=4)
    assert len(found.issues) == 14  # 10 kept + 4 new from the walk
    # ...and refreshed a batch at a time, not refetched all at once
    assert github.calls[-1] == ("lookup", (250, 249, 248, 247))
    assert github.calls[0] == ("sync", T0)  # position and history kept

    clock[0] = T0 + timedelta(hours=2)
    issues.fetch_issues("o/r", c, batch=4)
    assert github.calls[-1] == ("lookup", (246, 245, 244, 243))


def test_old_nodes_without_new_fields_still_parse():
    node = {"number": 1, "url": "https://github.com/o/r/issues/1", "state": "OPEN"}
    issue = issues.Issue.from_graphql(node)
    assert (issue.title, issue.labels, issue.comments, issue.linked_prs) == (
        "",
        frozenset(),
        0,
        (),
    )
