from datetime import UTC, datetime
from pathlib import Path

from shallow_triage.config import RepoConfig
from shallow_triage.issues import Issue
from shallow_triage.local import Commit, FileChange, FixBranch
from shallow_triage.page import excerpt, render_page
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
    assert "cd &#x27;/src/my r&#x27;" in html
    assert "git diff upstream/main...triage/issue-7" in html
    assert "git switch triage/issue-7 &amp;&amp; bin/test tests/test_m.py" in html
    assert "https://github.com/o/r/commit/1234567890ab" in html
    assert "upstream/main</code> @ <code>feedbeef00" in html
    assert "no longer open (1)" in html and "https://github.com/o/r/issues/9" in html
    assert "$" not in html  # every template placeholder was filled
