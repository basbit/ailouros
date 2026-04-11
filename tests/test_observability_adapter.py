"""Tests for OtelObservabilityAdapter and NullObservabilityAdapter."""
from unittest.mock import patch

from backend.App.integrations.domain.ports import ObservabilityPort
from backend.App.integrations.infrastructure.observability.observability_adapter import (
    NullObservabilityAdapter,
    OtelObservabilityAdapter,
)


def test_null_adapter_record_metric_no_raise():
    NullObservabilityAdapter().record_metric("foo", 1.0)


def test_null_adapter_trace_step_no_raise():
    NullObservabilityAdapter().trace_step("step1", {})


def test_null_adapter_step_span_ctx_no_raise():
    adapter = NullObservabilityAdapter()
    with adapter.step_span_ctx("step1", {}):
        pass


def test_null_implements_port():
    assert isinstance(NullObservabilityAdapter(), ObservabilityPort)


def test_otel_adapter_record_metric_swallows_import_error():
    with patch(
        "backend.App.integrations.infrastructure.observability.observability_adapter.OtelObservabilityAdapter.record_metric",
        wraps=None,
        side_effect=None,
    ):
        pass

    # Patch the inner import so record_histogram_ms raises ImportError.
    with patch.dict(
        "sys.modules",
        {"backend.App.integrations.infrastructure.observability.otel_tracing": None},
    ):
        # Should not raise even when the otel_tracing module is absent.
        OtelObservabilityAdapter().record_metric("x", 1.0)
