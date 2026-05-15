from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from collections.abc import ItemsView, Iterator
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

__all__ = [
    "CacheStats",
    "LRUDict",
    "ThreadSafeLRUDict",
    "hash_cache_key",
]

K = TypeVar("K")
V = TypeVar("V")


class LRUDict(Generic[K, V]):
    def __init__(self, max_size: int = 128) -> None:
        self._data: OrderedDict[K, V] = OrderedDict()
        self._max_size = max(1, max_size)

    def get(self, key: K, default: V | None = None) -> V | None:
        if key not in self._data:
            return default
        self._data.move_to_end(key)
        return self._data[key]

    def set(self, key: K, value: V) -> None:
        if key in self._data:
            self._data.move_to_end(key)
        self._data[key] = value
        while len(self._data) > self._max_size:
            self._data.popitem(last=False)

    def pop(self, key: K, default: V | None = None) -> V | None:
        if key in self._data:
            return self._data.pop(key)
        return default

    def clear(self) -> None:
        self._data.clear()

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self) -> Iterator[K]:
        return iter(self._data)

    def __getitem__(self, key: K) -> V:
        if key not in self._data:
            raise KeyError(key)
        self._data.move_to_end(key)
        return self._data[key]

    def __setitem__(self, key: K, value: V) -> None:
        self.set(key, value)

    def items(self) -> ItemsView[K, V]:
        return self._data.items()


class ThreadSafeLRUDict(Generic[K, V]):

    def __init__(self, max_size: int = 128) -> None:
        self._data: OrderedDict[K, V] = OrderedDict()
        self._max_size = max(1, max_size)
        self._lock = threading.Lock()

    def get(self, key: K, default: V | None = None) -> V | None:
        with self._lock:
            if key not in self._data:
                return default
            self._data.move_to_end(key)
            return self._data[key]

    def set(self, key: K, value: V) -> None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = value
            while len(self._data) > self._max_size:
                self._data.popitem(last=False)

    def get_many(self, keys: list[K]) -> dict[K, V]:
        with self._lock:
            result: dict[K, V] = {}
            for key in keys:
                if key in self._data:
                    self._data.move_to_end(key)
                    result[key] = self._data[key]
            return result

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __contains__(self, key: object) -> bool:
        with self._lock:
            return key in self._data

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


@dataclass
class CacheStats:

    hits: int = 0
    misses: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def record_hit(self) -> None:
        self.hits += 1

    def record_miss(self) -> None:
        self.misses += 1

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"hits": self.hits, "misses": self.misses}
        payload.update(self.extra)
        return payload


def hash_cache_key(prefix: str, payload: Any, *, digest_len: int = 16) -> str:
    if isinstance(payload, str):
        raw = payload
    else:
        raw = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[
        : max(8, digest_len)
    ]
    return f"{prefix}:{digest}" if prefix else digest
