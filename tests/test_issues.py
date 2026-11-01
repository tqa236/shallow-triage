from shallow_triage import issues
from shallow_triage.issues import Issue, rank, score_issue


def _issue(n, body="", labels=(), **kw):
    return Issue(n, f"t{n}", f"u{n}", body, frozenset(labels), **kw)


def test_fetch_skips_pull_requests(monkeypatch):
    raw = [
        {"number": 1, "title": "a", "html_url": "u1", "body": None, "labels": []},
        {"number": 2, "title": "b", "html_url": "u2", "pull_request": {}},
    ]
    monkeypatch.setattr(issues.gh, "api", lambda *a, **k: raw)
    assert [i.number for i in issues.fetch_open_issues("o/r")] == [1]


def test_score_prefers_reproducible_bugs():
    good = _issue(1, "Steps to reproduce:\n```py\nx()\n```", {"bug"})
    vague = _issue(2, "what about X?", {"question"})
    assert score_issue(good) > 0 > score_issue(vague)


def test_rank_orders_and_limits():
    a, b, c = _issue(1), _issue(2, labels={"bug"}), _issue(3, assigned=True)
    assert rank([a, b, c], limit=2) == [b, a]
