from datetime import UTC, datetime

import pytest

from shallow_triage.findings import FIXED, NOT_REPRODUCIBLE, Finding, Findings

NOW = datetime(2026, 11, 1, tzinfo=UTC)


def test_findings_round_trip(tmp_path):
    findings = Findings(tmp_path, "o/r")
    findings.add(
        Finding(1, FIXED, NOW, commit="abc", pr="https://github.com/o/r/pull/2")
    )
    findings.add(Finding(3, NOT_REPRODUCIBLE, NOW, method="ran on main"))
    findings.save()
    path = tmp_path / "o" / "r.json"
    assert path.stat().st_mode & 0o777 == 0o600
    again = Findings(tmp_path, "o/r")
    assert again.items == findings.items
    assert again.remove(1) and not again.remove(1)


def test_bad_status(tmp_path):
    with pytest.raises(ValueError, match="status"):
        Findings(tmp_path, "o/r").add(Finding(1, "maybe", NOW))
