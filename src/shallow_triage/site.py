"""Collect reports for several repositories, and render them as a site.

``collect`` is input -> data: it builds each repository's report and saves
it. ``render_site`` is data -> output: it writes an index plus one page per
saved report, without GitHub or git. ``build_site`` does both.
"""

import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from shallow_triage import gh
from shallow_triage.cache import IssueCache
from shallow_triage.config import RepoConfig
from shallow_triage.findings import Findings
from shallow_triage.issues import valid_repo
from shallow_triage.local import LocalError
from shallow_triage.marks import Marks
from shallow_triage.page import page_href, render_index, render_page
from shallow_triage.report import (
    RepoReport,
    build_report,
    load_report,
    save_report,
)
from shallow_triage.store import repo_path

# Repositories are fetched in parallel; the work is waiting on gh and git.
MAX_WORKERS = 4


def collect(
    repos: list[RepoConfig],
    data_dir: Path,
    *,
    limit: int | None,
    include_linked: bool,
    generated_at: datetime,
    cache: IssueCache | None = None,
    fetch: bool = False,
    batch: int | None = 100,
    findings_dir: Path | None = None,
    marks_dir: Path | None = None,
) -> tuple[list[RepoReport], list[str]]:
    """Build and save a report for each repository.

    ``findings_dir`` and ``marks_dir`` hold the user's recorded findings and
    safe marks; without them, reports have neither.

    Returns the reports in config order (duplicates dropped) and the names
    of the repositories that failed; their errors are printed to stderr.
    """
    unique, seen = [], set()
    for config in repos:
        if config.name not in seen:
            seen.add(config.name)
            unique.append(config)

    def build(config: RepoConfig) -> RepoReport | Exception:
        try:
            ok = valid_repo(config.name)
            return build_report(
                config,
                limit=limit,
                include_linked=include_linked,
                generated_at=generated_at,
                cache=cache,
                fetch=fetch,
                batch=batch,
                findings=Findings(findings_dir, config.name)
                if findings_dir and ok
                else None,
                marks=Marks(marks_dir, config.name) if marks_dir and ok else None,
            )
        except (gh.GhError, LocalError, ValueError) as exc:
            return exc

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        results = list(pool.map(build, unique))

    reports, failed = [], []
    for config, result in zip(unique, results, strict=True):
        if isinstance(result, Exception):
            print(f"error: {config.name}: {result}", file=sys.stderr)
            failed.append(config.name)
        else:
            save_report(data_dir, result)
            reports.append(result)
    return reports, failed


def render_site(
    reports: list[RepoReport], out_dir: Path, *, generated_at: datetime
) -> None:
    """Write ``index.html`` and one page per report under ``out_dir``."""
    for report in reports:
        path = out_dir / page_href(report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_page(report, back_href="../index.html"), "utf-8")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(
        render_index(reports, generated_at=generated_at), encoding="utf-8"
    )


def build_site(
    repos: list[RepoConfig],
    out_dir: Path,
    data_dir: Path,
    *,
    limit: int | None,
    include_linked: bool,
    generated_at: datetime,
    cache: IssueCache | None = None,
    fetch: bool = False,
    batch: int | None = 100,
    findings_dir: Path | None = None,
    marks_dir: Path | None = None,
) -> list[str]:
    """Collect, then render. Returns the repositories that failed.

    A repository that fails keeps its last saved report on the site, if it
    has one; without one it is left out.
    """
    reports, failed = collect(
        repos,
        data_dir,
        limit=limit,
        include_linked=include_linked,
        generated_at=generated_at,
        cache=cache,
        fetch=fetch,
        batch=batch,
        findings_dir=findings_dir,
        marks_dir=marks_dir,
    )
    for name in failed:
        if not valid_repo(name):
            continue
        path = repo_path(data_dir, name)
        if path.exists():
            try:
                reports.append(load_report(path))
                print(f"note: {name}: showing the report from before", file=sys.stderr)
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
    order = {c.name: i for i, c in enumerate(repos)}
    reports.sort(key=lambda r: order.get(r.repo, len(order)))
    render_site(reports, out_dir, generated_at=generated_at)
    return failed
