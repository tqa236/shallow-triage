from datetime import UTC, datetime

from shallow_triage import gh, report, site
from shallow_triage.config import RepoConfig
from shallow_triage.issues import Issue, IssueSet

NOW = datetime(2026, 11, 1, tzinfo=UTC)
LATER = datetime(2026, 11, 2, tzinfo=UTC)


def _fake_fetch(repo, cache=None, **kw):
    if repo == "o/broken":
        raise gh.GhError("not found")
    found = [
        Issue(1, "a", "u1", "Traceback", frozenset({"bug"})),
        Issue(2, "b", "u2", "", linked_prs=("p",)),
    ]
    return IssueSet(found, total_open=2, complete=True)


def _build(tmp_path, names, at=NOW):
    return site.build_site(
        [RepoConfig(n) for n in names],
        tmp_path / "site",
        tmp_path / "data",
        limit=10,
        include_linked=False,
        generated_at=at,
    )


def test_build_site_writes_index_pages_and_data(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(report, "fetch_issues", _fake_fetch)
    failed = _build(tmp_path, ["o/one", "o/broken", "x/two", "o/one"])
    assert failed == ["o/broken"]
    assert "o/broken: not found" in capsys.readouterr().err

    index = (tmp_path / "site" / "index.html").read_text()
    assert index.count("<tr>") == 3  # header + two repos, duplicate dropped
    assert 'href="o/one.html"' in index and 'href="x/two.html"' in index
    assert "o/broken" not in index

    page = (tmp_path / "site" / "o" / "one.html").read_text()
    assert 'href="../index.html"' in page
    assert page.count('<li class="card"') == 1  # linked issue dropped

    saved = report.report_paths(tmp_path / "data")
    assert [p.relative_to(tmp_path / "data").as_posix() for p in saved] == [
        "o/one.json",
        "x/two.json",
    ]


def test_render_site_from_saved_reports_only(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "fetch_issues", _fake_fetch)
    _build(tmp_path, ["o/one"])

    def no_network(*a, **k):
        raise AssertionError("render must not fetch")

    monkeypatch.setattr(report, "fetch_issues", no_network)
    reports = [report.load_report(p) for p in report.report_paths(tmp_path / "data")]
    site.render_site(reports, tmp_path / "again", generated_at=LATER)
    assert (tmp_path / "again" / "o" / "one.html").read_text() == (
        tmp_path / "site" / "o" / "one.html"
    ).read_text()


def test_failed_repo_keeps_its_last_report(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(report, "fetch_issues", _fake_fetch)
    _build(tmp_path, ["o/one"])

    def fail(*a, **k):
        raise gh.GhError("rate limited")

    monkeypatch.setattr(report, "fetch_issues", fail)
    assert _build(tmp_path, ["o/one", "../x"], at=LATER) == ["o/one", "../x"]
    assert "showing the report from before" in capsys.readouterr().err
    index = (tmp_path / "site" / "index.html").read_text()
    assert 'href="o/one.html"' in index
    page = (tmp_path / "site" / "o" / "one.html").read_text()
    assert "2026-11-01" in page  # the old report's own date


def test_build_site_reports_bad_clone(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(report, "fetch_issues", _fake_fetch)
    failed = site.build_site(
        [RepoConfig("o/one", path=tmp_path / "missing")],
        tmp_path / "out",
        tmp_path / "data",
        limit=10,
        include_linked=False,
        generated_at=NOW,
    )
    assert failed == ["o/one"]
    assert "o/one: git log" in capsys.readouterr().err
