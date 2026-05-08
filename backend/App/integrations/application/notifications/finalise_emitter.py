from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from backend.App.integrations.application.notifications.channel_factory import (
    build_channels_from_env,
)
from backend.App.integrations.application.notifications.policy import (
    policy_from_config,
)
from backend.App.integrations.application.notifications.router import (
    NotificationRouter,
)
from backend.App.integrations.domain.notifications import (
    NotificationEvent,
)
from backend.App.integrations.infrastructure.notifications.in_memory_ledger import (
    InMemoryNotificationLedger,
)

logger = logging.getLogger(__name__)


_router_singleton: NotificationRouter | None = None
_ledger_singleton: InMemoryNotificationLedger | None = None


def _build_router() -> NotificationRouter:
    global _router_singleton, _ledger_singleton
    if _router_singleton is not None:
        return _router_singleton
    _ledger_singleton = InMemoryNotificationLedger(capacity=400)
    channels = build_channels_from_env()
    severity_threshold = (os.getenv("SWARM_NOTIFY_MIN_SEVERITY") or "info").strip().lower()
    rate_limit_raw = (os.getenv("SWARM_NOTIFY_RATE_LIMIT_PER_MIN") or "30").strip()
    try:
        rate_limit = max(1, int(rate_limit_raw))
    except ValueError:
        rate_limit = 30
    policy = policy_from_config(
        {
            "severity_threshold": severity_threshold,
            "rate_limit_per_minute": rate_limit,
        }
    )
    _router_singleton = NotificationRouter(
        policy=policy,
        channels=channels,
        ledger=_ledger_singleton,
    )
    return _router_singleton


def list_recent_deliveries(limit: int = 50) -> list[dict[str, Any]]:
    if _ledger_singleton is None:
        return []
    return [delivery.to_dict() for delivery in _ledger_singleton.list_recent(limit)]


def reset_for_tests() -> None:
    global _router_singleton, _ledger_singleton
    _router_singleton = None
    _ledger_singleton = None


def _classify_kind_and_severity(
    final_status: str,
    *,
    failed_trusted_gates: list[str],
) -> tuple[str, str]:
    status = (final_status or "").strip().lower()
    if status == "failed":
        return "run_failed", "error"
    if status == "completed":
        return "run_completed", "info"
    if status == "completed_no_writes":
        return "run_completed", "warning"
    if status == "completed_with_failures":
        return "run_completed", "warning"
    if status == "blocked":
        return "run_blocked", "warning"
    if status == "cancelled":
        return "run_completed", "info"
    if failed_trusted_gates:
        return "risk_detected", "warning"
    return "run_completed", "info"


def emit_finalise_notification(
    *,
    task_id: str,
    final_status: str,
    final_error: str,
    pipeline_snapshot: dict[str, Any],
    workspace_path: Path | None,
) -> None:
    enabled_raw = (os.getenv("SWARM_NOTIFY_ENABLED") or "0").strip().lower()
    if enabled_raw not in {"1", "true", "yes", "on"}:
        return
    failed_trusted = list(pipeline_snapshot.get("_failed_trusted_gates") or [])
    kind, severity = _classify_kind_and_severity(
        final_status, failed_trusted_gates=failed_trusted,
    )
    scenario_id = (
        str(pipeline_snapshot.get("scenario_id") or "")
        if pipeline_snapshot.get("scenario_id")
        else None
    )
    workspace_text = str(workspace_path) if workspace_path else None
    project_label = (
        Path(workspace_text).name if workspace_text else None
    )
    title_prefix = (
        scenario_id or "run"
    )
    title = f"{title_prefix} → {final_status}"
    summary_parts = [f"task_id={task_id[:8]}", f"status={final_status}"]
    if final_error:
        summary_parts.append(f"error={final_error[:160]}")
    if failed_trusted:
        summary_parts.append(
            f"failed_gates={','.join(failed_trusted)[:160]}"
        )
    summary = "; ".join(summary_parts)
    artifact_path = (
        f"/artifacts/{task_id}/pipeline.json"
        if task_id
        else None
    )
    event = NotificationEvent(
        kind=kind,
        severity=severity,
        title=title,
        summary=summary,
        task_id=task_id,
        project=project_label,
        scenario_id=scenario_id,
        artifact_path=artifact_path,
        extra={
            "failed_trusted_gates": failed_trusted,
            "scenario_title": pipeline_snapshot.get("scenario_title"),
            "scenario_category": pipeline_snapshot.get("scenario_category"),
        },
    )
    router = _build_router()
    router.dispatch(event)


__all__ = (
    "emit_finalise_notification",
    "list_recent_deliveries",
    "reset_for_tests",
)
