from __future__ import annotations

import json
import logging
import threading
from collections.abc import Generator
from pathlib import Path
from typing import Any, Optional

from backend.App.shared.infrastructure.openai_sse import build_done, sse_delta_line
from backend.App.shared.application.settings_resolver import get_setting_bool
from backend.App.orchestration.application.snapshot_serializer import pipeline_snapshot_for_disk
from backend.App.orchestration.application.use_cases.post_run_intelligence import persist_post_run_intelligence
from backend.App.workspace.application.use_cases.incremental_workspace_writes import (
    stream_incremental_workspace_enabled,
)
from backend.App.workspace.application.use_cases.apply_pipeline_writes import (
    apply_final_workspace_writes,
    capture_workspace_diff_after_writes,
    workspace_followup_lines,
)
from backend.App.workspace.application.assets import (
    build_asset_manifest as build_workspace_asset_manifest,
    write_workspace_asset_manifest,
)
from backend.App.tasks.infrastructure.task_run_log import append_task_run_log

logger = logging.getLogger(__name__)


def write_agents_error_txt(task_dir: Path, agents_dir: Path, err_text: str) -> None:
    try:
        task_dir.mkdir(parents=True, exist_ok=True)
        agents_dir.mkdir(parents=True, exist_ok=True)
        (agents_dir / "error.txt").write_text(err_text, encoding="utf-8")
    except OSError as ose:
        logger.warning("Could not write agents/error.txt for task %s: %s", task_dir.name, ose)


def write_agent_artifact(agents_dir: Path, agent: str, text: str) -> None:
    out_path = agents_dir / f"{agent}.txt"
    out_path.write_text(text, encoding="utf-8")


def build_run_manifest(pipeline_snapshot: dict[str, Any]) -> dict[str, Any]:
    shell_runs: list[dict[str, Any]] = []

    def add_runs(container: Any, *, source: str) -> None:
        if not isinstance(container, dict):
            return
        for run in container.get("shell_runs") or []:
            if not isinstance(run, dict):
                continue
            item = dict(run)
            item["source"] = source
            shell_runs.append(item)

    add_runs(pipeline_snapshot.get("workspace_writes"), source="final")
    for idx, item in enumerate(pipeline_snapshot.get("workspace_writes_incremental") or []):
        if isinstance(item, dict):
            add_runs(item, source=str(item.get("step") or f"incremental_{idx}"))

    attempted = [run for run in shell_runs if not run.get("dry_run")]
    executed = [
        run for run in attempted
        if not run.get("skipped") and not run.get("error")
    ]
    failed = [
        run for run in executed
        if int(run.get("returncode") or 0) != 0
    ]
    skipped = [run for run in attempted if run.get("skipped")]
    errored = [run for run in attempted if run.get("error")]

    status = "not_attempted"
    if executed and not failed:
        status = "executed"
    elif failed or errored:
        status = "failed"
    elif skipped:
        status = "blocked"

    return {
        "schema": "swarm_run_manifest/v1",
        "status": status,
        "commands_attempted": len(attempted),
        "commands_executed": len(executed),
        "commands_failed": len(failed),
        "commands_skipped": len(skipped),
        "shell_runs": shell_runs,
    }


def build_workspace_truth(pipeline_snapshot: dict[str, Any]) -> dict[str, Any]:
    changed: set[str] = set()
    errors: list[str] = []
    binary_assets: set[str] = set()

    def merge_write_container(container: Any) -> None:
        if not isinstance(container, dict):
            return
        for key in ("written", "patched", "udiff_applied"):
            for item in container.get(key) or []:
                if item:
                    changed.add(str(item))
        for error in container.get("errors") or []:
            errors.append(str(error))
        for item in container.get("binary_assets_requested") or []:
            if item:
                binary_assets.add(str(item))

    merge_write_container(pipeline_snapshot.get("workspace_writes"))
    for item in pipeline_snapshot.get("workspace_writes_incremental") or []:
        merge_write_container(item)

    filesystem_truth = pipeline_snapshot.get("filesystem_truth")
    if isinstance(filesystem_truth, dict):
        diff = filesystem_truth.get("diff")
        if isinstance(diff, dict):
            for item in diff.get("changed_files") or []:
                if item:
                    changed.add(str(item))

    return {
        "schema": "swarm_workspace_truth/v1",
        "changed_files": sorted(changed),
        "write_errors": errors,
        "binary_assets_requested": sorted(binary_assets),
        "filesystem_truth": filesystem_truth if isinstance(filesystem_truth, dict) else {},
    }


def build_asset_manifest(pipeline_snapshot: dict[str, Any]) -> dict[str, Any]:
    return build_workspace_asset_manifest(pipeline_snapshot)


def stream_finalise(
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
    apply_final_workspace_writes(
        task_id,
        pipeline_snapshot,
        workspace_path,
        workspace_apply_writes,
        task_store,
        cancel_event=cancel_event,
        skip_all_shell=stream_incremental_workspace_enabled(),
    )
    if workspace_path and workspace_apply_writes and pipeline_snapshot.get("workspace_writes"):
        capture_workspace_diff_after_writes(pipeline_snapshot, workspace_path)
        mcp_write_count = pipeline_snapshot.get("dev_mcp_write_count", 0)
        if mcp_write_count and isinstance(pipeline_snapshot.get("workspace_writes"), dict):
            pipeline_snapshot["workspace_writes"]["mcp_tool_writes"] = mcp_write_count

    if workspace_path and pipeline_snapshot.get("workspace_apply_writes"):
        from backend.App.workspace.application.wiki.wiki_auto_updater import update_wiki_from_pipeline
        try:
            update_wiki_from_pipeline(pipeline_snapshot, Path(workspace_path))
        except Exception as wiki_exc:
            logger.debug("wiki auto-update failed: %s", wiki_exc)

    if workspace_path and workspace_apply_writes and not pipeline_snapshot.get("partial_state"):
        ws_writes = pipeline_snapshot.get("workspace_writes") or {}
        files_written = len(ws_writes.get("written") or []) + len(ws_writes.get("patched") or [])
        incremental = pipeline_snapshot.get("workspace_writes_incremental") or []
        any_incremental = any(
            len((inc.get("written") or [])) + len((inc.get("patched") or [])) > 0
            for inc in incremental
            if isinstance(inc, dict)
        )
        mcp_writes = ws_writes.get("mcp_tool_writes", 0) or pipeline_snapshot.get("dev_mcp_write_count", 0)
        stop_early = bool(pipeline_snapshot.get("_pipeline_stop_early"))
        if files_written == 0 and not any_incremental and not stop_early and mcp_writes == 0:
            require_writes = get_setting_bool(
                "swarm.require_dev_writes",
                workspace_root=workspace_path,
                env_key="SWARM_REQUIRE_DEV_WRITES",
                default=True,
            )
            log_level = "ERROR" if require_writes else "WARNING"
            warn = (
                f"[orchestrator] {log_level}: workspace_apply_writes=True but files_written=0. "
                "No <swarm_file>/<swarm_patch> tags found in any agent output. "
                "Models must use <swarm_file> tags or workspace__write_file tool calls."
            )
            append_task_run_log(task_dir, warn)
            logger.error(warn) if require_writes else logger.warning(warn)
            yield sse_delta_line(now, request_model, warn + "\n")
            if require_writes:
                pipeline_snapshot["_ec1_zero_writes"] = True

    for followup_line in workspace_followup_lines(
        workspace_path, workspace_apply_writes, pipeline_snapshot
    ):
        append_task_run_log(task_dir, followup_line.strip())
        yield sse_delta_line(now, request_model, followup_line)

    for auto_approval in (pipeline_snapshot.get("auto_approvals") or []):
        if not isinstance(auto_approval, dict):
            continue
        auto_approval_event = {
            "agent": "system",
            "status": "auto_approved",
            "step": auto_approval.get("step"),
            "audit": auto_approval.get("audit"),
        }
        yield sse_delta_line(now, request_model, json.dumps(auto_approval_event) + "\n")

    pipeline_snapshot["workspace_truth"] = build_workspace_truth(pipeline_snapshot)
    asset_manifest = build_workspace_asset_manifest(pipeline_snapshot, workspace_path)
    pipeline_snapshot["asset_manifest"] = asset_manifest
    if asset_manifest.get("requested_assets"):
        (task_dir / "asset_manifest.json").write_text(
            json.dumps(asset_manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if workspace_path is not None:
            write_workspace_asset_manifest(workspace_path, asset_manifest)
    run_manifest = build_run_manifest(pipeline_snapshot)
    pipeline_snapshot["run_manifest"] = run_manifest
    (task_dir / "run_manifest.json").write_text(
        json.dumps(run_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    post_run_reports = persist_post_run_intelligence(task_dir, workspace_path, pipeline_snapshot)
    pipeline_snapshot["automation_agents"] = post_run_reports["automation_agents"]
    pipeline_snapshot["agent_identity"] = post_run_reports["agent_identity"]

    _final_status = "completed_no_writes" if pipeline_snapshot.get("_ec1_zero_writes") else "completed"
    task_store.update_task(task_id, status=_final_status)
    (task_dir / "pipeline.json").write_text(
        json.dumps(
            pipeline_snapshot_for_disk(pipeline_snapshot),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    append_task_run_log(task_dir, "stream completed, pipeline.json written")

    _dream_enabled = get_setting_bool(
        "dream.enabled",
        workspace_root=workspace_path,
        env_key="SWARM_DREAM_ENABLED",
        default=False,
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

    yield build_done(now, request_model)
    yield "data: [DONE]\n\n"
