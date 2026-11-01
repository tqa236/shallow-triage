"""Common tooling for building issue triage pages."""

from shallow_triage.issues import Issue, fetch_open_issues, score_issue

__all__ = ["Issue", "fetch_open_issues", "score_issue"]
__version__ = "0.1.0"
