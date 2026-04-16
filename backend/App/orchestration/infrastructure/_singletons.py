"""Shared adapter singletons for the orchestration layer.

Provides lazily-initialised, process-wide instances of:
- InMemorySessionStore / RedisSessionStore  (session persistence, R1.1)
- InMemoryTraceCollector / RedisTraceCollector  (step tracing, R1.4)
- SessionManager  (R1.1 lifecycle helper)

Redis variants are selected automatically when REDIS_URL is set.
Everything falls back to in-memory for zero-config local usage.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

_session_store_lock = threading.Lock()
_trace_collector_lock = threading.Lock()
_session_manager_lock = threading.Lock()

_session_store: Optional[Any] = None
_trace_collector: Optional[Any] = None
_session_manager: Optional[Any] = None


def _redis_available() -> bool:
    return bool(os.getenv("REDIS_URL"))


def get_session_store() -> Any:
    """Return the process-wide SessionStorePort adapter."""
    global _session_store
    if _session_store is not None:
        return _session_store
    with _session_store_lock:
        if _session_store is not None:
            return _session_store
        if _redis_available():
            try:
                import redis  # type: ignore[import-not-found]
                from backend.App.orchestration.infrastructure.redis_session_store import (
                    RedisSessionStore,
                )
                _redis_client = redis.Redis.from_url(os.environ["REDIS_URL"])
                _session_store = RedisSessionStore(_redis_client)
                logger.info("Singletons: using RedisSessionStore")
            except Exception as exc:
                logger.warning("RedisSessionStore unavailable (%s), falling back to in-memory", exc)
                from backend.App.orchestration.infrastructure.in_memory_session_store import (
                    InMemorySessionStore,
                )
                _session_store = InMemorySessionStore()
        else:
            from backend.App.orchestration.infrastructure.in_memory_session_store import (
                InMemorySessionStore,
            )
            _session_store = InMemorySessionStore()
            logger.debug("Singletons: using InMemorySessionStore")
    return _session_store


def get_trace_collector() -> Any:
    """Return the process-wide TraceCollectorPort adapter."""
    global _trace_collector
    if _trace_collector is not None:
        return _trace_collector
    with _trace_collector_lock:
        if _trace_collector is not None:
            return _trace_collector
        if _redis_available():
            try:
                import redis  # type: ignore[import-not-found]
                from backend.App.orchestration.infrastructure.redis_trace_collector import (
                    RedisTraceCollector,
                )
                _redis_client = redis.Redis.from_url(os.environ["REDIS_URL"])
                _trace_collector = RedisTraceCollector(_redis_client)
                logger.info("Singletons: using RedisTraceCollector")
            except Exception as exc:
                logger.warning("RedisTraceCollector unavailable (%s), falling back to in-memory", exc)
                from backend.App.orchestration.infrastructure.in_memory_trace_collector import (
                    InMemoryTraceCollector,
                )
                _trace_collector = InMemoryTraceCollector()
        else:
            from backend.App.orchestration.infrastructure.in_memory_trace_collector import (
                InMemoryTraceCollector,
            )
            _trace_collector = InMemoryTraceCollector()
            logger.debug("Singletons: using InMemoryTraceCollector")
    return _trace_collector


def get_session_manager() -> Any:
    """Return the process-wide SessionManager."""
    global _session_manager
    if _session_manager is not None:
        return _session_manager
    with _session_manager_lock:
        if _session_manager is not None:
            return _session_manager
        from backend.App.orchestration.application.session_manager import SessionManager
        _session_manager = SessionManager(get_session_store())
        logger.debug("Singletons: SessionManager created")
    return _session_manager
