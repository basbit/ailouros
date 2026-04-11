"""Экспорт метрик пайплайна в формате Prometheus (optional: ``prometheus-client``).

Включение: пакет установлен и env **не** ``SWARM_PROMETHEUS=0``.

Эндпоинт приложения: ``GET /metrics`` (не путать с ``GET /v1/observability/metrics`` — JSON).
"""

from __future__ import annotations

import os
import threading
from typing import Any, Mapping, Optional

_lock = threading.Lock()
_initialized = False
_step_duration: Any = None
_step_total: Any = None


def prometheus_enabled() -> bool:
    v = os.getenv("SWARM_PROMETHEUS", "1").strip().lower()
    return v not in ("0", "false", "off", "no")


def _ensure_metrics() -> tuple[Any, Any] | tuple[None, None]:
    global _initialized, _step_duration, _step_total
    if not prometheus_enabled():
        return None, None
    try:
        from prometheus_client import Counter, Histogram
    except ImportError:
        return None, None
    with _lock:
        if not _initialized:
            _step_duration = Histogram(
                "swarm_pipeline_step_duration_seconds",
                "Wall clock time per pipeline graph step",
                ("step_id",),
                buckets=(
                    0.01,
                    0.05,
                    0.1,
                    0.25,
                    0.5,
                    1.0,
                    2.0,
                    5.0,
                    10.0,
                    30.0,
                    60.0,
                    120.0,
                    300.0,
                    600.0,
                ),
            )
            _step_total = Counter(
                "swarm_pipeline_step_completed_total",
                "Pipeline step completions (post-hook)",
                ("step_id",),
            )
            _initialized = True
        return _step_duration, _step_total


def observe_pipeline_step(
    step_id: str,
    duration_ms: float,
    step_delta: Optional[Mapping[str, Any]] = None,
) -> None:
    """Дублирует учёт в Prometheus (``step_delta`` зарезервирован)."""
    del step_delta  # labels ограничиваем — избегаем high cardinality
    hist, ctr = _ensure_metrics()
    if hist is None or ctr is None:
        return
    sid = (step_id or "unknown").strip() or "unknown"
    if any(c in sid for c in ('"', "\\", "\n")):
        sid = "invalid"
    try:
        sec = max(0.0, float(duration_ms) / 1000.0)
        hist.labels(step_id=sid).observe(sec)
        ctr.labels(step_id=sid).inc()
    except Exception:
        pass


def prometheus_metrics_response() -> Any:
    """``starlette.responses.Response`` или ``None`` если выключено / нет пакета."""
    if not prometheus_enabled():
        return None
    try:
        from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, generate_latest
        from starlette.responses import Response
    except ImportError:
        return None
    data = generate_latest(REGISTRY)
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)
