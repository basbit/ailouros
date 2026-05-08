from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from backend.App.orchestration.application.enforcement.enforcement_policy import (
    pre_review_blocker_steps,
)
from backend.App.orchestration.application.enforcement.verification_contract import (
    is_human_gate_in_pipeline,
)
from backend.App.orchestration.application.pipeline.ephemeral_state import (
    ephemeral_as_dict,
)
from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState
from backend.App.orchestration.domain.exceptions import HumanApprovalRequired
from backend.App.shared.application.settings_resolver import get_setting_bool

_logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _route_steps() -> tuple[str, str]:
    steps = pre_review_blocker_steps()
    human_step_id = steps.get("human_step_id")
    resume_pipeline_step = steps.get("resume_pipeline_step")
    if not human_step_id or not resume_pipeline_step:
        raise RuntimeError("pipeline_enforcement_policy.pre_review_blockers is incomplete")
    return human_step_id, resume_pipeline_step


def _workspace_path_or_none(state: PipelineState) -> Path | None:
    workspace_root = str(state.get("workspace_root") or "").strip()
    if not workspace_root:
        return None
    try:
        return Path(workspace_root).resolve()
    except OSError:
        return None


def _zero_writes_detected(state: PipelineState) -> bool:
    if not bool(state.get("workspace_apply_writes")):
        return False
    workspace_writes = cast(dict[str, Any], state.get("workspace_writes") or {})
    parsed = int(workspace_writes.get("parsed", 0) or 0)
    if parsed > 0:
        return False
    written = list(workspace_writes.get("written") or [])
    patched = list(workspace_writes.get("patched") or [])
    udiff_applied = list(workspace_writes.get("udiff_applied") or [])
    if written or patched or udiff_applied:
        return False
    mcp_writes = int(cast(Any, state.get("dev_mcp_write_count")) or 0)
    if mcp_writes > 0:
        return False
    return True


def _route_block(
    state: PipelineState,
    *,
    human_step_id: str,
    detail: str,
    resume_pipeline_step: str,
) -> None:
    if is_human_gate_in_pipeline(state, human_step_id):
        raise HumanApprovalRequired(
            step="pre_review_blocker",
            detail=detail,
            partial_state={
                "failed_trusted_gates": list(cast(Any, state.get("_failed_trusted_gates")) or []),
                "failed_trusted_gates_summary": str(
                    state.get("_failed_trusted_gates_summary") or ""
                ),
            },
            resume_pipeline_step=resume_pipeline_step,
        )
    raise RuntimeError(detail)


def enforce_pre_review_blockers(state: PipelineState) -> None:
    workspace_path = _workspace_path_or_none(state)
    state_dict = ephemeral_as_dict(state)
    human_step_id, resume_pipeline_step = _route_steps()

    if _zero_writes_detected(state):
        require_writes = get_setting_bool(
            "swarm.require_dev_writes",
            workspace_root=workspace_path,
            env_key="SWARM_REQUIRE_DEV_WRITES",
            default=True,
        )
        if require_writes:
            state_dict["_ec1_zero_writes"] = True
            detail = (
                "pre_review_blocker: dev step produced 0 workspace writes "
                "with apply_writes=True. Pipeline halted before review/QA so "
                "no false-green gate can run."
            )
            state_dict["_ec1_error"] = detail
            _logger.error(detail)
            _route_block(
                state,
                human_step_id=human_step_id,
                detail=detail,
                resume_pipeline_step=resume_pipeline_step,
            )

    failed_trusted = list(cast(Any, state.get("_failed_trusted_gates")) or [])
    if failed_trusted:
        require_trusted = get_setting_bool(
            "swarm.require_trusted_gates_pass",
            workspace_root=workspace_path,
            env_key="SWARM_REQUIRE_TRUSTED_GATES_PASS",
            default=True,
        )
        if require_trusted:
            summary = str(state.get("_failed_trusted_gates_summary") or "")
            detail = (
                "pre_review_blocker: trusted verification gates failed before "
                f"review/QA: {summary or ', '.join(failed_trusted)}"
            )
            _logger.error(detail)
            _route_block(
                state,
                human_step_id=human_step_id,
                detail=detail,
                resume_pipeline_step=resume_pipeline_step,
            )


__all__ = ("enforce_pre_review_blockers",)
