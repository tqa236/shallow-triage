from datetime import UTC, datetime
from pathlib import Path

import pytest

from shallow_triage.config import RepoConfig
from shallow_triage.issues import Issue
from shallow_triage.local import Commit, FileChange, FixBranch
from shallow_triage.page import _hide_home, excerpt, render_page
from shallow_triage.report import Closable, FixEntry, RepoReport

NOW = datetime(2026, 11, 1, tzinfo=UTC)


def test_excerpt_skips_code_and_truncates():
    body = "```\ncode here\n```\n\nFirst   para\nwraps.\n\nSecond para."
    assert excerpt(body) == "First para wraps. Second para."
    assert excerpt("x" * 500, limit=10) == "x" * 9 + "…"


def test_render_page_escapes_and_lists_candidates():
    issue = Issue(
        7,
        "<script>alert(1)</script>",
        "https://github.com/o/r/issues/7",
        "Steps to reproduce: run it",
        frozenset({"bug"}),
        linked_prs=("https://github.com/fork/r/pull/3",),
    )
    html = render_page(RepoReport(RepoConfig("o/r"), 42, [issue]), generated_at=NOW)
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html
    assert "42 open issues" in html and "Candidates (1)" in html
    assert "+5" in html and "labelled &#x27;bug&#x27;" in html
    assert "fork/r/pull/3" in html
    assert "Ready to review" not in html  # empty sections are left out


def test_render_page_fixes_and_closable():
    cfg = RepoConfig(
        "o/r", path=Path("/src/my r"), base="upstream/main", test="bin/test {files}"
    )
    branch = FixBranch(
        "triage/issue-7",
        7,
        (Commit("abcdef1234567", "2026-10-30", "Fix <x>", "Because.\n\nFixes #7"),),
        (FileChange("m.py", 3, 1), FileChange("tests/test_m.py", 5, 0)),
        "diff --git a/m.py b/m.py\n@@ -1 +1 @@\n-old\n+new <b>\n",
    )
    issue = Issue(7, "bug", "https://github.com/o/r/issues/7", "")
    closable = Closable(
        Issue(8, "done", "https://github.com/o/r/issues/8", ""),
        (Commit("1234567890ab", "2026-10-01", "BUG: done"),),
    )
    gone = FixBranch("fix-9-old", 9, branch.commits, (), "")
    rep = RepoReport(
        cfg,
        2,
        [],
        [FixEntry(branch, issue)],
        [gone],
        [closable],
        Commit("feedbeef00", "2026-11-01", "base"),
    )
    html = render_page(rep, generated_at=NOW, back_href="../index.html")
    assert "Ready to review (1)" in html and "Possibly closable (1)" in html
    assert "Fix &lt;x&gt;" in html and "Fixes #7" in html
    assert '<span class="add">+new &lt;b&gt;</span>' in html
    assert '<span class="del">-old</span>' in html
    assert "# in your clone of o/r" in html
    assert "/src/my r" not in html  # the clone's location is never shown
    assert "git diff upstream/main...triage/issue-7" in html
    assert "git switch triage/issue-7 &amp;&amp; bin/test tests/test_m.py" in html
    assert "https://github.com/o/r/commit/1234567890ab" in html
    assert "Compared with <code>upstream/main</code> @ <code>feedbeef00" in html
    assert "no longer open (1)" in html and "https://github.com/o/r/issues/9" in html
    assert "$" not in html  # every template placeholder was filled

    # contents link to each non-empty section and to each fix
    toc = html.split('<nav class="toc"')[1].split("</nav>")[0]
    for anchor in ["#ready", "#closable", "#closed", "#issue-7"]:
        assert f'href="{anchor}"' in toc
    assert "#candidates" not in toc  # empty section left out
    for target in ['id="ready"', 'id="closable"', 'id="closed"', 'id="issue-7"']:
        assert target in html


def test_render_index_links_counts_to_sections():
    from shallow_triage.page import RepoSummary, render_index

    html = render_index([RepoSummary("o/r", "o/r.html", 9, 2, 0, 5)], generated_at=NOW)
    assert 'href="o/r.html#ready">2</a>' in html
    assert 'href="o/r.html#candidates">5</a>' in html
    assert "#closable" not in html  # zero is not a link


def test_commands_quote_branch_names():
    cfg = RepoConfig("o/r", path=Path("/src/r"), base="main")
    branch = FixBranch(
        "triage/issue-1-;id>pwned",
        1,
        (Commit("abc", "2026-01-01", "fix"),),
        (FileChange("tests/test_x.py", 1, 0),),
        "",
    )
    issue = Issue(1, "t", "https://github.com/o/r/issues/1", "")
    html = render_page(
        RepoReport(cfg, 1, [], [FixEntry(branch, issue)]), generated_at=NOW
    )
    cmds = html.split("<h3>Commands</h3>")[1].split("</pre>")[0]
    assert "git diff &#x27;main...triage/issue-1-;id&gt;pwned&#x27;" in cmds
    assert "git switch &#x27;triage/issue-1-;id&gt;pwned&#x27; &amp;&amp;" in cmds


def test_unsafe_urls_are_not_linked():
    issue = Issue(1, "t", "javascript:alert(1)", "", linked_prs=("data:text/html,x",))
    html = render_page(RepoReport(RepoConfig("o/r"), 1, [issue]), generated_at=NOW)
    assert 'href="javascript:' not in html and 'href="data:' not in html
    assert '<span class="title">#1 t</span>' in html  # shown, but not a link
    assert "default-src 'none'" in html  # CSP blocks scripts in any case


@pytest.mark.parametrize(
    "text, expected",
    [
        ("source /home/me/venv/bin/activate", "source ~/venv/bin/activate"),
        ("cd /home/me", "cd ~"),
        ("&#x27;/home/me&#x27;", "&#x27;~&#x27;"),
        ("/home/me2/x and /home/meow", "/home/me2/x and /home/meow"),  # other users
    ],
)
def test_hide_home(text, expected):
    assert _hide_home(text, home="/home/me") == expected
    assert _hide_home(text, home="/") == text


def test_page_never_shows_home_or_clone_path(monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: Path("/home/me"))
    cfg = RepoConfig(
        "o/r",
        path=Path("/home/me/src/r"),
        base="main",
        test="/home/me/venv/bin/python -m pytest {files}",
    )
    branch = FixBranch(
        "triage/issue-1",
        1,
        (Commit("abc", "2026-01-01", "fix", "Tried /home/me/scratch/repro.py"),),
        (FileChange("tests/test_x.py", 1, 0),),
        "+see /home/me/notes\n",
    )
    issue = Issue(1, "t", "https://github.com/o/r/issues/1", "")
    rep = RepoReport(cfg, 1, [], [FixEntry(branch, issue)], base=Commit("b", "d", "s"))
    html = render_page(rep, generated_at=NOW)
    assert "/home/me" not in html
    assert "~/venv/bin/python -m pytest tests/test_x.py" in html
