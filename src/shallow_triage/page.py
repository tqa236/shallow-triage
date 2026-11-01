"""Render ranked issues as a single self-contained HTML page."""

import re
from dataclasses import dataclass
from datetime import datetime
from html import escape

from shallow_triage.issues import Issue, score_issue, score_reasons

_CODE_BLOCK_RE = re.compile(r"```.*?(```|$)", re.S)
_EXCERPT_LEN = 280

_CSS = """
:root {
  --bg: #fafaf9; --fg: #1c1917; --muted: #78716c; --card: #ffffff;
  --border: #e7e5e4; --link: #1d4ed8; --plus: #15803d; --minus: #b91c1c;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #1c1917; --fg: #f5f5f4; --muted: #a8a29e; --card: #292524;
    --border: #44403c; --link: #93c5fd; --plus: #86efac; --minus: #fca5a5;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 32px 16px; background: var(--bg); color: var(--fg);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
}
main { max-width: 760px; margin: 0 auto; }
h1 { font-size: 1.5rem; margin: 0 0 4px; }
.meta { color: var(--muted); margin: 0 0 24px; }
a { color: var(--link); }
ol { list-style: none; padding: 0; margin: 0; }
li {
  background: var(--card); border: 1px solid var(--border); border-radius: 8px;
  padding: 16px; margin-bottom: 12px; overflow-wrap: anywhere;
}
.head { display: flex; gap: 12px; align-items: baseline; }
.score { font-weight: 600; font-variant-numeric: tabular-nums; min-width: 2.5em; }
.title { font-weight: 600; text-decoration: none; }
.excerpt { color: var(--muted); margin: 8px 0; }
.reasons { margin: 0; padding: 0; font-size: 0.875rem; }
.reasons span { display: inline-block; margin-right: 12px; }
.plus { color: var(--plus); }
.minus { color: var(--minus); }
.linked { font-size: 0.875rem; margin-top: 8px; }
.back { display: inline-block; margin-bottom: 16px; font-size: 0.875rem; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; background: var(--card); }
th, td {
  padding: 8px 12px; border-bottom: 1px solid var(--border); text-align: left;
}
th { font-size: 0.8125rem; color: var(--muted); font-weight: 600; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
"""


def excerpt(body: str, limit: int = _EXCERPT_LEN) -> str:
    """Start of the prose in ``body``, with code blocks and whitespace collapsed."""
    text = " ".join(_CODE_BLOCK_RE.sub(" ", body).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _issue_html(issue: Issue) -> str:
    reasons = "".join(
        f'<span class="{"plus" if pts > 0 else "minus"}">{pts:+d} {escape(why)}</span>'
        for pts, why in score_reasons(issue)
    )
    linked = ""
    if issue.linked_prs:
        links = ", ".join(
            f'<a href="{escape(url)}">{escape(url.removeprefix("https://github.com/"))}</a>'
            for url in issue.linked_prs
        )
        linked = f'<div class="linked">Open PRs: {links}</div>'
    return (
        "<li>"
        '<div class="head">'
        f'<span class="score">{score_issue(issue):+d}</span>'
        f'<a class="title" href="{escape(issue.url)}">'
        f"#{issue.number} {escape(issue.title)}</a>"
        "</div>"
        f'<p class="excerpt">{escape(excerpt(issue.body))}</p>'
        f'<div class="reasons">{reasons or "<span>no signals</span>"}</div>'
        f"{linked}"
        "</li>"
    )


def _document(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>{_CSS}</style>
</head>
<body>
<main>
{body}
</main>
</body>
</html>
"""


def _when(generated_at: datetime) -> str:
    return generated_at.strftime("%Y-%m-%d %H:%M %Z").strip()


def render_page(
    repo: str,
    issues: list[Issue],
    *,
    total_open: int,
    generated_at: datetime,
    back_href: str | None = None,
) -> str:
    """Return a complete HTML document listing ``issues`` in the given order."""
    items = "\n".join(_issue_html(i) for i in issues)
    when = _when(generated_at)
    meta = f"Top {len(issues)} of {total_open} open issues · generated {when}"
    back = ""
    if back_href:
        back = f'<a class="back" href="{escape(back_href)}">← All repositories</a>\n'
    repo_link = f'<a href="https://github.com/{escape(repo)}">{escape(repo)}</a>'
    return _document(
        f"{repo} triage",
        f"""{back}<h1>Triage: {repo_link}</h1>
<p class="meta">{escape(meta)}</p>
<ol>
{items}
</ol>""",
    )


@dataclass(frozen=True)
class RepoSummary:
    """One row of the index page."""

    repo: str
    href: str
    total_open: int
    linked: int
    shown: int
    best_score: int | None


def render_index(summaries: list[RepoSummary], *, generated_at: datetime) -> str:
    """Return the overview page linking to each repository's triage page."""
    rows = "\n".join(
        "<tr>"
        f'<td><a href="{escape(s.href)}">{escape(s.repo)}</a></td>'
        f'<td class="num">{s.total_open}</td>'
        f'<td class="num">{s.linked}</td>'
        f'<td class="num">{s.shown}</td>'
        f'<td class="num">{"–" if s.best_score is None else f"{s.best_score:+d}"}</td>'
        "</tr>"
        for s in summaries
    )
    meta = f"{len(summaries)} repositories · generated {_when(generated_at)}"
    return _document(
        "Triage overview",
        f"""<h1>Triage overview</h1>
<p class="meta">{escape(meta)}</p>
<div class="table-wrap"><table>
<thead><tr><th>Repository</th><th class="num">Open issues</th>
<th class="num">With open PR</th><th class="num">Candidates</th>
<th class="num">Best score</th></tr></thead>
<tbody>
{rows}
</tbody>
</table></div>""",
    )
