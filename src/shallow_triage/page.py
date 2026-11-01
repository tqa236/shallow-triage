"""Render triage reports as self-contained HTML pages."""

import re
import shlex
from dataclasses import dataclass
from datetime import datetime
from html import escape

from shallow_triage.issues import Issue, score_issue, score_reasons
from shallow_triage.local import FixBranch
from shallow_triage.report import Closable, FixEntry, RepoReport

_CODE_BLOCK_RE = re.compile(r"```.*?(```|$)", re.S)
_EXCERPT_LEN = 280
MAX_DIFF_LINES = 1500

_DARK = """
    --bg: #1c1917; --fg: #f5f5f4; --muted: #a8a29e; --card: #292524;
    --border: #44403c; --link: #93c5fd; --plus: #86efac; --minus: #fca5a5;
    --code: #1f1b19; --add-bg: #14532d55; --del-bg: #7f1d1d55; --hunk: #c4b5fd;
    color-scheme: dark;
"""
_CSS = f"""
:root {{
  --bg: #fafaf9; --fg: #1c1917; --muted: #78716c; --card: #ffffff;
  --border: #e7e5e4; --link: #1d4ed8; --plus: #15803d; --minus: #b91c1c;
  --code: #f5f5f4; --add-bg: #dcfce7; --del-bg: #fee2e2; --hunk: #6d28d9;
}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{{_DARK}}} }}
:root[data-theme="dark"] {{{_DARK}}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; padding-block: 32px; background: var(--bg); color: var(--fg);
  padding-left: max(16px, env(safe-area-inset-left, 0px));
  padding-right: max(16px, env(safe-area-inset-right, 0px));
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
}}
main {{ max-width: 860px; margin: 0 auto; display: flex; flex-direction: column; gap: 32px; }}
h1 {{ font-size: 1.5rem; margin: 0; }}
h2 {{ font-size: 1.15rem; margin: 0 0 4px; }}
h3 {{ font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.06em;
      color: var(--muted); margin: 12px 0 4px; }}
header, section {{ display: flex; flex-direction: column; gap: 8px; min-width: 0; }}
p {{ margin: 0; }}
.meta, .note {{ color: var(--muted); }}
.note {{ font-size: 0.875rem; }}
a {{ color: var(--link); }}
code, pre {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
code {{ font-size: 0.875em; }}
ol, ul.plain {{ list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 12px; }}
.card {{
  background: var(--card); border: 1px solid var(--border); border-radius: 8px;
  padding: 16px; overflow-wrap: anywhere; min-width: 0;
}}
.head {{ display: flex; gap: 12px; align-items: baseline; }}
.score {{ font-weight: 600; font-variant-numeric: tabular-nums; min-width: 2.5em; }}
.title {{ font-weight: 600; text-decoration: none; }}
.excerpt {{ color: var(--muted); margin: 8px 0; }}
.reasons {{ font-size: 0.875rem; }}
.reasons span {{ display: inline-block; margin-right: 12px; }}
.plus {{ color: var(--plus); }}
.minus {{ color: var(--minus); }}
.linked {{ font-size: 0.875rem; margin-top: 8px; }}
.sub {{ font-size: 0.875rem; color: var(--muted); }}
.subject {{ font-weight: 600; }}
pre {{
  margin: 0; background: var(--code); border: 1px solid var(--border);
  border-radius: 6px; padding: 8px 12px; overflow-x: auto; font-size: 0.8125rem;
}}
pre.msg {{ white-space: pre-wrap; }}
pre.diff {{ padding: 8px 0; }}
pre.diff span {{ display: block; padding: 0 12px; min-width: max-content; }}
pre.diff .add {{ background: var(--add-bg); }}
pre.diff .del {{ background: var(--del-bg); }}
pre.diff .hunk {{ color: var(--hunk); }}
pre.diff .meta {{ color: var(--muted); font-weight: 600; }}
ul.files {{ margin: 0; padding-left: 1.2em; font-size: 0.875rem; }}
summary {{ cursor: pointer; font-size: 0.875rem; color: var(--link); margin-top: 8px; }}
.back {{ font-size: 0.875rem; }}
.table-wrap {{ overflow-x: auto; }}
table {{ width: 100%; border-collapse: collapse; background: var(--card); }}
th, td {{ padding: 8px 12px; border-bottom: 1px solid var(--border); text-align: left; }}
th {{ font-size: 0.8125rem; color: var(--muted); font-weight: 600; }}
td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
"""


def excerpt(body: str, limit: int = _EXCERPT_LEN) -> str:
    """Start of the prose in ``body``, with code blocks and whitespace collapsed."""
    text = " ".join(_CODE_BLOCK_RE.sub(" ", body).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _link(url: str, text: str | None = None) -> str:
    label = text if text is not None else url.removeprefix("https://github.com/")
    return f'<a href="{escape(url)}">{escape(label)}</a>'


def _issue_title(issue: Issue) -> str:
    return _link(issue.url, f"#{issue.number} {issue.title}").replace(
        "<a ", '<a class="title" ', 1
    )


def _linked_prs(issue: Issue) -> str:
    if not issue.linked_prs:
        return ""
    links = ", ".join(_link(u) for u in issue.linked_prs)
    return f'<p class="linked">Open PRs: {links}</p>'


def _candidate_html(issue: Issue) -> str:
    reasons = "".join(
        f'<span class="{"plus" if pts > 0 else "minus"}">{pts:+d} {escape(why)}</span>'
        for pts, why in score_reasons(issue)
    )
    return (
        '<li class="card">'
        f'<div class="head"><span class="score">{score_issue(issue):+d}</span>'
        f"{_issue_title(issue)}</div>"
        f'<p class="excerpt">{escape(excerpt(issue.body))}</p>'
        f'<div class="reasons">{reasons or "<span>no signals</span>"}</div>'
        f"{_linked_prs(issue)}"
        "</li>"
    )


def _diff_html(diff: str) -> str:
    lines = diff.splitlines()
    out = []
    for line in lines[:MAX_DIFF_LINES]:
        if line.startswith(("diff --git", "index ", "--- ", "+++ ")):
            cls = "meta"
        elif line.startswith("@@"):
            cls = "hunk"
        elif line.startswith("+"):
            cls = "add"
        elif line.startswith("-"):
            cls = "del"
        else:
            cls = "ctx"
        out.append(f'<span class="{cls}">{escape(line) or " "}</span>')
    if len(lines) > MAX_DIFF_LINES:
        out.append(
            f'<span class="meta">… {len(lines) - MAX_DIFF_LINES} more lines</span>'
        )
    return f'<pre class="diff">{"".join(out)}</pre>'


def _commands(report: RepoReport, branch: FixBranch) -> str:
    cfg = report.config
    files = " ".join(shlex.quote(f) for f in branch.files_to_test)
    test = cfg.test.replace("{files}", files)
    cmds = [
        f"cd {shlex.quote(str(cfg.path))}",
        f"git diff {cfg.base}...{branch.name}",
        f"git switch {branch.name} && {test}",
    ]
    return f"<pre>{escape(chr(10).join(cmds))}</pre>"


def _fix_html(report: RepoReport, entry: FixEntry) -> str:
    b = entry.branch
    added = sum(f.added or 0 for f in b.files)
    deleted = sum(f.deleted or 0 for f in b.files)
    commits = "".join(
        f'<p class="subject">{escape(c.subject)}</p>'
        + (f'<pre class="msg">{escape(c.body)}</pre>' if c.body else "")
        for c in b.commits
    )
    files = "".join(
        f"<li><code>{escape(f.path)}</code> "
        + (
            "binary"
            if f.added is None
            else f'<span class="plus">+{f.added}</span> '
            f'<span class="minus">−{f.deleted}</span>'
        )
        + "</li>"
        for f in b.files
    )
    return (
        f'<li class="card" id="issue-{entry.issue.number}">'
        f'<div class="head">{_issue_title(entry.issue)}</div>'
        f'<p class="sub"><code>{escape(b.name)}</code> @ '
        f"<code>{escape(b.commits[-1].sha[:10])}</code> · {escape(b.commits[-1].date)}"
        f' · <span class="plus">+{added}</span> <span class="minus">−{deleted}</span>'
        f" in {len(b.files)} file{'s' if len(b.files) != 1 else ''}</p>"
        f"{_linked_prs(entry.issue)}"
        f"<h3>Suggested fix</h3>{commits}"
        f'<h3>Files</h3><ul class="files">{files}</ul>'
        f"<details><summary>Show diff</summary>{_diff_html(b.diff)}</details>"
        f"<h3>Commands</h3>{_commands(report, b)}"
        "</li>"
    )


def _closable_html(report: RepoReport, item: Closable) -> str:
    repo = report.config.name
    evidence = [
        f"<li>{_link(f'https://github.com/{repo}/commit/{c.sha}', c.sha[:10])} "
        f"({escape(c.date)}) {escape(c.subject)}</li>"
        for c in item.commits
    ] + [f"<li>merged PR {_link(u)} ({escape(d)})</li>" for u, d in item.merged_prs]
    return (
        '<li class="card">'
        f'<div class="head">{_issue_title(item.issue)}</div>'
        f'<ul class="files">{"".join(evidence)}</ul>'
        "</li>"
    )


def _section(title: str, note: str, items: list[str]) -> str:
    if not items:
        return ""
    return (
        f"<section><h2>{escape(title)} ({len(items)})</h2>"
        f'<p class="note">{escape(note)}</p>'
        f"<ol>{''.join(items)}</ol></section>"
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
    report: RepoReport, *, generated_at: datetime, back_href: str | None = None
) -> str:
    """Return a complete HTML document for one repository's report."""
    repo = report.config.name
    back = ""
    if back_href:
        back = f'<a class="back" href="{escape(back_href)}">← All repositories</a>'
    meta = f"{report.total_open} open issues · generated {_when(generated_at)}"
    facts = ""
    if report.base is not None:
        facts = (
            f'<p class="note">Local clone <code>{escape(str(report.config.path))}</code>'
            f" · base <code>{escape(report.config.base)}</code> @ "
            f"<code>{escape(report.base.sha[:10])}</code> ({escape(report.base.date)})"
            "</p>"
        )
    closed = ""
    if report.closed_fixes:
        items = "".join(
            f"<li><code>{escape(b.name)}</code> → "
            f"{_link(f'https://github.com/{repo}/issues/{b.issue}', f'#{b.issue}')}</li>"
            for b in report.closed_fixes
        )
        closed = (
            "<section><h2>Fix branches whose issue is no longer open "
            f'({len(report.closed_fixes)})</h2><ul class="files">{items}</ul></section>'
        )
    repo_link = _link(f"https://github.com/{repo}", repo)
    body = "\n".join(
        [
            f"<header>{back}<h1>Triage: {repo_link}</h1>"
            f'<p class="meta">{escape(meta)}</p>{facts}</header>',
            _section(
                "Ready to review",
                "Local fix branches for open issues, newest first.",
                [_fix_html(report, f) for f in report.fixes],
            ),
            _section(
                "Possibly closable",
                "Open issues that a commit on the base branch or a merged PR "
                "says it fixes. Check that each no longer reproduces before closing.",
                [_closable_html(report, c) for c in report.closable],
            ),
            closed,
            _section(
                "Candidates",
                "Open issues without a fix yet, ranked by how well-scoped they look.",
                [_candidate_html(i) for i in report.candidates],
            ),
        ]
    )
    return _document(f"{repo} triage", body)


@dataclass(frozen=True)
class RepoSummary:
    """One row of the index page."""

    repo: str
    href: str
    total_open: int
    fixes: int
    closable: int
    candidates: int


def render_index(summaries: list[RepoSummary], *, generated_at: datetime) -> str:
    """Return the overview page linking to each repository's triage page."""
    rows = "\n".join(
        "<tr>"
        f"<td>{_link(s.href, s.repo)}</td>"
        f'<td class="num">{s.total_open}</td>'
        f'<td class="num">{s.fixes}</td>'
        f'<td class="num">{s.closable}</td>'
        f'<td class="num">{s.candidates}</td>'
        "</tr>"
        for s in summaries
    )
    meta = f"{len(summaries)} repositories · generated {_when(generated_at)}"
    return _document(
        "Triage overview",
        f"""<header><h1>Triage overview</h1>
<p class="meta">{escape(meta)}</p></header>
<div class="table-wrap"><table>
<thead><tr><th>Repository</th><th class="num">Open issues</th>
<th class="num">Fixes ready</th><th class="num">Possibly closable</th>
<th class="num">Candidates</th></tr></thead>
<tbody>
{rows}
</tbody>
</table></div>""",
    )
