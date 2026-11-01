"""The HTML output: render saved triage reports as self-contained pages.

This module only formats a :class:`~shallow_triage.report.RepoReport`; it
computes nothing and reads no input, so the pages can be restyled or
rearranged and rendered again from saved reports. The markup and styles live
in ``templates/``; every value is HTML-escaped here before it goes into a
template.

Pages may be shared, so they never show where the local clones are, and the
user's home folder is replaced by ``~`` wherever it appears in the text.
"""

import re
import shlex
from datetime import datetime
from functools import cache
from html import escape
from importlib.resources import files
from pathlib import Path
from string import Template

from shallow_triage.issues import Issue
from shallow_triage.local import FixBranch
from shallow_triage.report import Candidate, Closable, FixEntry, RepoReport

_CODE_BLOCK_RE = re.compile(r"```.*?(```|$)", re.S)
_EXCERPT_LEN = 280
MAX_DIFF_LINES = 1500
_TEMPLATES = files("shallow_triage") / "templates"


@cache
def _template(name: str) -> str:
    return (_TEMPLATES / name).read_text(encoding="utf-8")


def _render(name: str, **values: object) -> str:
    """Fill ``templates/<name>``; values must already be HTML-escaped."""
    return Template(_template(name)).substitute(values)


def excerpt(body: str, limit: int = _EXCERPT_LEN) -> str:
    """Start of the prose in ``body``, with code blocks and whitespace collapsed."""
    text = " ".join(_CODE_BLOCK_RE.sub(" ", body).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _safe_href(url: str) -> bool:
    """GitHub URLs and the site's own relative links; nothing with a scheme
    like ``javascript:`` that would run in the page."""
    if url.startswith("https://github.com/"):
        return True
    return ":" not in url and not url.startswith(("/", "\\"))


def _link(url: str, text: str | None = None, cls: str = "") -> str:
    label = text if text is not None else url.removeprefix("https://github.com/")
    attr = f' class="{cls}"' if cls else ""
    if not _safe_href(url):
        return f"<span{attr}>{escape(label)}</span>"
    return f'<a{attr} href="{escape(url)}">{escape(label)}</a>'


def _issue_title(issue: Issue) -> str:
    return _link(issue.url, f"#{issue.number} {issue.title}", cls="title")


def _linked_prs(issue: Issue) -> str:
    if not issue.linked_prs:
        return ""
    links = ", ".join(_link(u) for u in issue.linked_prs)
    return f'<p class="linked">Open PRs: {links}</p>'


def _candidate_html(candidate: Candidate) -> str:
    issue = candidate.issue
    reasons = "".join(
        f'<span class="{"plus" if pts > 0 else "minus"}">{pts:+d} {escape(why)}</span>'
        for pts, why in candidate.reasons
    )
    return _render(
        "candidate.html",
        score=f"{candidate.score:+d}",
        title=_issue_title(issue),
        excerpt=escape(excerpt(issue.body)),
        reasons=reasons or "<span>no signals</span>",
        linked_prs=_linked_prs(issue),
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
    """Shell commands to copy. Every value is quoted: git allows ``;``, ``|``
    and ``$`` in branch names, and the human pastes these into a shell."""
    files = " ".join(shlex.quote(f) for f in branch.files_to_test)
    switch = f"git switch {shlex.quote(branch.name)}"
    cmds = [
        f"# in your clone of {report.repo}",
        f"git diff {shlex.quote(f'{report.base}...{branch.name}')}",
        f"{switch} && {report.test.replace('{files}', files)}"
        if report.test
        else switch,
    ]
    return escape("\n".join(cmds))


def _file_html(path: str, added: int | None, deleted: int | None) -> str:
    if added is None:
        counts = "binary"
    else:
        counts = (
            f'<span class="plus">+{added}</span> <span class="minus">−{deleted}</span>'
        )
    return f"<li><code>{escape(path)}</code> {counts}</li>"


def _fix_html(report: RepoReport, entry: FixEntry) -> str:
    b = entry.branch
    commits = "".join(
        f'<p class="subject">{escape(c.subject)}</p>'
        + (f'<pre class="msg">{escape(c.body)}</pre>' if c.body else "")
        for c in b.commits
    )
    n = len(b.files)
    return _render(
        "fix.html",
        number=entry.issue.number,
        title=_issue_title(entry.issue),
        branch=escape(b.name),
        sha=escape(b.commits[-1].sha[:10]),
        date=escape(b.commits[-1].date),
        added=sum(f.added or 0 for f in b.files),
        deleted=sum(f.deleted or 0 for f in b.files),
        file_count=f"{n} file{'s' if n != 1 else ''}",
        linked_prs=_linked_prs(entry.issue),
        commits=commits,
        files="".join(_file_html(f.path, f.added, f.deleted) for f in b.files),
        diff=_diff_html(b.diff),
        commands=_commands(report, b),
    )


def _closable_html(report: RepoReport, item: Closable) -> str:
    repo = report.repo
    evidence = [
        f"<li>{_link(f'https://github.com/{repo}/commit/{c.sha}', c.sha[:10])} "
        f"({escape(c.date)}) {escape(c.subject)}</li>"
        for c in item.commits
    ] + [f"<li>merged PR {_link(u)} ({escape(d)})</li>" for u, d in item.merged_prs]
    return _render(
        "closable.html",
        number=item.issue.number,
        title=_issue_title(item.issue),
        evidence="".join(evidence),
    )


def _section(id_: str, title: str, note: str, items: list[str]) -> str:
    if not items:
        return ""
    return _render(
        "section.html",
        id=id_,
        title=escape(title),
        count=len(items),
        note=escape(note),
        items="\n".join(items),
    )


def _closed_fixes_html(report: RepoReport) -> str:
    if not report.closed_fixes:
        return ""
    repo = report.repo
    items = "".join(
        f"<li><code>{escape(b.name)}</code> → "
        f"{_link(f'https://github.com/{repo}/issues/{b.issue}', f'#{b.issue}')}</li>"
        for b in report.closed_fixes
    )
    return _render("closed_fixes.html", count=len(report.closed_fixes), items=items)


def _toc_html(report: RepoReport) -> str:
    """Contents: each non-empty section, and under "Ready to review" each fix."""
    fixes = "".join(
        f'<li><a href="#issue-{f.issue.number}">'
        f"#{f.issue.number} {escape(f.issue.title)}</a></li>"
        for f in report.fixes
    )
    entries = [
        ("ready", "Ready to review", len(report.fixes), fixes),
        ("closable", "Possibly closable", len(report.closable), ""),
        ("closed", "Fix branches without an open issue", len(report.closed_fixes), ""),
        ("candidates", "Candidates", len(report.candidates), ""),
    ]
    items = "\n".join(
        f'<li><a href="#{id_}">{title}</a> <span class="count">{count}</span>'
        + (f"<ol>{sub}</ol>" if sub else "")
        + "</li>"
        for id_, title, count, sub in entries
        if count
    )
    return _render("toc.html", items=items) if items else ""


def _hide_home(text: str, home: str | None = None) -> str:
    """Replace the user's home folder (e.g. in a test command) with ``~``."""
    home = (home or str(Path.home())).rstrip("/")
    if len(home) < 2:  # "/" as home: nothing meaningful to hide
        return text
    return re.sub(re.escape(home) + r"(?=/|\b|$)", "~", text)


def _document(title: str, body: str) -> str:
    return _hide_home(
        _render(
            "base.html", title=escape(title), style=_template("style.css"), body=body
        )
    )


def _when(generated_at: datetime) -> str:
    return generated_at.strftime("%Y-%m-%d %H:%M %Z").strip()


def render_page(report: RepoReport, *, back_href: str | None = None) -> str:
    """Return a complete HTML document for one repository's report."""
    repo = report.repo
    back = ""
    if back_href:
        back = f'<a class="back" href="{escape(back_href)}">← All repositories</a>'
    facts = ""
    if report.base is not None and report.base_commit is not None:
        facts = (
            f'<p class="note">Compared with <code>{escape(report.base)}</code> @ '
            f"<code>{escape(report.base_commit.sha[:10])}</code> "
            f"({escape(report.base_commit.date)})</p>"
        )
    sections = [
        _section(
            "ready",
            "Ready to review",
            "Local fix branches for open issues, newest first.",
            [_fix_html(report, f) for f in report.fixes],
        ),
        _section(
            "closable",
            "Possibly closable",
            "Open issues that a commit on the base branch or a merged PR "
            "says it fixes. Check that each no longer reproduces before closing.",
            [_closable_html(report, c) for c in report.closable],
        ),
        _closed_fixes_html(report),
        _section(
            "candidates",
            "Candidates",
            "Open issues without a fix yet, ranked by how well-scoped they look.",
            [_candidate_html(i) for i in report.candidates],
        ),
    ]
    coverage = f"{report.total_open} open issues"
    if report.known_open is not None:
        coverage += f", {report.known_open} fetched so far"
    meta = f"{coverage} · generated {_when(report.generated_at)}"
    body = _render(
        "repo.html",
        back=back,
        repo_link=_link(f"https://github.com/{repo}", repo),
        meta=escape(meta),
        facts=facts,
        toc=_toc_html(report),
        sections="\n".join(s for s in sections if s),
    )
    return _document(f"{repo} triage", body)


def page_href(report: RepoReport) -> str:
    """Where :func:`render_page`'s output goes, relative to the index."""
    return f"{report.repo}.html"


def render_index(reports: list[RepoReport], *, generated_at: datetime) -> str:
    """Return the overview page linking to each repository's page."""

    def count(r: RepoReport, n: int, section: str) -> str:
        return _link(f"{page_href(r)}#{section}", str(n)) if n else "0"

    rows = "".join(
        _render(
            "index_row.html",
            repo_link=_link(page_href(r), r.repo),
            total_open=r.total_open,
            fixes=count(r, len(r.fixes), "ready"),
            closable=count(r, len(r.closable), "closable"),
            candidates=count(r, len(r.candidates), "candidates"),
        )
        for r in reports
    )
    meta = f"{len(reports)} repositories · generated {_when(generated_at)}"
    return _document(
        "Triage overview", _render("index.html", meta=escape(meta), rows=rows)
    )
