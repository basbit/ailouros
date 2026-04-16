"""OtelObservabilityAdapter — implements ObservabilityPort using OTEL + step metrics.

Wraps the existing otel_tracing and step_metrics infrastructure modules.
"""
from __future__ import annotations

import contextlib
import logging
from typing import Any, Optional

from backend.App.integrations.domain.ports import ObservabilityPort

_logger = logging.getLogger(__name__)


class OtelObservabilityAdapter(ObservabilityPort):
    """Production adapter: routes to OTEL tracing and Prometheus step metrics."""

    def record_metric(self, name: str, value: float, tags: Optional[dict[str, str]] = None) -> None:
        try:
            from backend.App.integrations.infrastructure.observability.otel_tracing import record_histogram_ms
            record_histogram_ms(name, value)
        except Exception as exc:
            _logger.debug("OtelObservabilityAdapter.record_metric failed: %s", exc)

    def trace_step(self, step_id: str, data: dict[str, Any]) -> None:
        try:
            from backend.App.integrations.infrastructure.observability.step_metrics import record_step
            dt_ms = float(data.get("dt_ms", 0.0))
            task_id = str(data.get("task_id", ""))
            step_delta = data.get("step_delta", {})
            record_step(step_id, dt_ms, task_id=task_id, step_delta=step_delta)
        except Exception as exc:
            _logger.debug("OtelObservabilityAdapter.trace_step failed: %s", exc)

    @contextlib.contextmanager
    def step_span_ctx(self, step_id: str, state: dict[str, Any]):
        """Context manager wrapping OTEL step span.

        Falls back to nullcontext if otel_tracing is unavailable so that
        exceptions from the body (e.g. HumanApprovalRequired) propagate
        normally without a second yield causing RuntimeError.
        """
        span_ctx: Any
        try:
            from backend.App.integrations.infrastructure.observability.otel_tracing import step_span
            span_ctx = step_span(step_id, state)
        except Exception as exc:
            _logger.debug("OtelObservabilityAdapter.step_span_ctx: OTEL unavailable: %s", exc)
            span_ctx = contextlib.nullcontext()
        with span_ctx:
            yield


class NullObservabilityAdapter(ObservabilityPort):
    """No-op adapter for tests — does nothing, raises nothing."""

    def record_metric(self, name: str, value: float, tags: Optional[dict[str, str]] = None) -> None:
        pass

    def trace_step(self, step_id: str, data: dict[str, Any]) -> None:
        pass

    @contextlib.contextmanager
    def step_span_ctx(self, step_id: str, state: dict[str, Any]):
        yield
