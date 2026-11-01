from datetime import UTC, datetime

from shallow_triage import gh, site
from shallow_triage.issues import Issue

NOW = datetime(2026, 11, 1, tzinfo=UTC)


def _fake_fetch(repo):
    if repo == "o/broken":
        raise gh.GhError("not found")
    return [
        Issue(1, "a", "u1", "Traceback", frozenset({"bug"})),
        Issue(2, "b", "u2", "", linked_prs=("p",)),
    ]


def test_build_site_writes_index_and_pages(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(site, "fetch_open_issues", _fake_fetch)
    failed = site.build_site(
        ["o/one", "o/broken", "x/two", "o/one"],
        tmp_path,
        limit=10,
        include_linked=False,
        generated_at=NOW,
    )
    assert failed == ["o/broken"]
    assert "o/broken: not found" in capsys.readouterr().err

    index = (tmp_path / "index.html").read_text()
    assert index.count("<tr>") == 3  # header + two repos, duplicate dropped
    assert 'href="o/one.html"' in index and 'href="x/two.html"' in index
    assert "o/broken" not in index

    page = (tmp_path / "o" / "one.html").read_text()
    assert 'href="../index.html"' in page
    assert page.count("<li>") == 1  # linked issue dropped
