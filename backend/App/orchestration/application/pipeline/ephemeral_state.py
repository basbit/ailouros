
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional, TypeVar, cast

__all__ = [
    "append_ephemeral",
    "ephemeral_as_dict",
    "get_ephemeral",
    "pop_ephemeral",
    "set_ephemeral",
    "update_ephemeral",
]

T = TypeVar("T")


def set_ephemeral(state: Any, key: str, value: Any) -> None:
    cast(dict[str, Any], state)[key] = value


def get_ephemeral(state: Any, key: str, default: Optional[T] = None) -> Optional[T] | Any:
    if isinstance(state, Mapping):
        return state.get(key, default)  # type: ignore[return-value]
    return cast(dict[str, Any], state).get(key, default)


def pop_ephemeral(state: Any, key: str, default: Optional[T] = None) -> Optional[T] | Any:
    return cast(dict[str, Any], state).pop(key, default)


def append_ephemeral(state: Any, key: str, item: Any) -> None:
    cast(dict[str, Any], state).setdefault(key, []).append(item)


def update_ephemeral(state: Any, delta: Mapping[str, Any]) -> None:
    cast(dict[str, Any], state).update(delta)


def ephemeral_as_dict(state: Any) -> dict[str, Any]:
    return cast(dict[str, Any], state)
