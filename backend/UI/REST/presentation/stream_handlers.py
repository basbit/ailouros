"""SSE stream generator functions for the UI/REST layer.

Contains:
- _stream_chat_chunks — main pipeline SSE stream
- _stream_human_resume_chunks — resume after awaiting_human (re-export)
- _stream_retry_chunks — retry from failed step (re-export)
- _sync_sse_generator_to_async — re-exported from async_sse_bridge
- _active_tasks — re-exported from async_sse_bridge
"""

from __future__ import annotations

import logging
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any, Optional
import threading

from backend.App.orchestration.application.pipeline_graph import run_pipeline_stream
from backend.App.tasks.infrastructure.task_run_log import append_task_run_log
from backend.UI.REST.presentation.sse import (
    _ensure_task_dirs,
)
from backend.UI.REST.presentation.sse_serializer import build_extra_event
from backend.UI.REST.presentation.pipeline_sse_handler import PipelineSSEHandler
from backend.UI.REST.presentation.stream_utils import (
    _stream_finalise,
    _write_agent_artifact,
    _write_agents_error_txt,
)
# Re-export async bridge so existing callers remain unmodified.
from backend.UI.REST.presentation.async_sse_bridge import (
    _active_tasks,
    sync_to_async_sse as _sync_sse_generator_to_async,
)
# Re-exported from extracted modules so all existing imports remain intact.
from backend.UI.REST.presentation.stream_handlers_resume import (
    _stream_human_resume_chunks,
)
from backend.UI.REST.presentation.stream_handlers_retry import (
    _stream_retry_chunks,
)

__all__ = [
    "_stream_chat_chunks",
    "_stream_human_resume_chunks",
    "_stream_retry_chunks",
    "_stream_finalise",
    "_write_agent_artifact",
    "_write_agents_error_txt",
    "_active_tasks",
    "_sync_sse_generator_to_async",
]

logger = logging.getLogger(__name__)


def _stream_chat_chunks(
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
    from backend.App.orchestration.application.tasks import pipeline_workspace_parts_from_meta

    set_task_id(task_id)
    now = int(time.time())
    task_dir = artifacts_root / task_id
    agents_dir = task_dir / "agents"
    _ensure_task_dirs(task_dir, agents_dir)

    # Always write a valid pipeline_steps list — never null.
    # When topology is set, pipeline_steps comes as None from the caller;
    # fallback to DEFAULT_PIPELINE_STEP_IDS so retry/resume can work.
    _effective_pipeline_steps = pipeline_steps
    if not isinstance(_effective_pipeline_steps, list) or not _effective_pipeline_steps:
        from backend.App.orchestration.application.step_registry import DEFAULT_PIPELINE_STEP_IDS
        _effective_pipeline_steps = list(DEFAULT_PIPELINE_STEP_IDS)

    pipeline_snapshot: dict[str, Any] = {
        "user_prompt": original_prompt,
        "input": effective_prompt,
        "agent_config": agent_config or {},
        "pipeline_steps": _effective_pipeline_steps,
        "workspace": workspace_meta or {},
    }

    # §10.6 UI↔Backend contract integrity — persist the exact request
    # the orchestrator received so post-mortem can compare wire payload
    # vs what the UI claims the user selected. Separate from pipeline.json
    # (which is the running snapshot and gets mutated by enforcement).
    try:
        import json as _json
        (task_dir / "request.json").write_text(
            _json.dumps(
                {
                    "received_at": now,
                    "task_id": task_id,
                    "user_prompt": original_prompt,
                    "pipeline_steps": pipeline_steps,
                    "pipeline_stages": pipeline_stages,
                    "pipeline_steps_effective": _effective_pipeline_steps,
                    "agent_config": agent_config or {},
                    "workspace_root": workspace_root_str,
                    "workspace_apply_writes": workspace_apply_writes,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
    except OSError as _req_exc:
        logger.warning("request.json persistence failed: %s", _req_exc)

    append_task_run_log(
        task_dir,
        f"stream start workspace_root={bool(workspace_root_str.strip())} "
        f"workspace_apply_writes={workspace_apply_writes}",
    )
    wmeta = workspace_meta or {}
    logger.info(
        "Pipeline stream task_id=%s workspace=%s apply_writes=%s context_mode=%s "
        "assembled_input_chars=%s snapshot_chars=%s",
        task_id,
        bool(workspace_root_str.strip()),
        workspace_apply_writes,
        wmeta.get("workspace_context_mode", ""),
        wmeta.get("assembled_input_chars", ""),
        wmeta.get("workspace_snapshot_chars", ""),
    )

    # Emit workspace_index_stats SSE event if index was collected
    idx_stats = wmeta.get("workspace_index_stats")
    if idx_stats:
        yield build_extra_event(
            now,
            request_model,
            workspace_index_stats={"type": "workspace_index_stats", **idx_stats},
        )

    # --- G-2: session preflight ---
    from backend.App.orchestration.application.lifecycle_hooks import PreflightError, run_session_preflight
    context_mode = wmeta.get("workspace_context_mode", "")
    try:
        preflight_result = run_session_preflight(
            workspace_root_str,
            context_mode,
            require_git=bool(workspace_root_str),
        )
    except PreflightError as pfe:
        preflight_result = {
            "type": "session_preflight",
            "status": "failed",
            "error_code": pfe.code,
            "error": str(pfe),
        }
        yield build_extra_event(now, request_model, session_preflight=preflight_result)
        task_store.update_task(task_id, status="failed", agent="preflight", message=str(pfe))
        return
    yield build_extra_event(now, request_model, session_preflight=preflight_result)

    # --- G-3: MCP preflight ---
    mcp_servers: list[dict] = []
    if isinstance(agent_config, dict):
        mcp_cfg = agent_config.get("mcp") or {}
        if isinstance(mcp_cfg, dict):
            mcp_servers = mcp_cfg.get("servers") or []
    if mcp_servers:
        from backend.App.integrations.infrastructure.mcp.stdio.session import mcp_preflight_check
        first_server = mcp_servers[0]
        preflight_mcp = mcp_preflight_check(first_server)
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

    # Route to staged runner when the UI declared explicit stages (parallel
    # fan-out inside stages).  Otherwise fall back to the sequential runner
    # which preserves the user's step order.  Backend does not decide which
    # topology maps to which stages — the UI already did that (§3 of
    # docs/review-rules.md: code is execution, decisions are in configuration).
    _has_stages = (
        isinstance(pipeline_stages, list)
        and len(pipeline_stages) > 0
        and all(isinstance(stage, list) and stage for stage in pipeline_stages)
        and any(len(stage) > 1 for stage in pipeline_stages)
    )
    if _has_stages:
        from backend.App.orchestration.application.pipeline_runners import (
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
            pipeline_workspace_parts=pipeline_workspace_parts_from_meta(wmeta),
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
            pipeline_workspace_parts=pipeline_workspace_parts_from_meta(wmeta),
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
