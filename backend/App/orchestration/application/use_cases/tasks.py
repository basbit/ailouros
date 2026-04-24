
from __future__ import annotations

import copy
import json
import logging
import warnings
from pathlib import Path
from typing import Any, Optional

from backend.App.shared.application.settings_resolver import get_setting_bool
from backend.App.orchestration.domain.exceptions import HumanApprovalRequired
from backend.App.integrations.infrastructure.agent_registry import merge_agent_config
from backend.App.integrations.infrastructure.mcp.auto.auto import apply_auto_mcp_to_agent_config
from backend.App.integrations.infrastructure.pipeline_presets import resolve_preset
from backend.App.orchestration.application.routing.pipeline_graph import (
    ARTIFACT_AGENT_OUTPUT_KEYS,
    final_pipeline_user_message,
    run_pipeline,
    task_store_agent_label,
)

from backend.App.orchestration.infrastructure.shell_approval import run_shell_after_user_approval
from backend.App.tasks.infrastructure.task_run_log import append_task_run_log
from backend.App.workspace.infrastructure.workspace_io import (
    WORKSPACE_CONTEXT_MODE_DEFAULT,
    WORKSPACE_CONTEXT_MODE_INDEX_ONLY,
    WORKSPACE_CONTEXT_MODE_POST_ANALYSIS_COMPACT,
    WORKSPACE_CONTEXT_MODE_PRIORITY_PATHS,
    WORKSPACE_CONTEXT_MODE_RETRIEVE,
    WORKSPACE_CONTEXT_MODE_TOOLS_ONLY,
    build_input_with_workspace,
    collect_workspace_file_index,
    collect_workspace_priority_snapshot,
    collect_workspace_snapshot,
    read_project_context_file,
    resolve_project_context_path,
    resolve_workspace_context_mode,
    tools_only_workspace_placeholder,
    validate_readable_file,
    validate_workspace_root,
    workspace_write_allowed,
)
from backend.App.workspace.infrastructure.patch_parser import (
    apply_from_devops_and_dev_outputs,
)
from backend.App.orchestration.application.use_cases.chat_request_resolver import (
    ChatRequest,
    ChatRequestResolver,
)
from backend.App.workspace.infrastructure.at_mention_loader import (
    load_at_mentions as _load_at_mentions_new,
    AtMentionLoader,
)
from backend.App.workspace.infrastructure.project_context_scanner import (
    scan_project as _scan_project_new,
    ProjectContextScanner,
)

__all__ = [
    "ChatRequest",
    "ChatRequestResolver",
    "AtMentionLoader",
    "ProjectContextScanner",
]

logger = logging.getLogger(__name__)


def resolve_chat_request(req: Any) -> tuple[dict[str, Any], Optional[list[str]]]:
    agent_config = merge_agent_config(req.agent_config)
    _apply_global_automation_settings(agent_config)
    steps = req.pipeline_steps
    if steps is None and req.pipeline_preset:
        steps = resolve_preset(req.pipeline_preset)
    return agent_config, steps


def _apply_global_automation_settings(agent_config: dict[str, Any]) -> None:
    """Overwrite Automation & Quality fields in agent_config["swarm"] with
    the globally persisted user settings. These fields are managed in the
    global Settings drawer — per-project values are ignored at runtime."""
    from backend.App.integrations.application.user_settings_service import (
        get_persisted_automation_settings,
    )

    glob = get_persisted_automation_settings()
    swarm = agent_config.get("swarm")
    if not isinstance(swarm, dict):
        swarm = {}
        agent_config["swarm"] = swarm

    def _set_bool(swarm_key: str, form_key: str) -> None:
        if glob.get(form_key):
            swarm[swarm_key] = True
        else:
            swarm.pop(swarm_key, None)

    def _set_str(swarm_key: str, form_key: str) -> None:
        value = str(glob.get(form_key, "") or "").strip()
        if value:
            swarm[swarm_key] = value
        else:
            swarm.pop(swarm_key, None)

    def _set_int(swarm_key: str, form_key: str) -> None:
        raw = str(glob.get(form_key, "") or "").strip()
        if not raw:
            swarm.pop(swarm_key, None)
            return
        try:
            parsed = int(raw)
        except ValueError:
            swarm.pop(swarm_key, None)
            return
        if parsed > 0:
            swarm[swarm_key] = parsed
        else:
            swarm.pop(swarm_key, None)

    _set_bool("self_verify", "swarm_self_verify")
    _set_str("self_verify_model", "swarm_self_verify_model")
    _set_str("self_verify_provider", "swarm_self_verify_provider")
    _set_str("auto_approve", "swarm_auto_approve")
    _set_int("auto_approve_timeout", "swarm_auto_approve_timeout")
    _set_bool("auto_retry", "swarm_auto_retry")
    _set_int("max_step_retries", "swarm_max_step_retries")
    _set_bool("deep_planning", "swarm_deep_planning")
    _set_str("deep_planning_model", "swarm_deep_planning_model")
    _set_str("deep_planning_provider", "swarm_deep_planning_provider")
    _set_bool("background_agent", "swarm_background_agent")
    _set_str("background_agent_model", "swarm_background_agent_model")
    _set_str("background_agent_provider", "swarm_background_agent_provider")
    _set_str("background_watch_paths", "swarm_background_watch_paths")
    _set_bool("dream_enabled", "swarm_dream_enabled")
    _set_bool("quality_gate_enabled", "swarm_quality_gate")
    _set_bool("auto_plan", "swarm_auto_plan")

    # swarm_planner sits on the top-level agent_config, not under "swarm"
    planner_model = str(glob.get("swarm_planner_model", "") or "").strip()
    planner_provider = str(glob.get("swarm_planner_provider", "") or "").strip()
    if planner_model:
        planner_cfg: dict[str, Any] = {"model": planner_model}
        if planner_provider:
            planner_cfg["environment"] = planner_provider
        agent_config["swarm_planner"] = planner_cfg
    else:
        agent_config.pop("swarm_planner", None)


def _try_quick_project_scan(root: Path) -> None:
    _scan_project_new(root)


def _load_at_mentioned_files(user_prompt: str, workspace_root_str: str) -> str:
    return _load_at_mentions_new(user_prompt, workspace_root_str)


def prepare_workspace(
    user_prompt: str,
    workspace_root: Optional[str],
    workspace_write: bool,
    project_context_file: Optional[str] = None,
    agent_config: Optional[dict[str, Any]] = None,
    at_mention_source_prompt: Optional[str] = None,
) -> tuple[str, Optional[Path], dict[str, Any]]:
    mode = resolve_workspace_context_mode(agent_config)
    meta: dict[str, Any] = {
        "workspace_context_mode": mode,
        "user_task": user_prompt,
        "user_task_chars": len(user_prompt),
        "project_manifest": "",
        "workspace_snapshot": "",
        "workspace_section_title": "Workspace snapshot",
        "workspace_context_mcp_fallback": False,
    }
    manifest_text = ""

    root_for_paths: Optional[Path] = None
    if workspace_root and str(workspace_root).strip():
        if workspace_write and not workspace_write_allowed():
            raise ValueError(
                "workspace_write requires SWARM_ALLOW_WORKSPACE_WRITE=1 on the server"
            )
        root_for_paths = validate_workspace_root(Path(str(workspace_root).strip()))

    if project_context_file and str(project_context_file).strip():
        context_file_path = resolve_project_context_path(
            str(project_context_file).strip(),
            root_for_paths,
        )
        context_file_path = validate_readable_file(context_file_path)
        manifest_text = read_project_context_file(context_file_path)
        meta["project_context_file"] = str(context_file_path)
        meta["project_context_chars"] = len(manifest_text)

    meta["project_manifest"] = manifest_text

    if not manifest_text and root_for_paths:
        auto_ctx_path = root_for_paths / ".swarm" / "project-context.md"
        if not auto_ctx_path.is_file():
            _try_quick_project_scan(root_for_paths)
        if auto_ctx_path.is_file():
            try:
                manifest_text = auto_ctx_path.read_text(encoding="utf-8")
                meta["project_context_file"] = str(auto_ctx_path)
                meta["project_context_chars"] = len(manifest_text)
                meta["project_manifest"] = manifest_text
            except OSError as exc:
                logger.debug("prepare_workspace: could not read .swarm/project-context.md: %s", exc)

    if not root_for_paths:
        snap = ""
        section_title = "Workspace snapshot"
        if mode == WORKSPACE_CONTEXT_MODE_TOOLS_ONLY:
            snap = tools_only_workspace_placeholder("")
        effective = build_input_with_workspace(
            user_prompt,
            snap,
            manifest=manifest_text,
            workspace_section_title=section_title,
        )
        meta["workspace_snapshot"] = snap
        meta["workspace_snapshot_files"] = 0
        meta["workspace_snapshot_chars"] = len(snap)
        meta["workspace_section_title"] = section_title
        meta["assembled_input_chars"] = len(effective)
        return effective, None, meta

    nfiles: int
    if mode == WORKSPACE_CONTEXT_MODE_TOOLS_ONLY:
        snap = tools_only_workspace_placeholder(str(root_for_paths))
        nfiles = 0
        section_title = "Workspace snapshot"
    elif mode == WORKSPACE_CONTEXT_MODE_RETRIEVE:
        merged = apply_auto_mcp_to_agent_config(
            copy.deepcopy(agent_config or {}),
            workspace_root=str(root_for_paths),
        )
        _mcp_raw = merged.get("mcp")
        mcp: dict[str, Any] = _mcp_raw if isinstance(_mcp_raw, dict) else {}
        if mcp.get("servers"):
            snap = tools_only_workspace_placeholder(str(root_for_paths))
            nfiles = 0
            section_title = "Workspace snapshot"
            meta["workspace_context_mcp_fallback"] = False
        else:
            logger.warning(
                "workspace_context_mode=retrieve: MCP servers not available after auto-config — "
                "using path index only (set agent_config.mcp.servers or enable npx for SWARM_MCP_AUTO). "
                "workspace_root=%s",
                root_for_paths,
            )
            idx_stats: dict = {}
            snap, nfiles = collect_workspace_file_index(root_for_paths, stats_out=idx_stats)
            section_title = "Workspace index"
            meta["workspace_context_mcp_fallback"] = True
            meta["workspace_index_stats"] = idx_stats
    elif mode == WORKSPACE_CONTEXT_MODE_INDEX_ONLY:
        idx_stats = {}
        snap, nfiles = collect_workspace_file_index(root_for_paths, stats_out=idx_stats)
        section_title = "Workspace index"
        meta["workspace_index_stats"] = idx_stats
    elif mode == WORKSPACE_CONTEXT_MODE_PRIORITY_PATHS:
        snap, nfiles = collect_workspace_priority_snapshot(root_for_paths)
        section_title = "Workspace snapshot"
    elif mode in (
        WORKSPACE_CONTEXT_MODE_POST_ANALYSIS_COMPACT,
    ):
        snap, nfiles = collect_workspace_snapshot(root_for_paths)
        section_title = "Workspace snapshot"
    else:
        snap, nfiles = collect_workspace_snapshot(root_for_paths)
        section_title = "Workspace snapshot"

    meta["workspace_root_resolved"] = str(root_for_paths)
    meta["workspace_snapshot_files"] = nfiles
    meta["workspace_snapshot_chars"] = len(snap)
    meta["workspace_snapshot"] = snap
    meta["workspace_section_title"] = section_title
    effective = build_input_with_workspace(
        user_prompt,
        snap,
        manifest=manifest_text,
        workspace_section_title=section_title,
    )
    meta["assembled_input_chars"] = len(effective)
    at_prompt = at_mention_source_prompt if at_mention_source_prompt is not None else user_prompt
    at_block = _load_at_mentioned_files(at_prompt, workspace_root or "")
    if at_block:
        effective = at_block + "\n\n---\n\n" + effective
    return effective, root_for_paths, meta


def pipeline_workspace_parts_from_meta(meta_ws: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_task": str(meta_ws.get("user_task") or ""),
        "raw_user_task": str(meta_ws.get("raw_user_task") or ""),
        "security_rewrite_output": str(meta_ws.get("security_rewrite_output") or ""),
        "security_rewrite_model": str(meta_ws.get("security_rewrite_model") or ""),
        "security_rewrite_provider": str(meta_ws.get("security_rewrite_provider") or ""),
        "project_manifest": str(meta_ws.get("project_manifest") or ""),
        "workspace_snapshot": str(meta_ws.get("workspace_snapshot") or ""),
        "workspace_context_mode": str(
            meta_ws.get("workspace_context_mode") or WORKSPACE_CONTEXT_MODE_DEFAULT
        ),
        "workspace_section_title": str(meta_ws.get("workspace_section_title") or "Workspace snapshot"),
        "workspace_context_mcp_fallback": bool(meta_ws.get("workspace_context_mcp_fallback")),
    }


def start_pipeline_run(
    *,
    user_prompt: str,
    effective_prompt: str,
    agent_config: dict[str, Any],
    steps: Optional[list[str]],
    workspace_root_str: str,
    workspace_apply_writes: bool,
    workspace_path: Optional[Path],
    workspace_meta: dict[str, Any],
    task_id: str,
    task_store: Any,
    artifacts_root: Path,
    pipeline_snapshot_for_disk: Any,
    workspace_followup_lines: Any = None,
) -> dict[str, Any]:
    warnings.warn(
        "start_pipeline_run() is deprecated. "
        "Use StartPipelineRunUseCase from "
        "backend.App.orchestration.application.use_cases.start_pipeline_run instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    from backend.App.integrations.infrastructure.observability.logging_config import set_task_id

    set_task_id(task_id)
    task_dir = artifacts_root / task_id
    agents_dir = task_dir / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)

    try:
        result = run_pipeline(
            effective_prompt,
            agent_config,
            steps,
            workspace_root_str,
            workspace_apply_writes,
            task_id,
            pipeline_workspace_parts=pipeline_workspace_parts_from_meta(workspace_meta),
            pipeline_step_ids=steps,
        )
    except HumanApprovalRequired as exc:
        task_store.update_task(
            task_id,
            status="awaiting_human",
            agent="orchestrator",
            message=str(exc)[:2000],
        )
        ns_snap: dict[str, Any] = {
            "user_prompt": user_prompt,
            "input": effective_prompt,
            "agent_config": agent_config,
            "pipeline_steps": steps,
            "workspace": workspace_meta,
            "human_approval_step": exc.step,
            "error": str(exc),
            "partial_state": exc.partial_state,
            "resume_from_step": exc.resume_pipeline_step,
        }
        try:
            (task_dir / "pipeline.json").write_text(
                json.dumps(
                    pipeline_snapshot_for_disk(ns_snap),
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError as ose:
            logger.warning("Could not write pipeline.json on HumanApprovalRequired: %s", ose)
        return {
            "status": "awaiting_human",
            "task_id": task_id,
            "human_approval_step": exc.step,
            "error": str(exc),
            "partial_state": exc.partial_state,
            "resume_from_step": exc.resume_pipeline_step,
        }
    except Exception as exc:
        task_store.update_task(
            task_id,
            status="failed",
            agent="orchestrator",
            message=str(exc)[:2000],
        )
        return {
            "status": "failed",
            "task_id": task_id,
            "error": str(exc),
            "exc_type": exc.__class__.__name__,
        }

    final_text = final_pipeline_user_message(result, steps)
    last_agent = task_store_agent_label(result, steps)
    task_store.update_task(
        task_id,
        status="completed",
        message=final_text[:4000],
        agent=last_agent,
    )

    for agent_key, out_key in ARTIFACT_AGENT_OUTPUT_KEYS:
        content = result.get(out_key)
        if isinstance(content, str):
            (agents_dir / f"{agent_key}.txt").write_text(content, encoding="utf-8")
    for out_key, content in result.items():
        if (
            isinstance(out_key, str)
            and out_key.startswith("crole_")
            and out_key.endswith("_output")
            and isinstance(content, str)
        ):
            stem = out_key[: -len("_output")]
            (agents_dir / f"{stem}.txt").write_text(content, encoding="utf-8")

    snapshot = dict(result)
    snapshot["pipeline_steps"] = steps
    snapshot["user_prompt"] = user_prompt
    snapshot["workspace"] = workspace_meta
    if workspace_path and workspace_apply_writes and workspace_write_allowed():
        run_sh = run_shell_after_user_approval(
            task_id,
            snapshot,
            task_store,
            cancel_event=None,
            skip_all_shell=False,
        )
        snapshot["workspace_writes"] = apply_from_devops_and_dev_outputs(
            snapshot,
            workspace_path,
            run_shell=run_sh,
        )
        workspace_writes_raw = snapshot.get("workspace_writes") or {}
        workspace_writes_result: dict[str, Any] = workspace_writes_raw if isinstance(workspace_writes_raw, dict) else {}
        require_writes = get_setting_bool(
            "swarm.require_dev_writes",
            workspace_root=workspace_path,
            env_key="SWARM_REQUIRE_DEV_WRITES",
            default=True,
        )
        mcp_write_count = snapshot.get("dev_mcp_write_count", 0)
        zero_writes = (
            not workspace_writes_result.get("written")
            and not workspace_writes_result.get("patched")
            and not workspace_writes_result.get("udiff_applied")
            and workspace_writes_result.get("parsed", 0) == 0
            and mcp_write_count == 0
        )
        if require_writes and zero_writes:
            error_message = (
                "Dev step produced 0 workspace writes with apply_writes=True. "
                "Models must use <swarm_file> tags or workspace__write_file tool calls."
            )
            logger.error("pipeline: %s", error_message)
            snapshot["_ec1_zero_writes"] = True
            snapshot["_ec1_error"] = error_message

    try:
        (task_dir / "pipeline.json").write_text(
            json.dumps(
                pipeline_snapshot_for_disk(snapshot),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError as ose:
        logger.warning("Could not write pipeline.json: %s", ose)

    append_task_run_log(task_dir, "non-stream pipeline completed")
    _followup_fn = workspace_followup_lines
    if _followup_fn is None:
        from backend.App.workspace.application.use_cases.apply_pipeline_writes import (
            workspace_followup_lines as _default_followup,
        )
        _followup_fn = _default_followup
    for wl in _followup_fn(workspace_path, workspace_apply_writes, snapshot):
        append_task_run_log(task_dir, wl.strip())

    _final_status = "completed"
    if snapshot.get("_ec1_zero_writes"):
        _final_status = "completed_no_writes"
    return {
        "status": _final_status,
        "task_id": task_id,
        "final_text": final_text,
        "last_agent": last_agent,
        **({"error": snapshot["_ec1_error"]} if snapshot.get("_ec1_error") else {}),
    }
