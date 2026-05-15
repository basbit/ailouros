from __future__ import annotations

import contextlib
import logging
import os
import time
import uuid
from typing import Any, Iterator, Optional

from backend.App.shared.infrastructure.app_config_load import load_app_config_json

logger = logging.getLogger(__name__)

_TRACING_CONFIG_FILE = "tracing.json"


def _config() -> dict[str, Any]:
    return load_app_config_json(_TRACING_CONFIG_FILE)


def tracing_enabled() -> bool:
    raw = (os.getenv("SWARM_OTEL_ENABLED") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def service_name() -> str:
    raw = _config().get("service_name")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    raise RuntimeError(f"{_TRACING_CONFIG_FILE}: service_name is required")


def exporter() -> str:
    raw = _config().get("exporter")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    raise RuntimeError(f"{_TRACING_CONFIG_FILE}: exporter is required")


def _console_body_limit() -> int:
    raw = _config().get("console_max_body_chars", 200)
    if isinstance(raw, int) and raw > 0:
        return raw
    raise RuntimeError(f"{_TRACING_CONFIG_FILE}: console_max_body_chars must be > 0")


def _new_span_id() -> str:
    return uuid.uuid4().hex[:16]


def _emit_console(record: dict[str, Any]) -> None:
    limit = _console_body_limit()
    rendered = " ".join(f"{key}={value!r}" for key, value in record.items())
    if len(rendered) > limit:
        rendered = rendered[:limit] + "…"
    logger.info("trace: %s", rendered)


@contextlib.contextmanager
def trace_span(
    name: str,
    *,
    attributes: Optional[dict[str, Any]] = None,
) -> Iterator[dict[str, Any]]:
    if not tracing_enabled():
        empty: dict[str, Any] = {}
        yield empty
        return
    span_id = _new_span_id()
    start = time.monotonic()
    payload: dict[str, Any] = {
        "service": service_name(),
        "name": name,
        "span_id": span_id,
        "status": "ok",
    }
    if attributes:
        payload.update(attributes)
    try:
        yield payload
    except BaseException as exc:
        payload["status"] = "error"
        payload["error"] = type(exc).__name__
        payload["message"] = str(exc)[:200]
        raise
    finally:
        payload["duration_ms"] = round((time.monotonic() - start) * 1000.0, 1)
        sink = exporter()
        if sink == "console":
            _emit_console(payload)
        else:
            raise RuntimeError(
                f"{_TRACING_CONFIG_FILE}: exporter {sink!r} is not supported; "
                "current build only ships the console exporter"
            )


__all__ = [
    "exporter",
    "service_name",
    "trace_span",
    "tracing_enabled",
]
