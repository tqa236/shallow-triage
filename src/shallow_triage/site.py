"""Build a multi-repository triage site: an index plus one page per repo."""

import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from shallow_triage import gh
from shallow_triage.cache import IssueCache
from shallow_triage.config import RepoConfig
from shallow_triage.local import LocalError
from shallow_triage.page import RepoSummary, render_index, render_page
from shallow_triage.report import RepoReport, build_report

# Repositories are fetched in parallel; the work is waiting on gh and git.
MAX_WORKERS = 4


def build_site(
    repos: list[RepoConfig],
    out_dir: Path,
    *,
    limit: int | None,
    include_linked: bool,
    generated_at: datetime,
    cache: IssueCache | None = None,
    fetch: bool = False,
    batch: int | None = 100,
) -> list[str]:
    """Write ``index.html`` and ``<owner>/<name>.html`` under ``out_dir``.

    A repository that fails to load is reported on stderr and left out of the
    index; the others are still written. Returns the repositories that failed.
    """
    unique, seen = [], set()
    for config in repos:
        if config.name not in seen:
            seen.add(config.name)
            unique.append(config)

    def load(config: RepoConfig) -> RepoReport | Exception:
        try:
            return build_report(
                config,
                limit=limit,
                include_linked=include_linked,
                cache=cache,
                fetch=fetch,
                batch=batch,
            )
        except (gh.GhError, LocalError, ValueError) as exc:
            return exc

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        results = list(pool.map(load, unique))

    summaries, failed = [], []
    for config, result in zip(unique, results, strict=True):
        if isinstance(result, Exception):
            print(f"error: {config.name}: {result}", file=sys.stderr)
            failed.append(config.name)
            continue
        path = out_dir / f"{config.name}.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            render_page(result, generated_at=generated_at, back_href="../index.html"),
            encoding="utf-8",
        )
        summaries.append(
            RepoSummary(
                repo=config.name,
                href=f"{config.name}.html",
                total_open=result.total_open,
                fixes=len(result.fixes),
                closable=len(result.closable),
                candidates=len(result.candidates),
            )
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(
        render_index(summaries, generated_at=generated_at), encoding="utf-8"
    )
    return failed
