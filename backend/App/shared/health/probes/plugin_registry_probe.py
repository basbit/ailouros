from __future__ import annotations

import os
import time
from typing import Callable, Optional
from urllib.parse import urlparse, urlunparse

from backend.App.shared.health.probe import ProbeResult


def _redact(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    if parsed.username or parsed.password:
        host = parsed.hostname or ""
        netloc = host if not parsed.port else f"{host}:{parsed.port}"
        if parsed.username:
            netloc = f"***@{netloc}"
        parsed = parsed._replace(netloc=netloc)
    return urlunparse(parsed)


def _default_registry_urls() -> list[str]:
    raw = (os.getenv("SWARM_PLUGIN_REGISTRIES") or "").strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _default_installed_plugins() -> list[object]:
    from backend.App.plugins.infrastructure.plugin_store_fs import installed_plugins

    return list(installed_plugins())


class PluginRegistryProbe:
    subsystem: str = "plugin_registry"

    def __init__(
        self,
        installed_getter: Optional[Callable[[], list[object]]] = None,
        registry_url_getter: Optional[Callable[[], list[str]]] = None,
    ) -> None:
        self._installed_getter = installed_getter
        self._registry_url_getter = registry_url_getter

    def probe(self) -> ProbeResult:
        start = time.perf_counter()
        try:
            installed = (self._installed_getter or _default_installed_plugins)()
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000.0
            return ProbeResult(
                subsystem=self.subsystem,
                status="error",
                latency_ms=elapsed,
                detail=f"installed_plugins failed: {type(exc).__name__}: {exc}",
                metadata={},
            )

        try:
            registry_urls = (self._registry_url_getter or _default_registry_urls)()
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000.0
            return ProbeResult(
                subsystem=self.subsystem,
                status="error",
                latency_ms=elapsed,
                detail=f"registry list failed: {type(exc).__name__}: {exc}",
                metadata={"installed_count": str(len(installed))},
            )

        redacted = [_redact(u) for u in registry_urls]
        elapsed = (time.perf_counter() - start) * 1000.0
        metadata = {
            "installed_count": str(len(installed)),
            "registry_count": str(len(redacted)),
            "registries": ",".join(redacted),
        }
        return ProbeResult(
            subsystem=self.subsystem,
            status="ok",
            latency_ms=elapsed,
            detail=f"{len(installed)} plugin(s); {len(redacted)} registry url(s)",
            metadata=metadata,
        )


__all__ = ["PluginRegistryProbe"]
