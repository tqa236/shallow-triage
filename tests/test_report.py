from shallow_triage import report
from shallow_triage.config import RepoConfig
from shallow_triage.issues import Issue, IssueSet

PR = "https://github.com/o/r/pull/1"
FORK_PR = "https://github.com/someone/r/pull/1"


def test_build_report_splits_issues(git_repo, monkeypatch):
    r = git_repo
    r.commit("init", {"m.py": ""})
    r.commit("Closes #3", {"a.py": ""})
    r.git("switch", "-q", "-c", "triage/issue-1")
    r.commit("Fix 1", {"m.py": "x"})
    r.git("switch", "-q", "-c", "fix-9-gone", "main")
    r.commit("Fix 9", {"m.py": "y"})
    r.git("switch", "-q", "main")

    issues = [
        Issue(1, "has branch", "u1", ""),
        Issue(2, "plain", "u2", "Traceback", frozenset({"bug"})),
        Issue(3, "fixed on main", "u3", ""),
        Issue(4, "merged pr", "u4", "", merged_prs=((PR, "2026-05-01"),)),
        Issue(5, "old merged pr", "u5", "", merged_prs=((PR, "2019-05-01"),)),
        Issue(6, "fork pr", "u6", "", merged_prs=((FORK_PR, "2026-05-01"),)),
    ]
    asked = {}

    def fake_fetch(name, cache=None, *, batch, wanted, extra):
        asked.update(batch=batch, wanted=wanted, extra=extra)
        return IssueSet(issues, total_open=40, complete=False)

    monkeypatch.setattr(report, "fetch_issues", fake_fetch)
    rep = report.build_report(
        RepoConfig("o/r", path=r.path, base="main", since="2020-01-01"),
        limit=None,
        include_linked=False,
    )
    assert [f.issue.number for f in rep.fixes] == [1]
    assert [b.issue for b in rep.closed_fixes] == [9]
    assert [(c.issue.number, len(c.commits), c.merged_prs) for c in rep.closable] == [
        (4, 0, ((PR, "2026-05-01"),)),  # newest evidence first
        (3, 1, ()),
    ]
    assert [i.number for i in rep.candidates] == [2, 5, 6]  # old PR, fork PR
    assert rep.base.subject == "Closes #3"
    # the clone tells which issues to look up: fix branches and fixing commits
    assert (asked["batch"], set(asked["wanted"]), asked["extra"]) == (100, {1, 9}, (3,))
    assert (rep.total_open, rep.known_open) == (40, 6)


def test_build_report_without_clone(monkeypatch):
    monkeypatch.setattr(
        report,
        "fetch_issues",
        lambda name, cache=None, **kw: IssueSet([Issue(5, "t", "u", "")], 1, True),
    )
    rep = report.build_report(RepoConfig("o/r"), limit=None, include_linked=False)
    assert rep.base is None and rep.fixes == [] and len(rep.candidates) == 1
    assert rep.known_open is None  # everything fetched
