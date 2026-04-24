from __future__ import annotations

from typing import Any

from backend.App.orchestration.infrastructure.agents.seo_specialist_agent import SEOSpecialistAgent
from backend.App.orchestration.infrastructure.agents.ai_citation_strategist_agent import AICitationStrategistAgent
from backend.App.orchestration.infrastructure.agents.app_store_optimizer_agent import AppStoreOptimizerAgent
from backend.App.orchestration.application.agents.review_moa import run_reviewer_or_moa
from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState

from backend.App.orchestration.application.nodes._shared import (
    _cfg_model,
    _documentation_locale_line,
    _effective_spec_for_build,
    _llm_planning_agent_run,
    _make_human_agent,
    _make_reviewer_agent,
    _pipeline_context_block,
    _project_knowledge_block,
    _swarm_prompt_prefix,
    planning_mcp_tool_instruction,
    _remote_api_client_kwargs_for_role,
    _skills_extra_for_role_cfg,
    pipeline_user_task,
    embedded_pipeline_input_for_review,
    embedded_review_artifact,
)


def seo_specialist_node(state: PipelineState) -> dict[str, Any]:
    agent_config = state.get("agent_config") or {}
    cfg = agent_config.get("seo_specialist") or agent_config.get("reviewer") or {}
    if not isinstance(cfg, dict):
        cfg = {}
    agent = SEOSpecialistAgent(
        system_prompt_path_override=cfg.get("prompt_path") or cfg.get("prompt"),
        model_override=_cfg_model(cfg),
        environment_override=cfg.get("environment"),
        system_prompt_extra=_skills_extra_for_role_cfg(state, cfg),
        **_remote_api_client_kwargs_for_role(state, cfg),
    )
    ctx = _pipeline_context_block(state, "seo_specialist")
    prompt = (
        ctx
        + _swarm_prompt_prefix(state)
        + _documentation_locale_line(state)
        + planning_mcp_tool_instruction(state)
        + _project_knowledge_block(state)
        + "[Pipeline rule] Based on the project specification, provide SEO strategy:\n"
        "- Technical SEO audit and recommendations\n"
        "- Keyword research and content optimization plan\n"
        "- On-page SEO checklist for the project pages\n"
        "- Structured data / schema markup recommendations\n"
        "- Core Web Vitals considerations\n\n"
        f"User task:\n{pipeline_user_task(state)}\n\n"
        f"Specification:\n{_effective_spec_for_build(state)}\n\n"
    )
    result, _, _ = _llm_planning_agent_run(agent, prompt, state)
    if (result or "").strip():
        from backend.App.workspace.application.doc_workspace import write_step_wiki
        write_step_wiki(state, "seo_specialist", result)
    return {
        "seo_specialist_output": result,
        "seo_specialist_model": agent.used_model,
        "seo_specialist_provider": agent.used_provider,
    }


def review_seo_specialist_node(state: PipelineState) -> dict[str, Any]:
    user_block = embedded_pipeline_input_for_review(state, log_node="review_seo_specialist_node")
    spec_art = embedded_review_artifact(
        state, _effective_spec_for_build(state),
        log_node="review_seo_specialist_node", part_name="specification",
        env_name="SWARM_REVIEW_SPEC_MAX_CHARS", default_max=40_000, mcp_max=3_000,
    )
    seo_art = embedded_review_artifact(
        state, state.get("seo_specialist_output"),
        log_node="review_seo_specialist_node", part_name="seo_specialist_output",
        env_name="SWARM_REVIEW_SEO_MAX_CHARS", default_max=60_000, mcp_max=4_000,
    )
    prompt = (
        "Step: seo_specialist (technical SEO, keywords, content optimization).\n"
        "Checklist — VERDICT: NEEDS_WORK if ANY fails:\n"
        "[ ] Technical SEO recommendations are specific and actionable\n"
        "[ ] Keyword strategy is data-driven with volume/competition estimates\n"
        "[ ] On-page SEO checklist covers all critical elements\n"
        "[ ] Schema markup recommendations match the content types\n\n"
        f"User task:\n{user_block}\n\n"
        f"Specification:\n{spec_art}\n\n"
        f"SEO output:\n{seo_art}"
    )
    return run_reviewer_or_moa(
        state, pipeline_step="review_seo_specialist", prompt=prompt,
        output_key="seo_specialist_review_output",
        model_key="seo_specialist_review_model",
        provider_key="seo_specialist_review_provider",
        agent_factory=lambda: _make_reviewer_agent(state),
    )


def human_seo_specialist_node(state: PipelineState) -> dict[str, Any]:
    bundle = (
        f"SEO Strategy:\n{state.get('seo_specialist_output', '')}\n\n"
        f"Review:\n{state.get('seo_specialist_review_output', '')}"
    )
    agent = _make_human_agent(state, "seo_specialist")
    return {"seo_specialist_human_output": agent.run(bundle)}


def ai_citation_strategist_node(state: PipelineState) -> dict[str, Any]:
    agent_config = state.get("agent_config") or {}
    cfg = agent_config.get("ai_citation_strategist") or agent_config.get("reviewer") or {}
    if not isinstance(cfg, dict):
        cfg = {}
    agent = AICitationStrategistAgent(
        system_prompt_path_override=cfg.get("prompt_path") or cfg.get("prompt"),
        model_override=_cfg_model(cfg),
        environment_override=cfg.get("environment"),
        system_prompt_extra=_skills_extra_for_role_cfg(state, cfg),
        **_remote_api_client_kwargs_for_role(state, cfg),
    )
    ctx = _pipeline_context_block(state, "ai_citation_strategist")
    seo_output = state.get("seo_specialist_output") or ""
    seo_block = f"SEO Strategy (from previous step):\n{seo_output}\n\n" if seo_output else ""
    prompt = (
        ctx
        + _swarm_prompt_prefix(state)
        + _documentation_locale_line(state)
        + planning_mcp_tool_instruction(state)
        + _project_knowledge_block(state)
        + "[Pipeline rule] Based on the project, provide AI citation strategy:\n"
        "- Multi-platform citation audit plan (ChatGPT, Claude, Gemini, Perplexity)\n"
        "- Lost prompt analysis and competitor citation mapping\n"
        "- Content gap detection for AI-preferred formats\n"
        "- Schema markup and entity optimization for AI discoverability\n"
        "- Prioritized fix pack with implementation plan\n\n"
        f"User task:\n{pipeline_user_task(state)}\n\n"
        f"Specification:\n{_effective_spec_for_build(state)}\n\n"
        f"{seo_block}"
    )
    result, _, _ = _llm_planning_agent_run(agent, prompt, state)
    if (result or "").strip():
        from backend.App.workspace.application.doc_workspace import write_step_wiki
        write_step_wiki(state, "ai_citation_strategist", result)
    return {
        "ai_citation_strategist_output": result,
        "ai_citation_strategist_model": agent.used_model,
        "ai_citation_strategist_provider": agent.used_provider,
    }


def review_ai_citation_strategist_node(state: PipelineState) -> dict[str, Any]:
    user_block = embedded_pipeline_input_for_review(state, log_node="review_ai_citation_strategist_node")
    spec_art = embedded_review_artifact(
        state, _effective_spec_for_build(state),
        log_node="review_ai_citation_strategist_node", part_name="specification",
        env_name="SWARM_REVIEW_SPEC_MAX_CHARS", default_max=40_000, mcp_max=3_000,
    )
    acs_art = embedded_review_artifact(
        state, state.get("ai_citation_strategist_output"),
        log_node="review_ai_citation_strategist_node", part_name="ai_citation_strategist_output",
        env_name="SWARM_REVIEW_ACS_MAX_CHARS", default_max=60_000, mcp_max=4_000,
    )
    prompt = (
        "Step: ai_citation_strategist (AEO/GEO, AI citation audit, fix packs).\n"
        "Checklist — VERDICT: NEEDS_WORK if ANY fails:\n"
        "[ ] Multi-platform audit plan covers 4 major AI engines\n"
        "[ ] Content gaps and lost prompts are identified\n"
        "[ ] Fix pack is prioritized by expected citation impact\n"
        "[ ] No guarantees of citation outcomes (non-deterministic)\n\n"
        f"User task:\n{user_block}\n\n"
        f"Specification:\n{spec_art}\n\n"
        f"AI Citation Strategy output:\n{acs_art}"
    )
    return run_reviewer_or_moa(
        state, pipeline_step="review_ai_citation_strategist", prompt=prompt,
        output_key="ai_citation_strategist_review_output",
        model_key="ai_citation_strategist_review_model",
        provider_key="ai_citation_strategist_review_provider",
        agent_factory=lambda: _make_reviewer_agent(state),
    )


def human_ai_citation_strategist_node(state: PipelineState) -> dict[str, Any]:
    bundle = (
        f"AI Citation Strategy:\n{state.get('ai_citation_strategist_output', '')}\n\n"
        f"Review:\n{state.get('ai_citation_strategist_review_output', '')}"
    )
    agent = _make_human_agent(state, "ai_citation_strategist")
    return {"ai_citation_strategist_human_output": agent.run(bundle)}


def app_store_optimizer_node(state: PipelineState) -> dict[str, Any]:
    agent_config = state.get("agent_config") or {}
    cfg = agent_config.get("app_store_optimizer") or agent_config.get("reviewer") or {}
    if not isinstance(cfg, dict):
        cfg = {}
    agent = AppStoreOptimizerAgent(
        system_prompt_path_override=cfg.get("prompt_path") or cfg.get("prompt"),
        model_override=_cfg_model(cfg),
        environment_override=cfg.get("environment"),
        system_prompt_extra=_skills_extra_for_role_cfg(state, cfg),
        **_remote_api_client_kwargs_for_role(state, cfg),
    )
    ctx = _pipeline_context_block(state, "app_store_optimizer")
    prompt = (
        ctx
        + _swarm_prompt_prefix(state)
        + _documentation_locale_line(state)
        + planning_mcp_tool_instruction(state)
        + _project_knowledge_block(state)
        + "[Pipeline rule] Based on the project, provide App Store optimization:\n"
        "- Keyword research and metadata optimization strategy\n"
        "- Visual asset optimization (icon, screenshots, preview video)\n"
        "- A/B testing roadmap for store listing elements\n"
        "- Localization strategy for international markets\n"
        "- Conversion rate optimization recommendations\n\n"
        f"User task:\n{pipeline_user_task(state)}\n\n"
        f"Specification:\n{_effective_spec_for_build(state)}\n\n"
    )
    result, _, _ = _llm_planning_agent_run(agent, prompt, state)
    if (result or "").strip():
        from backend.App.workspace.application.doc_workspace import write_step_wiki
        write_step_wiki(state, "app_store_optimizer", result)
    return {
        "app_store_optimizer_output": result,
        "app_store_optimizer_model": agent.used_model,
        "app_store_optimizer_provider": agent.used_provider,
    }


def review_app_store_optimizer_node(state: PipelineState) -> dict[str, Any]:
    user_block = embedded_pipeline_input_for_review(state, log_node="review_app_store_optimizer_node")
    spec_art = embedded_review_artifact(
        state, _effective_spec_for_build(state),
        log_node="review_app_store_optimizer_node", part_name="specification",
        env_name="SWARM_REVIEW_SPEC_MAX_CHARS", default_max=40_000, mcp_max=3_000,
    )
    aso_art = embedded_review_artifact(
        state, state.get("app_store_optimizer_output"),
        log_node="review_app_store_optimizer_node", part_name="app_store_optimizer_output",
        env_name="SWARM_REVIEW_ASO_MAX_CHARS", default_max=60_000, mcp_max=4_000,
    )
    prompt = (
        "Step: app_store_optimizer (ASO, keyword optimization, conversion).\n"
        "Checklist — VERDICT: NEEDS_WORK if ANY fails:\n"
        "[ ] Keyword research includes volume and competition data\n"
        "[ ] Visual asset strategy covers icon, screenshots, video\n"
        "[ ] A/B testing plan is systematic and measurable\n"
        "[ ] Recommendations are platform-specific (iOS/Android)\n\n"
        f"User task:\n{user_block}\n\n"
        f"Specification:\n{spec_art}\n\n"
        f"ASO output:\n{aso_art}"
    )
    return run_reviewer_or_moa(
        state, pipeline_step="review_app_store_optimizer", prompt=prompt,
        output_key="app_store_optimizer_review_output",
        model_key="app_store_optimizer_review_model",
        provider_key="app_store_optimizer_review_provider",
        agent_factory=lambda: _make_reviewer_agent(state),
    )


def human_app_store_optimizer_node(state: PipelineState) -> dict[str, Any]:
    bundle = (
        f"ASO Strategy:\n{state.get('app_store_optimizer_output', '')}\n\n"
        f"Review:\n{state.get('app_store_optimizer_review_output', '')}"
    )
    agent = _make_human_agent(state, "app_store_optimizer")
    return {"app_store_optimizer_human_output": agent.run(bundle)}
