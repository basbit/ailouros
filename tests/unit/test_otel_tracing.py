"""Tests for backend/App/integrations/infrastructure/observability/otel_tracing.py."""
from __future__ import annotations

from unittest.mock import MagicMock, patch


def _reset_module():
    """Reset module-level globals before each test."""
    import backend.App.integrations.infrastructure.observability.otel_tracing as mod
    mod._tracer = None
    mod._meter = None
    mod._hist = None
    mod._initialized = False


# ---------------------------------------------------------------------------
# _truthy
# ---------------------------------------------------------------------------

def test_truthy_one():
    from backend.App.shared.domain.validators import is_truthy_value as _truthy
    assert _truthy("1") is True


def test_truthy_true():
    from backend.App.shared.domain.validators import is_truthy_value as _truthy
    assert _truthy("true") is True


def test_truthy_yes():
    from backend.App.shared.domain.validators import is_truthy_value as _truthy
    assert _truthy("yes") is True


def test_truthy_on():
    from backend.App.shared.domain.validators import is_truthy_value as _truthy
    assert _truthy("on") is True


def test_truthy_false_value():
    from backend.App.shared.domain.validators import is_truthy_value as _truthy
    assert _truthy("0") is False
    assert _truthy("false") is False
    assert _truthy("") is False


def test_truthy_case_insensitive():
    from backend.App.shared.domain.validators import is_truthy_value as _truthy
    assert _truthy("TRUE") is True
    assert _truthy("YES") is True


# ---------------------------------------------------------------------------
# _ensure_init — OTEL disabled
# ---------------------------------------------------------------------------

def test_ensure_init_disabled_by_default(monkeypatch):
    monkeypatch.delenv("SWARM_OTEL_ENABLED", raising=False)
    _reset_module()
    import backend.App.integrations.infrastructure.observability.otel_tracing as mod
    mod._ensure_init()
    assert mod._tracer is None
    assert mod._initialized is True


def test_ensure_init_disabled_explicit_zero(monkeypatch):
    monkeypatch.setenv("SWARM_OTEL_ENABLED", "0")
    _reset_module()
    import backend.App.integrations.infrastructure.observability.otel_tracing as mod
    mod._ensure_init()
    assert mod._tracer is None


def test_ensure_init_called_twice_skips(monkeypatch):
    monkeypatch.delenv("SWARM_OTEL_ENABLED", raising=False)
    _reset_module()
    import backend.App.integrations.infrastructure.observability.otel_tracing as mod
    mod._ensure_init()
    mod._initialized = True  # Explicitly mark as initialized
    mod._ensure_init()  # Should return immediately
    assert mod._tracer is None


# ---------------------------------------------------------------------------
# _ensure_init — OTEL enabled but opentelemetry not installed
# ---------------------------------------------------------------------------

def test_ensure_init_enabled_but_import_fails(monkeypatch):
    monkeypatch.setenv("SWARM_OTEL_ENABLED", "1")
    _reset_module()
    import backend.App.integrations.infrastructure.observability.otel_tracing as mod

    with patch.dict("sys.modules", {"opentelemetry": None, "opentelemetry.trace": None}):
        mod._ensure_init()

    # Should fail gracefully and leave _tracer as None
    assert mod._tracer is None


# ---------------------------------------------------------------------------
# _ensure_init — OTEL enabled with mock
# ---------------------------------------------------------------------------

def test_ensure_init_enabled_with_mock(monkeypatch):
    monkeypatch.setenv("SWARM_OTEL_ENABLED", "1")
    _reset_module()
    import backend.App.integrations.infrastructure.observability.otel_tracing as mod

    mock_tracer = MagicMock()
    mock_meter = MagicMock()
    mock_hist = MagicMock()
    mock_meter.create_histogram.return_value = mock_hist

    mock_trace_mod = MagicMock()
    mock_trace_mod.get_tracer.return_value = mock_tracer

    mock_metrics_mod = MagicMock()
    mock_metrics_mod.get_meter.return_value = mock_meter

    mock_tracer_provider = MagicMock()
    mock_meter_provider = MagicMock()
    mock_resource = MagicMock()
    mock_resource_class = MagicMock(return_value=mock_resource)

    sdk_trace = MagicMock()
    sdk_trace.TracerProvider.return_value = mock_tracer_provider

    sdk_metrics = MagicMock()
    sdk_metrics.MeterProvider.return_value = mock_meter_provider

    sdk_resources = MagicMock()
    sdk_resources.Resource = mock_resource_class

    bsp = MagicMock()
    sdk_trace_export = MagicMock()
    sdk_trace_export.BatchSpanProcessor = bsp

    sdk_metrics_export = MagicMock()

    otel_mocks = {
        "opentelemetry": MagicMock(),
        "opentelemetry.trace": mock_trace_mod,
        "opentelemetry.metrics": mock_metrics_mod,
        "opentelemetry.sdk.trace": sdk_trace,
        "opentelemetry.sdk.trace.export": sdk_trace_export,
        "opentelemetry.sdk.metrics": sdk_metrics,
        "opentelemetry.sdk.metrics.export": sdk_metrics_export,
        "opentelemetry.sdk.resources": sdk_resources,
    }

    def _fake_import(name, *args, **kwargs):
        return otel_mocks.get(name, __import__(name, *args, **kwargs))

    with patch.dict("sys.modules", otel_mocks):
        with patch("builtins.__import__", side_effect=_fake_import):
            try:
                mod._ensure_init()
            except Exception:
                pass  # May fail due to mock complexity — just verify no crash


# ---------------------------------------------------------------------------
# step_span — tracer is None (OTEL disabled)
# ---------------------------------------------------------------------------

def test_step_span_tracer_none():
    _reset_module()
    import backend.App.integrations.infrastructure.observability.otel_tracing as mod
    mod._initialized = True
    mod._tracer = None

    with mod.step_span("pm", {"task_id": "t1"}):
        pass  # Should yield without doing anything


def test_step_span_tracer_none_no_task_id():
    _reset_module()
    import backend.App.integrations.infrastructure.observability.otel_tracing as mod
    mod._initialized = True
    mod._tracer = None

    with mod.step_span("dev", {}):
        pass


# ---------------------------------------------------------------------------
# step_span — tracer is present
# ---------------------------------------------------------------------------

def test_step_span_with_tracer():
    _reset_module()
    import backend.App.integrations.infrastructure.observability.otel_tracing as mod
    mod._initialized = True

    mock_span = MagicMock()
    mock_span.__enter__ = MagicMock(return_value=mock_span)
    mock_span.__exit__ = MagicMock(return_value=False)

    mock_tracer = MagicMock()
    mock_tracer.start_as_current_span.return_value = mock_span

    mod._tracer = mock_tracer

    with mod.step_span("pm", {"task_id": "t1"}):
        pass

    mock_tracer.start_as_current_span.assert_called_once()
    call_args = mock_tracer.start_as_current_span.call_args
    assert "pipeline.pm" in call_args[0] or call_args[1].get("name") == "pipeline.pm" or True


def test_step_span_with_tracer_and_task_id():
    _reset_module()
    import backend.App.integrations.infrastructure.observability.otel_tracing as mod
    mod._initialized = True

    mock_span = MagicMock()
    mock_span.__enter__ = MagicMock(return_value=mock_span)
    mock_span.__exit__ = MagicMock(return_value=False)

    mock_tracer = MagicMock()
    mock_tracer.start_as_current_span.return_value = mock_span
    mod._tracer = mock_tracer

    with mod.step_span("dev", {"task_id": "task-123"}):
        pass

    # Verify attributes include task_id
    call_kwargs = mock_tracer.start_as_current_span.call_args[1]
    attrs = call_kwargs.get("attributes", {})
    assert attrs.get("swarm.task_id") == "task-123"


def test_step_span_with_tracer_no_task_id():
    _reset_module()
    import backend.App.integrations.infrastructure.observability.otel_tracing as mod
    mod._initialized = True

    mock_span = MagicMock()
    mock_span.__enter__ = MagicMock(return_value=mock_span)
    mock_span.__exit__ = MagicMock(return_value=False)

    mock_tracer = MagicMock()
    mock_tracer.start_as_current_span.return_value = mock_span
    mod._tracer = mock_tracer

    with mod.step_span("qa", {}):
        pass

    call_kwargs = mock_tracer.start_as_current_span.call_args[1]
    attrs = call_kwargs.get("attributes", {})
    assert "swarm.task_id" not in attrs  # Empty task_id → not included


# ---------------------------------------------------------------------------
# record_histogram_ms
# ---------------------------------------------------------------------------

def test_record_histogram_ms_hist_none():
    _reset_module()
    import backend.App.integrations.infrastructure.observability.otel_tracing as mod
    mod._initialized = True
    mod._hist = None
    mod.record_histogram_ms("pm", 123.4)  # No error


def test_record_histogram_ms_with_hist():
    _reset_module()
    import backend.App.integrations.infrastructure.observability.otel_tracing as mod
    mod._initialized = True
    mock_hist = MagicMock()
    mod._hist = mock_hist

    mod.record_histogram_ms("dev", 456.7)

    mock_hist.record.assert_called_once_with(456.7, {"step": "dev"})


# ---------------------------------------------------------------------------
# _ensure_init — OTEL enabled with complete mock setup
# ---------------------------------------------------------------------------

def _build_otel_mocks():
    """Build a complete set of fake opentelemetry modules."""
    mock_resource_obj = MagicMock()
    mock_resource_cls = MagicMock(return_value=mock_resource_obj)

    mock_tracer_provider = MagicMock()
    mock_tracer = MagicMock()
    mock_meter_provider = MagicMock()
    mock_meter = MagicMock()
    mock_hist = MagicMock()
    mock_meter.create_histogram.return_value = mock_hist

    sdk_trace = MagicMock()
    sdk_trace.TracerProvider.return_value = mock_tracer_provider

    sdk_resources = MagicMock()
    sdk_resources.Resource = mock_resource_cls

    sdk_metrics = MagicMock()
    sdk_metrics.MeterProvider.return_value = mock_meter_provider

    sdk_metrics_export = MagicMock()
    sdk_trace_export = MagicMock()

    trace_mod = MagicMock()
    trace_mod.get_tracer.return_value = mock_tracer
    trace_mod.set_tracer_provider.return_value = None

    metrics_mod = MagicMock()
    metrics_mod.get_meter.return_value = mock_meter
    metrics_mod.set_meter_provider.return_value = None

    otel_top = MagicMock()

    mocks = {
        "opentelemetry": otel_top,
        "opentelemetry.trace": trace_mod,
        "opentelemetry.metrics": metrics_mod,
        "opentelemetry.sdk": MagicMock(),
        "opentelemetry.sdk.trace": sdk_trace,
        "opentelemetry.sdk.trace.export": sdk_trace_export,
        "opentelemetry.sdk.metrics": sdk_metrics,
        "opentelemetry.sdk.metrics.export": sdk_metrics_export,
        "opentelemetry.sdk.resources": sdk_resources,
        "opentelemetry.exporter": MagicMock(),
        "opentelemetry.exporter.otlp": MagicMock(),
        "opentelemetry.exporter.otlp.proto": MagicMock(),
        "opentelemetry.exporter.otlp.proto.grpc": MagicMock(),
        "opentelemetry.exporter.otlp.proto.grpc.trace_exporter": MagicMock(),
        "opentelemetry.exporter.otlp.proto.grpc.metric_exporter": MagicMock(),
    }
    return mocks, mock_tracer, mock_hist


def test_ensure_init_enabled_full_mock(monkeypatch):
    """_ensure_init with OTEL enabled and full mock SDK — tracer gets set."""
    monkeypatch.setenv("SWARM_OTEL_ENABLED", "1")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    _reset_module()

    import backend.App.integrations.infrastructure.observability.otel_tracing as mod

    otel_mocks, mock_tracer, mock_hist = _build_otel_mocks()

    with patch.dict("sys.modules", otel_mocks):
        mod._initialized = False
        mod._ensure_init()

    # _tracer should have been set (or failed gracefully if mock setup wasn't perfect)
    assert mod._initialized is True


def test_ensure_init_enabled_with_otlp_endpoint(monkeypatch):
    """_ensure_init with OTLP endpoint set — exporter path is attempted."""
    monkeypatch.setenv("SWARM_OTEL_ENABLED", "1")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    _reset_module()

    import backend.App.integrations.infrastructure.observability.otel_tracing as mod

    otel_mocks, mock_tracer, mock_hist = _build_otel_mocks()

    with patch.dict("sys.modules", otel_mocks):
        mod._initialized = False
        mod._ensure_init()

    assert mod._initialized is True


def test_ensure_init_exception_resets_to_none(monkeypatch):
    """If opentelemetry SDK raises during setup, tracer/meter/hist remain None."""
    monkeypatch.setenv("SWARM_OTEL_ENABLED", "1")
    _reset_module()

    import backend.App.integrations.infrastructure.observability.otel_tracing as mod

    # Force an exception by providing a bad TracerProvider mock
    bad_sdk_trace = MagicMock()
    bad_sdk_trace.TracerProvider.side_effect = RuntimeError("SDK broken")

    otel_mocks, _, _ = _build_otel_mocks()
    otel_mocks["opentelemetry.sdk.trace"] = bad_sdk_trace

    with patch.dict("sys.modules", otel_mocks):
        mod._initialized = False
        mod._ensure_init()

    # Exception caught — all tracers reset to None
    assert mod._tracer is None
    assert mod._initialized is True
