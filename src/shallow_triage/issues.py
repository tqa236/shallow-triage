"""Fetch open issues and rank them by how well-scoped they look."""

import re
from dataclasses import dataclass, field

from shallow_triage import gh

_REPRO_RE = re.compile(
    r"(steps to reproduce|minimal (reproducible )?example|traceback|```)", re.I
)
_GOOD_LABELS = {"bug", "good first issue", "documentation", "docs", "regression"}
_BAD_LABELS = {"question", "discussion", "needs-design", "wontfix", "duplicate"}


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    url: str
    body: str
    labels: frozenset[str] = field(default_factory=frozenset)
    comments: int = 0
    assigned: bool = False

    @classmethod
    def from_api(cls, raw: dict) -> "Issue":
        return cls(
            number=raw["number"],
            title=raw["title"],
            url=raw["html_url"],
            body=raw.get("body") or "",
            labels=frozenset(lbl["name"].lower() for lbl in raw.get("labels", [])),
            comments=raw.get("comments", 0),
            assigned=bool(raw.get("assignees")),
        )


def fetch_open_issues(repo: str) -> list[Issue]:
    """Return open issues (not pull requests) for ``owner/name``."""
    raw = gh.api(f"repos/{repo}/issues?state=open&per_page=100", paginate=True)
    return [Issue.from_api(r) for r in raw if "pull_request" not in r]


def score_issue(issue: Issue) -> int:
    """Heuristic score; higher means more objective and fixable."""
    score = 0
    if _REPRO_RE.search(issue.body):
        score += 3
    score += 2 * len(issue.labels & _GOOD_LABELS)
    score -= 3 * len(issue.labels & _BAD_LABELS)
    if issue.assigned:
        score -= 2
    if issue.comments > 20:
        score -= 2  # long threads tend to be contentious or underspecified
    return score


def rank(issues: list[Issue], limit: int | None = None) -> list[Issue]:
    ranked = sorted(issues, key=score_issue, reverse=True)
    return ranked[:limit] if limit is not None else ranked
