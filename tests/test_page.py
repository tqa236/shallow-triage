from datetime import UTC, datetime

from shallow_triage.issues import Issue
from shallow_triage.page import excerpt, render_page


def test_excerpt_skips_code_and_truncates():
    body = "```\ncode here\n```\n\nFirst   para\nwraps.\n\nSecond para."
    assert excerpt(body) == "First para wraps. Second para."
    assert excerpt("x" * 500, limit=10) == "x" * 9 + "…"


def test_render_page_escapes_and_lists_issues():
    issue = Issue(
        7,
        "<script>alert(1)</script>",
        "https://github.com/o/r/issues/7",
        "Steps to reproduce: run it",
        frozenset({"bug"}),
        linked_prs=("https://github.com/fork/r/pull/3",),
    )
    html = render_page(
        "o/r", [issue], total_open=42, generated_at=datetime(2026, 11, 1, tzinfo=UTC)
    )
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html
    assert "Top 1 of 42 open issues" in html
    assert "+5" in html and "labelled &#x27;bug&#x27;" in html
    assert "fork/r/pull/3" in html
