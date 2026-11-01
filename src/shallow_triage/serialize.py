"""Convert the data model to and from JSON, tolerating version differences.

Saved data (reports) must stay readable when the model changes, so loading
follows two rules:

- a field missing from the file takes the dataclass default, so older files
  load after a field is added (every added field needs a default);
- a key the dataclass doesn't have is ignored, so newer files load in an
  older version.

Supported types: dataclasses, ``int``, ``str``, ``bool``, ``float``,
``datetime``, ``X | None``, ``list[X]``, ``tuple[X, ...]``, fixed tuples
and ``frozenset[X]``.
"""

import dataclasses
import types
import typing
from datetime import datetime
from typing import Any


def to_data(obj: Any) -> Any:
    """``obj`` as JSON-compatible lists, dicts and scalars."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_data(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, frozenset):
        return sorted(to_data(x) for x in obj)
    if isinstance(obj, list | tuple):
        return [to_data(x) for x in obj]
    return obj


def from_data(cls: Any, data: Any) -> Any:
    """Build a ``cls`` from what :func:`to_data` produced."""
    origin = typing.get_origin(cls)
    args = typing.get_args(cls)
    if origin in (typing.Union, types.UnionType):
        if data is None and type(None) in args:
            return None
        (inner,) = [a for a in args if a is not type(None)]
        return from_data(inner, data)
    if dataclasses.is_dataclass(cls):
        if not isinstance(data, dict):
            raise TypeError(f"expected an object for {cls.__name__}")
        hints = typing.get_type_hints(cls)
        values = {
            f.name: from_data(hints[f.name], data[f.name])
            for f in dataclasses.fields(cls)
            if f.name in data
        }
        return cls(**values)
    if origin is list:
        return [from_data(args[0], x) for x in _list(data)]
    if origin is frozenset:
        return frozenset(from_data(args[0], x) for x in _list(data))
    if origin is tuple:
        items = _list(data)
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(from_data(args[0], x) for x in items)
        if len(items) != len(args):
            raise TypeError(f"expected {len(args)} items, got {len(items)}")
        return tuple(from_data(a, x) for a, x in zip(args, items, strict=True))
    if cls is datetime:
        return datetime.fromisoformat(data)
    if cls in (int, str, bool, float):
        # bool is an int in Python; don't let true/false pass for numbers
        if type(data) is not cls and not (cls is float and type(data) is int):
            raise TypeError(f"expected {cls.__name__}, got {type(data).__name__}")
        return data
    raise TypeError(f"unsupported type {cls!r}")


def _list(data: Any) -> list:
    if not isinstance(data, list):
        raise TypeError(f"expected a list, got {type(data).__name__}")
    return data
