"""Tests for K-4: MCP Tool Result Cache."""
from __future__ import annotations

from backend.App.integrations.infrastructure.mcp.tool_cache import ToolResultCache


def test_put_get_hit():
    cache = ToolResultCache(max_mb=1.0, enabled=True)
    cache.put("read_file", {"path": "/foo.py"}, "file contents here")
    result = cache.get("read_file", {"path": "/foo.py"})
    assert result == "file contents here"
    assert cache.stats()["hits"] == 1
    assert cache.stats()["misses"] == 0


def test_miss():
    cache = ToolResultCache(max_mb=1.0, enabled=True)
    result = cache.get("read_file", {"path": "/bar.py"})
    assert result is None
    assert cache.stats()["misses"] == 1
    assert cache.stats()["hits"] == 0


def test_different_args_different_entries():
    cache = ToolResultCache(max_mb=1.0, enabled=True)
    cache.put("read_file", {"path": "/a.py"}, "aaa")
    cache.put("read_file", {"path": "/b.py"}, "bbb")
    assert cache.get("read_file", {"path": "/a.py"}) == "aaa"
    assert cache.get("read_file", {"path": "/b.py"}) == "bbb"


def test_args_order_independent():
    """Cache key must be identical regardless of dict key order."""
    cache = ToolResultCache(max_mb=1.0, enabled=True)
    cache.put("search", {"q": "hello", "limit": 10}, "result")
    # Same args in different order
    assert cache.get("search", {"limit": 10, "q": "hello"}) == "result"


def test_lru_eviction():
    """Oldest entries are evicted when size limit is exceeded."""
    # Each result is "x"*50 → json.dumps gives 52 bytes (with quotes).
    # Set limit to 60 bytes so that two entries (104 bytes) force eviction of the first.
    limit_bytes = 60
    max_mb = limit_bytes / (1024 * 1024)
    cache = ToolResultCache(max_mb=max_mb, enabled=True)
    # Put first entry (~52 bytes) — fits
    cache.put("read_file", {"path": "/first.py"}, "x" * 50)
    assert cache.stats()["entries"] == 1
    # Put second entry (~52 bytes) — total would exceed 60 bytes, first is evicted
    cache.put("read_file", {"path": "/second.py"}, "y" * 50)
    # First entry should be evicted
    assert cache.get("read_file", {"path": "/first.py"}) is None
    # Second entry should be present
    assert cache.get("read_file", {"path": "/second.py"}) is not None


def test_write_tool_not_cached():
    """Write tools must never be stored in the cache."""
    cache = ToolResultCache(max_mb=1.0, enabled=True)
    cache.put("write_file", {"path": "/out.py", "content": "data"}, "ok")
    # put is a no-op for write tools
    result = cache.get("write_file", {"path": "/out.py", "content": "data"})
    assert result is None
    assert cache.stats()["entries"] == 0


def test_invalidate_by_tool():
    cache = ToolResultCache(max_mb=1.0, enabled=True)
    cache.put("read_file", {"path": "/a.py"}, "aaa")
    cache.put("read_file", {"path": "/b.py"}, "bbb")
    cache.put("list_dir", {"path": "/"}, ["a", "b"])
    removed = cache.invalidate("read_file")
    assert removed == 2
    assert cache.get("read_file", {"path": "/a.py"}) is None
    assert cache.get("read_file", {"path": "/b.py"}) is None
    # list_dir entry should remain
    assert cache.get("list_dir", {"path": "/"}) == ["a", "b"]


def test_invalidate_all():
    cache = ToolResultCache(max_mb=1.0, enabled=True)
    cache.put("read_file", {"path": "/a.py"}, "aaa")
    cache.put("list_dir", {"path": "/"}, [])
    removed = cache.invalidate()
    assert removed == 2
    assert cache.stats()["entries"] == 0
    assert cache.stats()["bytes"] == 0


def test_invalidate_writes_clears_all():
    cache = ToolResultCache(max_mb=1.0, enabled=True)
    cache.put("read_file", {"path": "/a.py"}, "content")
    cache.put("list_dir", {"path": "/"}, [])
    # A write tool call should invalidate all read entries
    removed = cache.invalidate_writes("write_file")
    assert removed == 2
    assert cache.stats()["entries"] == 0


def test_invalidate_writes_noop_for_read_tool():
    cache = ToolResultCache(max_mb=1.0, enabled=True)
    cache.put("read_file", {"path": "/a.py"}, "content")
    removed = cache.invalidate_writes("read_file")  # not a write tool
    assert removed == 0
    assert cache.stats()["entries"] == 1


def test_stats():
    cache = ToolResultCache(max_mb=10.0, enabled=True)
    cache.put("read_file", {"path": "/x.py"}, "data")
    cache.get("read_file", {"path": "/x.py"})   # hit
    cache.get("read_file", {"path": "/y.py"})   # miss
    s = cache.stats()
    assert s["hits"] == 1
    assert s["misses"] == 1
    assert s["entries"] == 1
    assert s["bytes"] > 0
    assert s["enabled"] is True


def test_disabled_cache_always_misses():
    cache = ToolResultCache(max_mb=1.0, enabled=False)
    cache.put("read_file", {"path": "/a.py"}, "should not store")
    assert cache.get("read_file", {"path": "/a.py"}) is None
    assert cache.stats()["entries"] == 0
