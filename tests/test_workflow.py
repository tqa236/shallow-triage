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
    with pytest.raises(ValueError, match="nothing changed"):
        workflow.run_tests(config)

    history.commit("fix", {"tests/test_x.py": "ok\n", "src.py": "x\n"})
    build, test = workflow.run_tests(config)
    assert build.returncode == 0
    assert (test.command, test.returncode, test.output) == (
        "cat tests/test_x.py",
        0,
        "ok\n",
    )


def test_run_tests_includes_uncommitted_and_new_files(history):
    config = RepoConfig("o/r", path=history.path, base="main", test="cat {files}")
    workflow.start_fix(config, 42)
    (history.path / "tests").mkdir()
    (history.path / "tests" / "test_new.py").write_text("new\n")  # untracked
    (test,) = workflow.run_tests(config)
    assert (test.command, test.output) == ("cat tests/test_new.py", "new\n")

    (history.path / "tests" / "test_new.py").unlink()
    (history.path / "n.txt").write_text("edited\n")  # tracked, not committed
    (test,) = workflow.run_tests(config)
    assert (test.command, test.output) == ("cat n.txt", "edited\n")


def test_keep_local_paths(history, tmp_path_factory):
    (history.path / "notes.txt").write_text("my settings\n")
    config = RepoConfig(
        "o/r",
        path=history.path,
        base="main",
        run="sh {script}",
        test="cat {files}",
        keep_local=("notes.txt",),
    )
    outside = tmp_path_factory.mktemp("scripts")  # not an untracked file
    script = _script(outside, "grep -q fixed state.txt\n")
    assert workflow.bisect(config, script, old="v0", log=print).status == "fixed"
    assert (history.path / "notes.txt").read_text() == "my settings\n"
    assert history.git("branch", "--show-current").strip() == "topic"

    workflow.start_fix(config, 42)  # carries the kept change along
    assert (history.path / "notes.txt").read_text() == "my settings\n"
    with pytest.raises(ValueError, match="nothing changed"):
        workflow.run_tests(config)  # the kept change isn't tested

    (history.path / "n.txt").write_text("edited\n")
    with pytest.raises(ValueError, match=r"uncommitted changes \(n.txt\)"):
        workflow.start_fix(config, 43)


@pytest.mark.parametrize(
    ("code", "output", "state"),
    [
        (0, "", FIXED),
        (1, "AssertionError", BROKEN),
        (1, "Traceback\nModuleNotFoundError: No module named 'x'\n", SKIP),
        (1, "ImportError: cannot import name 'y'", SKIP),
        (125, "", SKIP),
        (127, "sh: foo: not found", SKIP),
        (124, "[timed out]", SKIP),
    ],
)
def test_classify(code, output, state):
    assert workflow.classify(workflow.Result("x", code, output))[0] == state


def test_bisect_skips_import_errors(history, tmp_path):
    # the old end can't import: that's not the bug, so bisect refuses
    config = RepoConfig("o/r", path=history.path, base="main", run="sh {script}")
    script = _script(
        tmp_path,
        "grep -q fixed state.txt && exit 0\n"
        "echo \"ModuleNotFoundError: No module named 'dep'\"; exit 1\n",
    )
    with pytest.raises(ValueError, match="couldn't tell at v0"):
        workflow.bisect(config, script, old="v0", log=print)


def test_run_script_at_a_commit(history, tmp_path):
    config = RepoConfig(
        "o/r", path=history.path, base="main", run="sh {script}", build="true"
    )
    script = _script(tmp_path, "cat n.txt\n")
    build, ran = workflow.run_script(config, script, at="v0")
    assert (build.command, ran.output) == ("true", "0\n")
    assert history.git("branch", "--show-current").strip() == "topic"
    (ran,) = workflow.run_script(config, script)  # current checkout, no build
    assert ran.output == "5\n"


def test_check_config(history, tmp_path):
    good = RepoConfig("o/r", path=history.path, base="main", run="sh {script}")
    assert all(c.ok for c in workflow.check_config(good))
    bad = RepoConfig(
        "o/r",
        path=history.path,
        base="nope",
        run="no-such-command {script}",
        venv=tmp_path / "venv",
    )
    failed = {c.name: c.detail for c in workflow.check_config(bad) if not c.ok}
    assert set(failed) == {"base", "venv", "run"}
    assert failed["run"].startswith("exit 127")
    missing = RepoConfig("o/r", path=tmp_path / "missing")
    assert [c.ok for c in workflow.check_config(missing)] == [False]
    assert [c.ok for c in workflow.check_config(RepoConfig("o/r"))] == [False]


def test_pull_request_of(monkeypatch):
    pulls = [
        {"merged_at": None, "html_url": "https://github.com/o/r/pull/1"},
        {"merged_at": "2026", "html_url": "https://github.com/fork/r/pull/2"},
        {"merged_at": "2026", "html_url": "https://github.com/o/r/pull/3"},
    ]
    monkeypatch.setattr(workflow.gh, "api", lambda path: pulls)
    assert workflow.pull_request_of("o/r", "abc") == "https://github.com/o/r/pull/3"
