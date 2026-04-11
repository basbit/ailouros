"""LRU cache for MCP tool results within a pipeline run (K-4).

Keyed by ``(tool_name, sha256(args_json))``.  Write tools unconditionally
invalidate their entries.  Cache is bounded by ``SWARM_MCP_CACHE_MAX_MB``.

Config env vars:
    SWARM_MCP_CACHE=0             # 0 = disabled (default), 1 = enabled
    SWARM_MCP_CACHE_MAX_MB=50     # maximum total cache size in megabytes
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Tool names that mutate state — their results must NOT be cached and they
# must invalidate overlapping read entries.
_WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "write_file",
        "apply_patch",
        "edit_file",
        "create_file",
        "delete_file",
        "move_file",
        "rename_file",
        "git_commit",
        "git_push",
        "run_command",
        "execute_command",
        "bash",
    }
)


@dataclass
class CacheEntry:
    key: str
    result: Any
    timestamp: float
    size_bytes: int


class ToolResultCache:
    """In-process LRU cache for MCP tool results.

    Args:
        max_mb: Maximum total cache size in megabytes.  Oldest entries are
                evicted when this limit is exceeded.
        enabled: When ``False`` the cache is fully transparent (get always
                 returns ``None``; put is a no-op).
    """

    def __init__(
        self,
        max_mb: float = 50.0,
        enabled: Optional[bool] = None,
    ) -> None:
        if enabled is None:
            enabled = os.getenv("SWARM_MCP_CACHE", "0").strip() == "1"
        self._enabled = enabled
        self._max_bytes = int(max_mb * 1024 * 1024)
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._current_bytes = 0
        self._hits = 0
        self._misses = 0

    # ------------------------------------------------------------------
    # Key helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_key(tool_name: str, args: dict[str, Any]) -> str:
        args_json = json.dumps(args, sort_keys=True, default=str)
        digest = hashlib.sha256(args_json.encode()).hexdigest()[:16]
        return f"{tool_name}:{digest}"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, tool_name: str, args: dict[str, Any]) -> Optional[Any]:
        """Return cached result for ``(tool_name, args)``, or ``None`` on miss.

        Cache hit/miss are logged at DEBUG level (K-4 DoD).
        When disabled always returns ``None``.
        """
        if not self._enabled:
            return None
        key = self._make_key(tool_name, args)
        entry = self._cache.get(key)
        if entry is not None:
            self._hits += 1
            self._cache.move_to_end(key)
            logger.debug("tool_cache: HIT tool=%s key=%s", tool_name, key)
            return entry.result
        self._misses += 1
        logger.debug("tool_cache: MISS tool=%s key=%s", tool_name, key)
        return None

    def put(self, tool_name: str, args: dict[str, Any], result: Any) -> None:
        """Store a tool result in the cache.

        Write tools are never cached.  Evicts oldest entries when size limit
        is reached.
        """
        if not self._enabled:
            return
        if tool_name in _WRITE_TOOLS:
            logger.debug("tool_cache: skip cache for write tool=%s", tool_name)
            return

        key = self._make_key(tool_name, args)
        size = len(json.dumps(result, default=str).encode("utf-8"))

        # Evict LRU entries until we have room
        while self._current_bytes + size > self._max_bytes and self._cache:
            _, evicted = self._cache.popitem(last=False)
            self._current_bytes -= evicted.size_bytes
            logger.debug("tool_cache: evicted key=%s size=%d", evicted.key, evicted.size_bytes)

        # If even after full eviction the single entry doesn't fit, skip it
        if size > self._max_bytes:
            logger.debug("tool_cache: entry too large to cache tool=%s size=%d", tool_name, size)
            return

        entry = CacheEntry(key=key, result=result, timestamp=time.time(), size_bytes=size)
        self._cache[key] = entry
        self._current_bytes += size

    def invalidate(self, tool_name: str = "", args: Optional[dict[str, Any]] = None) -> int:
        """Invalidate cache entries.

        - ``tool_name`` given → remove all entries for that tool.
        - Neither given → clear all entries.

        Returns the number of entries removed.
        """
        if not tool_name:
            count = len(self._cache)
            self._cache.clear()
            self._current_bytes = 0
            return count

        to_remove = [k for k in self._cache if k.startswith(f"{tool_name}:")]
        for k in to_remove:
            self._current_bytes -= self._cache[k].size_bytes
            del self._cache[k]
        return len(to_remove)

    def invalidate_writes(self, tool_name: str) -> int:
        """Invalidate entries for write tools (call after any mutation).

        If *tool_name* is a write tool, invalidates ALL read-tool entries
        (conservative — we can't know which files were affected).
        """
        if tool_name in _WRITE_TOOLS:
            count = len(self._cache)
            self._cache.clear()
            self._current_bytes = 0
            logger.debug("tool_cache: write-invalidation tool=%s cleared %d entries", tool_name, count)
            return count
        return 0

    def stats(self) -> dict[str, Any]:
        """Return hit/miss statistics and current size information."""
        return {
            "enabled": self._enabled,
            "hits": self._hits,
            "misses": self._misses,
            "entries": len(self._cache),
            "bytes": self._current_bytes,
            "max_bytes": self._max_bytes,
        }
