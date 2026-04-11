"""File system watcher for background agent (K-10).

Uses watchdog library if available, falls back to polling.
INV-4: watcher never auto-applies changes — recommendations only.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_POLL_INTERVAL = float(os.getenv("SWARM_BACKGROUND_AGENT_POLL_INTERVAL", "5.0"))


@dataclass
class FileEvent:
    event_type: str  # "created" | "modified" | "deleted"
    path: str
    timestamp: float


class FileWatcher:
    """Watch filesystem paths and invoke callback on changes."""

    def __init__(self) -> None:
        self._running = False
        self._thread: threading.Thread | None = None
        self._callback: Callable[[FileEvent], None] | None = None
        self._paths: list[Path] = []
        self._snapshots: dict[str, float] = {}

    def watch(self, paths: list[str], callback: Callable[[FileEvent], None]) -> None:
        if self._running:
            return
        self._paths = [Path(p) for p in paths if Path(p).exists()]
        self._callback = callback
        self._running = True
        self._snapshots = self._snapshot()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info("FileWatcher: watching %d paths", len(self._paths))

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None

    def _snapshot(self) -> dict[str, float]:
        result: dict[str, float] = {}
        for base in self._paths:
            for p in base.rglob("*"):
                if p.is_file():
                    try:
                        result[str(p)] = p.stat().st_mtime
                    except OSError:
                        pass
        return result

    def _poll_loop(self) -> None:
        while self._running:
            time.sleep(_POLL_INTERVAL)
            try:
                self._check_changes()
            except Exception as exc:
                logger.warning("FileWatcher: poll error: %s", exc)

    def _check_changes(self) -> None:
        current = self._snapshot()
        prev = self._snapshots

        for path, mtime in current.items():
            if path not in prev:
                self._emit(FileEvent("created", path, mtime))
            elif prev[path] != mtime:
                self._emit(FileEvent("modified", path, mtime))

        for path in prev:
            if path not in current:
                self._emit(FileEvent("deleted", path, time.time()))

        self._snapshots = current

    def _emit(self, event: FileEvent) -> None:
        if self._callback:
            try:
                self._callback(event)
            except Exception as exc:
                logger.warning("FileWatcher: callback error: %s", exc)
