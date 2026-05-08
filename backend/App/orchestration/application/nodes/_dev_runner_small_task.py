from __future__ import annotations

import os
from typing import Any


def small_task_profile(task: dict[str, Any]) -> dict[str, Any]:
    expected_paths = [
        str(item or "").strip()
        for item in (task.get("expected_paths") or [])
        if str(item or "").strip()
    ]
    dependencies = [
        str(item or "").strip()
        for item in (task.get("dependencies") or [])
        if str(item or "").strip()
    ]
    is_small = len(expected_paths) <= 2 and len(dependencies) <= 2
    return {
        "enabled": is_small,
        "spec_max_chars": int(
            os.environ.get("SWARM_DEV_SMALL_TASK_SPEC_MAX_CHARS", "6000")
        ),
        "code_analysis_max_chars": int(
            os.environ.get("SWARM_DEV_SMALL_TASK_CODE_ANALYSIS_MAX_CHARS", "2500")
        ),
        "duration_budget_sec": float(
            os.environ.get("SWARM_DEV_SMALL_TASK_DURATION_BUDGET_SEC", "120")
        ),
        "split_recovery_enabled": os.environ.get(
            "SWARM_DEV_SMALL_TASK_SPLIT_RECOVERY", "1",
        ).strip() in ("1", "true", "yes"),
        "escalation_model": os.environ.get(
            "SWARM_DEV_SMALL_TASK_ESCALATION_MODEL", "",
        ).strip(),
    }


def small_task_missing_path_batches(missing_paths: list[str]) -> list[list[str]]:
    return [[path] for path in missing_paths if str(path or "").strip()]


def read_last_mcp_writes() -> tuple[int, list[dict[str, Any]]]:
    try:
        from backend.App.integrations.infrastructure.mcp.openai_loop.loop import (
            _last_mcp_write_count,
        )
        return (
            int(getattr(_last_mcp_write_count, "count", 0) or 0),
            list(getattr(_last_mcp_write_count, "actions", []) or []),
        )
    except Exception:
        return 0, []


__all__ = (
    "small_task_profile",
    "small_task_missing_path_batches",
    "read_last_mcp_writes",
)
