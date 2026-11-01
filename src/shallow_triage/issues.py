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

_FIELDS = """
fragment IssueFields on Issue {
  number state title url body
  comments { totalCount }
  assignees(first: 1) { totalCount }
  labels(first: 20) { nodes { name } }
  closedByPullRequestsReferences(first: 10, includeClosedPrs: true) {
    nodes { url state mergedAt }
  }
}
"""
# The next page of open issues, newest first. Creation order doesn't change
# between runs, so a saved cursor continues where the last run stopped.
_WALK_QUERY = (
    """
query($owner: String!, $name: String!, $first: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    issues(states: OPEN, first: $first, after: $after,
           orderBy: {field: CREATED_AT, direction: DESC}) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes { ...IssueFields }
    }
  }
}
"""
    + _FIELDS
)
# Every issue, open or closed, updated since the last run.
_SYNC_QUERY = (
    """
query($owner: String!, $name: String!, $since: DateTime!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    issues(states: [OPEN, CLOSED], first: 100, after: $endCursor,
           filterBy: {since: $since}) {
      pageInfo { hasNextPage endCursor }
      nodes { ...IssueFields }
    }
  }
}
"""
    + _FIELDS
)
_TOTAL_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) { issues(states: OPEN) { totalCount } }
}
"""
# Margin for clock skew between this machine and GitHub.
_SINCE_SKEW = timedelta(minutes=5)
_PAGE = 100  # GitHub's maximum page size
_LOOKUP_CHUNK = 50


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


@dataclass(frozen=True)
class IssueSet:
    """The open issues known so far, and how they relate to all of them."""

    issues: list[Issue]
    total_open: int  # open issues on GitHub
    complete: bool  # every open issue has been fetched


def _walk(
    owner: str, name: str, after: str | None, count: int | None
) -> tuple[list[dict], str | None, bool]:
    """Up to ``count`` (all if None) open issues after ``after``.

    Returns the nodes, the cursor to continue from, and whether the end of
    the list was reached.
    """
    nodes: list[dict] = []
    while count is None or len(nodes) < count:
        first = _PAGE if count is None else min(_PAGE, count - len(nodes))
        conn = gh.graphql_once(
            _WALK_QUERY, owner=owner, name=name, first=first, after=after
        )["repository"]["issues"]
        nodes += conn["nodes"]
        if not conn["pageInfo"]["hasNextPage"]:
            return nodes, None, True
        after = conn["pageInfo"]["endCursor"]
    return nodes, after, False


def _sync(owner: str, name: str, since: datetime) -> list[dict]:
    stamp = (since - _SINCE_SKEW).strftime("%Y-%m-%dT%H:%M:%SZ")
    pages = gh.graphql(_SYNC_QUERY, owner=owner, name=name, since=stamp)
    return [node for page in pages for node in page["repository"]["issues"]["nodes"]]


def _lookup(owner: str, name: str, numbers: list[int]) -> dict[int, dict | None]:
    """The issue node for each number; None for pull requests and unknowns."""
    found: dict[int, dict | None] = {}
    for i in range(0, len(numbers), _LOOKUP_CHUNK):
        chunk = numbers[i : i + _LOOKUP_CHUNK]
        fields = "\n".join(
            f"i{n}: issueOrPullRequest(number: {n}) {{ __typename ...IssueFields }}"
            for n in chunk
        )
        query = (
            "query($owner: String!, $name: String!) {\n"
            f"  repository(owner: $owner, name: $name) {{\n{fields}\n  }}\n}}\n"
            + _FIELDS
        )
        repo = gh.graphql_once(query, allow_not_found=True, owner=owner, name=name)
        for n in chunk:
            node = repo["repository"].get(f"i{n}")
            found[n] = node if node and node["__typename"] == "Issue" else None
    return found


def _open_total(owner: str, name: str) -> int:
    data = gh.graphql_once(_TOTAL_QUERY, owner=owner, name=name)
    return data["repository"]["issues"]["totalCount"]


def fetch_issues(
    repo: str,
    cache: IssueCache | None = None,
    *,
    batch: int | None = 100,
    wanted: tuple[int, ...] = (),
    extra: tuple[int, ...] = (),
) -> IssueSet:
    """Bring the known open issues of ``owner/name`` up to date.

    Each call fetches the next ``batch`` open issues (all if None), drops or
    refreshes known issues that changed since the previous call, and looks
    up the issues in ``wanted`` plus up to ``batch`` of ``extra`` whose state
    isn't known yet. With a ``cache`` the next call continues from here.
    """
    if not _REPO_RE.fullmatch(repo):
        raise ValueError(f"expected owner/name, got {repo!r}")
    owner, name = repo.split("/")
    now = now_utc()
    entry = (cache.load(repo) if cache else None) or CacheEntry()
    nodes, closed = dict(entry.nodes), set(entry.closed)

    def add(node: dict) -> None:
        if node["state"] == "OPEN":
            nodes[node["number"]] = node
            closed.discard(node["number"])
        else:
            nodes.pop(node["number"], None)
            closed.add(node["number"])

    if entry.fetched_at is not None:
        for node in _sync(owner, name, entry.fetched_at):
            add(node)

    cursor, complete = entry.cursor, entry.complete
    if not complete and batch != 0:
        page, cursor, complete = _walk(owner, name, cursor, batch)
        for node in page:
            add(node)

    def unknown(numbers: tuple[int, ...]) -> list[int]:
        return [n for n in dict.fromkeys(numbers) if n not in nodes and n not in closed]

    extra_unknown = unknown(extra)
    lookups = unknown(wanted) + (
        extra_unknown if batch is None else extra_unknown[:batch]
    )
    for n, node in _lookup(owner, name, lookups).items():
        if node is None:
            closed.add(n)  # a pull request or no such issue: never open
        else:
            add(node)

    total_open = _open_total(owner, name)
    if cache:
        cache.save(repo, CacheEntry(now, cursor, complete, nodes, frozenset(closed)))
    return IssueSet(
        [Issue.from_graphql(n) for n in nodes.values()], total_open, complete
    )


def fetch_open_issues(repo: str, cache: IssueCache | None = None) -> list[Issue]:
    """Every open issue of ``owner/name``."""
    return fetch_issues(repo, cache, batch=None).issues


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
