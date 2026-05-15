from __future__ import annotations

import time

from backend.App.shared.health.health_service import (
    aggregate_status,
    probe_timeout_sec,
    run_all_probes,
)
from backend.App.shared.health.probe import ProbeResult


class _StaticProbe:
    def __init__(self, name: str, status: str, detail: str = "") -> None:
        self.subsystem = name
        self._status = status
        self._detail = detail

    def probe(self) -> ProbeResult:
        return ProbeResult(
            subsystem=self.subsystem,
            status=self._status,  # type: ignore[arg-type]
            latency_ms=0.5,
            detail=self._detail or self._status,
        )


class _SlowProbe:
    subsystem = "slow"

    def __init__(self, seconds: float) -> None:
        self._seconds = seconds

    def probe(self) -> ProbeResult:
        time.sleep(self._seconds)
        return ProbeResult(
            subsystem=self.subsystem, status="ok", latency_ms=0.0, detail="late"
        )


class _BoomProbe:
    subsystem = "boom"

    def probe(self) -> ProbeResult:
        raise RuntimeError("kaboom")


def test_run_all_probes_executes_in_parallel() -> None:
    probes = (_SlowProbe(0.2), _SlowProbe(0.2))
    start = time.perf_counter()
    results = run_all_probes(probes, timeout_sec=2.0)
    elapsed = time.perf_counter() - start
    assert len(results) == 2
    assert all(r.status == "ok" for r in results)
    assert elapsed < 0.6


def test_run_all_probes_marks_timeout_as_error() -> None:
    probes = (_SlowProbe(1.5),)
    results = run_all_probes(probes, timeout_sec=0.3)
    assert len(results) == 1
    assert results[0].status == "error"
    assert "timed out" in results[0].detail


def test_run_all_probes_catches_exceptions() -> None:
    results = run_all_probes((_BoomProbe(),), timeout_sec=1.0)
    assert len(results) == 1
    assert results[0].status == "error"
    assert "RuntimeError" in results[0].detail


def test_run_all_probes_empty_returns_empty() -> None:
    assert run_all_probes(()) == ()


def test_aggregate_status_ok_when_all_ok() -> None:
    probes = (_StaticProbe("a", "ok"), _StaticProbe("b", "ok"))
    results = run_all_probes(probes)
    assert aggregate_status(results) == "ok"


def test_aggregate_status_degraded_when_any_degraded() -> None:
    probes = (_StaticProbe("a", "ok"), _StaticProbe("b", "degraded"))
    results = run_all_probes(probes)
    assert aggregate_status(results) == "degraded"


def test_aggregate_status_error_dominates() -> None:
    probes = (
        _StaticProbe("a", "ok"),
        _StaticProbe("b", "degraded"),
        _StaticProbe("c", "error"),
    )
    results = run_all_probes(probes)
    assert aggregate_status(results) == "error"


def test_aggregate_status_disabled_ignored() -> None:
    probes = (_StaticProbe("a", "disabled"), _StaticProbe("b", "ok"))
    results = run_all_probes(probes)
    assert aggregate_status(results) == "ok"


def test_aggregate_status_all_disabled_returns_ok() -> None:
    probes = (_StaticProbe("a", "disabled"),)
    results = run_all_probes(probes)
    assert aggregate_status(results) == "ok"


def test_probe_timeout_sec_default(monkeypatch) -> None:
    monkeypatch.delenv("SWARM_HEALTH_PROBE_TIMEOUT_SEC", raising=False)
    assert probe_timeout_sec() == 5.0


def test_probe_timeout_sec_override(monkeypatch) -> None:
    monkeypatch.setenv("SWARM_HEALTH_PROBE_TIMEOUT_SEC", "2.5")
    assert probe_timeout_sec() == 2.5


def test_probe_timeout_sec_invalid_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("SWARM_HEALTH_PROBE_TIMEOUT_SEC", "garbage")
    assert probe_timeout_sec() == 5.0


def test_probe_timeout_sec_negative_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("SWARM_HEALTH_PROBE_TIMEOUT_SEC", "-3")
    assert probe_timeout_sec() == 5.0
