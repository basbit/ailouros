"""Флаги кооперативной отмены SSE-пайплайна (disconnect / shutdown uvicorn)."""

from __future__ import annotations

import threading

# Выставляется в lifespan при выходе из приложения — активные стримы прекращают шаги без
# ожидания завершения уже субмиченного LLM-запроса в ThreadPoolExecutor.
SERVER_STREAM_SHUTDOWN = threading.Event()

# Реестр cancel-событий активных задач: task_id → Event.
# Позволяет cancel endpoint'у остановить конкретный pipeline run по task_id.
_task_cancel_registry: dict[str, threading.Event] = {}
_task_cancel_registry_lock = threading.Lock()


def clear_stream_shutdown() -> None:
    SERVER_STREAM_SHUTDOWN.clear()


def mark_stream_shutdown_start() -> None:
    SERVER_STREAM_SHUTDOWN.set()


def register_task_cancel_event(task_id: str, event: threading.Event) -> None:
    """Зарегистрировать cancel-событие для активной задачи."""
    if not task_id:
        return
    with _task_cancel_registry_lock:
        _task_cancel_registry[task_id] = event


def unregister_task_cancel_event(task_id: str) -> None:
    """Снять регистрацию после завершения или отмены задачи."""
    if not task_id:
        return
    with _task_cancel_registry_lock:
        _task_cancel_registry.pop(task_id, None)


def cancel_task_by_id(task_id: str) -> bool:
    """Выставить cancel-событие для задачи. Возвращает True если задача была активна."""
    with _task_cancel_registry_lock:
        ev = _task_cancel_registry.get(task_id)
    if ev is not None:
        ev.set()
        return True
    return False
