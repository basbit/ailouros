"""Опциональные OpenTelemetry spans для шагов пайплайна.

Включение: ``SWARM_OTEL_ENABLED=1`` и установленные пакеты
``opentelemetry-api``, ``opentelemetry-sdk``; стандартные переменные OTEL_* для экспорта.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from contextlib import contextmanager
from typing import Any, Iterator

_tracer: Any = None
_meter: Any = None
_hist: Any = None
_initialized = False


def _ensure_init() -> None:
    global _tracer, _meter, _hist, _initialized
    if _initialized:
        return
    _initialized = True
    if not _truthy(os.getenv("SWARM_OTEL_ENABLED", "")):
        return
    try:
        from opentelemetry import metrics, trace
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create(
            {
                "service.name": os.getenv("OTEL_SERVICE_NAME", "AIlourOS"),
            }
        )
        provider = TracerProvider(resource=resource)
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer("AIlourOS.pipeline")

        # OTLP span exporter if endpoint set
        endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
        if endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                    OTLPSpanExporter,
                )

                provider.add_span_processor(
                    BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
                )
            except Exception:
                pass

        reader: Any = None
        if endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
                    OTLPMetricExporter,
                )

                reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=endpoint))
            except Exception:
                reader = None
        if reader:
            metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))
        _meter = metrics.get_meter("AIlourOS.pipeline")
        _hist = _meter.create_histogram(
            name="swarm.pipeline.step.duration_ms",
            description="Pipeline step duration in milliseconds",
            unit="ms",
        )
    except Exception:
        _tracer = None
        _meter = None
        _hist = None


def _truthy(val: str) -> bool:
    return val.strip().lower() in ("1", "true", "yes", "on")


@contextmanager
def step_span(
    step_id: str,
    state: Mapping[str, Any],
) -> Iterator[None]:
    _ensure_init()
    if _tracer is None:
        yield
        return
    tid = str(state.get("task_id") or "")
    attrs = {"swarm.pipeline.step": step_id}
    if tid:
        attrs["swarm.task_id"] = tid
    with _tracer.start_as_current_span(f"pipeline.{step_id}", attributes=attrs):
        yield


def record_histogram_ms(step_id: str, duration_ms: float) -> None:
    _ensure_init()
    if _hist is None:
        return
    _hist.record(duration_ms, {"step": step_id})
