from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from shallow_triage.serialize import from_data, to_data


@dataclass(frozen=True)
class Inner:
    a: int
    b: str = "x"


@dataclass(frozen=True)
class Outer:
    inner: Inner
    when: datetime
    items: list[Inner] = field(default_factory=list)
    pairs: tuple[tuple[int, str], ...] = ()
    tags: frozenset[str] = frozenset()
    maybe: Inner | None = None
    ratio: float = 0.0


def test_round_trip():
    obj = Outer(
        Inner(1),
        datetime(2026, 1, 2, tzinfo=UTC),
        [Inner(2, "y")],
        ((1, "a"),),
        frozenset({"b", "a"}),
        Inner(3),
        1.5,
    )
    data = to_data(obj)
    assert data["tags"] == ["a", "b"]  # sets are stored sorted
    assert from_data(Outer, data) == obj


def test_missing_fields_default_and_unknown_keys_are_ignored():
    data = {"inner": {"a": 1, "new": 2}, "when": "2026-01-02T00:00:00+00:00", "x": 0}
    assert from_data(Outer, data) == Outer(Inner(1), datetime(2026, 1, 2, tzinfo=UTC))


@pytest.mark.parametrize(
    "data",
    [
        {"inner": {"a": "1"}, "when": "2026-01-02"},  # str for int
        {"inner": {"a": True}, "when": "2026-01-02"},  # bool for int
        {"inner": {"a": 1}, "when": "2026-01-02", "pairs": [[1]]},  # short tuple
        {"inner": {"a": 1}, "when": "2026-01-02", "items": {}},  # not a list
        {"inner": [], "when": "2026-01-02"},  # not an object
        {"when": "2026-01-02"},  # required field missing
    ],
)
def test_wrong_shapes_are_rejected(data):
    with pytest.raises((TypeError, ValueError)):
        from_data(Outer, data)


def test_int_is_accepted_for_float():
    data = {"inner": {"a": 1}, "when": "2026-01-02", "ratio": 2}
    assert from_data(Outer, data).ratio == 2
