from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from backend.App.orchestration.domain.trace import EventType, TraceEvent

_logger = logging.getLogger(__name__)

__all__ = [
    "emit_trace_event",
    "emit_trace_child_event",
]


def emit_trace_event(
    trace_collector: Any,
    task_id: str,
    session_id: str,
    step: str,
    event_type_value: str,
    data: dict,
) -> str | None:
    try:
        event_id = str(uuid.uuid4())
        trace_collector.record(
            TraceEvent(
                event_id=event_id,
                trace_id=task_id,
                session_id=session_id,
                task_id=task_id,
                step=step,
                event_type=EventType(event_type_value),
                timestamp=datetime.now(tz=timezone.utc).isoformat(),
                data=data,
            )
        )
        return event_id
    except Exception as exc:
        _logger.warning(
            "trace_emitter: emit_trace_event failed for event_type=%r step=%r: %s",
            event_type_value,
            step,
            exc,
        )
        return None


def emit_trace_child_event(
    trace_collector: Any,
    task_id: str,
    session_id: str,
    step: str,
    event_type_value: str,
    parent_event_id: str | None,
    data: dict,
) -> str | None:
    try:
        event_id = str(uuid.uuid4())
        trace_collector.record(
            TraceEvent(
                event_id=event_id,
                trace_id=task_id,
                session_id=session_id,
                task_id=task_id,
                step=step,
                event_type=EventType(event_type_value),
                timestamp=datetime.now(tz=timezone.utc).isoformat(),
                data=data,
                parent_event_id=parent_event_id,
            )
        )
        return event_id
    except Exception as exc:
        _logger.warning(
            "trace_emitter: emit_trace_child_event failed for event_type=%r step=%r: %s",
            event_type_value,
            step,
            exc,
        )
        return None
