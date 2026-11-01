import json

from shallow_triage import cli, report
from shallow_triage.issues import Issue, IssueSet


def _fake_fetch(repo, cache=None, **kw):
    issue = Issue(1, "a", "https://github.com/o/r/issues/1", "Traceback")
    return IssueSet([issue], total_open=1, complete=True)


def test_collect_then_render_offline(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "fetch_issues", _fake_fetch)
    data = tmp_path / "data"
    assert cli.main(["collect", "o/r", "--data", str(data), "--no-cache"]) == 0
    assert (data / "o" / "r.json").exists()

    def no_network(*a, **k):
        raise AssertionError("render must not fetch")

    monkeypatch.setattr(report, "fetch_issues", no_network)
    out = tmp_path / "site"
    assert cli.main(["render", "--data", str(data), "-o", str(out)]) == 0
    assert "#1 a" in (out / "o" / "r.html").read_text()
    assert 'href="o/r.html"' in (out / "index.html").read_text()


def test_render_reports_missing_and_unreadable(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(report, "fetch_issues", _fake_fetch)
    data = tmp_path / "data"
    cli.main(["collect", "o/r", "--data", str(data), "--no-cache"])
    (data / "o" / "bad.json").write_text("not json")

    out = tmp_path / "site"
    code = cli.main(["render", "o/r", "o/none", "--data", str(data), "-o", str(out)])
    err = capsys.readouterr().err
    assert code == 1
    assert "bad.json: not a readable report" in err
    assert "o/none: no saved report" in err
    assert (out / "o" / "r.html").exists()  # the readable one is still rendered


def test_candidates_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(report, "fetch_issues", _fake_fetch)
    code = cli.main(["candidates", "o/r", "--data", str(tmp_path), "--no-cache"])
    assert code == 0
    assert json.loads(capsys.readouterr().out) == [
        {
            "number": 1,
            "title": "a",
            "url": "https://github.com/o/r/issues/1",
            "score": 3,
            "linked_prs": [],
        }
    ]


def test_errors_exit_1(tmp_path, capsys):
    assert cli.main(["candidates", "bad", "--data", str(tmp_path)]) == 1
    assert "expected owner/name" in capsys.readouterr().err


def test_render_keeps_the_given_order(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "fetch_issues", _fake_fetch)
    data = tmp_path / "data"
    cli.main(["collect", "z/last", "a/first", "--data", str(data), "--no-cache"])
    out = tmp_path / "site"
    cli.main(["render", "z/last", "a/first", "--data", str(data), "-o", str(out)])
    index = (out / "index.html").read_text()
    assert index.index("z/last.html") < index.index("a/first.html")
