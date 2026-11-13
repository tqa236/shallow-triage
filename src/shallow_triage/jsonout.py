"""The JSON output: a report's candidates as a stable list for scripts.

The keys of each item are part of the documented interface (see
docs/triage-agent.md): add keys freely, but don't rename or remove them.
"""

from shallow_triage.report import RepoReport


def candidates_json(report: RepoReport) -> list[dict]:
    return [
        {
            "number": c.issue.number,
            "title": c.issue.title,
            "url": c.issue.url,
            "score": c.score,
            "linked_prs": list(c.issue.linked_prs),
            "labels": sorted(c.issue.labels),
            "comments": c.issue.comments,
            "assigned": c.issue.assigned,
        }
        for c in report.candidates
    ]
