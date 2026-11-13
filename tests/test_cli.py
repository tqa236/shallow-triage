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
            "labels": [],
            "comments": 0,
            "assigned": False,
        }
    ]


def test_candidates_filters_and_config(tmp_path, git_repo, monkeypatch, capsys):
    git_repo.commit("init", {"a.py": ""})
    git_repo.git("switch", "-q", "-c", "triage/issue-4")
    git_repo.commit("fix", {"a.py": "x"})
    git_repo.git("switch", "-q", "main")
    issues = [
        Issue(2, "bug", "https://github.com/o/r/issues/2", "", frozenset({"bug"})),
        Issue(3, "q", "https://github.com/o/r/issues/3", "", comments=30),
        Issue(4, "fixed", "https://github.com/o/r/issues/4", "", frozenset({"bug"})),
        Issue(5, "mine", "https://github.com/o/r/issues/5", "", assigned=True),
    ]

    def fetch(repo, cache=None, **kw):
        return IssueSet(issues, total_open=4, complete=True)

    monkeypatch.setattr(report, "fetch_issues", fetch)
    cfg = _write_config(tmp_path, git_repo.path)
    data = tmp_path / "data"

    def numbers(*options):
        argv = ["candidates", "o/r", "--data", str(data), "--no-cache", "-c", str(cfg)]
        assert cli.main([*argv, *options]) == 0
        return [c["number"] for c in json.loads(capsys.readouterr().out)]

    # #4 has a fix branch in the configured clone, so it isn't a candidate
    assert sorted(numbers()) == [2, 3, 5]
    assert (data / "o" / "r.json").exists()
    (data / "o" / "r.json").unlink()
    assert numbers("--label", "BUG") == [2]
    assert sorted(numbers("--max-comments", "5", "--unassigned")) == [2]
    assert sorted(numbers("--exclude-label", "bug")) == [3, 5]
    assert not (data / "o" / "r.json").exists()  # filtered lists aren't saved


def test_named_repos_use_their_config(tmp_path, git_repo, monkeypatch):
    git_repo.commit("init", {"a.py": ""})
    git_repo.git("switch", "-q", "-c", "triage/issue-1")
    git_repo.commit("fix", {"a.py": "x"})
    git_repo.git("switch", "-q", "main")
    monkeypatch.setattr(report, "fetch_issues", _fake_fetch)
    cfg = _write_config(tmp_path, git_repo.path)
    data = tmp_path / "data"
    assert cli.main(["collect", "o/r", "-c", str(cfg), "--data", str(data)]) == 0
    saved = report.load_report(data / "o" / "r.json")
    assert [f.branch.name for f in saved.fixes] == ["triage/issue-1"]
    page = tmp_path / "page.html"
    argv = ["page", "o/r", "-c", str(cfg), "--data", str(data), "-o", str(page)]
    assert cli.main([*argv, "--no-cache"]) == 0
    assert "triage/issue-1" in page.read_text()


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


def _write_config(tmp_path, repo_path, **extra):
    lines = [
        "[[repo]]",
        'name = "o/r"',
        f'path = "{repo_path}"',
        'base = "main"',
        'run = "sh {script}"',
        'test = "cat {files}"',
    ] + [f'{k} = "{v}"' for k, v in extra.items()]
    cfg = tmp_path / "triage.toml"
    cfg.write_text("\n".join(lines) + "\n")
    return cfg


def test_fix_and_test_commands(tmp_path, git_repo, capsys):
    git_repo.commit("init", {"a.py": ""})
    cfg = _write_config(tmp_path, git_repo.path)
    assert cli.main(["fix", "o/r", "7", "-c", str(cfg)]) == 0
    out = capsys.readouterr().out
    assert "On triage/issue-7" in out and "Fixes #7" in out
    git_repo.commit("fix", {"tests/test_a.py": "passed\n"})
    assert cli.main(["test", "o/r", "-c", str(cfg)]) == 0
    assert "$ cat tests/test_a.py\npassed" in capsys.readouterr().out


def test_commands_use_the_default_config(tmp_path, git_repo, capsys):
    from shallow_triage.config import default_config_path

    git_repo.commit("init", {"a.py": ""})
    path = default_config_path()
    path.parent.mkdir(parents=True)
    path.write_text(_write_config(tmp_path, git_repo.path).read_text())
    assert cli.main(["fix", "o/r", "8"]) == 0
    assert cli.main(["fix", "o/unknown", "8"]) == 1
    assert "o/unknown is not in the config" in capsys.readouterr().err


def test_bisect_records_a_finding(tmp_path, git_repo, monkeypatch, capsys):
    from shallow_triage import workflow
    from shallow_triage.findings import Findings, default_findings_dir

    for i, state in enumerate(["broken", "broken", "fixed", "fixed"]):
        git_repo.commit(f"c{i}", {"state.txt": state, "n.txt": str(i)})
    monkeypatch.setattr(workflow, "pull_request_of", lambda r, s: None)
    cfg = _write_config(tmp_path, git_repo.path)
    script = tmp_path / "repro.sh"
    script.write_text("grep -q fixed state.txt\n")
    code = cli.main(
        [
            "bisect",
            "o/r",
            "5",
            "--old",
            "HEAD~3",
            "--script",
            str(script),
            "-c",
            str(cfg),
        ]
    )
    assert code == 0
    fixed_by = git_repo.git("rev-parse", "HEAD~1").strip()
    assert f"#5 was fixed by {fixed_by}" in capsys.readouterr().out
    finding = Findings(default_findings_dir(), "o/r").items[5]
    assert finding.commit == fixed_by and finding.method.startswith(
        "bisect HEAD~3..main"
    )


def test_run_and_check_commands(tmp_path, git_repo, capsys):
    git_repo.commit("c0", {"n.txt": "0\n"})
    git_repo.commit("c1", {"n.txt": "1\n"})
    cfg = _write_config(tmp_path, git_repo.path)
    scripts = tmp_path.parent / f"{tmp_path.name}-scripts"
    scripts.mkdir()
    script = scripts / "repro.sh"
    script.write_text("grep -q 1 n.txt\n")
    assert cli.main(["run", "o/r", str(script), "-c", str(cfg)]) == 0
    assert "fixed at the current checkout" in capsys.readouterr().err
    assert cli.main(["run", "o/r", str(script), "--at", "HEAD~1", "-c", str(cfg)]) == 1
    assert "broken at HEAD~1 (exit 1)" in capsys.readouterr().err

    assert cli.main(["check", "-c", str(cfg)]) == 0
    assert "ok   run: sh {script} exits 0" in capsys.readouterr().out
    bad = _write_config(tmp_path, git_repo.path, venv=str(tmp_path / "none"))
    assert cli.main(["check", "-c", str(bad)]) == 1
    assert "FAIL venv: " in capsys.readouterr().out


def test_closable_add_list_remove(capsys):
    assert (
        cli.main(
            ["closable", "add", "o/r", "3", "--commit", "abc", "--method", "ran it"]
        )
        == 0
    )
    assert cli.main(["closable", "list", "o/r"]) == 0
    assert "#3: fixed by abc" in capsys.readouterr().out
    assert (
        cli.main(
            ["closable", "add", "o/r", "4", "--pr", "https://github.com/x/y/pull/1"]
        )
        == 1
    )
    assert "--pr must be a PR of o/r" in capsys.readouterr().err
    assert cli.main(["closable", "remove", "o/r", "3"]) == 0
    assert cli.main(["closable", "remove", "o/r", "3"]) == 1
    assert cli.main(["closable", "list", "../x"]) == 1


def test_safe_marks_and_repro(tmp_path, monkeypatch, capsys):
    from shallow_triage import cli as cli_mod

    body = {"text": "Repro:\n\n```python\nimport x\nx.f()\n```\n"}

    def fake_fetch(repo, number):
        return Issue(
            number, "t", f"https://github.com/o/r/issues/{number}", body["text"]
        )

    monkeypatch.setattr(cli_mod, "fetch_issue", fake_fetch)
    out = tmp_path / "repro.py"
    assert cli.main(["repro", "o/r", "5", "-o", str(out)]) == 1
    assert "isn't marked safe" in capsys.readouterr().err

    assert cli.main(["safe", "mark", "o/r", "5"]) == 0
    assert cli.main(["repro", "o/r", "5", "-o", str(out)]) == 0
    assert out.read_text().endswith("# --- block 1 ---\nimport x\nx.f()\n")

    body["text"] += "\n```python\nimport os; os.system('evil')\n```\n"  # edited later
    assert cli.main(["repro", "o/r", "5"]) == 1
    assert "changed since marked safe" in capsys.readouterr().err
    assert cli.main(["safe", "list", "o/r"]) == 0
    assert "#5: changed since marked safe" in capsys.readouterr().out
    assert cli.main(["safe", "unmark", "o/r", "5"]) == 0
