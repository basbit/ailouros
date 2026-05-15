"""Tests for the host metrics collector that drives the live chart in the UI.

The frontend's HostMetricsChart depends on three contract points: timestamps
are present so the X-axis aligns across ticks, GPU fields are absent (not
zeroed) when no GPU is detected, and a broken GPU collector permanently
disables itself instead of paying subprocess cost every second.
"""
from __future__ import annotations

from typing import Any

import backend.App.integrations.application.system_metrics as sm


def _reset_caches() -> None:
    """Reset the module-level GPU discovery caches between tests."""
    sm._nvml_init_ok = None  # type: ignore[attr-defined]
    sm._nvidia_smi_path = None  # type: ignore[attr-defined]
    sm._gpu_disabled_reason = None  # type: ignore[attr-defined]


def test_payload_always_carries_a_timestamp(monkeypatch: Any) -> None:
    _reset_caches()
    monkeypatch.setattr(sm, "_pynvml", None)
    monkeypatch.setattr(sm, "_resolve_nvidia_smi", lambda: None)
    data = sm.metrics_payload()
    assert isinstance(data.get("timestamp_ms"), int)
    assert data["timestamp_ms"] > 0


def test_payload_omits_gpu_fields_when_no_gpu_available(monkeypatch: Any) -> None:
    _reset_caches()
    monkeypatch.setattr(sm, "_pynvml", None)
    monkeypatch.setattr(sm, "_resolve_nvidia_smi", lambda: None)
    data = sm.metrics_payload()
    # Absent rather than zeroed — the chart hides the GPU line entirely.
    assert "gpu_percent" not in data
    assert "gpu_memory_percent" not in data
    assert "gpu_name" not in data


def test_gpu_from_nvml_aggregates_across_devices(monkeypatch: Any) -> None:
    _reset_caches()

    class _FakeUtil:
        def __init__(self, gpu: int) -> None:
            self.gpu = gpu

    class _FakeMem:
        def __init__(self, used: int, total: int) -> None:
            self.used = used
            self.total = total

    class _FakePynvml:
        @staticmethod
        def nvmlInit() -> None:
            return None

        @staticmethod
        def nvmlDeviceGetCount() -> int:
            return 2

        @staticmethod
        def nvmlDeviceGetHandleByIndex(idx: int) -> int:
            return idx

        @staticmethod
        def nvmlDeviceGetUtilizationRates(handle: int) -> _FakeUtil:
            return _FakeUtil(40 if handle == 0 else 60)

        @staticmethod
        def nvmlDeviceGetMemoryInfo(handle: int) -> _FakeMem:
            gib = 1024**3
            return _FakeMem(used=2 * gib, total=8 * gib)

        @staticmethod
        def nvmlDeviceGetName(handle: int) -> bytes:
            return b"FakeGPU"

    monkeypatch.setattr(sm, "_pynvml", _FakePynvml)
    snap = sm._gpu_from_nvml()
    assert snap is not None
    # Average of 40 and 60.
    assert snap["gpu_percent"] == 50.0
    assert snap["gpu_count"] == 2
    assert snap["gpu_name"] == "FakeGPU"
    # 2 GB used out of 8 GB total per device, summed: 4 / 16 = 25 %.
    assert snap["gpu_memory_percent"] == 25.0
    assert snap["gpu_memory_used_gb"] == 4.0
    assert snap["gpu_memory_total_gb"] == 16.0


def test_failing_nvidia_smi_disables_itself_permanently(monkeypatch: Any) -> None:
    """If `nvidia-smi` raises once, subsequent ticks must skip the subprocess.

    Otherwise every tick pays ~50 ms of process-spawn cost on machines where
    the binary exists but is broken (driver mismatch, no permissions, …).
    """
    _reset_caches()
    monkeypatch.setattr(sm, "_pynvml", None)
    monkeypatch.setattr(sm, "_nvidia_smi_path", "/fake/nvidia-smi")

    call_count = {"n": 0}

    def _explode(*_args: Any, **_kwargs: Any) -> Any:
        call_count["n"] += 1
        raise RuntimeError("driver mismatch")

    monkeypatch.setattr("subprocess.run", _explode)

    assert sm._gpu_from_nvidia_smi() is None
    assert sm._gpu_from_nvidia_smi() is None
    assert call_count["n"] == 1  # the second call short-circuits


def test_payload_does_not_raise_when_psutil_missing(monkeypatch: Any) -> None:
    _reset_caches()
    monkeypatch.setattr(sm, "_psutil", None)
    monkeypatch.setattr(sm, "_pynvml", None)
    monkeypatch.setattr(sm, "_resolve_nvidia_smi", lambda: None)
    data = sm.metrics_payload()
    assert "loadavg" in data
    assert "timestamp_ms" in data
