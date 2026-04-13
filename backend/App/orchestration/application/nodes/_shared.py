"""Shared helper functions for pipeline node modules.

These functions are extracted from pipeline.graph to allow node files
to import them without circular dependencies.

Sub-modules:
- _prompt_builders.py  — build_*_context, planning helpers, MCP tool helpers
- _workspace_instructions.py — _dev_workspace_instructions, _qa_workspace_verification_instructions
"""
from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any, Optional

from backend.App.orchestration.infrastructure.agents.dev_agent import DevAgent
from backend.App.orchestration.infrastructure.agents.qa_agent import QAAgent
from backend.App.orchestration.infrastructure.agents.reviewer_agent import ReviewerAgent
from backend.App.integrations.infrastructure.skill_repository import format_role_skills_extra
from backend.App.integrations.infrastructure.documentation_links import format_documentation_links_block
from backend.App.orchestration.application.pipeline_state import PipelineState
from backend.App.orchestration.application.self_verify import (
    SelfVerifier,
    VerifyResult,
    run_with_self_verify,
)
from backend.App.orchestration.domain.agent_factory import AgentFactoryPort
from backend.App.orchestration.infrastructure.agent_factory import ConcreteAgentFactory

from backend.App.orchestration.application.nodes._prompt_builders import (
    _code_analysis_is_weak,
    _compact_code_analysis_for_prompt,
    _doc_chain_spec_max_chars,
    _doc_generate_second_pass_analysis_max_chars,
    _doc_spec_max_each_chars,
    _documentation_product_context_block,
    _effective_spec_block_for_doc_chain,
    _effective_spec_for_build,
    _llm_agent_run_with_optional_mcp,
    _spec_for_build_mcp_safe,
    _llm_planning_agent_run,
    _dev_sibling_tasks_block,
    _pipeline_context_block,
    _project_knowledge_block,
    _review_int_env,
    _should_compact_for_reviewer,
    _should_use_mcp_for_workspace,
    _spec_arch_context_for_docs,
    _swarm_block,
    _workspace_context_mode_normalized,
    build_compact_build_phase_user_context,
    build_phase_pipeline_user_context,
    embedded_pipeline_input_for_review,
    embedded_review_artifact,
    pipeline_user_task,
    planning_mcp_tool_instruction,
    planning_pipeline_user_context,
    should_use_compact_build_pipeline_input,
)
from backend.App.orchestration.application.nodes._workspace_instructions import (
    _bare_repo_scaffold_instruction,
    _dev_workspace_instructions,
    _path_hints_automated_tests,
    _qa_workspace_verification_instructions,
)
from backend.App.orchestration.application.agent_config_reader import (
    reviewer_cfg as _reviewer_cfg_new,
    remote_api_kwargs_for_role as _remote_api_kwargs_for_role_new,
    skills_extra as _skills_extra_new,
)
from backend.App.orchestration.application.stream_progress import emit_progress as _emit_progress

logger = logging.getLogger(__name__)

__all__ = [
    # Re-exported from _prompt_builders
    "_code_analysis_is_weak",
    "_compact_code_analysis_for_prompt",
    "_doc_chain_spec_max_chars",
    "_doc_generate_second_pass_analysis_max_chars",
    "_doc_spec_max_each_chars",
    "_documentation_product_context_block",
    "_effective_spec_block_for_doc_chain",
    "_effective_spec_for_build",
    "_llm_agent_run_with_optional_mcp",
    "_spec_for_build_mcp_safe",
    "_llm_planning_agent_run",
    "_dev_sibling_tasks_block",
    "_pipeline_context_block",
    "_project_knowledge_block",
    "_review_int_env",
    "_should_compact_for_reviewer",
    "_should_use_mcp_for_workspace",
    "_spec_arch_context_for_docs",
    "_swarm_block",
    "_workspace_context_mode_normalized",
    "build_compact_build_phase_user_context",
    "build_phase_pipeline_user_context",
    "embedded_pipeline_input_for_review",
    "embedded_review_artifact",
    "pipeline_user_task",
    "planning_mcp_tool_instruction",
    "planning_pipeline_user_context",
    "should_use_compact_build_pipeline_input",
    # Re-exported from _workspace_instructions
    "_bare_repo_scaffold_instruction",
    "_dev_workspace_instructions",
    "_path_hints_automated_tests",
    "_qa_workspace_verification_instructions",
    # Re-exported from agent_config_reader
    "_reviewer_cfg_new",
    "_remote_api_kwargs_for_role_new",
    "_skills_extra_new",
    # Re-exported from stream_progress
    "_emit_progress",
    # Re-exported from self_verify
    "SelfVerifier",
    "VerifyResult",
    "run_with_self_verify",
    # Own public API
    "_capability_model",
    "_cfg_model",
    "_env_model_override",
    "_pipeline_should_cancel",
    "_remote_api_client_kwargs",
    "_remote_api_client_kwargs_for_role",
    "_stream_progress_emit",
    "make_agent",
]


def _stream_progress_emit(state: Mapping[str, Any], message: str) -> None:
    """Queue for run_pipeline_stream: main generator thread reads between yields."""
    progress_queue = state.get("_stream_progress_queue")
    _emit_progress(progress_queue, message)


def _server_stream_shutdown_requested() -> bool:
    try:
        from backend.App.orchestration.infrastructure.stream_cancel import SERVER_STREAM_SHUTDOWN
    except Exception as exc:
        logger.debug("Could not import SERVER_STREAM_SHUTDOWN: %s", exc)
        return False
    return SERVER_STREAM_SHUTDOWN.is_set()


def _pipeline_should_cancel(state: Mapping[str, Any]) -> bool:
    if _server_stream_shutdown_requested():
        return True
    cancel_event = state.get("_pipeline_cancel_event")
    return bool(cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)())


def _reviewer_cfg(state: PipelineState) -> dict[str, Any]:
    return (state.get("agent_config") or {}).get("reviewer") or {}


def _human_cfg(state: PipelineState) -> dict[str, Any]:
    return (state.get("agent_config") or {}).get("human") or {}


def _make_human_agent(state: PipelineState, step: str) -> Any:
    """Create HumanAgent with task context for in-process blocking approval."""
    from backend.App.orchestration.infrastructure.agents.human_agent import HumanAgent
    task_id = state.get("task_id") or ""
    task_store_ref = None
    if task_id:
        try:
            from backend.UI.REST.task_instance import task_store as _ts
            task_store_ref = _ts
        except Exception:
            pass
    return HumanAgent(
        step=step,
        agent_config=_human_cfg(state),
        task_id=task_id if task_store_ref else None,
        task_store=task_store_ref,
    )


def _remote_api_client_kwargs(state: PipelineState) -> dict[str, Any]:
    """Remote API credentials: agent_config.remote_api; legacy cloud → anthropic."""
    agent_config = state.get("agent_config") or {}
    remote_api = agent_config.get("remote_api")
    legacy_cloud = agent_config.get("cloud")
    provider = ""
    api_key = ""
    base_url = ""
    if isinstance(remote_api, dict):
        provider = str(remote_api.get("provider") or "").strip().lower()
        api_key = str(remote_api.get("api_key") or "").strip()
        base_url = str(remote_api.get("base_url") or "").strip()
    if isinstance(legacy_cloud, dict):
        if not api_key:
            api_key = str(legacy_cloud.get("api_key") or "").strip()
        if not base_url:
            base_url = str(legacy_cloud.get("base_url") or "").strip()
        if not provider and (api_key or base_url):
            provider = "anthropic"
    if not provider and (api_key or base_url):
        provider = "anthropic"
    client_kwargs: dict[str, Any] = {}
    if provider:
        client_kwargs["remote_provider"] = provider
    if api_key:
        client_kwargs["remote_api_key"] = api_key
    if base_url:
        client_kwargs["remote_base_url"] = base_url
    return client_kwargs


def _remote_api_client_kwargs_for_role(
    state: PipelineState,
    role_cfg: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """remote_api + legacy cloud; optionally ``remote_profile`` — key in ``remote_api_profiles``."""
    base_kwargs = _remote_api_client_kwargs(state)
    if not isinstance(role_cfg, dict):
        return base_kwargs
    profile_name = str(
        role_cfg.get("remote_profile") or role_cfg.get("remote_api_profile") or ""
    ).strip()
    if not profile_name:
        return base_kwargs
    agent_config = state.get("agent_config") or {}
    profiles = agent_config.get("remote_api_profiles")
    if not isinstance(profiles, dict):
        return base_kwargs
    profile = profiles.get(profile_name)
    if not isinstance(profile, dict):
        return base_kwargs
    provider = str(profile.get("provider") or "").strip().lower()
    api_key = str(profile.get("api_key") or "").strip()
    base_url = str(profile.get("base_url") or "").strip()
    client_kwargs = dict(base_kwargs)
    if provider:
        client_kwargs["remote_provider"] = provider
    if api_key:
        client_kwargs["remote_api_key"] = api_key
    if base_url:
        client_kwargs["remote_base_url"] = base_url
    return client_kwargs


def _workspace_root_str(state: PipelineState) -> str:
    return str(state.get("workspace_root") or "").strip()


def _stack_reviewer_cfg(state: PipelineState) -> dict[str, Any]:
    """Stack reviewer config; falls back to reviewer for model/environment if not set."""
    agent_config = state.get("agent_config") or {}
    reviewer_cfg: dict[str, Any] = dict(agent_config.get("stack_reviewer") or {})
    reviewer_base_cfg = agent_config.get("reviewer") or {}
    for key in ("environment", "model"):
        if key not in reviewer_cfg and key in reviewer_base_cfg:
            reviewer_cfg[key] = reviewer_base_cfg[key]
    return reviewer_cfg


def _skills_extra_for_role_cfg(
    state: PipelineState, role_cfg: Optional[dict[str, Any]]
) -> str:
    agent_config = state.get("agent_config") or {}
    if not isinstance(agent_config, dict):
        return ""
    cfg = role_cfg if isinstance(role_cfg, dict) else None
    return format_role_skills_extra(
        agent_config, cfg, workspace_root=_workspace_root_str(state)
    )


def _capability_model(capability: str) -> Optional[str]:
    """Map a planner capability string to a concrete model ID.

    Resolution order:
    1. Env var ``SWARM_MODEL_CAPABILITY_<CAPABILITY>`` (user override for any env).
    2. Built-in cloud defaults when route is cloud.
    3. None — let normal model resolution handle it.

    Cloud defaults:
      needs_tool_calling → claude-sonnet-4-6
      needs_code         → claude-sonnet-4-6
      needs_reasoning    → claude-opus-4-6
      needs_fast         → claude-haiku-4-5-20251001

    For local (ollama/lmstudio) set e.g.
      SWARM_MODEL_CAPABILITY_NEEDS_TOOL_CALLING=qwen3:latest
    """
    if not capability:
        return None
    cap_key = capability.upper().replace("-", "_")
    # User-configured override (works for any env)
    env_override = os.getenv(f"SWARM_MODEL_CAPABILITY_{cap_key}", "").strip()
    if env_override:
        return env_override
    # Built-in cloud defaults
    route = os.getenv("SWARM_ROUTE_DEFAULT", "local").lower()
    if route == "cloud":
        _CLOUD_MAP: dict[str, str] = {
            "NEEDS_TOOL_CALLING": "claude-sonnet-4-6",
            "NEEDS_CODE": "claude-sonnet-4-6",
            "NEEDS_REASONING": "claude-opus-4-6",
            "NEEDS_FAST": "claude-haiku-4-5-20251001",
        }
        return _CLOUD_MAP.get(cap_key)
    return None


def _cfg_model(cfg: dict) -> Optional[str]:
    """Return the effective model from a role config dict.

    Checks explicit ``model`` key first, then falls back to resolving a
    ``_planner_capability`` recommendation left by the auto-planner.
    """
    explicit = str(cfg.get("model") or "").strip()
    if explicit:
        return explicit
    capability = str(cfg.get("_planner_capability") or "").strip()
    return _capability_model(capability) if capability else None


def _env_model_override(
    env_var: str,
    cfg_model: Optional[str],
    planner_capability: Optional[str] = None,
) -> Optional[str]:
    """Return model override: config value takes priority, env var is the fallback.

    Allows operators to set per-role model routing without touching the agent_config
    JSON. Config file always wins when non-empty.

    If *cfg_model* is empty and *planner_capability* is provided, falls back to
    :func:`_capability_model` before checking the env var.

    Example env vars (see docs/improve-plan.md §P1 model routing):
    - SWARM_REVIEWER_MODEL  — model for all reviewer / clarify_input steps
    - SWARM_PM_MODEL        — model for pm step
    - SWARM_DEV_LEAD_MODEL  — model for dev_lead step
    """
    if cfg_model and cfg_model.strip():
        return cfg_model.strip()
    if planner_capability:
        cap_model = _capability_model(planner_capability)
        if cap_model:
            return cap_model
    env_val = os.getenv(env_var, "").strip()
    return env_val or None


def _make_reviewer_agent(state: PipelineState) -> ReviewerAgent:
    rcfg = _reviewer_cfg(state)
    # SWARM_REVIEWER_MAX_OUTPUT_TOKENS caps reviewer verbosity to prevent
    # narrative bloat (e.g. 1305-token plans instead of short VERDICT responses).
    # Default 0 = no cap (preserves existing behaviour); set to e.g. 1500 to cap.
    _reviewer_max_tokens = int(os.getenv("SWARM_REVIEWER_MAX_OUTPUT_TOKENS", "0").strip() or "0")
    return ReviewerAgent(
        system_prompt_path_override=rcfg.get("prompt_path") or rcfg.get("prompt"),
        model_override=_env_model_override("SWARM_REVIEWER_MODEL", rcfg.get("model"), rcfg.get("_planner_capability")),
        environment_override=rcfg.get("environment"),
        system_prompt_extra=_skills_extra_for_role_cfg(state, rcfg),
        max_output_tokens=_reviewer_max_tokens,
        **_remote_api_client_kwargs_for_role(state, rcfg),
    )


def _swarm_languages_line(state: PipelineState) -> str:
    sw = _swarm_block(state)
    langs = sw.get("languages")
    if isinstance(langs, list) and langs:
        return "Project languages (code and stack): " + ", ".join(str(x) for x in langs) + ".\n"
    return ""


def _documentation_locale_line(state: PipelineState) -> str:
    """Instruction prepended to user messages for doc/diagram agents. Default: English."""
    sw = _swarm_block(state)
    loc = sw.get("documentation_locale") or sw.get("locale")
    if isinstance(loc, str) and loc.strip():
        return (
            "Response language (documentation and explanations from the model): "
            f"{loc.strip()}.\n"
        )
    return (
        "Response language (documentation and explanations from the model): English.\n"
    )


def _redact_database_url(url: str) -> str:
    u = url.strip()
    for prefix in ("postgresql://", "postgres://", "mysql://", "mongodb://", "redis://"):
        if not u.lower().startswith(prefix):
            continue
        try:
            rest = u.split("://", 1)[1]
            if "@" in rest:
                creds, hostpart = rest.rsplit("@", 1)
                if ":" in creds:
                    user, _pw = creds.split(":", 1)
                    return f"{prefix}{user}:***@{hostpart}"
        except ValueError:
            break
    return u


def _database_context_for_prompt(state: PipelineState) -> str:
    sw = _swarm_block(state)
    url = sw.get("database_url") or sw.get("db_url")
    hint = sw.get("database_hint") or sw.get("db_hint") or ""
    if not url and not (isinstance(hint, str) and hint.strip()):
        return ""
    ro = sw.get("database_readonly", True)
    lines = ["\n[Project DB]"]
    if url and isinstance(url, str) and url.strip():
        lines.append(f"DSN (redacted): {_redact_database_url(url)}")
    lines.append(f"Prefer read-only: {bool(ro)}")
    if isinstance(hint, str) and hint.strip():
        lines.append(f"Schema / conventions: {hint.strip()}")
    lines.append(
        "Do not duplicate passwords in responses. Real queries — via MCP DB, client, or migrations."
    )
    return "\n".join(lines) + "\n"


def _documentation_links_for_prompt(state: PipelineState) -> str:
    man = state.get("doc_fetch_manifest")
    if not isinstance(man, list):
        man = None
    return format_documentation_links_block(
        _swarm_block(state),
        fetched_manifest=man,
    )


def _swarm_prompt_prefix(state: PipelineState) -> str:
    """DB + external documentation links from agent_config.swarm."""
    return _database_context_for_prompt(state) + _documentation_links_for_prompt(state)


def _llm_build_agent_run(
    agent: DevAgent | QAAgent,
    prompt: str,
    state: PipelineState,
) -> tuple[str, str, str]:
    """Dev/QA: как ``_llm_agent_run_with_optional_mcp`` (совместимость типов)."""
    return _llm_agent_run_with_optional_mcp(agent, prompt, state)


def _validate_tools_only_mcp_state(state: PipelineState) -> None:
    from backend.App.orchestration.domain.agent_config_validator import (
        validate_tools_only_mcp_state as _validate_tools_only_mcp_state_domain,
    )

    agent_config = state.get("agent_config") or {}
    mcp_cfg = agent_config.get("mcp")
    mcp_config = mcp_cfg if isinstance(mcp_cfg, dict) else {}
    mcp_servers = mcp_config.get("servers") or []
    _validate_tools_only_mcp_state_domain(
        context_mode=_workspace_context_mode_normalized(state),
        workspace_root=_workspace_root_str(state),
        mcp_servers=mcp_servers,
    )


def _warn_workspace_context_vs_custom_pipeline(
    state: PipelineState,
    step_ids: Optional[list[str]],
) -> None:
    from backend.App.orchestration.domain.agent_config_validator import (
        warn_workspace_context_vs_custom_pipeline as _warn_workspace_context_vs_custom_pipeline_domain,
    )

    task_id_prefix = (state.get("task_id") or "")[:36]
    warnings = _warn_workspace_context_vs_custom_pipeline_domain(
        context_mode=_workspace_context_mode_normalized(state),
        step_ids=list(step_ids) if step_ids else [],
        task_id_prefix=task_id_prefix,
    )
    for msg in warnings:
        logger.warning("%s", msg)


# Self-verification helpers re-exported for node modules
# L-2: AgentFactory — use factory for new code; direct imports kept for existing node callsites
# (Both imported at module top to avoid late-import lint warnings)

# Module-level default factory (used by _make_reviewer_agent and future migrations)
_default_agent_factory: AgentFactoryPort = ConcreteAgentFactory()


def make_agent(role: str, **kwargs: Any) -> Any:
    """Create an agent using the module-level default factory (L-2).

    Prefer this over direct agent instantiation in new code.
    """
    return _default_agent_factory.create(role, **kwargs)
