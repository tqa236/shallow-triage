from datetime import UTC, datetime

import pytest

from shallow_triage.marks import CHANGED, SAFE, Marks, code_blocks

NOW = datetime(2026, 11, 1, tzinfo=UTC)


def test_mark_holds_until_title_or_body_change(tmp_path):
    marks = Marks(tmp_path, "o/r")
    marks.mark(1, "title", "body", NOW)
    assert marks.state(1, "title", "body") == SAFE
    assert marks.state(1, "title", "body edited") == CHANGED
    assert marks.state(1, "new title", "body") == CHANGED
    assert marks.state(2, "title", "body") is None


def test_marks_are_saved_owner_only(tmp_path):
    marks = Marks(tmp_path, "o/r")
    marks.mark(1, "t", "b", NOW)
    marks.save()
    path = tmp_path / "o" / "r.json"
    assert path.stat().st_mode & 0o777 == 0o600
    again = Marks(tmp_path, "o/r")
    assert again.state(1, "t", "b") == SAFE
    assert again.unmark(1) and not again.unmark(1)


def test_unreadable_marks_are_an_error_not_a_reset(tmp_path):
    path = tmp_path / "o" / "r.json"
    path.parent.mkdir()
    path.write_text('{"version": 99, "marks": []}')
    with pytest.raises(ValueError, match="unknown format"):
        Marks(tmp_path, "o/r")


def test_code_blocks():
    body = """Report.

```python
import x
x.f()
```

```bash
rm -rf /
```

```
y = 1
```

```pycon
>>> import z
>>> z.g(
...     1)
Traceback (most recent call last):
ValueError
```
"""
    assert code_blocks(body) == [
        "import x\nx.f()\n",
        "y = 1\n",
        "import z\nz.g(\n    1)\n",
    ]
