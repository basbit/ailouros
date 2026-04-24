"""A minimal generic registry primitive.

Multiple domains had their own one-off ``register()`` / ``get()`` /
``keys()`` dictionaries (``AnalyzerRegistry`` in workspace, ``RoleRegistry`` in
orchestration, ``PipelineStepRegistry`` in orchestration). Most of that
boilerplate is identical — a dict backing + sorted key listing + small
validation. This module provides the common primitive so future registries
don't grow yet another bespoke class.

The existing ``RoleRegistry`` and ``PipelineStepRegistry`` keep their own APIs
because they carry richer domain semantics (builtin-vs-custom flags,
pre-populated module-level mappings). ``AnalyzerRegistry`` has been migrated
to use ``GenericRegistry`` directly.
"""

from __future__ import annotations

from typing import Generic, Iterable, Iterator, TypeVar

__all__ = ["GenericRegistry"]

K = TypeVar("K")
V = TypeVar("V")


class GenericRegistry(Generic[K, V]):
    """Small typed key→value registry with deterministic ordering.

    Not thread-safe. Intended for process-lifetime registrations done at
    import time (language analyzers, pipeline steps, tool handlers, …). If
    you need concurrent registration, wrap with your own lock.
    """

    def __init__(self, *, name: str = "") -> None:
        self._name = name
        self._items: dict[K, V] = {}

    @property
    def name(self) -> str:
        return self._name

    def register(self, key: K, value: V, *, overwrite: bool = True) -> None:
        if not overwrite and key in self._items:
            raise ValueError(
                f"{self._name or 'registry'}: key {key!r} already registered"
            )
        self._items[key] = value

    def get(self, key: K, default: V | None = None) -> V | None:
        return self._items.get(key, default)

    def require(self, key: K) -> V:
        if key not in self._items:
            raise KeyError(
                f"{self._name or 'registry'}: unknown key {key!r}"
            )
        return self._items[key]

    def has(self, key: K) -> bool:
        return key in self._items

    def __contains__(self, key: object) -> bool:
        return key in self._items

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[K]:
        return iter(self._items)

    def keys_sorted(self) -> list[K]:
        try:
            return sorted(self._items.keys())  # type: ignore[type-var]
        except TypeError:
            # Fallback for non-orderable keys — preserve insertion order.
            return list(self._items.keys())

    def items(self) -> Iterable[tuple[K, V]]:
        return self._items.items()

    def clear(self) -> None:
        self._items.clear()
