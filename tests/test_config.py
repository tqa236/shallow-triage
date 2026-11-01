from pathlib import Path

import pytest

from shallow_triage.config import DEFAULT_BRANCHES, RepoConfig, load_config


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
