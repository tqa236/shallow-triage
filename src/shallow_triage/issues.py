"""Fetch open issues and rank them by how well-scoped they look."""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from shallow_triage import gh
from shallow_triage.cache import CacheEntry, IssueCache, now_utc

_REPRO_RE = re.compile(
    r"(steps to reproduce|minimal (reproducible )?example|traceback|```)", re.I
)
_GOOD_LABELS = {"bug", "good first issue", "documentation", "docs", "regression"}
_BAD_LABELS = {"question", "discussion", "needs-design", "wontfix", "duplicate"}

# GitHub owner and repo names; also keeps them safe to use as file paths.
_REPO_RE = re.compile(r"[A-Za-z0-9-]+/(?!\.\.?\Z)[A-Za-z0-9._-]+")

# Without $since: every open issue. With $since: every issue (open or closed)
# updated since then, which is enough to bring a cached copy up to date.
_ISSUES_QUERY = """
query($owner: String!, $name: String!, $endCursor: String%(since_var)s) {
  repository(owner: $owner, name: $name) {
    issues(states: %(states)s, first: 100, after: $endCursor%(filter)s) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number state title url body
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
_FULL_QUERY = _ISSUES_QUERY % {"since_var": "", "states": "OPEN", "filter": ""}
_SINCE_QUERY = _ISSUES_QUERY % {
    "since_var": ", $since: DateTime!",
    "states": "[OPEN, CLOSED]",
    "filter": ", filterBy: {since: $since}",
}
# Margin for clock skew between this machine and GitHub.
_SINCE_SKEW = timedelta(minutes=5)


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


def _fetch_nodes(repo: str, since: datetime | None = None) -> list[dict]:
    owner, name = repo.split("/")
    if since is None:
        pages = gh.graphql(_FULL_QUERY, owner=owner, name=name)
    else:
        stamp = (since - _SINCE_SKEW).strftime("%Y-%m-%dT%H:%M:%SZ")
        pages = gh.graphql(_SINCE_QUERY, owner=owner, name=name, since=stamp)
    return [node for page in pages for node in page["repository"]["issues"]["nodes"]]


def fetch_open_issues(repo: str, cache: IssueCache | None = None) -> list[Issue]:
    """Return open issues for ``owner/name``, with their linked PRs.

    With a ``cache``, only issues updated since the last run are fetched.
    """
    if not _REPO_RE.fullmatch(repo):
        raise ValueError(f"expected owner/name, got {repo!r}")
    if cache is None:
        return [Issue.from_graphql(n) for n in _fetch_nodes(repo)]

    now = now_utc()
    entry = cache.load(repo, now)
    if entry is None:
        nodes = {n["number"]: n for n in _fetch_nodes(repo)}
        full_fetch_at = now
    else:
        nodes = dict(entry.nodes)
        for node in _fetch_nodes(repo, since=entry.fetched_at):
            if node["state"] == "OPEN":
                nodes[node["number"]] = node
            else:
                nodes.pop(node["number"], None)
        full_fetch_at = entry.full_fetch_at
    cache.save(repo, CacheEntry(full_fetch_at, now, nodes))
    return [Issue.from_graphql(n) for n in nodes.values()]


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
