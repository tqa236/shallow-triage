import pytest

from shallow_triage import workflow
from shallow_triage.config import RepoConfig
from shallow_triage.workflow import BROKEN, FIXED, SKIP, find_first_fixed


@pytest.mark.parametrize("first", range(8))
def test_find_first_fixed(first):
    commits = [f"c{i}" for i in range(8)]
    asked = []

    def state_of(sha):
        asked.append(sha)
        return FIXED if int(sha[1:]) >= first else BROKEN

    commits_with_end = commits + ["end"]  # the last one is known fixed
    found, candidates = find_first_fixed(
        commits_with_end, lambda s: FIXED if s == "end" else state_of(s)
    )
    assert found == commits_with_end[first] and candidates == []
    assert len(asked) <= 4  # log2 of 9, rounded up


def test_find_first_fixed_with_skips():
    # a skip that doesn't matter: c is broken, so d is the fix
    states = {"a": BROKEN, "b": SKIP, "c": BROKEN, "d": FIXED}
    assert find_first_fixed(list("abcd"), states.__getitem__) == ("d", [])

    # b couldn't be tested and could itself be the fix: either b or c
    states = {"a": BROKEN, "b": SKIP, "c": FIXED, "d": FIXED}
    assert find_first_fixed(list("abcd"), states.__getitem__) == (None, ["b", "c"])


@pytest.fixture
def history(git_repo):
    """main: c0..c5; the bug is fixed in c3. A topic branch is checked out."""
    r = git_repo
    for i in range(6):
        state = "fixed\n" if i >= 3 else "broken\n"
        r.commit(f"c{i}", {"state.txt": state, "n.txt": f"{i}\n"})
    r.git("tag", "v0", "HEAD~5")  # c0
    r.git("switch", "-q", "-c", "topic")
    r.commit("topic work", {"notes.txt": "x\n"})
    return r


def _script(tmp_path, code):
    script = tmp_path / "repro.sh"
    script.write_text(code)
    return script


def _sha(r, rev):
    return r.git("rev-parse", rev).strip()


def test_bisect_finds_the_fix_and_restores_the_branch(history, tmp_path, monkeypatch):
    monkeypatch.setattr(
        workflow, "pull_request_of", lambda repo, sha: "https://github.com/o/r/pull/9"
    )
    config = RepoConfig("o/r", path=history.path, base="main", run="sh {script}")
    script = _script(tmp_path, "grep -q fixed state.txt\n")
    result = workflow.bisect(config, script, old="v0", log=lambda line: None)
    assert result.status == "fixed"
    assert result.first_fixed == _sha(history, "main~2")  # c3
    assert result.pr == "https://github.com/o/r/pull/9"
    assert history.git("branch", "--show-current").strip() == "topic"
    assert (history.path / "notes.txt").exists()


def test_bisect_unclear_when_the_commit_before_the_fix_cant_build(history, tmp_path):
    c2 = _sha(history, "main~3")
    config = RepoConfig(
        "o/r",
        path=history.path,
        base="main",
        run="sh {script}",
        build=f'test "$(git rev-parse HEAD)" != {c2}',
    )
    script = _script(tmp_path, "grep -q fixed state.txt\n")
    result = workflow.bisect(config, script, old="v0", log=lambda line: None)
    assert result.status == "unclear"
    assert result.candidates == (c2, _sha(history, "main~2"))


def test_bisect_still_broken_and_not_reproducible(history, tmp_path):
    config = RepoConfig("o/r", path=history.path, base="main", run="sh {script}")
    always_broken = _script(tmp_path, "exit 1\n")
    assert workflow.bisect(config, always_broken, old="v0", log=print).status == (
        "still broken"
    )
    always_fixed = _script(tmp_path, "exit 0\n")
    assert workflow.bisect(config, always_fixed, old="v0", log=print).status == (
        "not reproducible"
    )
    assert history.git("branch", "--show-current").strip() == "topic"


def test_bisect_and_fix_refuse_a_dirty_workdir(history, tmp_path):
    (history.path / "notes.txt").write_text("edited\n")
    config = RepoConfig("o/r", path=history.path, base="main", run="sh {script}")
    with pytest.raises(ValueError, match="uncommitted changes"):
        workflow.bisect(config, _script(tmp_path, "exit 0\n"), old="v0")
    with pytest.raises(ValueError, match="uncommitted changes"):
        workflow.start_fix(config, 5)


def test_start_fix_and_run_tests(history):
    config = RepoConfig(
        "o/r", path=history.path, base="main", test="cat {files}", build="true"
    )
    assert workflow.start_fix(config, 42) == "triage/issue-42"
    assert history.git("branch", "--show-current").strip() == "triage/issue-42"
    with pytest.raises(ValueError, match="already exists"):
        workflow.start_fix(config, 42)
    with pytest.raises(ValueError, match="changes nothing"):
        workflow.run_tests(config)

    history.commit("fix", {"tests/test_x.py": "ok\n", "src.py": "x\n"})
    build, test = workflow.run_tests(config)
    assert build.returncode == 0
    assert (test.command, test.returncode, test.output) == (
        "cat tests/test_x.py",
        0,
        "ok\n",
    )


def test_pull_request_of(monkeypatch):
    pulls = [
        {"merged_at": None, "html_url": "https://github.com/o/r/pull/1"},
        {"merged_at": "2026", "html_url": "https://github.com/fork/r/pull/2"},
        {"merged_at": "2026", "html_url": "https://github.com/o/r/pull/3"},
    ]
    monkeypatch.setattr(workflow.gh, "api", lambda path: pulls)
    assert workflow.pull_request_of("o/r", "abc") == "https://github.com/o/r/pull/3"
