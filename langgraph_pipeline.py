"""Совместимость: код в `pipeline.graph`; тесты часто делают patch `langgraph_pipeline.*`."""

from __future__ import annotations

from backend.App.orchestration.application.routing.pipeline_graph import (
    ARTIFACT_AGENT_OUTPUT_KEYS,
    DEFAULT_PIPELINE_STEP_IDS,
    PIPELINE_STEP_REGISTRY,
    build_graph,
    final_pipeline_user_message,
    normalize_dev_qa_tasks_to_count,
    parse_dev_qa_task_plan,
    primary_output_for_step,
    read_dev_qa_task_count_target,
    run_pipeline,
    run_pipeline_stream,
    task_store_agent_label,
    validate_pipeline_steps,
)
from backend.App.orchestration.infrastructure.agents.arch_agent import ArchitectAgent
from backend.App.orchestration.infrastructure.agents.ba_agent import BAAgent
from backend.App.orchestration.infrastructure.agents.dev_agent import DevAgent
from backend.App.orchestration.infrastructure.agents.dev_lead_agent import DevLeadAgent
from backend.App.orchestration.infrastructure.agents.devops_agent import DevopsAgent
from backend.App.orchestration.infrastructure.agents.human_agent import HumanAgent
from backend.App.orchestration.infrastructure.agents.pm_agent import PMAgent
from backend.App.orchestration.infrastructure.agents.qa_agent import QAAgent
from backend.App.orchestration.infrastructure.agents.reviewer_agent import ReviewerAgent
from backend.App.orchestration.infrastructure.agents.stack_reviewer_agent import StackReviewerAgent

__all__ = [
    "ARTIFACT_AGENT_OUTPUT_KEYS",
    "ArchitectAgent",
    "BAAgent",
    "DEFAULT_PIPELINE_STEP_IDS",
    "DevAgent",
    "DevLeadAgent",
    "DevopsAgent",
    "HumanAgent",
    "PIPELINE_STEP_REGISTRY",
    "PMAgent",
    "QAAgent",
    "ReviewerAgent",
    "StackReviewerAgent",
    "build_graph",
    "final_pipeline_user_message",
    "normalize_dev_qa_tasks_to_count",
    "parse_dev_qa_task_plan",
    "read_dev_qa_task_count_target",
    "primary_output_for_step",
    "run_pipeline",
    "run_pipeline_stream",
    "task_store_agent_label",
    "validate_pipeline_steps",
]
