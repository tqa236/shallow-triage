import subprocess

import pytest


class GitRepo:
    """A throwaway git repository with helpers to build history."""

    def __init__(self, path):
        self.path = path
        self.git("init", "-q", "-b", "main")

    def git(self, *args):
        cmd = ["git", "-c", "user.name=T", "-c", "user.email=t@example.com", *args]
        return subprocess.run(
            cmd, cwd=self.path, check=True, capture_output=True, text=True
        ).stdout

    def commit(self, message, files, date="2026-01-01T00:00:00"):
        for name, text in files.items():
            target = self.path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
            self.git("add", name)
        env_date = f"GIT_COMMITTER_DATE={date}"
        subprocess.run(
            [
                "env",
                env_date,
                "git",
                "-c",
                "user.name=T",
                "-c",
                "user.email=t@e.com",
                "commit",
                "-q",
                "-m",
                message,
            ],
            cwd=self.path,
            check=True,
            capture_output=True,
        )


@pytest.fixture
def git_repo(tmp_path):
    return GitRepo(tmp_path)
