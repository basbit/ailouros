"""Design pipeline nodes: ux_researcher, ux_architect, ui_designer + reviews + human gates."""
from __future__ import annotations

from typing import Any

from backend.App.orchestration.infrastructure.agents.ux_researcher_agent import UXResearcherAgent
from backend.App.orchestration.infrastructure.agents.ux_architect_agent import UXArchitectAgent
from backend.App.orchestration.infrastructure.agents.ui_designer_agent import UIDesignerAgent
from backend.App.orchestration.application.review_moa import run_reviewer_or_moa
from backend.App.orchestration.application.pipeline_state import PipelineState

from backend.App.orchestration.application.nodes._shared import (
    _cfg_model,
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
    build_phase_pipeline_user_context,
    pipeline_user_task,
    embedded_pipeline_input_for_review,
    embedded_review_artifact,
)


# ---------------------------------------------------------------------------
# UX Researcher
# ---------------------------------------------------------------------------

def ux_researcher_node(state: PipelineState) -> dict[str, Any]:
    """UX research: user personas, journey maps, usability insights."""
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
    prompt = (
        ctx
        + _swarm_prompt_prefix(state)
        + planning_mcp_tool_instruction(state)
        + _project_knowledge_block(state)
        + "[Pipeline rule] Based on the specification, conduct UX research:\n"
        "- Create user personas based on the target audience\n"
        "- Map user journeys and identify pain points\n"
        "- Define usability testing criteria and success metrics\n"
        "- Provide actionable research recommendations for design\n\n"
        f"User task:\n{pipeline_user_task(state)}\n\n"
        f"Specification:\n{_effective_spec_for_build(state)}\n\n"
    )
    result, _, _ = _llm_planning_agent_run(agent, prompt, state)
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


# ---------------------------------------------------------------------------
# UX Architect
# ---------------------------------------------------------------------------

def ux_architect_node(state: PipelineState) -> dict[str, Any]:
    """UX architecture: CSS systems, layout frameworks, UX structure."""
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
    prompt = (
        ctx
        + _swarm_prompt_prefix(state)
        + planning_mcp_tool_instruction(state)
        + _project_knowledge_block(state)
        + "[Pipeline rule] Based on the specification and UX research, create UX architecture:\n"
        "- Design CSS variable system (colors, typography, spacing)\n"
        "- Create layout framework with responsive breakpoints\n"
        "- Define component architecture and naming conventions\n"
        "- Establish information architecture and content hierarchy\n"
        "- Include theme toggle (light/dark/system) specification\n\n"
        f"User task:\n{pipeline_user_task(state)}\n\n"
        f"Specification:\n{_effective_spec_for_build(state)}\n\n"
        f"{ux_block}"
    )
    result, _, _ = _llm_planning_agent_run(agent, prompt, state)
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


# ---------------------------------------------------------------------------
# UI Designer
# ---------------------------------------------------------------------------

def ui_designer_node(state: PipelineState) -> dict[str, Any]:
    """UI design: visual design systems, component libraries, interfaces."""
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
