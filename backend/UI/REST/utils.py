"""Utility functions for the UI/REST layer.

Non-route, non-stream, non-model helpers used by route handlers and
stream generators.
"""

from __future__ import annotations

import copy
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SSE / response helpers
# ---------------------------------------------------------------------------

def _openai_nonstream_response(content: str, request_model: str) -> dict[str, Any]:
    """Build an OpenAI-compatible non-stream chat completion response dict."""
    now = int(time.time())
    return {
        "id": f"chatcmpl-{now}",
        "object": "chat.completion",
        "created": now,
        "model": request_model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _extract_user_prompt(messages: list[Any]) -> str:
    """Extract the last user message content from a list of ChatMessage objects."""
    for msg in reversed(messages):
        if msg.role == "user":
            return msg.content
    return messages[-1].content if messages else ""


# ---------------------------------------------------------------------------
# Pipeline snapshot helpers
# ---------------------------------------------------------------------------

def _redact_agent_config_secrets(cfg: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Deep-copy cfg and redact api_key fields."""
    if not cfg:
        return {}
    out = copy.deepcopy(cfg)
    cloud = out.get("cloud")
    if isinstance(cloud, dict) and cloud.get("api_key"):
        cloud["api_key"] = "***REDACTED***"
    ra = out.get("remote_api")
    if isinstance(ra, dict) and ra.get("api_key"):
        ra["api_key"] = "***REDACTED***"
    rap = out.get("remote_api_profiles")
    if isinstance(rap, dict):
        red_p: dict[str, Any] = {}
        for pk, prof in rap.items():
            if isinstance(prof, dict):
                pvc = copy.deepcopy(prof)
                if pvc.get("api_key"):
                    pvc["api_key"] = "***REDACTED***"
                red_p[str(pk)] = pvc
            else:
                red_p[str(pk)] = prof
        out["remote_api_profiles"] = red_p
    return out


def _pipeline_snapshot_for_disk(snap: dict[str, Any]) -> dict[str, Any]:
    """Snapshot for pipeline.json: redact api_key in agent_config and partial_state.

    Both the top-level ``agent_config`` and ``partial_state.agent_config`` are redacted
    so that API keys are never written to disk.  For human-resume the client must re-supply
    ``agent_config`` in the ``POST /v1/tasks/{id}/human-resume`` request body.
    """
    out = copy.deepcopy(snap)
    ac = out.get("agent_config")
    if isinstance(ac, dict):
        out["agent_config"] = _redact_agent_config_secrets(ac)
    ps = out.get("partial_state")
    if isinstance(ps, dict):
        ps_ac = ps.get("agent_config")
        if isinstance(ps_ac, dict):
            ps["agent_config"] = _redact_agent_config_secrets(ps_ac)
    return out


# ---------------------------------------------------------------------------
# Workspace followup helpers
# ---------------------------------------------------------------------------

def _stream_incremental_workspace_enabled() -> bool:
    """After dev/devops immediately write files/patches to disk (shell — only in final pass)."""
    v = os.getenv("SWARM_STREAM_INCREMENTAL_WORKSPACE", "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _workspace_followup_lines(
    workspace_path: Optional[Path],
    workspace_apply_writes: bool,
    pipeline_snapshot: dict[str, Any],
) -> list[str]:
    """Messages for SSE and pipeline_run.log after a run (why files were not written)."""
    from backend.App.workspace.infrastructure.workspace_io import workspace_write_allowed
    from backend.App.workspace.infrastructure.patch_parser import any_snapshot_output_has_swarm

    lines: list[str] = []
    if not workspace_path:
        lines.append(
            "[orchestrator] workspace writes: skipped (workspace_root not set in request)\n"
        )
        return lines
    if not workspace_apply_writes:
        lines.append(
            "[orchestrator] workspace writes: skipped "
            "(workspace_write=false — enable checkbox in UI)\n"
        )
        return lines
    if not workspace_write_allowed():
        lines.append(
            "[orchestrator] workspace writes: skipped "
            "(set SWARM_ALLOW_WORKSPACE_WRITE=1 on orchestrator)\n"
        )
        return lines
    w = pipeline_snapshot.get("workspace_writes") or {}
    written = w.get("written") or []
    errs = w.get("errors") or []
    note = str(w.get("note") or "")
    lines.append(
        f"[orchestrator] workspace writes: files_written={len(written)} "
        f"errors={len(errs)} note={note!r}\n"
    )
    if errs:
        lines.append(f"[orchestrator] workspace write errors: {errs[:5]}\n")
    doc_paths = pipeline_snapshot.get("documentation_workspace_files")
    if isinstance(doc_paths, list) and doc_paths:
        lines.append(
            f"[orchestrator] generate_documentation → workspace files: {doc_paths}\n"
        )
    if not any_snapshot_output_has_swarm(pipeline_snapshot) and not (
        isinstance(doc_paths, list) and doc_paths
    ):
        lines.append(
            "[orchestrator] hint: no <swarm_file>/<swarm_patch>/<swarm_shell>/"
            "<swarm-command>/<swarm_udiff> "
            "in any *_output (or dev_task_outputs/qa_task_outputs) — "
            "models must emit those tags for workspace writes; plain markdown only goes to "
            "artifacts (generate_documentation при workspace_write дополнительно пишет "
            "docs/swarm/*.md — см. README)\n"
        )
    return lines


# ---------------------------------------------------------------------------
# Artifact TTL cleanup
# ---------------------------------------------------------------------------

async def _cleanup_old_artifacts(artifacts_root: Path) -> None:
    """Delete artifact directories older than SWARM_ARTIFACT_TTL_DAYS (default 7)."""
    try:
        ttl_days = int(os.getenv("SWARM_ARTIFACT_TTL_DAYS", "7"))
    except ValueError:
        ttl_days = 7
    if ttl_days <= 0:
        return
    cutoff = time.time() - ttl_days * 86400
    removed = 0
    for entry in artifacts_root.iterdir():
        if not entry.is_dir():
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
        except OSError:
            pass
    if removed:
        logger.info("Cleaned up %d old artifact directories", removed)


# ---------------------------------------------------------------------------
# Startup warnings
# ---------------------------------------------------------------------------

def _warn_malformed_urls() -> None:
    """Warn at startup if critical URL env vars are clearly malformed."""
    from urllib.parse import urlparse
    for var in ("OPENAI_BASE_URL", "ANTHROPIC_BASE_URL", "LMSTUDIO_BASE_URL", "REDIS_URL"):
        val = os.getenv(var, "").strip()
        if not val:
            continue
        try:
            parsed = urlparse(val)
            if not parsed.scheme or not parsed.netloc:
                logger.warning(
                    "Env var %s=%r looks malformed (missing scheme or host). "
                    "Expected format: http://host:port/path",
                    var, val,
                )
        except Exception:
            logger.warning("Env var %s=%r could not be parsed as a URL.", var, val)


# ---------------------------------------------------------------------------
# Retry helpers
# ---------------------------------------------------------------------------

def _apply_retry_with(
    agent_config: dict[str, Any],
    partial_state: dict[str, Any],
    retry_with: Any,
) -> dict[str, Any]:
    """Apply retry_with overrides to agent_config / state; return updated agent_config."""
    import copy as _copy_mod
    ac = _copy_mod.deepcopy(agent_config)
    if retry_with.different_model:
        model = retry_with.different_model.strip()
        for role_cfg in ac.values():
            if isinstance(role_cfg, dict):
                role_cfg["model"] = model
    if retry_with.tools_off is True:
        for role_cfg in ac.values():
            if isinstance(role_cfg, dict):
                mcp = role_cfg.get("mcp")
                if isinstance(mcp, dict):
                    mcp["servers"] = []
    if retry_with.reduced_context:
        partial_state["workspace_context_mode"] = retry_with.reduced_context
    return ac


# ---------------------------------------------------------------------------
# Workspace + task preparation
# ---------------------------------------------------------------------------

def _chat_sync_prepare_workspace_and_task(
    user_prompt: str,
    workspace_root: Optional[str],
    workspace_write: bool,
    task_store: Any,
    project_context_file: Optional[str] = None,
    agent_config: Optional[dict[str, Any]] = None,
) -> tuple[str, Optional[Path], dict[str, Any], dict[str, Any]]:
    """Snapshot workspace + create_task in Redis — run only from worker thread (not event loop)."""
    from backend.App.orchestration.application.tasks import prepare_workspace
    effective_prompt, workspace_path, meta_ws = prepare_workspace(
        user_prompt,
        workspace_root,
        workspace_write,
        project_context_file,
        agent_config,
    )
    task = task_store.create_task(user_prompt)
    return effective_prompt, workspace_path, meta_ws, task
