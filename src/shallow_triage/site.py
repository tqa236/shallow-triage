"""Build a multi-repository triage site: an index plus one page per repo."""

import sys
from datetime import datetime
from pathlib import Path

from shallow_triage import gh
from shallow_triage.config import RepoConfig
from shallow_triage.local import LocalError
from shallow_triage.page import RepoSummary, render_index, render_page
from shallow_triage.report import build_report


def build_site(
    repos: list[RepoConfig],
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
    summaries, failed, seen = [], [], set()
    for config in repos:
        if config.name in seen:
            continue
        seen.add(config.name)
        try:
            report = build_report(config, limit=limit, include_linked=include_linked)
        except (gh.GhError, LocalError, ValueError) as exc:
            print(f"error: {config.name}: {exc}", file=sys.stderr)
            failed.append(config.name)
            continue
        path = out_dir / f"{config.name}.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            render_page(report, generated_at=generated_at, back_href="../index.html"),
            encoding="utf-8",
        )
        summaries.append(
            RepoSummary(
                repo=config.name,
                href=f"{config.name}.html",
                total_open=report.total_open,
                fixes=len(report.fixes),
                closable=len(report.closable),
                candidates=len(report.candidates),
            )
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(
        render_index(summaries, generated_at=generated_at), encoding="utf-8"
    )
    return failed
