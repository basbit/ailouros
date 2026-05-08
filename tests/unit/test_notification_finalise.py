from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from backend.App.integrations.application.notifications.finalise_emitter import (
    emit_finalise_notification,
    list_recent_deliveries,
    reset_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_router() -> Any:
    reset_for_tests()
    yield
    reset_for_tests()


def _snapshot(scenario_id: str = "build_feature") -> dict[str, Any]:
    return {
        "scenario_id": scenario_id,
        "scenario_title": "Build Feature",
        "scenario_category": "development",
    }


def test_emit_skipped_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARM_NOTIFY_ENABLED", "0")
    emit_finalise_notification(
        task_id="t-1",
        final_status="completed",
        final_error="",
        pipeline_snapshot=_snapshot(),
        workspace_path=Path("/tmp/project"),
    )
    assert list_recent_deliveries() == []


def test_emit_records_delivery_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARM_NOTIFY_ENABLED", "1")
    monkeypatch.delenv("SWARM_NOTIFY_WEBHOOK_URL", raising=False)
    emit_finalise_notification(
        task_id="t-2",
        final_status="completed",
        final_error="",
        pipeline_snapshot=_snapshot(),
        workspace_path=Path("/tmp/project"),
    )
    assert list_recent_deliveries() == []


def test_failed_status_classifies_as_run_failed_severity_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SWARM_NOTIFY_ENABLED", "1")
    monkeypatch.setenv("SWARM_NOTIFY_WEBHOOK_URL", "http://127.0.0.1:1/notify")
    monkeypatch.setenv("SWARM_NOTIFY_RATE_LIMIT_PER_MIN", "5")
    snapshot = _snapshot()
    snapshot["_failed_trusted_gates"] = ["source_corruption"]
    snapshot["_failed_trusted_gates_summary"] = "source_corruption: 1 finding"
    emit_finalise_notification(
        task_id="t-3",
        final_status="failed",
        final_error="Trusted verification gates failed: source_corruption",
        pipeline_snapshot=snapshot,
        workspace_path=Path("/tmp/project"),
    )
    deliveries = list_recent_deliveries()
    assert len(deliveries) == 1
    delivery = deliveries[0]
    assert delivery["kind"] == "run_failed"
    assert delivery["severity"] == "error"
    assert delivery["channel"] == "webhook"
