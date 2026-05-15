from __future__ import annotations

import os
import time
from typing import Any, Callable, Optional
from urllib.parse import urlparse, urlunparse

from backend.App.shared.health.probe import ProbeResult


def redact_url(raw: str) -> str:
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
    except ValueError:
        return raw
    if parsed.password or parsed.username:
        host = parsed.hostname or ""
        netloc = host
        if parsed.port:
            netloc = f"{host}:{parsed.port}"
        if parsed.username:
            netloc = f"***@{netloc}"
        parsed = parsed._replace(netloc=netloc)
    return urlunparse(parsed)


class RedisProbe:
    subsystem: str = "redis"

    def __init__(
        self,
        client_factory: Optional[Callable[[str, float], Any]] = None,
        url_getter: Optional[Callable[[], str]] = None,
        timeout_sec: float = 1.0,
    ) -> None:
        self._client_factory = client_factory
        self._url_getter = url_getter
        self._timeout_sec = timeout_sec

    def _default_url(self) -> str:
        return (
            os.getenv("SWARM_REDIS_URL")
            or os.getenv("REDIS_URL")
            or "redis://localhost:6379/0"
        ).strip()

    def _default_client(self, url: str, timeout_sec: float) -> Any:
        import redis  # type: ignore[import-not-found]

        return redis.Redis.from_url(
            url,
            socket_timeout=timeout_sec,
            socket_connect_timeout=timeout_sec,
            decode_responses=True,
        )

    def probe(self) -> ProbeResult:
        start = time.perf_counter()
        url = (self._url_getter or self._default_url)()
        redacted = redact_url(url)
        metadata: dict[str, str] = {"url": redacted}

        try:
            client = (self._client_factory or self._default_client)(url, self._timeout_sec)
        except ImportError as exc:
            elapsed = (time.perf_counter() - start) * 1000.0
            return ProbeResult(
                subsystem=self.subsystem,
                status="error",
                latency_ms=elapsed,
                detail=f"redis package missing: {exc}",
                metadata=metadata,
            )
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000.0
            return ProbeResult(
                subsystem=self.subsystem,
                status="error",
                latency_ms=elapsed,
                detail=f"client init failed: {type(exc).__name__}: {exc}",
                metadata=metadata,
            )

        try:
            pong = client.ping()
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000.0
            return ProbeResult(
                subsystem=self.subsystem,
                status="error",
                latency_ms=elapsed,
                detail=f"PING failed: {type(exc).__name__}: {exc}",
                metadata=metadata,
            )

        aof_enabled = True
        try:
            info = client.config_get("appendonly") if hasattr(client, "config_get") else {}
            aof_value = (info or {}).get("appendonly", "yes") if isinstance(info, dict) else "yes"
            aof_enabled = str(aof_value).lower() in ("yes", "1", "true", "on")
        except Exception:
            aof_enabled = True
        metadata["aof"] = "on" if aof_enabled else "off"
        metadata["ping"] = str(pong)

        elapsed = (time.perf_counter() - start) * 1000.0
        if not aof_enabled:
            return ProbeResult(
                subsystem=self.subsystem,
                status="degraded",
                latency_ms=elapsed,
                detail="PING ok but AOF persistence is disabled",
                metadata=metadata,
            )
        return ProbeResult(
            subsystem=self.subsystem,
            status="ok",
            latency_ms=elapsed,
            detail=f"PING ok ({redacted})",
            metadata=metadata,
        )


__all__ = ["RedisProbe", "redact_url"]
