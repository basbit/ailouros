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
from urllib.parse import urlparse
from typing import Any

logger = logging.getLogger(__name__)

# Module-level defaults for test patchability. Runtime functions re-read env.
_AGENT_ENABLED = os.getenv("SWARM_BACKGROUND_AGENT", "0") == "1"
_BACKGROUND_MODEL = os.getenv("SWARM_BACKGROUND_AGENT_MODEL", "")
_WATCH_PATHS_ENV = os.getenv("SWARM_BACKGROUND_AGENT_WATCH_PATHS", "")
_LEGACY_ANTHROPIC_BACKGROUND_MODEL = "claude-haiku-4-5"
_MODEL_IDS_CACHE_TTL_SEC = float(os.getenv("SWARM_BACKGROUND_AGENT_MODEL_CACHE_TTL_SEC", "60"))
_PROVIDER_MODEL_IDS_CACHE: dict[tuple[str, str, str], tuple[float, list[str]]] = {}
_LOCAL_MODEL_IDS_CACHE: dict[str, tuple[float, list[str]]] = {}
_PROVIDER_FALLBACK_MODELS: dict[str, str] = {
    "anthropic": _LEGACY_ANTHROPIC_BACKGROUND_MODEL,
    "gemini": "gemini-2.0-flash",
    "openai_compatible": "gpt-4o-mini",
    "deepseek": "deepseek-chat",
}
_PROVIDER_MODEL_PREFERENCES: dict[str, tuple[str, ...]] = {
    "anthropic": (
        "claude-haiku-4-5",
        "claude-3-5-haiku-latest",
        "claude-3-5-sonnet-latest",
    ),
    "gemini": (
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-flash",
        "gemini-1.5-pro",
    ),
    "openai_compatible": (
        "gpt-4o-mini",
        "gpt-4.1-mini",
        "gpt-4o",
        "gpt-4.1",
    ),
    "deepseek": (
        "deepseek-chat",
        "deepseek-reasoner",
    ),
}


def _agent_enabled() -> bool:
    """Read at call time so UI toggle wiring works."""
    return os.getenv("SWARM_BACKGROUND_AGENT", "0") == "1"


def _background_model() -> str:
    return os.getenv("SWARM_BACKGROUND_AGENT_MODEL", "").strip()


def _background_environment() -> str:
    return os.getenv("SWARM_BACKGROUND_AGENT_PROVIDER", "cloud").strip() or "cloud"


def _watch_paths_env() -> str:
    return os.getenv("SWARM_BACKGROUND_AGENT_WATCH_PATHS", "")


def _effective_cloud_provider(
    environment: str,
    remote_provider: str,
    model_for_infer: str,
) -> str:
    env_key = (environment or "").strip().lower()
    provider = (remote_provider or "").strip().lower()
    if env_key == "anthropic":
        return provider or "anthropic"
    if provider:
        return provider
    model = (model_for_infer or "").strip().lower()
    if model.startswith("claude") or model.startswith("anthropic/"):
        return "anthropic"
    if model.startswith("gemini") or model.startswith("learnlm"):
        return "gemini"
    if model.startswith("deepseek"):
        return "deepseek"
    if model.startswith(("gpt", "o1", "o3", "o4", "chatgpt", "openai/", "codex")):
        return "openai_compatible"
    return "anthropic"


def _pick_preferred_model(provider: str, model_ids: list[str]) -> str:
    if not model_ids:
        return ""
    by_lower = {mid.lower(): mid for mid in model_ids if mid}
    for preferred in _PROVIDER_MODEL_PREFERENCES.get(provider, ()):
        hit = by_lower.get(preferred.lower())
        if hit:
            return hit
    if provider == "gemini":
        for mid in model_ids:
            lowered = mid.lower()
            if lowered.startswith(("gemini-", "learnlm-")) and "flash" in lowered:
                return mid
    if provider == "anthropic":
        for mid in model_ids:
            lowered = mid.lower()
            if lowered.startswith("claude") and "haiku" in lowered:
                return mid
    return model_ids[0]


def _is_obviously_incompatible_model(provider: str, model: str) -> bool:
    lowered = (model or "").strip().lower()
    if not lowered:
        return False
    if provider == "anthropic":
        return not (lowered.startswith("claude") or lowered.startswith("anthropic/"))
    if provider == "gemini":
        return not (lowered.startswith("gemini") or lowered.startswith("learnlm"))
    if provider in {"openai_compatible", "groq", "cerebras", "deepseek"}:
        return lowered.startswith(("claude", "anthropic/", "gemini", "learnlm"))
    return False


def _is_gemini_first_party_base_url(base_url: str) -> bool:
    host = (urlparse((base_url or "").strip()).hostname or "").lower()
    if not host:
        return False
    return (
        host == "generativelanguage.googleapis.com"
        or host.endswith(".generativelanguage.googleapis.com")
    )


def _gemini_native_models_url(base_url: str) -> str:
    parsed = urlparse((base_url or "").strip())
    if parsed.scheme and parsed.netloc and _is_gemini_first_party_base_url(base_url):
        return f"{parsed.scheme}://{parsed.netloc}/v1beta/models"
    return "https://generativelanguage.googleapis.com/v1beta/models"


def _openai_model_ids(payload: dict[str, Any]) -> list[str]:
    return [
        str(item.get("id") or "").strip()
        for item in (payload.get("data") or [])
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ]


def _gemini_model_ids(payload: dict[str, Any]) -> list[str]:
    model_ids: list[str] = []
    for item in payload.get("models") or []:
        if not isinstance(item, dict):
            continue
        supported = item.get("supportedGenerationMethods") or []
        if not isinstance(supported, list) or "generateContent" not in supported:
            continue
        mid = str(item.get("baseModelId") or "").strip()
        if not mid:
            name = str(item.get("name") or "").strip()
            if name.startswith("models/"):
                mid = name.split("/", 1)[1].strip()
            else:
                mid = name
        if mid:
            model_ids.append(mid)
    return model_ids


def _fetch_local_model_ids(environment: str) -> list[str]:
    import httpx

    from backend.App.integrations.infrastructure.llm.config import (
        LMSTUDIO_BASE_URL,
        OLLAMA_BASE_URL,
    )

    env_key = (environment or "").strip().lower()
    cached = _LOCAL_MODEL_IDS_CACHE.get(env_key)
    now = time.monotonic()
    if cached and (now - cached[0]) < _MODEL_IDS_CACHE_TTL_SEC:
        return list(cached[1])

    try:
        if env_key in {"lmstudio", "lm_studio"}:
            base_url = os.getenv("LMSTUDIO_BASE_URL", LMSTUDIO_BASE_URL).rstrip("/")
            api_key = os.getenv("LMSTUDIO_API_KEY", "lm-studio")
        else:
            base_url = os.getenv("OPENAI_BASE_URL", OLLAMA_BASE_URL).rstrip("/")
            api_key = os.getenv("OPENAI_API_KEY", "ollama")
        with httpx.Client(timeout=8.0) as client:
            response = client.get(
                f"{base_url}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            response.raise_for_status()
            payload = response.json()
    except Exception:
        return []
    model_ids = _openai_model_ids(payload)
    _LOCAL_MODEL_IDS_CACHE[env_key] = (now, model_ids)
    return model_ids


def _default_local_background_model(environment: str) -> str:
    env_key = (environment or "").strip().lower()
    candidates: list[str]
    if env_key in {"lmstudio", "lm_studio"}:
        candidates = [
            os.getenv("SWARM_LMSTUDIO_MODEL_BUILD", "").strip(),
            os.getenv("SWARM_LMSTUDIO_MODEL_PLANNING", "").strip(),
            os.getenv("SWARM_MODEL_BUILD", "").strip(),
            os.getenv("SWARM_MODEL", "").strip(),
            os.getenv("SWARM_MODEL_PLANNING", "").strip(),
        ]
    else:
        candidates = [
            os.getenv("SWARM_MODEL_BUILD", "").strip(),
            os.getenv("SWARM_MODEL", "").strip(),
            os.getenv("SWARM_MODEL_PLANNING", "").strip(),
        ]
    for candidate in candidates:
        if candidate:
            return candidate
    local_models = _fetch_local_model_ids(environment)
    return local_models[0] if local_models else ""


def _fetch_provider_model_ids(
    provider: str,
    *,
    api_key: str,
    base_url: str,
) -> list[str]:
    import httpx

    from backend.App.integrations.infrastructure.llm.remote_presets import (
        resolve_openai_compat_base_url,
        uses_anthropic_sdk,
    )

    provider_key = (provider or "").strip().lower()
    cache_key = (
        provider_key,
        (base_url or "").strip(),
        (api_key or "").strip(),
    )
    cached = _PROVIDER_MODEL_IDS_CACHE.get(cache_key)
    now = time.monotonic()
    if cached and (now - cached[0]) < _MODEL_IDS_CACHE_TTL_SEC:
        return list(cached[1])

    if uses_anthropic_sdk(provider_key):
        model_ids = [
            "claude-opus-4-6",
            "claude-sonnet-4-6",
            "claude-haiku-4-5",
            "claude-3-5-sonnet-latest",
            "claude-3-5-haiku-latest",
        ]
        _PROVIDER_MODEL_IDS_CACHE[cache_key] = (now, model_ids)
        return model_ids

    resolved_base_url = resolve_openai_compat_base_url(
        provider_key,
        (base_url or "").strip() or None,
    )
    if not resolved_base_url:
        return []
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        with httpx.Client(timeout=12.0) as client:
            if provider_key == "gemini" and _is_gemini_first_party_base_url(resolved_base_url):
                params: dict[str, Any] = {"pageSize": 1000}
                if api_key:
                    params["key"] = api_key
                response = client.get(
                    _gemini_native_models_url(resolved_base_url),
                    params=params,
                )
                response.raise_for_status()
                model_ids = _gemini_model_ids(response.json())
            else:
                response = client.get(
                    f"{resolved_base_url.rstrip('/')}/models",
                    headers=headers,
                )
                response.raise_for_status()
                model_ids = _openai_model_ids(response.json())
    except Exception:
        return []
    _PROVIDER_MODEL_IDS_CACHE[cache_key] = (now, model_ids)
    return model_ids


def _resolve_background_model(
    *,
    environment: str,
    model: str,
    remote_provider: str,
    remote_api_key: str,
    remote_base_url: str,
) -> str:
    requested = (model or "").strip()
    env_key = (environment or "").strip().lower()
    if env_key not in {"cloud", "anthropic"}:
        return requested or _default_local_background_model(environment)

    provider = _effective_cloud_provider(environment, remote_provider, requested)
    available_model_ids = _fetch_provider_model_ids(
        provider,
        api_key=(remote_api_key or "").strip(),
        base_url=(remote_base_url or "").strip(),
    )
    if available_model_ids:
        requested_lower = requested.lower()
        for available in available_model_ids:
            if available.lower() == requested_lower and requested:
                return available
        replacement = _pick_preferred_model(provider, available_model_ids)
        if replacement:
            if requested and replacement != requested:
                logger.info(
                    "BackgroundAgent: provider=%s does not advertise model=%s; using model=%s",
                    provider,
                    requested,
                    replacement,
                )
            return replacement

    if requested and not _is_obviously_incompatible_model(provider, requested):
        return requested

    fallback = _PROVIDER_FALLBACK_MODELS.get(provider, "")
    if fallback:
        if requested and requested != fallback:
            logger.info(
                "BackgroundAgent: provider=%s is incompatible with model=%s; falling back to model=%s",
                provider,
                requested,
                fallback,
            )
        return fallback
    return requested


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
    """Call lightweight LLM and return parsed recommendation fields.

    Returns an explicit error recommendation on failure — never raises.
    INV-1: result is logged and failures are surfaced to the UI.

    Args:
        remote_provider: Optional provider id from the UI/profile (anthropic,
            openrouter, gemini, etc.). When present we route explicitly instead
            of guessing from the model name alone.
        remote_api_key: Optional API key forwarded from the UI/profile.
        remote_base_url: Optional base URL for OpenAI-compat endpoints.
    """
    import json as _json

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
            max_tokens=256,
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
            "message": (
                raw[:200]
                if raw
                else f"Background agent returned unreadable output for {event_type}: {path}"
            ),
            "severity": "warning",
            "suggested_action": "Review the change manually and inspect background-agent logs if this keeps happening.",
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
        self._watcher: Any = None  # FileWatcher instance

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background agent; non-blocking."""
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

    @property
    def watch_paths(self) -> list[str]:
        return list(self._watch_paths)

    @property
    def active(self) -> bool:
        return self._running

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
    """Resolve watch paths from env or fall back to module-level default."""
    raw = os.getenv("SWARM_BACKGROUND_AGENT_WATCH_PATHS", "").strip()
    if not raw:
        raw = _WATCH_PATHS_ENV.strip()  # fallback for test patchability
    if raw:
        return [p.strip() for p in raw.split(",") if p.strip()]
    return []
