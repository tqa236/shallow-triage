"""Fetch open issues and rank them by how well-scoped they look."""

import re
from dataclasses import dataclass, field

from shallow_triage import gh

_REPRO_RE = re.compile(
    r"(steps to reproduce|minimal (reproducible )?example|traceback|```)", re.I
)
_GOOD_LABELS = {"bug", "good first issue", "documentation", "docs", "regression"}
_BAD_LABELS = {"question", "discussion", "needs-design", "wontfix", "duplicate"}

_ISSUES_QUERY = """
query($owner: String!, $name: String!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    issues(states: OPEN, first: 100, after: $endCursor) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title url body
        comments { totalCount }
        assignees(first: 1) { totalCount }
        labels(first: 20) { nodes { name } }
        closedByPullRequestsReferences(first: 10, includeClosedPrs: false) {
          nodes { url state }
        }
      }
    }
  }
}
"""


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    url: str
    body: str
    labels: frozenset[str] = field(default_factory=frozenset)
    comments: int = 0
    assigned: bool = False
    linked_prs: tuple[str, ...] = ()  # PR URLs; may be in other repos

    @classmethod
    def from_graphql(cls, node: dict) -> "Issue":
        prs = node["closedByPullRequestsReferences"]["nodes"]
        return cls(
            number=node["number"],
            title=node["title"],
            url=node["url"],
            body=node.get("body") or "",
            labels=frozenset(lbl["name"].lower() for lbl in node["labels"]["nodes"]),
            comments=node["comments"]["totalCount"],
            assigned=node["assignees"]["totalCount"] > 0,
            linked_prs=tuple(pr["url"] for pr in prs if pr["state"] == "OPEN"),
        )


def fetch_open_issues(repo: str) -> list[Issue]:
    """Return open issues for ``owner/name``, with their open linked PRs."""
    owner, _, name = repo.partition("/")
    if not owner or not name:
        raise ValueError(f"expected owner/name, got {repo!r}")
    pages = gh.graphql(_ISSUES_QUERY, owner=owner, name=name)
    return [
        Issue.from_graphql(node)
        for page in pages
        for node in page["repository"]["issues"]["nodes"]
    ]


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


def rank(
    issues: list[Issue], limit: int | None = None, *, include_linked: bool = False
) -> list[Issue]:
    """Sort by score, dropping issues that already have an open PR by default."""
    if not include_linked:
        issues = [i for i in issues if not i.linked_prs]
    ranked = sorted(issues, key=score_issue, reverse=True)
    return ranked[:limit] if limit is not None else ranked
