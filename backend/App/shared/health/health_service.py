from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Iterable

from backend.App.shared.health.probe import HealthProbe, ProbeResult, ProbeStatus

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SEC = 5.0
_TIMEOUT_ENV = "SWARM_HEALTH_PROBE_TIMEOUT_SEC"


def probe_timeout_sec() -> float:
    raw = (os.getenv(_TIMEOUT_ENV) or "").strip()
    if not raw:
        return _DEFAULT_TIMEOUT_SEC
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_TIMEOUT_SEC
    return value if value > 0 else _DEFAULT_TIMEOUT_SEC


def _run_one(probe: HealthProbe, timeout_sec: float) -> ProbeResult:
    started = time.perf_counter()
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(probe.probe)
    try:
        result = future.result(timeout=timeout_sec)
    except FuturesTimeout:
        future.cancel()
        elapsed = (time.perf_counter() - started) * 1000.0
        executor.shutdown(wait=False)
        return ProbeResult(
            subsystem=probe.subsystem,
            status="error",
            latency_ms=elapsed,
            detail=f"probe timed out after {timeout_sec:.1f}s",
            metadata={"timeout_sec": f"{timeout_sec:.1f}"},
        )
    except Exception as exc:
        elapsed = (time.perf_counter() - started) * 1000.0
        executor.shutdown(wait=False)
        return ProbeResult(
            subsystem=probe.subsystem,
            status="error",
            latency_ms=elapsed,
            detail=f"{type(exc).__name__}: {exc}",
            metadata={},
        )
    executor.shutdown(wait=False)
    return result


def run_all_probes(
    probes: tuple[HealthProbe, ...],
    timeout_sec: float | None = None,
) -> tuple[ProbeResult, ...]:
    if not probes:
        return ()
    cap = timeout_sec if timeout_sec is not None else probe_timeout_sec()
    results: list[ProbeResult | None] = [None] * len(probes)
    executor = ThreadPoolExecutor(max_workers=max(1, len(probes)))
    futures = {
        executor.submit(_run_one, probe, cap): index
        for index, probe in enumerate(probes)
    }
    for future in list(futures.keys()):
        index = futures[future]
        try:
            results[index] = future.result(timeout=cap + 2.0)
        except Exception as exc:
            results[index] = ProbeResult(
                subsystem=probes[index].subsystem,
                status="error",
                latency_ms=0.0,
                detail=f"executor failure: {type(exc).__name__}: {exc}",
                metadata={},
            )
    executor.shutdown(wait=False)
    return tuple(r for r in results if r is not None)


def aggregate_status(results: Iterable[ProbeResult]) -> ProbeStatus:
    seen = [r.status for r in results if r.status != "disabled"]
    if not seen:
        return "ok"
    if any(s == "error" for s in seen):
        return "error"
    if any(s == "degraded" for s in seen):
        return "degraded"
    return "ok"


__all__ = [
    "aggregate_status",
    "probe_timeout_sec",
    "run_all_probes",
]
