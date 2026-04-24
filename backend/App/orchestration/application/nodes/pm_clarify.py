from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from backend.App.orchestration.infrastructure.agents.reviewer_agent import ReviewerAgent
from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState
from backend.App.orchestration.domain.quality_gate_policy import (
    CLARIFY_SIMPLE_ANSWER,
    CLARIFY_NEEDS_CLARIFICATION,
    CLARIFY_READY,
)
from backend.App.orchestration.application.nodes._shared import (
    _env_model_override,
    _llm_planning_agent_run,
    _make_human_agent,
    planning_mcp_tool_instruction,
    _remote_api_client_kwargs_for_role,
    _reviewer_cfg,
    planning_pipeline_user_context,
)

_log = logging.getLogger(__name__)
_CACHE_TTL_SECONDS = int(os.environ.get("SWARM_CLARIFY_CACHE_TTL_SEC", str(24 * 3600)))
_CLARIFY_CACHE_VERSION = "2026-04-09.v2"
_DEFAULT_CLARIFY_FRESHNESS_MARKERS = (
    "internet,web,website,websites,google,search,browse,latest,current,recent,"
    "найди,найти,поищи,поиск,интернет,сайт,сайты,актуальн,свеж"
)


def _clarify_freshness_markers() -> tuple[str, ...]:
    raw = os.environ.get("SWARM_CLARIFY_FRESHNESS_MARKERS")
    if raw is None:
        raw = _DEFAULT_CLARIFY_FRESHNESS_MARKERS
    return tuple(item.strip().lower() for item in raw.split(",") if item.strip())


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _clarify_cache_identity(state: PipelineState, task_text: str) -> dict[str, str]:
    workspace_root = str(state.get("workspace_root") or "").strip()
    project_manifest = str(state.get("project_manifest") or "")
    workspace_snapshot = str(state.get("workspace_snapshot") or "")
    return {
        "version": _CLARIFY_CACHE_VERSION,
        "task_hash": _sha256_text(task_text),
        "workspace_root": workspace_root,
        "project_manifest_hash": _sha256_text(project_manifest),
        "workspace_snapshot_hash": _sha256_text(workspace_snapshot),
    }


def _clarify_cache_key(identity: dict[str, str]) -> str:
    stable_payload = json.dumps(identity, ensure_ascii=False, sort_keys=True)
    return hashlib.md5(stable_payload.encode("utf-8", errors="replace")).hexdigest()[:16]


def _clarify_cache_dir() -> Path:
    from backend.App.paths import artifacts_root as _anchored_artifacts_root
    return _anchored_artifacts_root() / "cache"


def _clarify_requires_fresh_research(state: PipelineState, task_text: str) -> bool:
    task_lower = str(task_text or "").lower()
    markers = _clarify_freshness_markers()
    if markers and any(marker in task_lower for marker in markers):
        return True
    agent_config = state.get("agent_config") or {}
    mcp_cfg = agent_config.get("mcp")
    if not isinstance(mcp_cfg, dict):
        return False
    servers = mcp_cfg.get("servers")
    if not isinstance(servers, list):
        return False
    for server in servers:
        if not isinstance(server, dict):
            continue
        name = str(server.get("name") or "").strip().lower()
        command = str(server.get("command") or "").strip().lower()
        if "search" in name or "browser" in name:
            return True
        if "browser" in command:
            return True
    return False


def _load_clarify_cache(
    cache_key: str,
    identity: dict[str, str],
    force_rerun: bool,
) -> dict[str, Any] | None:
    if force_rerun:
        return None
    cache_file = _clarify_cache_dir() / f"{cache_key}.json"
    if not cache_file.is_file():
        return None
    try:
        age = time.time() - cache_file.stat().st_mtime
        if age > _CACHE_TTL_SECONDS:
            return None
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        cached_identity = data.get("identity")
        if not isinstance(cached_identity, dict):
            return None
        for key, expected in identity.items():
            if str(cached_identity.get(key) or "") != str(expected):
                return None
        output = str(data.get("output", ""))
        if not output.strip():
            return None
        return {
            "output": output,
            "identity": cached_identity,
            "cache_key": cache_key,
        }
    except (OSError, json.JSONDecodeError, KeyError):
        return None


def _save_clarify_cache(cache_key: str, identity: dict[str, str], output: str) -> None:
    cache_dir = _clarify_cache_dir()
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / f"{cache_key}.json"
        cache_file.write_text(
            json.dumps(
                {
                    "output": output,
                    "identity": identity,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        _log.warning("clarify_input cache write failed: %s", exc)


_CLARIFY_INPUT_PROMPT_PATH = str(
    Path(os.environ.get("SWARM_PROJECT_ROOT", "")).resolve() / "var" / "prompts" / "specialized" / "clarify-input.md"
    if os.environ.get("SWARM_PROJECT_ROOT")
    else Path(__file__).resolve().parents[5] / "var" / "prompts" / "specialized" / "clarify-input.md"
)

_CLARIFY_VALID_PREFIXES = (CLARIFY_READY, CLARIFY_NEEDS_CLARIFICATION, CLARIFY_SIMPLE_ANSWER)


def _clarify_has_valid_prefix(output: str) -> bool:
    stripped = (output or "").strip()
    return any(stripped.startswith(prefix) for prefix in _CLARIFY_VALID_PREFIXES)


def clarify_input_node(state: PipelineState) -> dict[str, Any]:
    plan_ctx = planning_pipeline_user_context(state)
    force_rerun = bool(
        (state.get("agent_config") or {}).get("swarm", {}).get("force_rerun", False)
    )
    task_text = str(state.get("input") or "")
    requires_fresh_research = _clarify_requires_fresh_research(state, task_text)
    cache_identity = _clarify_cache_identity(state, task_text)
    cache_key: str | None = _clarify_cache_key(cache_identity) if task_text.strip() else None

    cached = None
    if cache_key and not requires_fresh_research:
        cached = _load_clarify_cache(cache_key, cache_identity, force_rerun)
    if cached:
        cached_output = str(cached.get("output") or "")
        clarify_output = cached_output + "\n\n*(result from cache of previous run)*"
        state["clarify_input_output"] = clarify_output
        state["clarify_input_model"] = "cache"
        state["clarify_input_provider"] = "cache"
        state["clarify_input_cache"] = {
            "hit": True,
            "cache_key": cache_key or "",
            "identity": dict(cached.get("identity") or {}),
        }
        if clarify_output.strip().startswith(CLARIFY_SIMPLE_ANSWER):
            return {
                "clarify_input_output": clarify_output,
                "clarify_input_model": "cache",
                "clarify_input_provider": "cache",
                "clarify_input_cache": state["clarify_input_cache"],
                "_pipeline_stop_early": True,
            }
        _cached_stripped = cached_output.strip()
        _has_needs_clarification_prefix = _cached_stripped.startswith(CLARIFY_NEEDS_CLARIFICATION)
        _has_questions_without_prefix = (
            not _has_needs_clarification_prefix
            and not _cached_stripped.startswith(CLARIFY_READY)
            and not _cached_stripped.startswith(CLARIFY_SIMPLE_ANSWER)
            and "?" in _cached_stripped
            and len(_cached_stripped) >= 50
        )
        if _has_needs_clarification_prefix or _has_questions_without_prefix:
            if _has_needs_clarification_prefix:
                _cached_body = _cached_stripped[len(CLARIFY_NEEDS_CLARIFICATION):].strip()
            else:
                _cached_body = _cached_stripped
                _log.warning(
                    "clarify_input: cached output contains questions but lacks "
                    "NEEDS_CLARIFICATION prefix — treating as clarification request."
                )
            if len(_cached_body) >= 50 and "?" in _cached_body:
                from backend.App.orchestration.domain.exceptions import HumanApprovalRequired
                raise HumanApprovalRequired(
                    step="clarify_input",
                    detail=clarify_output,
                    resume_pipeline_step="human_clarify_input",
                    partial_state={"clarify_input_output": clarify_output},
                )
            _log.warning(
                "clarify_input: cached NEEDS_CLARIFICATION has no real questions — "
                "invalidating cache and proceeding as READY."
            )
            clarify_output = CLARIFY_READY + "\n" + cached_output
            state["clarify_input_output"] = clarify_output
        return {
            "clarify_input_output": clarify_output,
            "clarify_input_model": "cache",
            "clarify_input_provider": "cache",
            "clarify_input_cache": state["clarify_input_cache"],
        }
    state["clarify_input_cache"] = {
        "hit": False,
        "cache_key": cache_key or "",
        "identity": cache_identity,
    }
    if requires_fresh_research:
        state["clarify_input_cache"]["reuse_blocked_reason"] = "fresh_external_research_required"

    mcp_hint = planning_mcp_tool_instruction(state)
    prompt = (
        mcp_hint
        + "Business requirement from the user:\n\n"
        f"{plan_ctx}\n\n"
        "Analyze the requirement and produce your output in the format described in your system prompt."
    )
    reviewer_cfg = _reviewer_cfg(state)
    _clarify_max_tokens = int(os.getenv("SWARM_CLARIFY_MAX_OUTPUT_TOKENS", "1000").strip() or "1000")
    agent = ReviewerAgent(
        system_prompt_path_override=_CLARIFY_INPUT_PROMPT_PATH,
        model_override=_env_model_override("SWARM_CLARIFY_MODEL", reviewer_cfg.get("model"), reviewer_cfg.get("_planner_capability")),
        environment_override=reviewer_cfg.get("environment"),
        max_output_tokens=_clarify_max_tokens,
        **_remote_api_client_kwargs_for_role(state, reviewer_cfg),
    )
    _clarify_disable_tools = os.getenv("SWARM_CLARIFY_DISABLE_TOOLS", "0").strip() in ("1", "true", "yes", "on")
    clarify_output, _, _ = _llm_planning_agent_run(agent, prompt, state, disable_tools=_clarify_disable_tools)

    if not _clarify_has_valid_prefix(clarify_output):
        _log.warning(
            "clarify_input: output lacks required routing prefix (response_rejected_by_format_gate). "
            "len=%d. Sending repair-prompt.",
            len(clarify_output or ""),
        )
        _repair_prompt = (
            prompt
            + "\n\n[CRITICAL] Your previous response did not start with the required prefix. "
            "You MUST begin your response with exactly one of:\n"
            f"  {CLARIFY_READY}\n"
            f"  {CLARIFY_NEEDS_CLARIFICATION}\n"
            f"  {CLARIFY_SIMPLE_ANSWER}\n"
            "No preamble, no plan, no architecture. Only the routing prefix followed by your "
            "brief content (max 800 chars total)."
        )
        clarify_output, _, _ = _llm_planning_agent_run(agent, _repair_prompt, state)
        if not _clarify_has_valid_prefix(clarify_output):
            _log.warning(
                "clarify_input: STILL no valid prefix after repair — forcing READY prefix "
                "(treating as unambiguous task)."
            )
            clarify_output = CLARIFY_READY + "\n" + clarify_output

    state["clarify_input_output"] = clarify_output
    state["clarify_input_model"] = agent.used_model
    state["clarify_input_provider"] = agent.used_provider

    if clarify_output.strip().startswith(CLARIFY_SIMPLE_ANSWER):
        _simple_body = clarify_output.strip()[len(CLARIFY_SIMPLE_ANSWER):].strip()
        if len(_simple_body) > 500:
            _log.warning(
                "clarify_input: SIMPLE_ANSWER returned but content is %d chars "
                "(>500) — likely a misrouted plan. Treating as READY.",
                len(_simple_body),
            )
            clarify_output = CLARIFY_READY + "\n" + _simple_body
            state["clarify_input_output"] = clarify_output
        else:
            if cache_key is not None:
                _save_clarify_cache(cache_key, cache_identity, clarify_output)
            return {
                "clarify_input_output": clarify_output,
                "clarify_input_model": agent.used_model,
                "clarify_input_provider": agent.used_provider,
                "clarify_input_cache": state["clarify_input_cache"],
                "_pipeline_stop_early": True,
            }

    if clarify_output.strip().startswith(CLARIFY_NEEDS_CLARIFICATION):
        _clarify_body = clarify_output.strip()[len(CLARIFY_NEEDS_CLARIFICATION):].strip()
        if len(_clarify_body) < 50 or "?" not in _clarify_body:
            _log.warning(
                "clarify_input: NEEDS_CLARIFICATION returned but content has no real questions "
                "(%d chars, '?' present: %s). Retrying with explicit instruction.",
                len(_clarify_body), "?" in _clarify_body,
            )
            _retry_q_prompt = (
                prompt
                + "\n\n[CRITICAL] You returned NEEDS_CLARIFICATION but did not include any actual questions. "
                "Either list specific numbered questions the user must answer (each ending with '?'), "
                "or if the task is actually clear enough, respond with READY instead."
            )
            clarify_output, _, _ = _llm_planning_agent_run(agent, _retry_q_prompt, state)
            state["clarify_input_output"] = clarify_output
            if clarify_output.strip().startswith(CLARIFY_NEEDS_CLARIFICATION):
                _retry_body = clarify_output.strip()[len(CLARIFY_NEEDS_CLARIFICATION):].strip()
                if len(_retry_body) < 50 or "?" not in _retry_body:
                    _log.warning(
                        "clarify_input: retry still has no questions — forcing READY to avoid blocking."
                    )
                    clarify_output = CLARIFY_READY + "\n" + clarify_output
                    state["clarify_input_output"] = clarify_output
        if clarify_output.strip().startswith(CLARIFY_NEEDS_CLARIFICATION):
            if cache_key is not None:
                _save_clarify_cache(cache_key, cache_identity, clarify_output)
            from backend.App.orchestration.domain.exceptions import HumanApprovalRequired
            raise HumanApprovalRequired(
                step="clarify_input",
                detail=clarify_output,
                resume_pipeline_step="human_clarify_input",
                partial_state={"clarify_input_output": clarify_output},
            )

    if cache_key is not None:
        _save_clarify_cache(cache_key, cache_identity, clarify_output)
    return {
        "clarify_input_output": clarify_output,
        "clarify_input_model": agent.used_model,
        "clarify_input_provider": agent.used_provider,
        "clarify_input_cache": state["clarify_input_cache"],
    }


def human_clarify_input_node(state: PipelineState) -> dict[str, Any]:
    questions = (state.get("clarify_input_output") or "").strip()
    if questions.startswith(CLARIFY_READY):
        return {
            "clarify_input_human_output": "[human:clarify_input] Input confirmed ready (no questions).",
            "human_clarify_status": "skipped_by_router",
        }
    bundle = (
        f"Business requirement:\n{planning_pipeline_user_context(state)}\n\n"
        f"Clarifying questions from the system:\n{questions}"
    )
    agent = _make_human_agent(state, "clarify_input")
    result = agent.run(bundle)
    if not (result or "").strip():
        _log.error(
            "human_clarify_input: human agent returned empty output "
            "(human_clarify_status=empty_unexpected) — stopping pipeline. "
            "The clarification step required a user response but received none."
        )
        return {
            "clarify_input_human_output": "",
            "human_clarify_status": "empty_unexpected",
            "_pipeline_stop_early": True,
            "_pipeline_stop_reason": (
                "human_clarify_input: empty response from human agent "
                "(NEEDS_CLARIFICATION was raised but no user answer received). "
                "Re-submit the task with explicit clarifications or mark it READY."
            ),
        }
    return {
        "clarify_input_human_output": result,
        "human_clarify_status": "answered_by_user",
    }
