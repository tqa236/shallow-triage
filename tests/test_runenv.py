from pathlib import Path

import pytest

from shallow_triage import runenv
from shallow_triage.config import RepoConfig
from shallow_triage.runenv import TIMEOUT, clean_env, run_command


def test_clean_env_drops_credentials_and_activates_venv():
    base = {
        "PATH": "/usr/bin",
        "HOME": "/home/me",
        "GH_TOKEN": "x",
        "GITHUB_TOKEN": "x",
        "AWS_SECRET_ACCESS_KEY": "x",
        "OPENAI_API_KEY": "x",
        "SSH_AUTH_SOCK": "x",
        "DB_PASSWORD": "x",
        "GIT_AUTHOR_NAME": "me",
        "TOKENIZERS_PARALLELISM": "false",
        "PYTHONHOME": "/py",
    }
    env = clean_env(Path("/venvs/a"), base)
    assert set(env) == {
        "PATH",
        "HOME",
        "GIT_AUTHOR_NAME",
        "TOKENIZERS_PARALLELISM",
        "VIRTUAL_ENV",
    }
    assert env["PATH"] == "/venvs/a/bin:/usr/bin"
    assert env["VIRTUAL_ENV"] == "/venvs/a"
    assert clean_env(None, {"PATH": "/usr/bin"}) == {"PATH": "/usr/bin"}


def test_run_command_in_workdir_with_venv(tmp_path, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "secret")
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    tool = venv / "bin" / "only-in-venv"
    tool.write_text("#!/bin/sh\necho from-venv\n")
    tool.chmod(0o755)
    config = RepoConfig("o/r", path=tmp_path, venv=venv)
    result = run_command(config, 'only-in-venv; pwd; echo "token=${GH_TOKEN:-none}"')
    assert result.returncode == 0
    assert result.output.splitlines() == ["from-venv", str(tmp_path), "token=none"]


def test_run_command_timeout(tmp_path):
    result = run_command(RepoConfig("o/r", path=tmp_path), "sleep 5", timeout=0.2)
    assert result.returncode == TIMEOUT and "timed out" in result.output


def test_run_command_needs_a_path():
    with pytest.raises(ValueError, match="no 'path'"):
        run_command(RepoConfig("o/r"), "true")


def test_test_command_quotes_files():
    config = RepoConfig("o/r", test="pytest -q {files}")
    assert runenv.test_command(config, ("a b.py", "c.py")) == "pytest -q 'a b.py' c.py"
