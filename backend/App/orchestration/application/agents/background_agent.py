from __future__ import annotations

import logging
import os
import re
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.App.orchestration.application.agents._background_model_resolver import (
    fetch_provider_model_ids as _fetch_provider_model_ids,
    resolve_background_model as _resolve_background_model_core,
)

logger = logging.getLogger(__name__)

_AGENT_ENABLED = os.getenv("SWARM_BACKGROUND_AGENT", "0") == "1"
_BACKGROUND_MODEL = os.getenv("SWARM_BACKGROUND_AGENT_MODEL", "")
_WATCH_PATHS_ENV = os.getenv("SWARM_BACKGROUND_AGENT_WATCH_PATHS", "")


def _agent_enabled() -> bool:
    return os.getenv("SWARM_BACKGROUND_AGENT", "0") == "1"


def _background_model() -> str:
    return os.getenv("SWARM_BACKGROUND_AGENT_MODEL", "").strip()


def _background_environment() -> str:
    return os.getenv("SWARM_BACKGROUND_AGENT_PROVIDER", "cloud").strip() or "cloud"


def _watch_paths_env() -> str:
    return os.getenv("SWARM_BACKGROUND_AGENT_WATCH_PATHS", "")


@dataclass
class Recommendation:
    event_type: str
    path: str
    message: str
    severity: str
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


_THINK_TAG_RE = re.compile(
    r"<(?:think|reasoning|thinking)>.*?</(?:think|reasoning|thinking)>",
    re.DOTALL | re.IGNORECASE,
)


def _strip_reasoning_tags(raw: str) -> str:
    if not raw:
        return raw
    return _THINK_TAG_RE.sub("", raw).strip()


def _extract_json_payload(raw: str) -> dict[str, str]:
    import json as _json

    clean = _strip_reasoning_tags(raw or "").strip()
    if clean.startswith("```"):
        fenced_parts = clean.split("```")
        if len(fenced_parts) >= 2:
            clean = fenced_parts[1]
            if clean.startswith("json"):
                clean = clean[4:]
            clean = clean.strip()

    try:
        return _json.loads(clean)
    except Exception:
        start = clean.find("{")
        end = clean.rfind("}")
        if start != -1 and end != -1 and end > start:
            return _json.loads(clean[start:end + 1])
        raise


def _should_ignore_event_path(path: str) -> bool:
    parts = {part.strip() for part in Path(path).parts}
    return ".swarm" in parts


def _background_max_tokens() -> int:
    env_value = os.getenv("SWARM_BACKGROUND_AGENT_MAX_TOKENS", "").strip()
    if env_value.isdigit():
        parsed_int = int(env_value)
        if parsed_int >= 64:
            return parsed_int
    return 512


def _resolve_background_model(
    *,
    environment: str,
    model: str,
    remote_provider: str,
    remote_api_key: str,
    remote_base_url: str,
) -> str:
    return _resolve_background_model_core(
        environment=environment,
        model=model,
        remote_provider=remote_provider,
        remote_api_key=remote_api_key,
        remote_base_url=remote_base_url,
        provider_model_fetcher=_fetch_provider_model_ids,
    )


def _call_llm(
    event_type: str,
    path: str,
    *,
    environment: str = "",
    model: str = "",
    remote_provider: str = "",
    remote_api_key: str = "",
    remote_base_url: str = "",
) -> dict[str, str]:
    prompt = _build_prompt(event_type, path)
    raw = ""
    try:
        from backend.App.integrations.infrastructure.llm.client import chat_completion_text
        from backend.App.orchestration.infrastructure.agents.llm_backend_selector import (
            LLMBackendSelector,
        )

        resolved_model = _resolve_background_model(
            environment=(environment or "").strip() or _background_environment(),
            model=(model or "").strip() or _background_model(),
            remote_provider=(remote_provider or "").strip(),
            remote_api_key=(remote_api_key or "").strip(),
            remote_base_url=(remote_base_url or "").strip(),
        )
        resolved_environment = (environment or "").strip() or _background_environment()
        selector = LLMBackendSelector()
        cfg = selector.select(
            role="BACKGROUND_AGENT",
            model=resolved_model,
            environment=resolved_environment,
            remote_provider=(remote_provider or "").strip() or None,
            remote_api_key=(remote_api_key or "").strip() or None,
            remote_base_url=(remote_base_url or "").strip() or None,
            max_tokens=_background_max_tokens(),
        )
        cred_kwargs = selector.ask_kwargs(cfg)

        raw = chat_completion_text(
            model=resolved_model,
            messages=[{"role": "user", "content": prompt}],
            **cred_kwargs,
        )
    except Exception as exc:
        logger.warning("BackgroundAgent: LLM call failed: %s", exc)
        return {
            "message": f"Background agent LLM call failed for {path}: {exc}",
            "severity": "error",
            "suggested_action": "Check the configured background model/backend and review the change manually.",
        }

    try:
        parsed: dict[str, str] = _extract_json_payload(raw)
        logger.info(
            "BackgroundAgent: LLM recommendation event=%s path=%s severity=%s",
            event_type,
            path,
            parsed.get("severity"),
        )
        return parsed
    except Exception as exc:
        logger.error(
            "BackgroundAgent: model %r returned non-JSON output for event=%s path=%s. "
            "Increase SWARM_BACKGROUND_AGENT_MAX_TOKENS, pick a model with stronger "
            "instruction-following, or disable the agent (background_agent=false). "
            "parse_error=%s raw_preview=%r",
            resolved_model, event_type, path, exc, (raw or "")[:300],
        )
        raise RuntimeError(
            f"BackgroundAgent: non-JSON output from model {resolved_model!r} "
            f"for {event_type} {path} — parse_error={exc}"
        ) from exc


class BackgroundAgent:

    def __init__(
        self,
        watch_paths: list[str] | None = None,
        *,
        enabled: bool | None = None,
        environment: str = "",
        model: str = "",
        remote_provider: str = "",
        remote_api_key: str = "",
        remote_base_url: str = "",
    ) -> None:
        self._watch_paths: list[str] = watch_paths or _default_watch_paths()
        self._enabled = _agent_enabled() if enabled is None else enabled
        self._environment = environment.strip() or _background_environment()
        self._remote_provider = remote_provider.strip()
        self._remote_api_key = remote_api_key.strip()
        self._remote_base_url = remote_base_url.strip()
        self._model = _resolve_background_model(
            environment=self._environment,
            model=model.strip() or _background_model(),
            remote_provider=self._remote_provider,
            remote_api_key=self._remote_api_key,
            remote_base_url=self._remote_base_url,
        )
        self._queue: queue.Queue[Recommendation] = queue.Queue()
        self._worker_thread: threading.Thread | None = None
        self._event_queue: queue.Queue[Any] = queue.Queue()
        self._running = False
        self._watcher: Any = None

    def start(self) -> None:
        if not self._enabled:
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
            "BackgroundAgent: started; model=%s environment=%s watch_paths=%s",
            self._model,
            self._environment,
            self._watch_paths,
        )

    def stop(self) -> None:
        self._running = False
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher = None
        self._event_queue.put(None)
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=15)
            self._worker_thread = None
        logger.info("BackgroundAgent: stopped")

    def drain_recommendations(self) -> list[Recommendation]:
        results: list[Recommendation] = []
        while True:
            try:
                results.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return results

    def pending_count(self) -> int:
        return self._queue.qsize()

    @property
    def watch_paths(self) -> list[str]:
        return list(self._watch_paths)

    @property
    def active(self) -> bool:
        return self._running

    def _on_file_event(self, event: Any) -> None:
        if _should_ignore_event_path(getattr(event, "path", "")):
            logger.debug(
                "BackgroundAgent: ignoring internal file event path=%s",
                getattr(event, "path", ""),
            )
            return
        self._event_queue.put(event)

    def _process_events(self) -> None:
        while self._running:
            try:
                event = self._event_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if event is None:
                break
            try:
                llm_result = _call_llm(
                    event.event_type,
                    event.path,
                    environment=getattr(self, "_environment", _background_environment()),
                    model=getattr(self, "_model", _background_model()),
                    remote_provider=getattr(self, "_remote_provider", ""),
                    remote_api_key=self._remote_api_key,
                    remote_base_url=self._remote_base_url,
                )
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
    raw = os.getenv("SWARM_BACKGROUND_AGENT_WATCH_PATHS", "").strip()
    if not raw:
        raw = _WATCH_PATHS_ENV.strip()
    if raw:
        return [p.strip() for p in raw.split(",") if p.strip()]
    return []
