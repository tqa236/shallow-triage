"""Fetch open issues and rank them by how well-scoped they look."""

import re
from dataclasses import dataclass, field

from shallow_triage import gh

_REPRO_RE = re.compile(
    r"(steps to reproduce|minimal (reproducible )?example|traceback|```)", re.I
)
_GOOD_LABELS = {"bug", "good first issue", "documentation", "docs", "regression"}
_BAD_LABELS = {"question", "discussion", "needs-design", "wontfix", "duplicate"}

# GitHub owner and repo names; also keeps them safe to use as file paths.
_REPO_RE = re.compile(r"^[A-Za-z0-9-]+/(?!\.\.?$)[A-Za-z0-9._-]+$")

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
        closedByPullRequestsReferences(first: 10, includeClosedPrs: true) {
          nodes { url state mergedAt }
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
    linked_prs: tuple[str, ...] = ()  # open PR URLs; may be in other repos
    # (url, merge date) of merged PRs that claimed to close it
    merged_prs: tuple[tuple[str, str], ...] = ()

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
            merged_prs=tuple(
                (pr["url"], pr["mergedAt"][:10])
                for pr in prs
                if pr["state"] == "MERGED"
            ),
        )


def fetch_open_issues(repo: str) -> list[Issue]:
    """Return open issues for ``owner/name``, with their linked PRs."""
    if not _REPO_RE.match(repo):
        raise ValueError(f"expected owner/name, got {repo!r}")
    owner, name = repo.split("/")
    pages = gh.graphql(_ISSUES_QUERY, owner=owner, name=name)
    return [
        Issue.from_graphql(node)
        for page in pages
        for node in page["repository"]["issues"]["nodes"]
    ]


def score_reasons(issue: Issue) -> list[tuple[int, str]]:
    """Return the ``(points, reason)`` pairs that make up an issue's score."""
    reasons = []
    if _REPRO_RE.search(issue.body):
        reasons.append((3, "has reproduction steps or code"))
    for label in sorted(issue.labels & _GOOD_LABELS):
        reasons.append((2, f"labelled {label!r}"))
    for label in sorted(issue.labels & _BAD_LABELS):
        reasons.append((-3, f"labelled {label!r}"))
    if issue.assigned:
        reasons.append((-2, "already assigned"))
    if issue.comments > 20:
        # long threads tend to be contentious or underspecified
        reasons.append((-2, f"long thread ({issue.comments} comments)"))
    return reasons


def score_issue(issue: Issue) -> int:
    """Heuristic score; higher means more objective and fixable."""
    return sum(points for points, _ in score_reasons(issue))


def rank(
    issues: list[Issue], limit: int | None = None, *, include_linked: bool = False
) -> list[Issue]:
    """Sort by score, dropping issues that already have an open PR by default."""
    if not include_linked:
        issues = [i for i in issues if not i.linked_prs]
    ranked = sorted(issues, key=score_issue, reverse=True)
    return ranked[:limit] if limit is not None else ranked
