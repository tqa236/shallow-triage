"""Build a multi-repository triage site: an index plus one page per repo."""

import sys
from datetime import datetime
from pathlib import Path

from shallow_triage import gh
from shallow_triage.issues import fetch_open_issues, rank, score_issue
from shallow_triage.page import RepoSummary, render_index, render_page


def build_site(
    repos: list[str],
    out_dir: Path,
    *,
    limit: int | None,
    include_linked: bool,
    generated_at: datetime,
) -> list[str]:
    """Write ``index.html`` and ``<owner>/<name>.html`` under ``out_dir``.

    A repository that fails to load is reported on stderr and left out of the
    index; the others are still written. Returns the repositories that failed.
    """
    summaries, failed = [], []
    for repo in dict.fromkeys(repos):  # dedupe, keep order
        try:
            issues = fetch_open_issues(repo)
        except (gh.GhError, ValueError) as exc:
            print(f"error: {repo}: {exc}", file=sys.stderr)
            failed.append(repo)
            continue
        ranked = rank(issues, limit, include_linked=include_linked)
        path = out_dir / f"{repo}.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            render_page(
                repo,
                ranked,
                total_open=len(issues),
                generated_at=generated_at,
                back_href="../index.html",
            ),
            encoding="utf-8",
        )
        summaries.append(
            RepoSummary(
                repo=repo,
                href=f"{repo}.html",
                total_open=len(issues),
                linked=sum(1 for i in issues if i.linked_prs),
                shown=len(ranked),
                best_score=max(map(score_issue, ranked), default=None),
            )
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(
        render_index(summaries, generated_at=generated_at), encoding="utf-8"
    )
    return failed
