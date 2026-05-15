from __future__ import annotations

import logging

import pytest

from backend.App.shared.infrastructure import tracing


def test_trace_span_noop_when_disabled(monkeypatch, caplog):
    monkeypatch.delenv("SWARM_OTEL_ENABLED", raising=False)
    with caplog.at_level(logging.INFO, logger=tracing.__name__):
        with tracing.trace_span("foo", attributes={"x": 1}) as payload:
            assert payload == {}
    assert not any("trace" in record.getMessage() for record in caplog.records)


def test_trace_span_emits_when_enabled(monkeypatch, caplog):
    monkeypatch.setenv("SWARM_OTEL_ENABLED", "1")
    with caplog.at_level(logging.INFO, logger=tracing.__name__):
        with tracing.trace_span("foo", attributes={"x": 1}) as payload:
            payload["custom"] = "value"
    log_lines = [record.getMessage() for record in caplog.records]
    assert any("trace:" in line and "name='foo'" in line for line in log_lines)
    assert any("custom='value'" in line for line in log_lines)


def test_trace_span_marks_error_on_exception(monkeypatch, caplog):
    monkeypatch.setenv("SWARM_OTEL_ENABLED", "1")
    with caplog.at_level(logging.INFO, logger=tracing.__name__):
        with pytest.raises(ValueError):
            with tracing.trace_span("boom"):
                raise ValueError("nope")
    error_lines = [
        record.getMessage()
        for record in caplog.records
        if "status='error'" in record.getMessage()
    ]
    assert error_lines, "expected an error trace line"


def test_tracing_enabled_default_false(monkeypatch):
    monkeypatch.delenv("SWARM_OTEL_ENABLED", raising=False)
    assert tracing.tracing_enabled() is False


def test_tracing_enabled_when_set(monkeypatch):
    monkeypatch.setenv("SWARM_OTEL_ENABLED", "yes")
    assert tracing.tracing_enabled() is True
