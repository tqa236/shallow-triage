import pytest

from shallow_triage import local


@pytest.fixture
def repo(git_repo):
    r = git_repo
    r.commit("init", {"pkg/mod.py": "x = 1\n"})
    # fix branch named after the issue, touching code and a test
    r.git("switch", "-q", "-c", "triage/issue-12")
    r.commit(
        "Fix x\n\nExplain the fix.\n\nFixes #12",
        {"pkg/mod.py": "x = 2\n", "pkg/tests/test_mod.py": "def test(): pass\n"},
        date="2026-02-01T00:00:00",
    )
    # fix branch without a number in its name: issue from the commit message
    r.git("switch", "-q", "-c", "fix-typo", "main")
    r.commit("Typo\n\nCloses #34", {"pkg/mod.py": "x = 1  # typo\n"})
    # branch with no issue at all is skipped
    r.git("switch", "-q", "-c", "fix-nothing", "main")
    r.commit("Nothing", {"other.py": ""})
    # merged branch is skipped
    r.git("switch", "-q", "-c", "fix-56-merged", "main")
    r.commit("Merged", {"merged.py": ""})
    r.git("switch", "-q", "main")
    r.git("merge", "-q", "--ff-only", "fix-56-merged")
    # commits on main that close issues
    r.commit("BUG: thing\n\nCloses #78", {"a.py": ""}, date="2026-03-01T00:00:00")
    r.commit("Old fix, closes #90", {"b.py": ""}, date="2010-01-01T00:00:00")
    r.commit("Fixes gh-91 and see #92", {"c.py": ""}, date="2026-03-02T00:00:00")
    # a branch not matching the patterns is ignored
    r.git("switch", "-q", "-c", "wip-99", "main")
    r.commit("wip", {"d.py": ""})
    r.git("switch", "-q", "main")
    return r


def test_fix_branches(repo):
    got = {
        b.name: b
        for b in local.fix_branches(repo.path, "main", ("triage/issue-*", "fix-*"))
    }
    assert set(got) == {"triage/issue-12", "fix-typo"}

    b = got["triage/issue-12"]
    assert b.issue == 12
    assert [c.subject for c in b.commits] == ["Fix x"]
    assert b.commits[0].body == "Explain the fix.\n\nFixes #12"
    assert b.commits[0].date == "2026-02-01"
    assert {(f.path, f.added, f.deleted) for f in b.files} == {
        ("pkg/mod.py", 1, 1),
        ("pkg/tests/test_mod.py", 1, 0),
    }
    assert b.files_to_test == ("pkg/tests/test_mod.py",)
    assert "+x = 2" in b.diff

    t = got["fix-typo"]
    assert t.issue == 34
    assert t.files_to_test == ("pkg/mod.py",)  # no test files: test what changed


def test_closing_commits(repo):
    found = local.closing_commits(repo.path, "main", {12, 78, 90, 91, 92})
    assert set(found) == {78, 90, 91}
    assert found[78][0].subject == "BUG: thing"

    recent = local.closing_commits(repo.path, "main", {78, 90, 91}, since="2020-01-01")
    assert set(recent) == {78, 91}


def test_closing_refs():
    assert local.closing_refs("Fixes #1, closes gh-2. Resolved: #3. See #4") == [
        1,
        2,
        3,
    ]


def test_git_error(tmp_path):
    with pytest.raises(local.LocalError, match="not a git repository"):
        local.base_commit(tmp_path, "main")


@pytest.mark.parametrize(
    "name, issue",
    [
        ("triage/issue-7", 7),
        ("fix-38591-zmod", 38591),
        ("bug-ecdf-6121", 6121),
        ("fix-py3-x", None),
    ],
)
def test_issue_in_branch_name(name, issue):
    m = local._ISSUE_IN_NAME_RE.search(name)
    assert (int(m.group(1)) if m else None) == issue


def test_fetch_base(git_repo, tmp_path):
    upstream = git_repo
    upstream.commit("one", {"a.py": ""})
    clone_path = tmp_path / "clone"
    upstream.git("clone", "-q", "-o", "upstream", str(upstream.path), str(clone_path))
    upstream.commit("two", {"b.py": ""})

    assert local.base_commit(clone_path, "upstream/main").subject == "one"
    local.fetch_base(clone_path, "upstream/main")
    assert local.base_commit(clone_path, "upstream/main").subject == "two"
    local.fetch_base(clone_path, "main")  # local branch: nothing to fetch


def test_git_errors_are_redacted(git_repo, monkeypatch):
    def fail(*args, **kwargs):
        import subprocess

        return subprocess.CompletedProcess(
            args, 128, b"", b"fatal: 'https://me:ghp_" + b"a" * 36 + b"@x/r' failed"
        )

    monkeypatch.setattr(local.subprocess, "run", fail)
    with pytest.raises(local.LocalError) as exc:
        local.base_commit(git_repo.path, "main")
    assert "ghp_" not in str(exc.value) and "me:" not in str(exc.value)


def test_unusual_file_names_and_user_git_config(git_repo):
    r = git_repo
    r.commit("init", {"old name.py": "x\n"})
    r.git("config", "color.ui", "always")
    r.git("switch", "-q", "-c", "triage/issue-5")
    r.git("mv", "old name.py", 'new "name".py')
    r.commit("rename", {'new "name".py': "y\n"})
    (b,) = local.fix_branches(r.path, "main", ("triage/issue-*",))
    assert {f.path for f in b.files} == {"old name.py", 'new "name".py'}
    assert "\x1b[" not in b.diff  # no color codes despite color.ui=always
