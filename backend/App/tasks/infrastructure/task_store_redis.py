from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional, cast

from backend.App.shared.application.datetime_utils import utc_now_iso

try:
    import redis  # type: ignore[import-not-found]
    from redis.exceptions import WatchError  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    redis = None  # type: ignore[assignment]
    WatchError = Exception  # type: ignore[misc,assignment]

from backend.App.tasks.infrastructure.config import REDIS_URL as _DEFAULT_REDIS_URL
from backend.App.shared.domain.exceptions import ConcurrentUpdateError
from backend.App.shared.infrastructure.app_config_load import load_app_config_json

logger = logging.getLogger(__name__)

_TASK_STORE_CONFIG = load_app_config_json("task_store.json")
_REDIS_KEY_PREFIX: str = str(_TASK_STORE_CONFIG["redis_key_prefix"])
_OPTIMISTIC_LOCK_MAX_RETRIES: int = int(_TASK_STORE_CONFIG["optimistic_lock_max_retries"])


__all__ = [
    "RedisTaskStore",
    "ConcurrentUpdateError",
    "TaskStore",
    "TASK_STATUS_IN_PROGRESS",
    "TASK_STATUS_COMPLETED",
    "TASK_STATUS_FAILED",
    "TASK_STATUS_CANCELLED",
    "TASK_STATUS_AWAITING_HUMAN",
    "TASK_STATUS_AWAITING_SHELL",
]

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
    def __init__(self, client: "redis.Redis", ttl_sec: int = _TASK_TTL_SECONDS) -> None:
        self.client = client
        self._ttl_sec = ttl_sec

    def _key(self, task_id: str) -> str:
        return f"{_REDIS_KEY_PREFIX}{task_id}"

    def _apply_update(
        self,
        payload: dict[str, Any],
        *,
        status: Optional[str] = None,
        agent: Optional[str] = None,
        message: Optional[str] = None,
    ) -> dict[str, Any]:
        if status is not None:
            payload["status"] = status
        if agent is not None and str(agent).strip():
            if agent not in payload["agents"]:
                payload["agents"].append(agent)
        if message is not None and str(message).strip():
            payload["history"].append(
                {
                    "timestamp": utc_now_iso(),
                    "agent": agent,
                    "message": message,
                }
            )
        payload["updated_at"] = utc_now_iso()
        payload["version"] = payload.get("version", 0) + 1
        return payload

    def create_task(self, prompt: str) -> dict[str, Any]:
        import uuid

        task_id = str(uuid.uuid4())
        payload: dict[str, Any] = {
            "task_id": task_id,
            "task": prompt,
            "status": TASK_STATUS_IN_PROGRESS,
            "agents": [],
            "history": [],
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
            "version": 0,
        }
        self.client.set(
            self._key(task_id),
            json.dumps(payload, ensure_ascii=False),
            ex=self._ttl_sec,
        )
        return payload

    def get_task(self, task_id: Any) -> dict[str, Any]:
        task_id = str(task_id)
        raw = self.client.get(self._key(task_id))
        if not raw:
            raise KeyError(task_id)
        return cast(dict[str, Any], json.loads(cast(Any, raw)))

    def update_task(
        self,
        task_id: Any,
        *,
        status: Optional[str] = None,
        agent: Optional[str] = None,
        message: Optional[str] = None,
    ) -> dict[str, Any]:
        task_id = str(task_id)
        key = self._key(task_id)
        for attempt in range(_OPTIMISTIC_LOCK_MAX_RETRIES):
            pipe = self.client.pipeline()
            try:
                pipe.watch(key)
                raw = self.client.get(key)
                if not raw:
                    pipe.reset()
                    raise KeyError(task_id)
                payload = json.loads(cast(Any, raw))
                payload = self._apply_update(payload, status=status, agent=agent, message=message)
                pipe.multi()
                pipe.set(key, json.dumps(payload, ensure_ascii=False), ex=self._ttl_sec)
                pipe.execute()
                return payload
            except KeyError:
                raise
            except WatchError:
                if attempt < _OPTIMISTIC_LOCK_MAX_RETRIES - 1:
                    logger.warning(
                        "Optimistic lock conflict on task %s (attempt %d/%d) — retrying.",
                        task_id, attempt + 1, _OPTIMISTIC_LOCK_MAX_RETRIES,
                    )
                    continue
                raise ConcurrentUpdateError(
                    f"Optimistic lock conflict on task {task_id} after {_OPTIMISTIC_LOCK_MAX_RETRIES} attempts — "
                    "concurrent update could not be applied safely."
                )
            finally:
                try:
                    pipe.reset()
                except Exception as exc:
                    logger.debug("Redis pipeline reset failed: %s", exc)
        raise RuntimeError(f"Failed to update task {task_id} after {_OPTIMISTIC_LOCK_MAX_RETRIES} attempts")

    def delete_task(self, task_id: Any) -> None:
        task_id = str(task_id)
        self.client.delete(self._key(task_id))


def _build_legacy_task_store() -> Any:
    from backend.App.tasks.infrastructure.task_store_memory import InMemoryTaskStore
    from backend.App.tasks.infrastructure.task_store_fallback import FallbackTaskStore

    redis_url = os.getenv("REDIS_URL", _DEFAULT_REDIS_URL)
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


class _LegacyInMemoryProxy:
    pass


def TaskStore() -> Any:
    return _build_legacy_task_store()
