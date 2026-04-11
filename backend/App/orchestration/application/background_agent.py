"""Background Agent (K-10): passive file-event watcher with LLM recommendations.

INV-4: NEVER auto-applies changes — recommendations only.
INV-1: all routing decisions are logged.
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Module-level defaults for test patchability. Runtime functions re-read env.
_AGENT_ENABLED = os.getenv("SWARM_BACKGROUND_AGENT", "0") == "1"
_BACKGROUND_MODEL = os.getenv("SWARM_BACKGROUND_AGENT_MODEL", "claude-haiku-4-5")
_WATCH_PATHS_ENV = os.getenv("SWARM_BACKGROUND_AGENT_WATCH_PATHS", "")


def _agent_enabled() -> bool:
    """Read at call time so UI toggle wiring works."""
    return os.getenv("SWARM_BACKGROUND_AGENT", "0") == "1"


def _background_model() -> str:
    return os.getenv("SWARM_BACKGROUND_AGENT_MODEL", "claude-haiku-4-5")


def _watch_paths_env() -> str:
    return os.getenv("SWARM_BACKGROUND_AGENT_WATCH_PATHS", "")


@dataclass
class Recommendation:
    """A passive, read-only recommendation produced by the background agent.

    INV-4: this is informational only — no side effects are ever applied.
    """

    event_type: str
    path: str
    message: str
    severity: str  # "info" | "warning" | "error"
    suggested_action: str
    timestamp: float = field(default_factory=time.time)


def _build_prompt(event_type: str, path: str) -> str:
    return (
        f"A file system event occurred in the project.\n"
        f"Event type: {event_type}\n"
        f"File path: {path}\n\n"
        "Briefly describe what this change might imply for the codebase and "
        "suggest one concrete, non-destructive action the developer could take. "
        "Reply in JSON with keys: message (str), severity (info|warning|error), "
        "suggested_action (str). Do not modify any files."
    )


def _call_llm(event_type: str, path: str) -> dict[str, str]:
    """Call lightweight LLM and return parsed recommendation fields.

    Falls back to a static response on any error — never raises.
    INV-1: result is logged.
    """
    import json as _json

    prompt = _build_prompt(event_type, path)
    raw = ""
    try:
        from backend.App.integrations.infrastructure.llm.client import (
            AnthropicClient,
        )

        client = AnthropicClient(model=_background_model())
        raw = client.complete(prompt, max_tokens=256)
    except Exception as exc:
        logger.warning("BackgroundAgent: LLM call failed: %s", exc)
        return {
            "message": f"File {event_type}: {path}",
            "severity": "info",
            "suggested_action": "Review the change manually.",
        }

    try:
        # Strip markdown fences if present
        clean = raw.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        parsed: dict[str, str] = _json.loads(clean)
        logger.info(
            "BackgroundAgent: LLM recommendation event=%s path=%s severity=%s",
            event_type,
            path,
            parsed.get("severity"),
        )  # INV-1
        return parsed
    except Exception as exc:
        logger.warning("BackgroundAgent: failed to parse LLM response: %s", exc)
        return {
            "message": raw[:200] if raw else f"File {event_type}: {path}",
            "severity": "info",
            "suggested_action": "Review the change manually.",
        }


class BackgroundAgent:
    """Passive background agent that watches files and surfaces recommendations.

    Usage::

        agent = BackgroundAgent(watch_paths=["/path/to/project"])
        agent.start()
        ...
        recs = agent.drain_recommendations()
        agent.stop()

    INV-4: this class never modifies files or applies changes.
    """

    def __init__(
        self,
        watch_paths: list[str] | None = None,
    ) -> None:
        self._watch_paths: list[str] = watch_paths or _default_watch_paths()
        self._queue: queue.Queue[Recommendation] = queue.Queue()
        self._worker_thread: threading.Thread | None = None
        self._event_queue: queue.Queue[Any] = queue.Queue()
        self._running = False
        self._watcher: Any = None  # FileWatcher instance

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background agent; non-blocking."""
        if not _agent_enabled():
            logger.info("BackgroundAgent: disabled via SWARM_BACKGROUND_AGENT=0")
            return
        if self._running:
            return

        from backend.App.orchestration.infrastructure.file_watcher import FileWatcher

        self._running = True
        self._watcher = FileWatcher()
        self._watcher.watch(self._watch_paths, self._on_file_event)

        self._worker_thread = threading.Thread(
            target=self._process_events, daemon=True, name="background-agent-worker"
        )
        self._worker_thread.start()
        logger.info(
            "BackgroundAgent: started; model=%s watch_paths=%s",
            _background_model(),
            self._watch_paths,
        )  # INV-1

    def stop(self) -> None:
        """Stop the background agent gracefully."""
        self._running = False
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher = None
        # Unblock worker
        self._event_queue.put(None)
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=15)
            self._worker_thread = None
        logger.info("BackgroundAgent: stopped")  # INV-1

    # ------------------------------------------------------------------
    # Recommendations API
    # ------------------------------------------------------------------

    def drain_recommendations(self) -> list[Recommendation]:
        """Return and clear all pending recommendations (non-blocking)."""
        results: list[Recommendation] = []
        while True:
            try:
                results.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return results

    def pending_count(self) -> int:
        """Return the number of queued recommendations without consuming them."""
        return self._queue.qsize()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_file_event(self, event: Any) -> None:
        """Enqueue a raw file event for async LLM processing."""
        self._event_queue.put(event)

    def _process_events(self) -> None:
        """Worker thread: consume file events and produce recommendations."""
        while self._running:
            try:
                event = self._event_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if event is None:
                break
            try:
                llm_result = _call_llm(event.event_type, event.path)
                rec = Recommendation(
                    event_type=event.event_type,
                    path=event.path,
                    message=llm_result.get("message", ""),
                    severity=llm_result.get("severity", "info"),
                    suggested_action=llm_result.get("suggested_action", ""),
                    timestamp=event.timestamp,
                )
                self._queue.put(rec)
            except Exception as exc:
                logger.warning("BackgroundAgent: event processing error: %s", exc)


def _default_watch_paths() -> list[str]:
    """Resolve watch paths from env or fall back to module-level default."""
    raw = os.getenv("SWARM_BACKGROUND_AGENT_WATCH_PATHS", "").strip()
    if not raw:
        raw = _WATCH_PATHS_ENV.strip()  # fallback for test patchability
    if raw:
        return [p.strip() for p in raw.split(",") if p.strip()]
    return []
