"""Redis-backed task store with optimistic locking.

Fails fast if Redis is unavailable — no silent in-memory fallback.
For a fallback-capable store see FallbackTaskStore in task_store_fallback.py.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

try:
    import redis
    from redis.exceptions import WatchError
except ImportError:  # pragma: no cover
    redis = None
    WatchError = Exception

from backend.App.tasks.infrastructure.config import REDIS_URL as _DEFAULT_REDIS_URL

logger = logging.getLogger(__name__)


class ConcurrentUpdateError(Exception):
    """Raised when a task update cannot be applied due to concurrent modification."""


__all__ = [
    "RedisTaskStore",
    "ConcurrentUpdateError",
    # Legacy name kept for backward compatibility — resolves to RedisTaskStore
    "TaskStore",
    "TASK_STATUS_IN_PROGRESS",
    "TASK_STATUS_COMPLETED",
    "TASK_STATUS_FAILED",
    "TASK_STATUS_CANCELLED",
    "TASK_STATUS_AWAITING_HUMAN",
    "TASK_STATUS_AWAITING_SHELL",
]

# Task status constants — use these instead of bare string literals.
# Mirrors backend.App.domain.ports.TaskStatus enum values.
TASK_STATUS_IN_PROGRESS: str = "in_progress"
TASK_STATUS_COMPLETED: str = "completed"
TASK_STATUS_FAILED: str = "failed"
TASK_STATUS_CANCELLED: str = "cancelled"
TASK_STATUS_AWAITING_HUMAN: str = "awaiting_human"
TASK_STATUS_AWAITING_SHELL: str = "awaiting_shell"

_TASK_TTL_SECONDS = int(os.getenv("SWARM_TASK_TTL_SECONDS", "604800"))

try:
    _TASK_MEMORY_MAX = max(1, int(os.getenv("SWARM_TASK_MEMORY_MAX", "1000")))
except ValueError:
    _TASK_MEMORY_MAX = 1000


def _redis_socket_timeout_params() -> dict[str, Any]:
    """Build Redis socket timeout kwargs from environment variables.

    Without explicit timeouts redis-py may hang indefinitely on a dead TCP
    connection during each update_task call (SSE heartbeat path).
    """

    def _opt_float(name: str, default: Optional[float]) -> Optional[float]:
        raw = os.getenv(name, "").strip()
        if not raw:
            return default
        if raw.lower() in ("none", "off", "unlimited"):
            return None
        try:
            v = float(raw)
            return v if v > 0 else None
        except ValueError:
            return default

    connect = _opt_float("REDIS_SOCKET_CONNECT_TIMEOUT", 5.0)
    sock = _opt_float("REDIS_SOCKET_TIMEOUT", 30.0)
    out: dict[str, Any] = {}
    if connect is not None:
        out["socket_connect_timeout"] = connect
    if sock is not None:
        out["socket_timeout"] = sock
    return out


class RedisTaskStore:
    """Task store backed by Redis with optimistic-locking (WATCH/MULTI/EXEC).

    Fails fast on construction if Redis is unavailable — callers should
    wrap this with FallbackTaskStore when graceful degradation is desired.
    """

    def __init__(self, client: "redis.Redis", ttl_sec: int = _TASK_TTL_SECONDS) -> None:
        self.client = client
        self._ttl_sec = ttl_sec

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _key(self, task_id: str) -> str:
        return f"swarm:task:{task_id}"

    def _apply_update(
        self,
        payload: dict[str, Any],
        *,
        status: Optional[str] = None,
        agent: Optional[str] = None,
        message: Optional[str] = None,
    ) -> dict[str, Any]:
        """Return *payload* mutated with the supplied fields."""
        # Distinguish "not passed" from "empty string" — use `is not None` checks.
        if status is not None:
            payload["status"] = status
        if agent is not None and str(agent).strip():
            if agent not in payload["agents"]:
                payload["agents"].append(agent)
        if message is not None and str(message).strip():
            payload["history"].append(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "agent": agent,
                    "message": message,
                }
            )
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        payload["version"] = payload.get("version", 0) + 1
        return payload

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def create_task(self, prompt: str) -> dict[str, Any]:
        """Create a new task record in Redis and return it."""
        import uuid

        task_id = str(uuid.uuid4())
        payload: dict[str, Any] = {
            "task_id": task_id,
            "task": prompt,
            "status": TASK_STATUS_IN_PROGRESS,
            "agents": [],
            "history": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "version": 0,
        }
        self.client.set(
            self._key(task_id),
            json.dumps(payload, ensure_ascii=False),
            ex=self._ttl_sec,
        )
        return payload

    def get_task(self, task_id: Any) -> dict[str, Any]:
        """Return the task record for *task_id*.

        Raises:
            KeyError: if the key does not exist in Redis.
        """
        task_id = str(task_id)
        raw = self.client.get(self._key(task_id))
        if not raw:
            raise KeyError(task_id)
        return json.loads(raw)

    def update_task(
        self,
        task_id: Any,
        *,
        status: Optional[str] = None,
        agent: Optional[str] = None,
        message: Optional[str] = None,
    ) -> dict[str, Any]:
        """Apply a partial update using optimistic locking (WATCH/MULTI/EXEC).

        Raises:
            KeyError: if the task does not exist.
            ConcurrentUpdateError: if the optimistic lock retries are exhausted.
        """
        task_id = str(task_id)
        key = self._key(task_id)
        max_retries = 5
        for attempt in range(max_retries):
            pipe = self.client.pipeline()
            try:
                pipe.watch(key)
                raw = self.client.get(key)
                if not raw:
                    pipe.reset()
                    raise KeyError(task_id)
                payload = json.loads(raw)
                payload = self._apply_update(payload, status=status, agent=agent, message=message)
                pipe.multi()
                pipe.set(key, json.dumps(payload, ensure_ascii=False), ex=self._ttl_sec)
                pipe.execute()
                return payload
            except KeyError:
                raise  # task not found — do not retry
            except WatchError:
                if attempt < max_retries - 1:
                    logger.warning(
                        "Optimistic lock conflict on task %s (attempt %d/%d) — retrying.",
                        task_id, attempt + 1, max_retries,
                    )
                    continue
                raise ConcurrentUpdateError(
                    f"Optimistic lock conflict on task {task_id} after {max_retries} attempts — "
                    "concurrent update could not be applied safely."
                )
            finally:
                try:
                    pipe.reset()
                except Exception as exc:
                    logger.debug("Redis pipeline reset failed: %s", exc)
        # Unreachable, but satisfies type checker
        raise RuntimeError(f"Failed to update task {task_id} after {max_retries} attempts")

    def delete_task(self, task_id: Any) -> None:
        """Remove a task from Redis."""
        task_id = str(task_id)
        self.client.delete(self._key(task_id))


# ---------------------------------------------------------------------------
# Backward-compat factory: "TaskStore" is kept importable from this module.
# Callers that do `from task_store_redis import TaskStore` continue to work.
# New code should use FallbackTaskStore from task_store_fallback.py instead.
# ---------------------------------------------------------------------------

def _build_legacy_task_store() -> "RedisTaskStore | _LegacyInMemoryProxy":
    """Instantiate a store using environment variables (legacy entry-point).

    Kept so that existing `task_instance.py` imports still resolve.
    Prefer constructing FallbackTaskStore explicitly in the composition root.
    """
    from backend.App.tasks.infrastructure.task_store_memory import InMemoryTaskStore
    from backend.App.tasks.infrastructure.task_store_fallback import FallbackTaskStore

    redis_url = os.getenv("REDIS_URL", _DEFAULT_REDIS_URL)
    # Default false: graceful degradation to in-memory if Redis is down (e.g. local dev).
    # Set REDIS_REQUIRED=1 in production when Redis must be up.
    redis_required = os.getenv("REDIS_REQUIRED", "0").strip().lower() in (
        "1", "true", "yes", "on",
    )
    if redis is None:
        if redis_required:
            raise RuntimeError(
                "REDIS_REQUIRED=1, but the redis package is not installed or cannot be imported."
            )
        logger.warning(
            "redis package is not installed: TaskStore is running in-process memory only "
            "(tasks will not be shared across multiple uvicorn workers)."
        )
        return InMemoryTaskStore(max_size=_TASK_MEMORY_MAX)

    try:
        redis_client = redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            **_redis_socket_timeout_params(),
        )
        redis_client.ping()
        primary = RedisTaskStore(redis_client, ttl_sec=_TASK_TTL_SECONDS)
        fallback = InMemoryTaskStore(max_size=_TASK_MEMORY_MAX)
        return FallbackTaskStore(primary=primary, fallback=fallback)
    except Exception as exc:
        if redis_required:
            raise RuntimeError(
                f"REDIS_REQUIRED=1, but Redis is unavailable: {exc}"
            ) from exc
        logger.warning(
            "Redis unavailable (%s): TaskStore is running in-process memory only "
            "(tasks will not be shared across multiple uvicorn workers).",
            exc,
        )
        return InMemoryTaskStore(max_size=_TASK_MEMORY_MAX)


# Expose "TaskStore" as a callable that returns the appropriate store — keeps
# `task_instance.py` working without changes.
class _LegacyInMemoryProxy:
    """Placeholder type for the union returned by _build_legacy_task_store."""


def TaskStore() -> Any:
    """Legacy factory kept for backward-compatibility with task_instance.py."""
    return _build_legacy_task_store()
