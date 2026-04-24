from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Optional

from backend.App.shared.application.retry_config import (
    apply_retry_with_to_agent_config as _apply_retry_with,
)
from backend.App.shared.application.settings_resolver import get_setting_int

__all__ = ["_apply_retry_with"]

logger = logging.getLogger(__name__)


def _openai_nonstream_response(content: str, request_model: str) -> dict[str, Any]:
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
    for msg in reversed(messages):
        if msg.role == "user":
            return msg.content
    return messages[-1].content if messages else ""


async def _cleanup_old_artifacts(artifacts_root: Path) -> None:
    ttl_days = get_setting_int(
        "artifacts.ttl_days",
        env_key="SWARM_ARTIFACT_TTL_DAYS",
        default=7,
    )
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


def _warn_malformed_urls() -> None:
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


def _chat_sync_prepare_workspace_and_task(
    user_prompt: str,
    workspace_root: Optional[str],
    workspace_write: bool,
    task_store: Any,
    project_context_file: Optional[str] = None,
    agent_config: Optional[dict[str, Any]] = None,
) -> tuple[str, Optional[Path], dict[str, Any], dict[str, Any]]:
    from backend.App.orchestration.application.use_cases.tasks import prepare_workspace
    from backend.App.orchestration.application.enforcement.ingress_security import rewrite_untrusted_input

    rewrite = rewrite_untrusted_input(
        user_prompt,
        agent_config,
        source="chat_user_prompt",
    )
    effective_prompt, workspace_path, meta_ws = prepare_workspace(
        rewrite.safe_text,
        workspace_root,
        workspace_write,
        project_context_file,
        agent_config,
        at_mention_source_prompt=user_prompt,
    )
    meta_ws["raw_user_task"] = user_prompt
    meta_ws["security_rewrite_output"] = rewrite.safe_text
    meta_ws["security_rewrite_model"] = rewrite.model
    meta_ws["security_rewrite_provider"] = rewrite.provider
    meta_ws["security_rewrite_flags"] = list(rewrite.security_flags)
    meta_ws["security_rewrite_risk_level"] = rewrite.risk_level
    meta_ws["security_rewrite_dropped_text_summary"] = rewrite.dropped_text_summary
    meta_ws["security_rewrite_used_fallback"] = rewrite.used_fallback
    task = task_store.create_task(user_prompt)
    return effective_prompt, workspace_path, meta_ws, task
