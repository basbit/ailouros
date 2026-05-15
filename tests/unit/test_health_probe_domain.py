from __future__ import annotations

import pytest

from backend.App.shared.health.probe import (
    PROBE_STATUS_VALUES,
    HealthProbe,
    ProbeResult,
)


def test_probe_status_values_complete() -> None:
    assert set(PROBE_STATUS_VALUES) == {"ok", "degraded", "error", "disabled"}


def test_probe_result_is_frozen() -> None:
    result = ProbeResult(
        subsystem="x",
        status="ok",
        latency_ms=1.0,
        detail="fine",
    )
    with pytest.raises(Exception):
        result.subsystem = "y"  # type: ignore[misc]


def test_probe_result_to_payload_round_trips_metadata() -> None:
    result = ProbeResult(
        subsystem="redis",
        status="degraded",
        latency_ms=12.345,
        detail="AOF disabled",
        metadata={"url": "redis://h:6379"},
    )
    payload = result.to_payload()
    assert payload["subsystem"] == "redis"
    assert payload["status"] == "degraded"
    assert payload["latency_ms"] == 12.345
    assert payload["detail"] == "AOF disabled"
    assert payload["metadata"] == {"url": "redis://h:6379"}


def test_probe_result_metadata_default_empty() -> None:
    result = ProbeResult(subsystem="x", status="ok", latency_ms=0.1, detail="ok")
    assert result.metadata == {}


def test_probe_result_latency_rounded_in_payload() -> None:
    result = ProbeResult(
        subsystem="x", status="ok", latency_ms=1.234567, detail="ok"
    )
    payload = result.to_payload()
    assert payload["latency_ms"] == pytest.approx(1.235, abs=1e-6)


def test_health_probe_protocol_runtime_checkable() -> None:
    class FakeProbe:
        subsystem = "fake"

        def probe(self) -> ProbeResult:
            return ProbeResult(
                subsystem=self.subsystem, status="ok", latency_ms=0.0, detail="hi"
            )

    fake = FakeProbe()
    assert isinstance(fake, HealthProbe)
