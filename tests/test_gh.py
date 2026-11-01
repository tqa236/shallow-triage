import subprocess

import pytest

from shallow_triage import gh


def test_redact_hides_tokens():
    text = "bad creds ghp_" + "a" * 36 + " and github_pat_" + "B1_" * 10
    assert gh.redact(text) == "bad creds [REDACTED] and [REDACTED]"


def test_api_error_is_redacted(monkeypatch):
    monkeypatch.setattr(gh.shutil, "which", lambda _: "/usr/bin/gh")
    leaked = "token ghs_" + "x" * 36 + " rejected"
    monkeypatch.setattr(
        gh.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", leaked),
    )
    with pytest.raises(gh.GhError) as exc:
        gh.api("repos/o/r/issues")
    assert "ghs_" not in str(exc.value)


def test_api_flattens_pages(monkeypatch):
    monkeypatch.setattr(gh.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(
        gh.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "[[1, 2], [3]]", ""),
    )
    assert gh.api("x", paginate=True) == [1, 2, 3]
