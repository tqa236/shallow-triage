import json
from datetime import UTC, datetime

import pytest

from shallow_triage import report
from shallow_triage.config import RepoConfig
from shallow_triage.issues import Issue, IssueSet
from shallow_triage.local import Commit, FileChange, FixBranch

NOW = datetime(2026, 11, 1, tzinfo=UTC)

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
        generated_at=NOW,
    )
    assert [f.issue.number for f in rep.fixes] == [1]
    assert [b.issue for b in rep.closed_fixes] == [9]
    assert [(c.issue.number, len(c.commits), c.merged_prs) for c in rep.closable] == [
        (4, 0, ((PR, "2026-05-01"),)),  # newest evidence first
        (3, 1, ()),
    ]
    assert [c.issue.number for c in rep.candidates] == [2, 5, 6]  # old/fork PR
    assert [(c.score, c.reasons) for c in rep.candidates][0] == (
        5,
        ((3, "has reproduction steps or code"), (2, "labelled 'bug'")),
    )
    assert (rep.base, rep.base_commit.subject) == ("main", "Closes #3")
    assert rep.test == "python -m pytest {files}"
    # the clone tells which issues to look up: fix branches and fixing commits
    assert (asked["batch"], set(asked["wanted"]), asked["extra"]) == (100, {1, 9}, (3,))
    assert (rep.total_open, rep.known_open) == (40, 6)


def test_build_report_without_clone(monkeypatch):
    monkeypatch.setattr(
        report,
        "fetch_issues",
        lambda name, cache=None, **kw: IssueSet([Issue(5, "t", "u", "")], 1, True),
    )
    rep = report.build_report(
        RepoConfig("o/r"), limit=None, include_linked=False, generated_at=NOW
    )
    assert rep.base is None and rep.test is None
    assert rep.base_commit is None and rep.fixes == [] and len(rep.candidates) == 1
    assert rep.known_open is None  # everything fetched


def _full_report():
    branch = FixBranch(
        "triage/issue-1",
        1,
        (Commit("abc", "2026-10-30", "Fix", "Body\n\nFixes #1"),),
        (FileChange("m.py", 1, 0), FileChange("img.png", None, None)),
        "diff --git a/m.py b/m.py\n+x\n",
    )
    issue = Issue(
        1,
        "t",
        "https://github.com/o/r/issues/1",
        "body",
        frozenset({"bug", "docs"}),
        3,
        True,
        ("https://github.com/o/r/pull/2",),
        (("https://github.com/o/r/pull/3", "2026-01-01"),),
    )
    return report.RepoReport(
        repo="o/r",
        generated_at=NOW,
        total_open=10,
        candidates=[report.Candidate(issue, 2, ((2, "labelled 'bug'"),))],
        fixes=[report.FixEntry(branch, issue)],
        closed_fixes=[branch],
        closable=[report.Closable(issue, branch.commits, issue.merged_prs)],
        known_open=4,
        base="upstream/main",
        base_commit=Commit("def", "2026-11-01", "base"),
        test="pytest {files}",
    )


def test_save_and_load_round_trip(tmp_path):
    rep = _full_report()
    path = report.save_report(tmp_path, rep)
    assert path == tmp_path / "o" / "r.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert report.load_report(path) == rep
    assert report.report_paths(tmp_path) == [path]


def test_saved_report_has_no_local_path(tmp_path, git_repo):
    # build from a real clone: its location must not end up in the data
    git_repo.commit("init", {"m.py": ""})
    rep = report.RepoReport(repo="o/r", generated_at=NOW, total_open=0)
    text = report.save_report(tmp_path, rep).read_text()
    assert str(git_repo.path) not in text
    assert "path" not in json.loads(text)["report"]


def test_load_older_and_newer_files(tmp_path):
    path = report.save_report(tmp_path, _full_report())
    data = json.loads(path.read_text())
    # older file: fields added later are missing -> defaults
    for key in ("known_open", "test", "base_commit"):
        del data["report"][key]
    del data["report"]["candidates"][0]["reasons"]
    # newer file: keys this version doesn't know -> ignored
    data["report"]["added_later"] = {"x": 1}
    data["report"]["fixes"][0]["issue"]["added_later"] = True
    path.write_text(json.dumps(data))

    rep = report.load_report(path)
    assert (rep.known_open, rep.test, rep.base_commit) == (None, None, None)
    assert rep.candidates[0].reasons == ()
    assert rep.fixes[0].issue.labels == {"bug", "docs"}


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        '{"version": 99, "report": {}}',
        '{"version": 1, "report": {"repo": "o/r"}}',
        '{"version": 1, "report": {"repo": 1, "generated_at": "2026-01-01", "total_open": 0}}',
    ],
)
def test_load_rejects_unreadable_files(tmp_path, content):
    path = tmp_path / "o" / "r.json"
    path.parent.mkdir()
    path.write_text(content)
    with pytest.raises(ValueError, match="r.json"):
        report.load_report(path)
