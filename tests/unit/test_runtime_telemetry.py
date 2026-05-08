import json
from pathlib import Path

from backend.App.shared.application.runtime_telemetry import build_runtime_telemetry
from backend.App.orchestration.application.use_cases.task_queries import (
    runtime_telemetry_payload,
)


def test_build_runtime_telemetry_with_servers_marks_ready() -> None:
    payload = build_runtime_telemetry(
        {"mcp": {"servers": [{"name": "workspace"}]}},
        {"workspace_context_mode": "retrieve"},
    )
    assert payload == {
        "context_mode": "retrieve",
        "tools_enabled": True,
        "mcp_phase": "ready",
    }


def test_build_runtime_telemetry_no_servers_marks_off() -> None:
    payload = build_runtime_telemetry(
        {"mcp": {"servers": []}},
        {"workspace_context_mode": "retrieve"},
    )
    assert payload["tools_enabled"] is False
    assert payload["mcp_phase"] == "off"


def test_build_runtime_telemetry_fallback_overrides_servers() -> None:
    payload = build_runtime_telemetry(
        {"mcp": {"servers": [{"name": "workspace"}]}},
        {"workspace_context_mode": "retrieve", "workspace_context_mcp_fallback": True},
    )
    assert payload["mcp_phase"] == "fallback"


def test_runtime_telemetry_payload_prefers_runtime_json(tmp_path: Path) -> None:
    task_dir = tmp_path / "task-id"
    task_dir.mkdir()
    (task_dir / "runtime.json").write_text(
        json.dumps({
            "context_mode": "retrieve",
            "tools_enabled": True,
            "mcp_phase": "ready",
        }),
        encoding="utf-8",
    )
    payload = runtime_telemetry_payload("task-id", tmp_path)
    assert payload == {
        "context_mode": "retrieve",
        "tools_enabled": True,
        "mcp_phase": "ready",
    }


def test_runtime_telemetry_payload_falls_back_to_pipeline_snapshot(
    tmp_path: Path,
) -> None:
    task_dir = tmp_path / "task-id"
    task_dir.mkdir()
    (task_dir / "pipeline.json").write_text(
        json.dumps({
            "partial_state": {
                "workspace_context_mode": "full",
                "workspace_context_mcp_fallback": False,
                "agent_config": {"mcp": {"servers": [{"name": "workspace"}]}},
            },
        }),
        encoding="utf-8",
    )
    payload = runtime_telemetry_payload("task-id", tmp_path)
    assert payload["context_mode"] == "full"
    assert payload["tools_enabled"] is True
    assert payload["mcp_phase"] == "ready"


def test_runtime_telemetry_payload_returns_empty_when_nothing_present(
    tmp_path: Path,
) -> None:
    payload = runtime_telemetry_payload("missing-task", tmp_path)
    assert payload == {}
