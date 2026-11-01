from pathlib import Path

import pytest

from shallow_triage.config import DEFAULT_BRANCHES, RepoConfig, find_repo, load_config


def test_load_config(tmp_path):
    cfg = tmp_path / "t.toml"
    cfg.write_text(
        """
[[repo]]
name = "o/a"

[[repo]]
name = "o/b"
path = "~/src/b"
base = "upstream/develop"
test = "tox -e py -- {files}"
branches = ["fix-*"]
since = 2023-02-01
"""
    )
    a, b = load_config(cfg)
    assert a == RepoConfig("o/a")
    assert a.branches == DEFAULT_BRANCHES
    assert b.path == Path("~/src/b").expanduser()
    assert (b.base, b.branches, b.since) == (
        "upstream/develop",
        ("fix-*",),
        "2023-02-01",
    )


@pytest.mark.parametrize(
    "text, match",
    [
        ('[[repo]]\nname = "o/a"\ntypo = 1', "unknown keys"),
        ('[[repo]]\npath = "x"', "missing"),
    ],
)
def test_load_config_errors(tmp_path, text, match):
    cfg = tmp_path / "t.toml"
    cfg.write_text(text)
    with pytest.raises(ValueError, match=match):
        load_config(cfg)


def test_load_config_run_settings(tmp_path):
    cfg = tmp_path / "t.toml"
    cfg.write_text(
        """
[[repo]]
name = "o/a"
path = "~/src/a"
workdir = "~/src/a-wt"
venv = "~/venv/a"
build = "pip install -e ."
run = "python -X dev {script}"
"""
    )
    (a,) = load_config(cfg)
    assert a.work_path == Path("~/src/a-wt").expanduser()
    assert a.venv == Path("~/venv/a").expanduser()
    assert (a.build, a.run) == ("pip install -e .", "python -X dev {script}")
    assert RepoConfig("o/b", path=Path("/x")).work_path == Path("/x")


@pytest.mark.parametrize(
    "line, match",
    [('test = "pytest"', "needs {files}"), ('run = "python"', "needs {script}")],
)
def test_commands_need_their_placeholder(tmp_path, line, match):
    cfg = tmp_path / "t.toml"
    cfg.write_text(f'[[repo]]\nname = "o/a"\n{line}\n')
    with pytest.raises(ValueError, match=match):
        load_config(cfg)


def test_find_repo():
    repos = [RepoConfig("o/a"), RepoConfig("o/b")]
    assert find_repo(repos, "o/b") is repos[1]
    with pytest.raises(ValueError, match="not in the config"):
        find_repo(repos, "o/c")
