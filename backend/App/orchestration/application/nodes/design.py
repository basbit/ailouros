from __future__ import annotations

import logging
from typing import Any

from backend.App.orchestration.infrastructure.agents.base_agent import (
    BaseAgent,
    load_prompt,
    resolve_agent_model,
    resolve_default_environment,
)
from backend.App.orchestration.infrastructure.agents.ux_researcher_agent import UXResearcherAgent
from backend.App.orchestration.infrastructure.agents.ux_architect_agent import UXArchitectAgent
from backend.App.orchestration.infrastructure.agents.ui_designer_agent import UIDesignerAgent
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
    _stream_progress_emit,
    _swarm_prompt_prefix,
    _web_research_guidance_block,
    planning_mcp_tool_instruction,
    _remote_api_client_kwargs_for_role,
    _skills_extra_for_role_cfg,
    pipeline_user_task,
    embedded_pipeline_input_for_review,
    embedded_review_artifact,
)
from backend.App.orchestration.application.nodes._prompt_builders import (
    _prompt_fragment,
)
from string import Template

logger = logging.getLogger(__name__)


def _make_planning_role_agent(
    state: PipelineState,
    role_id: str,
    default_prompt_path: str,
    fallback_prompt: str,
) -> BaseAgent:
    agent_config = state.get("agent_config") or {}
    cfg = agent_config.get(role_id) or agent_config.get("reviewer") or {}
    if not isinstance(cfg, dict):
        cfg = {}
    return BaseAgent(
        role=role_id.upper(),
        system_prompt=load_prompt(
            cfg.get("prompt_path") or cfg.get("prompt") or default_prompt_path,
            fallback_prompt,
        ),
        model=_cfg_model(cfg) or resolve_agent_model(role_id.upper()),
        environment=cfg.get("environment") or resolve_default_environment(),
        system_prompt_extra=_skills_extra_for_role_cfg(state, cfg),
        **_remote_api_client_kwargs_for_role(state, cfg),
    )


def _run_planning_role_node(
    state: PipelineState,
    *,
    role_id: str,
    default_prompt_path: str,
    fallback_prompt: str,
    progress_label: str,
    instruction: str,
    output_key: str,
    model_key: str,
    provider_key: str,
) -> dict[str, Any]:
    agent = _make_planning_role_agent(
        state, role_id, default_prompt_path, fallback_prompt
    )
    ctx = _pipeline_context_block(state, role_id)
    prompt = Template(
        _prompt_fragment("design_planning_role_prompt_template")
    ).safe_substitute(
        context=ctx,
        swarm_prefix=_swarm_prompt_prefix(state),
        locale=_documentation_locale_line(state),
        research_guidance=_web_research_guidance_block(state, role=role_id),
        mcp_instruction=planning_mcp_tool_instruction(state),
        knowledge=_project_knowledge_block(state),
        instruction=instruction,
        user_task=pipeline_user_task(state),
        spec=_effective_spec_for_build(state),
    )
    result, _, _ = _llm_planning_agent_run(agent, prompt, state)
    if not (result or "").strip():
        logger.warning(
            "%s_node: model returned empty output — retrying. task_id=%s",
            role_id,
            (state.get("task_id") or "")[:36],
        )
        _stream_progress_emit(state, f"{progress_label}: empty response — retrying…")
        result, _, _ = _llm_planning_agent_run(agent, prompt, state)
    if (result or "").strip():
        from backend.App.workspace.application.doc_workspace import write_step_wiki

        write_step_wiki(state, role_id, result)
    return {
        output_key: result,
        model_key: agent.used_model,
        provider_key: agent.used_provider,
    }


def code_quality_architect_node(state: PipelineState) -> dict[str, Any]:
    return _run_planning_role_node(
        state,
        role_id="code_quality_architect",
        default_prompt_path="engineering/code-quality-architect.md",
        fallback_prompt=_prompt_fragment("code_quality_architect_fallback"),
        progress_label="Code Quality Architect",
        instruction=_prompt_fragment("code_quality_architect_instruction"),
        output_key="code_quality_architect_output",
        model_key="code_quality_architect_model",
        provider_key="code_quality_architect_provider",
    )


def ux_researcher_node(state: PipelineState) -> dict[str, Any]:
    agent_config = state.get("agent_config") or {}
    cfg = agent_config.get("ux_researcher") or agent_config.get("reviewer") or {}
    if not isinstance(cfg, dict):
        cfg = {}
    agent = UXResearcherAgent(
        system_prompt_path_override=cfg.get("prompt_path") or cfg.get("prompt"),
        model_override=_cfg_model(cfg),
        environment_override=cfg.get("environment"),
        system_prompt_extra=_skills_extra_for_role_cfg(state, cfg),
        **_remote_api_client_kwargs_for_role(state, cfg),
    )
    ctx = _pipeline_context_block(state, "ux_researcher")
    prompt = Template(
        _prompt_fragment("design_planning_role_no_research_prompt_template")
    ).safe_substitute(
        context=ctx,
        swarm_prefix=_swarm_prompt_prefix(state),
        locale=_documentation_locale_line(state),
        mcp_instruction=planning_mcp_tool_instruction(state),
        knowledge=_project_knowledge_block(state),
        instruction=_prompt_fragment("ux_researcher_instruction"),
        user_task=pipeline_user_task(state),
        spec=_effective_spec_for_build(state),
    )
    result, _, _ = _llm_planning_agent_run(agent, prompt, state)
    if not (result or "").strip():
        logger.warning(
            "ux_researcher_node: model returned empty output — retrying. task_id=%s",
            (state.get("task_id") or "")[:36],
        )
        _stream_progress_emit(state, "UX Researcher: empty response — retrying…")
        result, _, _ = _llm_planning_agent_run(agent, prompt, state)
    if not (result or "").strip():
        logger.error(
            "ux_researcher_node: model returned empty output after retry. task_id=%s",
            (state.get("task_id") or "")[:36],
        )
    if (result or "").strip():
        from backend.App.workspace.application.doc_workspace import write_step_wiki
        write_step_wiki(state, "ux_researcher", result)
    return {
        "ux_researcher_output": result,
        "ux_researcher_model": agent.used_model,
        "ux_researcher_provider": agent.used_provider,
    }


def review_ux_researcher_node(state: PipelineState) -> dict[str, Any]:
    user_block = embedded_pipeline_input_for_review(state, log_node="review_ux_researcher_node")
    spec_art = embedded_review_artifact(
        state, _effective_spec_for_build(state),
        log_node="review_ux_researcher_node", part_name="specification",
        env_name="SWARM_REVIEW_SPEC_MAX_CHARS", default_max=40_000, mcp_max=3_000,
    )
    ux_art = embedded_review_artifact(
        state, state.get("ux_researcher_output"),
        log_node="review_ux_researcher_node", part_name="ux_researcher_output",
        env_name="SWARM_REVIEW_UX_RESEARCHER_MAX_CHARS", default_max=60_000, mcp_max=4_000,
    )
    prompt = (
        "Step: ux_researcher (user research, personas, journey maps).\n"
        "Checklist — VERDICT: NEEDS_WORK if ANY fails:\n"
        "[ ] User personas are evidence-based and specific to the project\n"
        "[ ] User journeys cover the primary use cases from the spec\n"
        "[ ] Pain points are clearly identified with solutions\n"
        "[ ] Research recommendations are actionable and prioritized\n\n"
        f"User task:\n{user_block}\n\n"
        f"Specification:\n{spec_art}\n\n"
        f"UX Research output:\n{ux_art}"
    )
    return run_reviewer_or_moa(
        state, pipeline_step="review_ux_researcher", prompt=prompt,
        output_key="ux_researcher_review_output",
        model_key="ux_researcher_review_model",
        provider_key="ux_researcher_review_provider",
        agent_factory=lambda: _make_reviewer_agent(state),
    )


def human_ux_researcher_node(state: PipelineState) -> dict[str, Any]:
    bundle = (
        f"UX Research:\n{state.get('ux_researcher_output', '')}\n\n"
        f"Review:\n{state.get('ux_researcher_review_output', '')}"
    )
    agent = _make_human_agent(state, "ux_researcher")
    return {"ux_researcher_human_output": agent.run(bundle)}


def ux_architect_node(state: PipelineState) -> dict[str, Any]:
    agent_config = state.get("agent_config") or {}
    cfg = agent_config.get("ux_architect") or agent_config.get("reviewer") or {}
    if not isinstance(cfg, dict):
        cfg = {}
    agent = UXArchitectAgent(
        system_prompt_path_override=cfg.get("prompt_path") or cfg.get("prompt"),
        model_override=_cfg_model(cfg),
        environment_override=cfg.get("environment"),
        system_prompt_extra=_skills_extra_for_role_cfg(state, cfg),
        **_remote_api_client_kwargs_for_role(state, cfg),
    )
    ctx = _pipeline_context_block(state, "ux_architect")
    ux_research = state.get("ux_researcher_output") or ""
    ux_block = f"UX Research findings:\n{ux_research}\n\n" if ux_research else ""
    prompt = Template(
        _prompt_fragment("design_planning_role_no_research_prompt_template")
    ).safe_substitute(
        context=ctx,
        swarm_prefix=_swarm_prompt_prefix(state),
        locale=_documentation_locale_line(state),
        mcp_instruction=planning_mcp_tool_instruction(state),
        knowledge=_project_knowledge_block(state),
        instruction=_prompt_fragment("ux_architect_instruction"),
        user_task=pipeline_user_task(state),
        spec=_effective_spec_for_build(state) + ("\n\n" + ux_block if ux_block else ""),
    )
    result, _, _ = _llm_planning_agent_run(agent, prompt, state)
    if not (result or "").strip():
        logger.warning(
            "ux_architect_node: model returned empty output — retrying. task_id=%s",
            (state.get("task_id") or "")[:36],
        )
        _stream_progress_emit(state, "UX Architect: empty response — retrying…")
        result, _, _ = _llm_planning_agent_run(agent, prompt, state)
    if not (result or "").strip():
        logger.error(
            "ux_architect_node: model returned empty output after retry. task_id=%s",
            (state.get("task_id") or "")[:36],
        )
    if (result or "").strip():
        from backend.App.workspace.application.doc_workspace import write_step_wiki
        write_step_wiki(state, "ux_architect", result)
    return {
        "ux_architect_output": result,
        "ux_architect_model": agent.used_model,
        "ux_architect_provider": agent.used_provider,
    }


def review_ux_architect_node(state: PipelineState) -> dict[str, Any]:
    user_block = embedded_pipeline_input_for_review(state, log_node="review_ux_architect_node")
    spec_art = embedded_review_artifact(
        state, _effective_spec_for_build(state),
        log_node="review_ux_architect_node", part_name="specification",
        env_name="SWARM_REVIEW_SPEC_MAX_CHARS", default_max=40_000, mcp_max=3_000,
    )
    ux_arch_art = embedded_review_artifact(
        state, state.get("ux_architect_output"),
        log_node="review_ux_architect_node", part_name="ux_architect_output",
        env_name="SWARM_REVIEW_UX_ARCHITECT_MAX_CHARS", default_max=60_000, mcp_max=4_000,
    )
    prompt = (
        "Step: ux_architect (CSS systems, layout frameworks, UX structure).\n"
        "Checklist — VERDICT: NEEDS_WORK if ANY fails:\n"
        "[ ] CSS design system variables are complete (colors, typography, spacing)\n"
        "[ ] Responsive breakpoint strategy is defined\n"
        "[ ] Component hierarchy and naming conventions are clear\n"
        "[ ] Theme toggle (light/dark/system) specification included\n\n"
        f"User task:\n{user_block}\n\n"
        f"Specification:\n{spec_art}\n\n"
        f"UX Architecture output:\n{ux_arch_art}"
    )
    return run_reviewer_or_moa(
        state, pipeline_step="review_ux_architect", prompt=prompt,
        output_key="ux_architect_review_output",
        model_key="ux_architect_review_model",
        provider_key="ux_architect_review_provider",
        agent_factory=lambda: _make_reviewer_agent(state),
    )


def human_ux_architect_node(state: PipelineState) -> dict[str, Any]:
    bundle = (
        f"UX Architecture:\n{state.get('ux_architect_output', '')}\n\n"
        f"Review:\n{state.get('ux_architect_review_output', '')}"
    )
    agent = _make_human_agent(state, "ux_architect")
    return {"ux_architect_human_output": agent.run(bundle)}


def ui_designer_node(state: PipelineState) -> dict[str, Any]:
    agent_config = state.get("agent_config") or {}
    cfg = agent_config.get("ui_designer") or agent_config.get("reviewer") or {}
    if not isinstance(cfg, dict):
        cfg = {}
    agent = UIDesignerAgent(
        system_prompt_path_override=cfg.get("prompt_path") or cfg.get("prompt"),
        model_override=_cfg_model(cfg),
        environment_override=cfg.get("environment"),
        system_prompt_extra=_skills_extra_for_role_cfg(state, cfg),
        **_remote_api_client_kwargs_for_role(state, cfg),
    )
    ctx = _pipeline_context_block(state, "ui_designer")
    ux_arch = state.get("ux_architect_output") or ""
    ux_research = state.get("ux_researcher_output") or ""
    ux_block = ""
    if ux_arch:
        ux_block += f"UX Architecture:\n{ux_arch}\n\n"
    if ux_research:
        ux_block += f"UX Research:\n{ux_research}\n\n"
    prompt = (
        ctx
        + _swarm_prompt_prefix(state)
        + _documentation_locale_line(state)
        + planning_mcp_tool_instruction(state)
        + _project_knowledge_block(state)
        + "[Pipeline rule] Based on the specification and UX foundations, create UI design:\n"
        "- Design component library with consistent visual language\n"
        "- Create design token system for colors, typography, spacing\n"
        "- Establish visual hierarchy and interaction patterns\n"
        "- Include accessibility compliance (WCAG AA minimum)\n"
        "- Provide developer handoff specifications\n\n"
        f"User task:\n{pipeline_user_task(state)}\n\n"
        f"Specification:\n{_effective_spec_for_build(state)}\n\n"
        f"{ux_block}"
    )
    result, _, _ = _llm_planning_agent_run(agent, prompt, state)
    if not (result or "").strip():
        logger.warning(
            "ui_designer_node: model returned empty output — retrying. task_id=%s",
            (state.get("task_id") or "")[:36],
        )
        _stream_progress_emit(state, "UI Designer: empty response — retrying…")
        result, _, _ = _llm_planning_agent_run(agent, prompt, state)
    if not (result or "").strip():
        logger.error(
            "ui_designer_node: model returned empty output after retry. task_id=%s",
            (state.get("task_id") or "")[:36],
        )
    if (result or "").strip():
        from backend.App.workspace.application.doc_workspace import write_step_wiki
        write_step_wiki(state, "ui_designer", result)
    return {
        "ui_designer_output": result,
        "ui_designer_model": agent.used_model,
        "ui_designer_provider": agent.used_provider,
    }


def review_ui_designer_node(state: PipelineState) -> dict[str, Any]:
    user_block = embedded_pipeline_input_for_review(state, log_node="review_ui_designer_node")
    spec_art = embedded_review_artifact(
        state, _effective_spec_for_build(state),
        log_node="review_ui_designer_node", part_name="specification",
        env_name="SWARM_REVIEW_SPEC_MAX_CHARS", default_max=40_000, mcp_max=3_000,
    )
    ui_art = embedded_review_artifact(
        state, state.get("ui_designer_output"),
        log_node="review_ui_designer_node", part_name="ui_designer_output",
        env_name="SWARM_REVIEW_UI_DESIGNER_MAX_CHARS", default_max=60_000, mcp_max=4_000,
    )
    prompt = (
        "Step: ui_designer (visual design, component library, design tokens).\n"
        "Checklist — VERDICT: NEEDS_WORK if ANY fails:\n"
        "[ ] Component library covers all required UI elements\n"
        "[ ] Design tokens are consistent and complete\n"
        "[ ] WCAG AA accessibility compliance addressed\n"
        "[ ] Developer handoff specs are clear and actionable\n\n"
        f"User task:\n{user_block}\n\n"
        f"Specification:\n{spec_art}\n\n"
        f"UI Design output:\n{ui_art}"
    )
    return run_reviewer_or_moa(
        state, pipeline_step="review_ui_designer", prompt=prompt,
        output_key="ui_designer_review_output",
        model_key="ui_designer_review_model",
        provider_key="ui_designer_review_provider",
        agent_factory=lambda: _make_reviewer_agent(state),
    )


def human_ui_designer_node(state: PipelineState) -> dict[str, Any]:
    bundle = (
        f"UI Design:\n{state.get('ui_designer_output', '')}\n\n"
        f"Review:\n{state.get('ui_designer_review_output', '')}"
    )
    agent = _make_human_agent(state, "ui_designer")
    return {"ui_designer_human_output": agent.run(bundle)}


def image_generator_node(state: PipelineState) -> dict[str, Any]:
    ui_design = state.get("ui_designer_output") or ""
    ui_block = f"\nUI Design context:\n{ui_design}\n" if ui_design else ""
    return _run_planning_role_node(
        state,
        role_id="image_generator",
        default_prompt_path="design/design-image-prompt-engineer.md",
        fallback_prompt=(
            "You are an Image Generator agent. Produce production-ready image "
            "generation prompts, style direction, constraints, and asset specs."
        ),
        progress_label="Image Generator",
        instruction=(
            "[Pipeline rule] Create an image generation plan for the requested product or feature:\n"
            "- Define required images/assets and their purpose\n"
            "- Write precise prompts with composition, style, lighting, aspect ratio, and constraints\n"
            "- Include negative prompts or avoidance notes where useful\n"
            "- Respect the media image provider/model settings from agent_config.media when relevant\n"
            f"{ui_block}"
        ),
        output_key="image_generator_output",
        model_key="image_generator_model",
        provider_key="image_generator_provider",
    )


def audio_generator_node(state: PipelineState) -> dict[str, Any]:
    return _run_planning_role_node(
        state,
        role_id="audio_generator",
        default_prompt_path="game-development/game-audio-engineer.md",
        fallback_prompt=(
            "You are an Audio Generator agent. Produce audio, TTS, voice, "
            "sound design, and production prompts/specifications."
        ),
        progress_label="Audio Generator",
        instruction=(
            "[Pipeline rule] Create an audio generation plan for the requested product or feature:\n"
            "- Define voice, music, ambience, and sound effect assets as needed\n"
            "- Write TTS/audio prompts with tone, pacing, duration, language, and delivery notes\n"
            "- Include file naming, usage context, and acceptance criteria\n"
            "- Respect the media audio provider/model/voice settings from agent_config.media when relevant\n"
        ),
        output_key="audio_generator_output",
        model_key="audio_generator_model",
        provider_key="audio_generator_provider",
    )


def asset_fetcher_node(state: PipelineState) -> dict[str, Any]:
    from backend.App.orchestration.application.nodes.asset_fetcher import (
        run_asset_fetcher,
    )
    return run_asset_fetcher(state)
