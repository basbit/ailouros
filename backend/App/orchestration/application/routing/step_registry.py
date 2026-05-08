
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Optional

from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState
from backend.App.orchestration.application.nodes.pm import (
    clarify_input_node,
    human_clarify_input_node,
    pm_node,
    review_pm_node,
    human_pm_node,
)
from backend.App.orchestration.application.nodes.ba import ba_node, review_ba_node, human_ba_node
from backend.App.orchestration.application.nodes.arch import (
    arch_node,
    review_stack_node,
    review_arch_node,
    human_arch_node,
    ba_arch_debate_node,
    merge_spec_node,
    review_spec_node,
    human_spec_node,
)
from backend.App.orchestration.application.nodes.documentation import (
    analyze_code_node,
    generate_documentation_node,
    problem_spotter_node,
    refactor_plan_node,
    human_code_review_node,
)
from backend.App.orchestration.application.nodes.devops import devops_node, review_devops_node, human_devops_node
from backend.App.orchestration.application.nodes.dev import (
    dev_lead_node,
    review_dev_lead_node,
    human_dev_lead_node,
    dev_node,
    review_dev_node,
    human_dev_node,
)
from backend.App.orchestration.application.nodes.qa import qa_node, review_qa_node, human_qa_node
from backend.App.orchestration.application.nodes.design import (
    code_quality_architect_node,
    ux_researcher_node,
    review_ux_researcher_node,
    human_ux_researcher_node,
    ux_architect_node,
    review_ux_architect_node,
    human_ux_architect_node,
    ui_designer_node,
    review_ui_designer_node,
    human_ui_designer_node,
    image_generator_node,
    audio_generator_node,
    asset_fetcher_node,
)
from backend.App.orchestration.application.nodes.media_generator import media_generator_node
from backend.App.orchestration.application.nodes.marketing import (
    seo_specialist_node,
    review_seo_specialist_node,
    human_seo_specialist_node,
    ai_citation_strategist_node,
    review_ai_citation_strategist_node,
    human_ai_citation_strategist_node,
    app_store_optimizer_node,
    review_app_store_optimizer_node,
    human_app_store_optimizer_node,
)
from backend.App.orchestration.application.nodes.e2e import e2e_node
from backend.App.orchestration.application.nodes.visual_probe import (
    visual_design_review_node,
    visual_probe_node,
)


PIPELINE_STEP_SEQUENCE: tuple[tuple[str, str, Callable[[PipelineState], dict[str, Any]]], ...] = (
    ("clarify_input", "Clarify: requirements analysis", clarify_input_node),
    ("human_clarify_input", "Human: requirements clarification", human_clarify_input_node),
    ("pm", "PM analyses the task", pm_node),
    ("review_pm", "Reviewer checks PM", review_pm_node),
    ("human_pm", "Human approve after PM", human_pm_node),
    ("ba", "BA defines requirements", ba_node),
    ("review_ba", "Reviewer checks BA", review_ba_node),
    ("human_ba", "Human approve after BA", human_ba_node),
    ("architect", "Architect builds the solution", arch_node),
    ("review_stack", "Reviewer approves the stack", review_stack_node),
    ("review_arch", "Reviewer checks Architect", review_arch_node),
    ("human_arch", "Human approve after Architect", human_arch_node),
    ("ba_arch_debate", "DebateWithJudge: Judge resolves BA↔Arch conflicts", ba_arch_debate_node),
    ("spec_merge", "Merge specification", merge_spec_node),
    ("review_spec", "Reviewer checks spec merge", review_spec_node),
    ("human_spec", "Human approve before Dev", human_spec_node),
    ("code_quality_architect", "Code Quality Architect: architecture and quality guardrails", code_quality_architect_node),
    ("ux_researcher", "UX Researcher: user personas, journeys, insights", ux_researcher_node),
    ("review_ux_researcher", "Reviewer checks UX Research", review_ux_researcher_node),
    ("human_ux_researcher", "Human approve after UX Research", human_ux_researcher_node),
    ("ux_architect", "UX Architect: CSS systems, layout, UX structure", ux_architect_node),
    ("review_ux_architect", "Reviewer checks UX Architecture", review_ux_architect_node),
    ("human_ux_architect", "Human approve after UX Architecture", human_ux_architect_node),
    ("ui_designer", "UI Designer: visual design, components, tokens", ui_designer_node),
    ("review_ui_designer", "Reviewer checks UI Design", review_ui_designer_node),
    ("human_ui_designer", "Human approve after UI Design", human_ui_designer_node),
    ("image_generator", "Image Generator: visual asset prompts and specs", image_generator_node),
    ("audio_generator", "Audio Generator: audio and TTS prompts and specs", audio_generator_node),
    ("asset_fetcher", "Asset Fetcher: download free/CC images and audio to workspace", asset_fetcher_node),
    ("media_generator", "Media Generator: provider-backed image and audio assets", media_generator_node),
    ("analyze_code", "Repository code analysis", analyze_code_node),
    ("generate_documentation", "Documentation and Mermaid from analysis", generate_documentation_node),
    ("problem_spotter", "Identify common code issues", problem_spotter_node),
    ("refactor_plan", "Refactoring plan", refactor_plan_node),
    ("human_code_review", "Confirm plan after code analysis", human_code_review_node),
    ("devops", "DevOps: bootstrap and dependencies", devops_node),
    ("review_devops", "Reviewer: DevOps output", review_devops_node),
    ("human_devops", "Human approve after DevOps", human_devops_node),
    ("dev_lead", "Dev Lead: Dev/QA subtasks after spec", dev_lead_node),
    ("review_dev_lead", "Reviewer: Dev Lead plan", review_dev_lead_node),
    ("human_dev_lead", "Human approve Dev Lead plan", human_dev_lead_node),
    ("dev", "Dev implements the solution", dev_node),
    ("review_dev", "Reviewer checks Dev", review_dev_node),
    ("human_dev", "Human approve after Dev", human_dev_node),
    ("visual_probe", "Visual Probe: launch UI and capture browser evidence", visual_probe_node),
    ("visual_design_review", "Visual Design Review: inspect runtime UI evidence", visual_design_review_node),
    ("qa", "QA runs verification", qa_node),
    ("review_qa", "Reviewer checks QA", review_qa_node),
    ("human_qa", "Final human approve", human_qa_node),
    ("seo_specialist", "SEO: technical audit, keywords, content", seo_specialist_node),
    ("review_seo_specialist", "Reviewer checks SEO", review_seo_specialist_node),
    ("human_seo_specialist", "Human approve after SEO", human_seo_specialist_node),
    ("ai_citation_strategist", "AI Citation: AEO/GEO audit, fix packs", ai_citation_strategist_node),
    ("review_ai_citation_strategist", "Reviewer checks AI Citation", review_ai_citation_strategist_node),
    ("human_ai_citation_strategist", "Human approve after AI Citation", human_ai_citation_strategist_node),
    ("app_store_optimizer", "ASO: keywords, visuals, conversion", app_store_optimizer_node),
    ("review_app_store_optimizer", "Reviewer checks ASO", review_app_store_optimizer_node),
    ("human_app_store_optimizer", "Human approve after ASO", human_app_store_optimizer_node),
    ("e2e", "E2E: run test suite", e2e_node),
)

PIPELINE_STEP_REGISTRY: dict[str, tuple[str, Callable[[PipelineState], dict[str, Any]]]] = {
    step_id: (label, node_func) for step_id, label, node_func in PIPELINE_STEP_SEQUENCE
}
for _legacy, _canonical in (
    ("pm_tasks", "dev_lead"),
    ("review_pm_tasks", "review_dev_lead"),
    ("human_pm_tasks", "human_dev_lead"),
):
    if _canonical in PIPELINE_STEP_REGISTRY:
        PIPELINE_STEP_REGISTRY[_legacy] = PIPELINE_STEP_REGISTRY[_canonical]

DEFAULT_PIPELINE_STEP_IDS: list[str] = [t[0] for t in PIPELINE_STEP_SEQUENCE]


class PipelineStepRegistry:

    def __init__(self) -> None:
        self._registry = PIPELINE_STEP_REGISTRY

    def resolve(self, step_name: str) -> Callable[[PipelineState], dict[str, Any]]:
        label_and_func = self._registry.get(step_name)
        if label_and_func is None:
            raise KeyError(step_name)
        _, func = label_and_func
        return func

    def validate(self, steps: list[str]) -> None:
        if not steps:
            raise ValueError("pipeline_steps must be a non-empty list")
        unknown = [s for s in steps if s not in self._registry]
        if unknown:
            raise ValueError(f"Unknown pipeline step ids: {unknown}")


def validate_pipeline_steps(
    steps: list[str],
    agent_config: Optional[dict[str, Any]] = None,
) -> None:
    if not steps:
        raise ValueError("pipeline_steps must be a non-empty list")
    from backend.App.orchestration.application.nodes.custom import parse_custom_role_slug
    unknown: list[str] = []
    for step_id in steps:
        if step_id in PIPELINE_STEP_REGISTRY:
            continue
        role_slug = parse_custom_role_slug(step_id)
        if role_slug:
            custom_roles = (agent_config or {}).get("custom_roles")
            if isinstance(custom_roles, dict) and role_slug in custom_roles:
                continue
        unknown.append(step_id)
    if unknown:
        raise ValueError(f"Unknown pipeline step ids: {unknown}")
