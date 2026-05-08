from __future__ import annotations

import logging
import os
from pathlib import Path
from string import Template
from typing import Any

from backend.App.orchestration.application.enforcement.enforcement_policy import (
    configured_string_list,
    workspace_preflight_policy,
)
from backend.App.orchestration.application.enforcement.source_corruption_scanner import (
    scan_workspace_for_source_corruption,
    summarize_findings,
)
from backend.App.orchestration.application.pipeline.ephemeral_state import ephemeral_as_dict
from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState
from backend.App.shared.application.settings_resolver import get_setting_bool

logger = logging.getLogger(__name__)


def _configured_write_steps() -> frozenset[str]:
    return frozenset(configured_string_list(workspace_preflight_policy(), "write_step_ids"))


def _policy_text(key: str) -> str:
    return str(workspace_preflight_policy().get(key) or "").strip()


def _render_policy_text(key: str, **values: Any) -> str:
    return Template(_policy_text(key)).safe_substitute(**values)


def _max_preflight_files() -> int:
    policy = workspace_preflight_policy()
    environment_key = str(policy.get("max_files_environment_key") or "").strip()
    environment_value = os.getenv(environment_key, "").strip() if environment_key else ""
    if environment_value.isdigit():
        return max(1, int(environment_value))
    try:
        return max(1, int(policy.get("max_files_default")))
    except (TypeError, ValueError):
        return 1


def _workspace_path_or_none(state: PipelineState) -> Path | None:
    workspace_root = str(state.get("workspace_root") or "").strip()
    if not workspace_root:
        return None
    try:
        return Path(workspace_root).resolve()
    except OSError:
        return None


def _preflight_enabled(state: PipelineState, workspace_path: Path | None) -> bool:
    policy = workspace_preflight_policy()
    return get_setting_bool(
        str(policy.get("setting_path") or ""),
        workspace_root=workspace_path,
        env_key=str(policy.get("enabled_environment_key") or ""),
        default=bool(policy.get("enabled_default")),
    )


def enforce_workspace_preflight(state: PipelineState, step_id: str) -> dict[str, Any] | None:
    policy = workspace_preflight_policy()
    if step_id not in _configured_write_steps():
        return None
    if not bool(state.get("workspace_apply_writes")):
        return None

    workspace_path = _workspace_path_or_none(state)
    if workspace_path is None:
        return None
    if not _preflight_enabled(state, workspace_path):
        return None

    findings = scan_workspace_for_source_corruption(
        workspace_path,
        max_files=_max_preflight_files(),
    )
    if not findings:
        summary = {"total": 0, "by_path": {}, "by_pattern": {}}
        ephemeral_as_dict(state)["workspace_preflight"] = {
            "passed": True,
            "step": step_id,
            "source_corruption_summary": summary,
        }
        return {
            "agent": _policy_text("agent_name"),
            "status": "completed",
            "message": _policy_text("passed_message"),
        }

    summary = summarize_findings(findings)
    preview_count = int(policy.get("preview_count") or 1)
    preview = ", ".join(
        f"{finding.path}:{finding.line} [{finding.pattern_id}]"
        for finding in findings[:preview_count]
    )
    failed_gate_name = _policy_text("failed_gate_name")
    state_dict = ephemeral_as_dict(state)
    state_dict["workspace_preflight"] = {
        "passed": False,
        "step": step_id,
        "source_corruption_summary": summary,
        "source_corruption_findings": [finding.to_dict() for finding in findings],
    }
    state_dict["_failed_trusted_gates"] = list(
        state_dict.get("_failed_trusted_gates") or []
    ) + [failed_gate_name]
    state_dict["_failed_trusted_gates_summary"] = _render_policy_text(
        "failed_summary_template",
        count=len(findings),
        step_id=step_id,
        preview=preview,
    )

    detail = _render_policy_text(
        "failed_detail_template",
        step_id=step_id,
        preview=preview,
    )
    logger.error(detail)
    raise RuntimeError(detail)


__all__ = ("enforce_workspace_preflight",)
