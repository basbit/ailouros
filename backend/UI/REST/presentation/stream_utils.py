"""Shared SSE stream utilities for the UI/REST presentation layer.

Extracted from stream_handlers.py: _write_agents_error_txt, _write_agent_artifact,
_stream_finalise.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Generator
from pathlib import Path
from typing import Any, Optional

from backend.UI.REST.presentation.sse import _ensure_task_dirs, _sse_delta_line
from backend.UI.REST.utils import (
    _pipeline_snapshot_for_disk,
    _stream_incremental_workspace_enabled,
    _workspace_followup_lines,
)
from backend.App.tasks.infrastructure.task_run_log import append_task_run_log

logger = logging.getLogger(__name__)


def _write_agents_error_txt(task_dir: Path, agents_dir: Path, err_text: str) -> None:
    try:
        _ensure_task_dirs(task_dir, agents_dir)
        (agents_dir / "error.txt").write_text(err_text, encoding="utf-8")
    except OSError as ose:
        logger.warning("Could not write agents/error.txt for task %s: %s", task_dir.name, ose)


def _write_agent_artifact(agents_dir: Path, agent: str, text: str) -> None:
    """Write agent output text to ``agents_dir/{agent}.txt``."""
    out_path = agents_dir / f"{agent}.txt"
    out_path.write_text(text, encoding="utf-8")


def _stream_finalise(
    task_id: str,
    task_dir: Path,
    pipeline_snapshot: dict[str, Any],
    workspace_path: Optional[Path],
    workspace_apply_writes: bool,
    cancel_event: Optional[threading.Event],
    now: int,
    request_model: str,
    task_store: Any,
) -> Generator[str, None, None]:
    """Handle post-pipeline workspace writes, followup lines, task completion, and pipeline.json."""
    from backend.App.workspace.infrastructure.workspace_io import workspace_write_allowed
    from backend.App.workspace.infrastructure.patch_parser import apply_from_devops_and_dev_outputs
    from backend.App.orchestration.infrastructure.shell_approval import run_shell_after_user_approval

    if (
        workspace_path
        and workspace_apply_writes
        and workspace_write_allowed()
    ):
        run_sh = run_shell_after_user_approval(
            task_id,
            pipeline_snapshot,
            task_store,
            cancel_event=cancel_event,
            skip_all_shell=_stream_incremental_workspace_enabled(),
        )
        pipeline_snapshot["workspace_writes"] = apply_from_devops_and_dev_outputs(
            pipeline_snapshot,
            workspace_path,
            run_shell=run_sh,
        )
        # Capture diff so workspace-diff endpoint can serve it from pipeline.json
        from backend.App.workspace.infrastructure.workspace_diff import capture_workspace_diff
        _ws = pipeline_snapshot["workspace_writes"]
        _written = list(_ws.get("written") or [])
        _patched = list(_ws.get("patched") or [])
        _udiff_applied = list(_ws.get("udiff_applied") or [])
        _all_changed = sorted(set(_written + _patched + _udiff_applied))
        pipeline_snapshot["dev_workspace_diff"] = capture_workspace_diff(workspace_path, _all_changed)
        # Include MCP write count in workspace_writes for accurate stats
        _mcp_wc = pipeline_snapshot.get("dev_mcp_write_count", 0)
        if _mcp_wc and isinstance(pipeline_snapshot.get("workspace_writes"), dict):
            pipeline_snapshot["workspace_writes"]["mcp_tool_writes"] = _mcp_wc

    if workspace_path and pipeline_snapshot.get("workspace_apply_writes"):
        from backend.App.orchestration.application.wiki_auto_updater import update_wiki_from_pipeline
        try:
            update_wiki_from_pipeline(pipeline_snapshot, Path(workspace_path))
        except Exception as _wiki_exc:
            logger.debug("wiki auto-update failed: %s", _wiki_exc)

    # Warn when workspace writes were requested but nothing was written
    if workspace_path and workspace_apply_writes and not pipeline_snapshot.get("partial_state"):
        ws_writes = pipeline_snapshot.get("workspace_writes") or {}
        files_written = len(ws_writes.get("written") or []) + len(ws_writes.get("patched") or [])
        incremental = pipeline_snapshot.get("workspace_writes_incremental") or []
        any_incremental = any(
            len((inc.get("written") or [])) + len((inc.get("patched") or [])) > 0
            for inc in incremental
            if isinstance(inc, dict)
        )
        _mcp_writes = ws_writes.get("mcp_tool_writes", 0) or pipeline_snapshot.get("dev_mcp_write_count", 0)
        stop_early = bool(pipeline_snapshot.get("_pipeline_stop_early"))
        if files_written == 0 and not any_incremental and not stop_early and _mcp_writes == 0:
            # Log error (not just warning) when SWARM_REQUIRE_DEV_WRITES=1
            _require = os.getenv("SWARM_REQUIRE_DEV_WRITES", "1").strip() in ("1", "true", "yes")
            _level = "ERROR" if _require else "WARNING"
            warn = (
                f"[orchestrator] {_level}: workspace_apply_writes=True but files_written=0. "
                "No <swarm_file>/<swarm_patch> tags found in any agent output. "
                "Models must use <swarm_file> tags or workspace__write_file tool calls."
            )
            append_task_run_log(task_dir, warn)
            logger.error(warn) if _require else logger.warning(warn)
            yield _sse_delta_line(now, request_model, warn + "\n")
            if _require:
                pipeline_snapshot["_ec1_zero_writes"] = True

    for wl in _workspace_followup_lines(
        workspace_path, workspace_apply_writes, pipeline_snapshot
    ):
        append_task_run_log(task_dir, wl.strip())
        yield _sse_delta_line(now, request_model, wl)

    # Emit SSE audit events for any auto-approvals recorded during the pipeline run.
    for _aa in (pipeline_snapshot.get("auto_approvals") or []):
        if not isinstance(_aa, dict):
            continue
        _aa_event = {
            "agent": "system",
            "status": "auto_approved",
            "step": _aa.get("step"),
            "audit": _aa.get("audit"),
        }
        yield _sse_delta_line(now, request_model, json.dumps(_aa_event) + "\n")

    _final_status = "completed_no_writes" if pipeline_snapshot.get("_ec1_zero_writes") else "completed"
    task_store.update_task(task_id, status=_final_status)
    (task_dir / "pipeline.json").write_text(
        json.dumps(
            _pipeline_snapshot_for_disk(pipeline_snapshot),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    append_task_run_log(task_dir, "stream completed, pipeline.json written")

    # Memory consolidation — gated on UI toggle (swarm.dream_enabled) or env SWARM_DREAM_ENABLED
    _dream_enabled = (
        os.getenv("SWARM_DREAM_ENABLED", "0").strip() in ("1", "true", "yes")
    )
    if _dream_enabled:
        try:
            import threading as _th
            from backend.App.integrations.application.memory_consolidation import MemoryConsolidator
            from backend.App.integrations.infrastructure.cross_task_memory import memory_namespace
            state_for_ns = pipeline_snapshot.get("partial_state") or pipeline_snapshot
            ns = memory_namespace(state_for_ns)
            pm_path = task_dir.parent.parent / ".swarm" / "pattern_memory.json"

            def _consolidate() -> None:
                try:
                    stats = MemoryConsolidator().run_consolidation(namespace=ns, pattern_path=pm_path)
                    logger.info("Memory consolidation completed: %s", stats)
                except Exception as exc:
                    logger.warning("Memory consolidation failed: %s", exc)

            _th.Thread(target=_consolidate, daemon=True).start()
        except Exception as exc:
            logger.warning("Could not schedule memory consolidation: %s", exc)

    final_payload = {
        "id": f"chatcmpl-{now}",
        "object": "chat.completion.chunk",
        "created": now,
        "model": request_model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(final_payload, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"
