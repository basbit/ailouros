from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any, Optional

from backend.App.orchestration.application.routing.pipeline_graph import run_pipeline_stream
from backend.App.orchestration.application.snapshot_serializer import redact_agent_config_secrets
from backend.App.shared.infrastructure.openai_sse import build_extra_event, ensure_task_dirs
from backend.App.tasks.infrastructure.task_run_log import append_task_run_log
from backend.App.orchestration.application.streaming.pipeline_sse_handler import PipelineSSEHandler

logger = logging.getLogger(__name__)


def stream_chat_chunks(
    original_prompt: str,
    effective_prompt: str,
    request_model: str,
    task_id: str,
    task_store: Any,
    artifacts_root: Path,
    agent_config: Optional[dict[str, Any]] = None,
    pipeline_steps: Optional[list[str]] = None,
    pipeline_stages: Optional[list[list[str]]] = None,
    workspace_root_str: str = "",
    workspace_apply_writes: bool = False,
    workspace_meta: Optional[dict[str, Any]] = None,
    workspace_path: Optional[Path] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Generator[str, None, None]:
    from backend.App.integrations.infrastructure.observability.logging_config import set_task_id
    from backend.App.orchestration.application.use_cases.tasks import pipeline_workspace_parts_from_meta

    set_task_id(task_id)
    now = int(time.time())
    task_dir = artifacts_root / task_id
    agents_dir = task_dir / "agents"
    ensure_task_dirs(task_dir, agents_dir)

    _effective_pipeline_steps = pipeline_steps
    if not isinstance(_effective_pipeline_steps, list) or not _effective_pipeline_steps:
        from backend.App.orchestration.application.routing.step_registry import DEFAULT_PIPELINE_STEP_IDS
        _effective_pipeline_steps = list(DEFAULT_PIPELINE_STEP_IDS)

    pipeline_snapshot: dict[str, Any] = {
        "user_prompt": original_prompt,
        "input": effective_prompt,
        "agent_config": agent_config or {},
        "pipeline_steps": _effective_pipeline_steps,
        "workspace": workspace_meta or {},
    }

    try:
        (task_dir / "request.json").write_text(
            json.dumps(
                {
                    "received_at": now,
                    "task_id": task_id,
                    "user_prompt": original_prompt,
                    "pipeline_steps": pipeline_steps,
                    "pipeline_stages": pipeline_stages,
                    "pipeline_steps_effective": _effective_pipeline_steps,
                    "agent_config": redact_agent_config_secrets(agent_config or {}),
                    "workspace_root": workspace_root_str,
                    "workspace_apply_writes": workspace_apply_writes,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
    except OSError as io_error:
        logger.warning("request.json persistence failed: %s", io_error)

    append_task_run_log(
        task_dir,
        f"stream start workspace_root={bool(workspace_root_str.strip())} "
        f"workspace_apply_writes={workspace_apply_writes}",
    )
    workspace_meta_safe = workspace_meta or {}
    logger.info(
        "Pipeline stream task_id=%s workspace=%s apply_writes=%s context_mode=%s "
        "assembled_input_chars=%s snapshot_chars=%s",
        task_id,
        bool(workspace_root_str.strip()),
        workspace_apply_writes,
        workspace_meta_safe.get("workspace_context_mode", ""),
        workspace_meta_safe.get("assembled_input_chars", ""),
        workspace_meta_safe.get("workspace_snapshot_chars", ""),
    )

    idx_stats = workspace_meta_safe.get("workspace_index_stats")
    if idx_stats:
        yield build_extra_event(
            now,
            request_model,
            workspace_index_stats={"type": "workspace_index_stats", **idx_stats},
        )

    from backend.App.orchestration.application.pipeline.lifecycle_hooks import PreflightError, run_session_preflight
    context_mode = workspace_meta_safe.get("workspace_context_mode", "")
    try:
        preflight_result = run_session_preflight(
            workspace_root_str,
            context_mode,
            require_git=bool(workspace_root_str),
        )
    except PreflightError as preflight_error:
        preflight_result = {
            "type": "session_preflight",
            "status": "failed",
            "error_code": preflight_error.code,
            "error": str(preflight_error),
        }
        yield build_extra_event(now, request_model, session_preflight=preflight_result)
        task_store.update_task(task_id, status="failed", agent="preflight", message=str(preflight_error))
        return
    yield build_extra_event(now, request_model, session_preflight=preflight_result)

    mcp_servers: list[dict] = []
    if isinstance(agent_config, dict):
        mcp_cfg = agent_config.get("mcp") or {}
        if isinstance(mcp_cfg, dict):
            mcp_servers = mcp_cfg.get("servers") or []
    if mcp_servers:
        from backend.App.integrations.application.mcp_service import check_mcp_server_preflight
        first_server = mcp_servers[0]
        preflight_mcp = check_mcp_server_preflight(first_server)
        yield build_extra_event(
            now,
            request_model,
            mcp_preflight={"type": "mcp_preflight", **preflight_mcp},
        )
        if preflight_mcp.get("status") == "failed":
            task_store.update_task(
                task_id, status="failed", agent="mcp_preflight",
                message=f"MCP preflight failed (phase={preflight_mcp.get('phase')}): {preflight_mcp.get('error')}",
            )
            return

    _has_stages = (
        isinstance(pipeline_stages, list)
        and len(pipeline_stages) > 0
        and all(isinstance(stage, list) and stage for stage in pipeline_stages)
        and any(len(stage) > 1 for stage in pipeline_stages)
    )
    if _has_stages:
        from backend.App.orchestration.application.pipeline.pipeline_runners import (
            run_pipeline_stream_staged,
        )
        events_gen = run_pipeline_stream_staged(
            effective_prompt,
            pipeline_stages=pipeline_stages,  # type: ignore[arg-type]
            agent_config=agent_config,
            workspace_root=workspace_root_str,
            workspace_apply_writes=workspace_apply_writes,
            task_id=task_id,
            cancel_event=cancel_event,
            pipeline_workspace_parts=pipeline_workspace_parts_from_meta(workspace_meta_safe),
        )
    else:
        events_gen = run_pipeline_stream(
            effective_prompt,
            agent_config=agent_config,
            pipeline_steps=pipeline_steps,
            workspace_root=workspace_root_str,
            workspace_apply_writes=workspace_apply_writes,
            task_id=task_id,
            cancel_event=cancel_event,
            pipeline_workspace_parts=pipeline_workspace_parts_from_meta(workspace_meta_safe),
            pipeline_step_ids=pipeline_steps,
        )

    handler = PipelineSSEHandler(task_store=task_store)
    yield from handler.handle_events(
        events_gen=events_gen,
        task_id=task_id,
        task_dir=task_dir,
        agents_dir=agents_dir,
        pipeline_snapshot=pipeline_snapshot,
        now=now,
        request_model=request_model,
        workspace_path=workspace_path,
        workspace_apply_writes=workspace_apply_writes,
        cancel_event=cancel_event,
    )
