import pytest

from shallow_triage import issues
from shallow_triage.issues import Issue, rank, score_issue, score_reasons


def _issue(n, body="", labels=(), **kw):
    return Issue(n, f"t{n}", f"u{n}", body, frozenset(labels), **kw)


def _node(n, prs=()):
    return {
        "number": n,
        "title": f"t{n}",
        "url": f"u{n}",
        "body": None,
        "comments": {"totalCount": 0},
        "assignees": {"totalCount": 0},
        "labels": {"nodes": [{"name": "Bug"}]},
        "closedByPullRequestsReferences": {
            "nodes": [{"url": url, "state": state} for url, state in prs]
        },
    }


def test_fetch_parses_pages_and_open_linked_prs(monkeypatch):
    pages = [
        {"repository": {"issues": {"nodes": [_node(1, [("p10", "OPEN")])]}}},
        {"repository": {"issues": {"nodes": [_node(2, [("p11", "MERGED")])]}}},
    ]
    monkeypatch.setattr(issues.gh, "graphql", lambda *a, **k: pages)
    got = issues.fetch_open_issues("o/r")
    assert [(i.number, i.linked_prs) for i in got] == [(1, ("p10",)), (2, ())]
    assert got[0].labels == {"bug"}


@pytest.mark.parametrize("repo", ["not-a-repo", "o/r/x", "../x", "o/..", "o/.", "/r"])
def test_fetch_rejects_bad_repo(repo):
    with pytest.raises(ValueError):
        issues.fetch_open_issues(repo)


def test_score_prefers_reproducible_bugs():
    good = _issue(1, "Steps to reproduce:\n```py\nx()\n```", {"bug"})
    vague = _issue(2, "what about X?", {"question"})
    assert score_issue(good) > 0 > score_issue(vague)


def test_score_is_sum_of_reasons():
    issue = _issue(1, "Traceback ...", {"bug", "question"}, comments=30)
    assert score_reasons(issue) == [
        (3, "has reproduction steps or code"),
        (2, "labelled 'bug'"),
        (-3, "labelled 'question'"),
        (-2, "long thread (30 comments)"),
    ]
    assert score_issue(issue) == 0


def test_rank_orders_and_limits():
    a, b, c = _issue(1), _issue(2, labels={"bug"}), _issue(3, assigned=True)
    assert rank([a, b, c], limit=2) == [b, a]


def test_rank_drops_linked_unless_asked():
    free, taken = _issue(1), _issue(2, labels={"bug"}, linked_prs=("p5",))
    assert rank([free, taken]) == [free]
    assert rank([free, taken], include_linked=True) == [taken, free]
