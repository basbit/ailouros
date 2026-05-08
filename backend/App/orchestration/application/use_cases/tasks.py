
from __future__ import annotations

import copy
import json
import logging
import os
import warnings
from pathlib import Path
from typing import Any, Optional

from backend.App.shared.application.settings_resolver import get_setting_bool
from backend.App.orchestration.domain.exceptions import HumanApprovalRequired
from backend.App.integrations.infrastructure.mcp.auto.auto import apply_auto_mcp_to_agent_config
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
from backend.App.integrations.infrastructure.agent_registry import merge_agent_config
from backend.App.integrations.infrastructure.pipeline_presets import resolve_preset
from backend.App.orchestration.application.scenarios.resolution import ResolvedScenario
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
    "resolve_chat_request_full",
]

logger = logging.getLogger(__name__)


def _resolve_chat_request(req: Any) -> ChatRequest:
    return ChatRequestResolver(
        merge_config=merge_agent_config,
        load_preset=resolve_preset,
    ).resolve(req)


def resolve_chat_request_full(
    req: Any,
) -> tuple[dict[str, Any], Optional[list[str]], Optional[ResolvedScenario]]:
    chat_req = _resolve_chat_request(req)
    _apply_global_automation_settings(chat_req.agent_config)
    return chat_req.agent_config, chat_req.pipeline_steps, chat_req.resolved_scenario


def resolve_chat_request(req: Any) -> tuple[dict[str, Any], Optional[list[str]]]:
    agent_config, pipeline_steps, _resolved_scenario = resolve_chat_request_full(req)
    return agent_config, pipeline_steps


def _apply_global_automation_settings(agent_config: dict[str, Any]) -> None:
    from backend.App.orchestration.application.use_cases._tasks_global_settings import (
        apply_global_automation_settings,
    )
    apply_global_automation_settings(agent_config)


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
            if os.getenv("AILOUROS_DESKTOP", "").strip() == "1":
                message = (
                    "Workspace writes are blocked. The desktop runtime should "
                    "set SWARM_ALLOW_WORKSPACE_WRITE=1 automatically; if you see "
                    "this, restart the app or check Settings → Capabilities."
                )
            else:
                message = (
                    "workspace_write requires SWARM_ALLOW_WORKSPACE_WRITE=1 on the server"
                )
            raise ValueError(message)
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
    resolved_scenario: Optional[ResolvedScenario] = None,
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
        if resolved_scenario is not None:
            ns_snap["scenario_id"] = resolved_scenario.scenario_id
            ns_snap["scenario_title"] = resolved_scenario.scenario_title
            ns_snap["scenario_category"] = resolved_scenario.scenario_category
            ns_snap["scenario_warnings"] = resolved_scenario.warnings
            ns_snap["scenario_expected_artifacts"] = list(
                resolved_scenario.expected_artifacts
            )
            ns_snap["scenario_quality_checks"] = [
                check.to_dict() for check in resolved_scenario.quality_checks
            ]
            ns_snap["scenario_skipped_gates"] = list(resolved_scenario.skipped_gates)
            ns_snap["scenario_model_profile_applied"] = dict(
                resolved_scenario.model_profile_applied
            )
            from backend.App.orchestration.application.scenarios.artifact_check import (
                check_scenario_artifacts,
                summarize_artifact_status,
            )
            from backend.App.orchestration.application.scenarios.quality_check_runner import (
                run_quality_checks,
                summarize_quality_results,
            )
            ns_status = check_scenario_artifacts(
                resolved_scenario.expected_artifacts, task_dir
            )
            ns_snap["scenario_artifact_status"] = [
                entry.to_dict() for entry in ns_status
            ]
            ns_snap["scenario_artifact_summary"] = summarize_artifact_status(ns_status)
            ns_quality_results = run_quality_checks(
                resolved_scenario.quality_checks,
                task_dir,
                ns_status,
                list(resolved_scenario.warnings),
                pipeline_steps=list(resolved_scenario.pipeline_steps),
            )
            ns_snap["scenario_quality_check_results"] = [
                result.to_dict() for result in ns_quality_results
            ]
            ns_snap["scenario_quality_check_summary"] = summarize_quality_results(
                ns_quality_results
            )
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
    if resolved_scenario is not None:
        snapshot["scenario_id"] = resolved_scenario.scenario_id
        snapshot["scenario_title"] = resolved_scenario.scenario_title
        snapshot["scenario_category"] = resolved_scenario.scenario_category
        snapshot["scenario_warnings"] = resolved_scenario.warnings
        snapshot["scenario_expected_artifacts"] = list(
            resolved_scenario.expected_artifacts
        )
        snapshot["scenario_quality_checks"] = [
            check.to_dict() for check in resolved_scenario.quality_checks
        ]
        snapshot["scenario_skipped_gates"] = list(resolved_scenario.skipped_gates)
        snapshot["scenario_model_profile_applied"] = dict(
            resolved_scenario.model_profile_applied
        )
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

    if workspace_path and workspace_apply_writes:
        from backend.App.orchestration.application.enforcement.secret_detector import (
            scan_paths as _scan_for_secrets,
            summarize as _summarize_secrets,
        )
        from backend.App.orchestration.application.enforcement.verification_honesty import (
            classify_verification as _classify_verification,
        )
        workspace_writes_for_secret_raw = snapshot.get("workspace_writes")
        workspace_writes_for_secret: dict[str, Any] = (
            workspace_writes_for_secret_raw
            if isinstance(workspace_writes_for_secret_raw, dict)
            else {}
        )
        secret_paths = sorted(set(
            list(workspace_writes_for_secret.get("written") or [])
            + list(workspace_writes_for_secret.get("patched") or [])
            + list(workspace_writes_for_secret.get("udiff_applied") or [])
        ))
        secret_findings = _scan_for_secrets(workspace_path, secret_paths)
        if secret_findings:
            snapshot["secret_findings"] = [
                finding.to_dict() for finding in secret_findings
            ]
            snapshot["secret_summary"] = _summarize_secrets(secret_findings)
        verification_verdict = _classify_verification(snapshot)
        snapshot["verification_verdict"] = verification_verdict.to_dict()

    if resolved_scenario is not None:
        from backend.App.orchestration.application.scenarios.artifact_check import (
            check_scenario_artifacts,
            summarize_artifact_status,
        )
        from backend.App.orchestration.application.scenarios.quality_check_runner import (
            run_quality_checks,
            summarize_quality_results,
        )
        artifact_status = check_scenario_artifacts(
            resolved_scenario.expected_artifacts, task_dir
        )
        snapshot["scenario_artifact_status"] = [
            entry.to_dict() for entry in artifact_status
        ]
        snapshot["scenario_artifact_summary"] = summarize_artifact_status(artifact_status)
        quality_results = run_quality_checks(
            resolved_scenario.quality_checks,
            task_dir,
            artifact_status,
            list(resolved_scenario.warnings),
            pipeline_steps=list(resolved_scenario.pipeline_steps),
        )
        snapshot["scenario_quality_check_results"] = [
            result.to_dict() for result in quality_results
        ]
        snapshot["scenario_quality_check_summary"] = summarize_quality_results(
            quality_results
        )

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

    require_writes_block = get_setting_bool(
        "swarm.require_dev_writes",
        workspace_root=workspace_path,
        env_key="SWARM_REQUIRE_DEV_WRITES",
        default=True,
    )
    require_trusted_gates_pass = get_setting_bool(
        "swarm.require_trusted_gates_pass",
        workspace_root=workspace_path,
        env_key="SWARM_REQUIRE_TRUSTED_GATES_PASS",
        default=True,
    )
    failed_trusted_gates_raw = snapshot.get("_failed_trusted_gates") or []
    failed_trusted_gates: list[str] = (
        [str(item) for item in failed_trusted_gates_raw]
        if isinstance(failed_trusted_gates_raw, list)
        else []
    )
    failed_trusted_gates_summary = str(
        snapshot.get("_failed_trusted_gates_summary") or ""
    )

    _final_status = "completed"
    _final_error: Optional[str] = None
    if snapshot.get("_ec1_zero_writes"):
        if require_writes_block:
            _final_status = "failed"
            ec1_error = snapshot.get("_ec1_error")
            _final_error = (
                str(ec1_error)
                if isinstance(ec1_error, str) and ec1_error.strip()
                else "Dev step produced 0 workspace writes with apply_writes=True."
            )
        else:
            _final_status = "completed_no_writes"
    if (
        _final_status == "completed"
        and require_trusted_gates_pass
        and failed_trusted_gates
    ):
        _final_status = "failed"
        _final_error = (
            "Trusted verification gates failed: "
            f"{failed_trusted_gates_summary or ', '.join(failed_trusted_gates)}"
        )
    if _final_status == "failed":
        task_store.update_task(
            task_id,
            status="failed",
            agent="orchestrator",
            message=(_final_error or "")[:2000],
        )
    elif _final_status == "completed_no_writes":
        task_store.update_task(task_id, status="completed_no_writes")
    return {
        "status": _final_status,
        "task_id": task_id,
        "final_text": final_text,
        "last_agent": last_agent,
        **({"error": _final_error} if _final_error else {}),
    }
