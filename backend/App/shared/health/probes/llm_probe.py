from __future__ import annotations

import os
import time
from typing import Callable, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

from backend.App.shared.health.probe import ProbeResult


def _env(key: str) -> str:
    return (os.getenv(key) or "").strip()


def _http_ping(url: str, timeout_sec: float) -> tuple[bool, str]:
    try:
        req = Request(url, method="GET")
        with urlopen(req, timeout=timeout_sec) as response:
            code = getattr(response, "status", 200) or 200
            if 200 <= int(code) < 500:
                return True, f"HTTP {code}"
            return False, f"HTTP {code}"
    except URLError as exc:
        return False, f"URLError: {exc.reason}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _detect_providers() -> list[dict[str, str]]:
    providers: list[dict[str, str]] = []
    ollama = _env("OLLAMA_BASE_URL") or "http://localhost:11434/v1"
    providers.append({"name": "ollama", "endpoint": ollama.rstrip("/") + "/models"})
    openai_base = _env("OPENAI_BASE_URL")
    openai_key = _env("OPENAI_API_KEY")
    if openai_base and openai_base != ollama:
        providers.append(
            {"name": "openai", "endpoint": openai_base.rstrip("/") + "/models"}
        )
    elif openai_key:
        providers.append({"name": "openai", "endpoint": "https://api.openai.com/v1/models"})
    if _env("ANTHROPIC_API_KEY"):
        providers.append({"name": "anthropic", "endpoint": "https://api.anthropic.com/v1/models"})
    return providers


class LlmProbe:
    subsystem: str = "llm"

    def __init__(
        self,
        detector: Optional[Callable[[], list[dict[str, str]]]] = None,
        pinger: Optional[Callable[[str, float], tuple[bool, str]]] = None,
        timeout_sec: float = 2.0,
    ) -> None:
        self._detector = detector
        self._pinger = pinger
        self._timeout_sec = timeout_sec

    def probe(self) -> ProbeResult:
        start = time.perf_counter()
        try:
            providers = (self._detector or _detect_providers)()
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000.0
            return ProbeResult(
                subsystem=self.subsystem,
                status="error",
                latency_ms=elapsed,
                detail=f"detection failed: {type(exc).__name__}: {exc}",
                metadata={},
            )

        if not providers:
            elapsed = (time.perf_counter() - start) * 1000.0
            return ProbeResult(
                subsystem=self.subsystem,
                status="disabled",
                latency_ms=elapsed,
                detail="no LLM providers configured",
                metadata={},
            )

        pinger = self._pinger or _http_ping
        per_status: dict[str, str] = {}
        details: list[str] = []
        ok_count = 0
        err_count = 0
        for entry in providers:
            ok, info = pinger(entry["endpoint"], self._timeout_sec)
            per_status[entry["name"]] = "ok" if ok else "error"
            details.append(f"{entry['name']}={info}")
            if ok:
                ok_count += 1
            else:
                err_count += 1

        elapsed = (time.perf_counter() - start) * 1000.0
        metadata = {f"provider_{k}": v for k, v in per_status.items()}
        metadata["providers"] = ",".join(p["name"] for p in providers)

        if ok_count == 0:
            return ProbeResult(
                subsystem=self.subsystem,
                status="error",
                latency_ms=elapsed,
                detail="; ".join(details),
                metadata=metadata,
            )
        if err_count > 0:
            return ProbeResult(
                subsystem=self.subsystem,
                status="degraded",
                latency_ms=elapsed,
                detail="; ".join(details),
                metadata=metadata,
            )
        return ProbeResult(
            subsystem=self.subsystem,
            status="ok",
            latency_ms=elapsed,
            detail="; ".join(details),
            metadata=metadata,
        )


__all__ = ["LlmProbe"]
