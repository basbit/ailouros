from __future__ import annotations

import logging
from typing import Any, Optional

from backend.App.tasks.infrastructure.task_store_memory import InMemoryTaskStore
from backend.App.tasks.infrastructure.task_store_redis import RedisTaskStore

logger = logging.getLogger(__name__)

__all__ = ["FallbackTaskStore"]


class FallbackTaskStore:
    def __init__(self, primary: RedisTaskStore, fallback: InMemoryTaskStore) -> None:
        self._primary = primary
        self._fallback = fallback

    @staticmethod
    def _is_connection_error(exc: Exception) -> bool:
        try:
            from redis.exceptions import ConnectionError as RedisConnectionError
            from redis.exceptions import TimeoutError as RedisTimeoutError
            if isinstance(exc, (RedisConnectionError, RedisTimeoutError)):
                return True
        except ImportError:
            pass
        return isinstance(exc, (OSError, IOError))

    def _with_fallback(self, primary_call: Any, fallback_call: Any) -> Any:
        try:
            return primary_call()
        except KeyError:
            raise
        except Exception as exc:
            if self._is_connection_error(exc):
                logger.warning(
                    "FallbackTaskStore: Redis unavailable (%s) — using in-memory fallback.",
                    exc,
                )
                return fallback_call()
            raise

    def create_task(self, prompt: str) -> dict[str, Any]:
        return self._with_fallback(
            lambda: self._primary.create_task(prompt),
            lambda: self._fallback.create_task(prompt),
        )

    def get_task(self, task_id: Any) -> dict[str, Any]:
        task_id_str = str(task_id)
        try:
            return self._primary.get_task(task_id_str)
        except KeyError:
            raise
        except Exception as exc:
            if self._is_connection_error(exc):
                logger.warning(
                    "FallbackTaskStore: Redis unavailable on get_task(%s) (%s) — using in-memory.",
                    task_id_str, exc,
                )
                return self._fallback.get_task(task_id_str)
            raise

    def update_task(
        self,
        task_id: Any,
        *,
        status: Optional[str] = None,
        agent: Optional[str] = None,
        message: Optional[str] = None,
    ) -> dict[str, Any]:
        return self._with_fallback(
            lambda: self._primary.update_task(
                task_id, status=status, agent=agent, message=message
            ),
            lambda: self._fallback.update_task(
                task_id, status=status, agent=agent, message=message
            ),
        )

    def delete_task(self, task_id: Any) -> None:
        task_id_str = str(task_id)
        try:
            self._primary.delete_task(task_id_str)
        except Exception as exc:
            if self._is_connection_error(exc):
                logger.warning(
                    "FallbackTaskStore: Redis unavailable on delete_task(%s) (%s).",
                    task_id_str, exc,
                )
            else:
                raise
        self._fallback.delete_task(task_id_str)
