from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass
class _WatchHandle:
    path: Path
    interval: float
    callback: Callable[[Path], None]
    stop_event: threading.Event
    thread: threading.Thread | None


_active_handles: list[_WatchHandle] = []
_lock = threading.Lock()


def _watch_loop(handle: _WatchHandle) -> None:
    last_mtime = 0.0
    last_size = 0
    while not handle.stop_event.is_set():
        try:
            stat = handle.path.stat()
        except OSError:
            handle.stop_event.wait(handle.interval)
            continue
        if stat.st_mtime != last_mtime or stat.st_size != last_size:
            last_mtime = stat.st_mtime
            last_size = stat.st_size
            try:
                handle.callback(handle.path)
            except Exception as exc:
                logger.warning("settings_watcher: callback error: %s", exc)
        handle.stop_event.wait(handle.interval)


def start_watch(
    path: Path,
    callback: Callable[[Path], None],
    *,
    interval_seconds: float = 1.5,
) -> Callable[[], None]:
    handle = _WatchHandle(
        path=path,
        interval=max(0.5, float(interval_seconds)),
        callback=callback,
        stop_event=threading.Event(),
        thread=None,
    )
    thread = threading.Thread(
        target=_watch_loop, args=(handle,), name=f"settings-watch:{path.name}", daemon=True,
    )
    handle.thread = thread
    with _lock:
        _active_handles.append(handle)
    thread.start()

    def stop() -> None:
        handle.stop_event.set()
        with _lock:
            try:
                _active_handles.remove(handle)
            except ValueError:
                pass

    return stop


def shutdown_all() -> None:
    with _lock:
        handles = list(_active_handles)
        _active_handles.clear()
    for handle in handles:
        handle.stop_event.set()
