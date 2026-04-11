"""StepOutputExtractor: resolve per-step state keys and build completion events.

Extracted from pipeline_step_runner.py to give the output key mapping
a dedicated home that infrastructure classes can query without knowing
about the full runner.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from backend.App.orchestration.application.pipeline_state import PipelineState
from backend.App.orchestration.application.nodes.custom import _CROLE_PREFIX

logger = logging.getLogger(__name__)


# Maps agent name → (output_key, model_key, provider_key).
# Empty string for model_key/provider_key means those keys are not available
# for this agent (human/merge steps that produce no model metadata).
_AGENT_STATE_KEYS: dict[str, tuple[str, str, str]] = {
    "clarify_input": ("clarify_input_output", "clarify_input_model", "clarify_input_provider"),
    "pm": ("pm_output", "pm_model", "pm_provider"),
    "review_pm": ("pm_review_output", "pm_review_model", "pm_review_provider"),
    "human_pm": ("pm_human_output", "", ""),
    "ba": ("ba_output", "ba_model", "ba_provider"),
    "review_ba": ("ba_review_output", "ba_review_model", "ba_review_provider"),
    "human_ba": ("ba_human_output", "", ""),
    "architect": ("arch_output", "arch_model", "arch_provider"),
    "review_stack": ("stack_review_output", "stack_review_model", "stack_review_provider"),
    "review_arch": ("arch_review_output", "arch_review_model", "arch_review_provider"),
    "human_arch": ("arch_human_output", "", ""),
    "spec_merge": ("spec_output", "", ""),
    "review_spec": ("spec_review_output", "spec_review_model", "spec_review_provider"),
    "human_spec": ("spec_human_output", "", ""),
    "analyze_code": ("analyze_code_output", "", ""),
    "generate_documentation": (
        "generate_documentation_output",
        "generate_documentation_model",
        "generate_documentation_provider",
    ),
    "problem_spotter": ("problem_spotter_output", "problem_spotter_model", "problem_spotter_provider"),
    "refactor_plan": ("refactor_plan_output", "refactor_plan_model", "refactor_plan_provider"),
    "human_code_review": ("code_review_human_output", "", ""),
    "devops": ("devops_output", "devops_model", "devops_provider"),
    "review_devops": ("devops_review_output", "devops_review_model", "devops_review_provider"),
    "human_devops": ("devops_human_output", "", ""),
    "dev_lead": ("dev_lead_output", "dev_lead_model", "dev_lead_provider"),
    "pm_tasks": ("dev_lead_output", "dev_lead_model", "dev_lead_provider"),
    "review_dev_lead": ("dev_lead_review_output", "dev_lead_review_model", "dev_lead_review_provider"),
    "review_pm_tasks": ("dev_lead_review_output", "dev_lead_review_model", "dev_lead_review_provider"),
    "human_dev_lead": ("dev_lead_human_output", "", ""),
    "human_pm_tasks": ("dev_lead_human_output", "", ""),
    "dev": ("dev_output", "dev_model", "dev_provider"),
    "review_dev": ("dev_review_output", "dev_review_model", "dev_review_provider"),
    "human_dev": ("dev_human_output", "", ""),
    "qa": ("qa_output", "qa_model", "qa_provider"),
    "review_qa": ("qa_review_output", "qa_review_model", "qa_review_provider"),
    "human_qa": ("qa_human_output", "", ""),
    "e2e": ("e2e_output", "", ""),
}


@dataclass
class StepOutput:
    """Structured output from a completed pipeline step."""

    message: str
    model: str
    provider: str


class StepOutputExtractor:
    """Resolve state keys for a step and extract its completed output."""

    def keys_for(self, step_id: str) -> tuple[str, str, str]:
        """Return (output_key, model_key, provider_key) for *step_id*.

        Custom-role steps (prefixed with ``_CROLE_PREFIX``) use a derived
        naming convention; all others look up ``_AGENT_STATE_KEYS``.

        Returns:
            A 3-tuple of key names; model_key and provider_key may be empty
            strings for steps that do not emit model metadata.
        """
        if step_id.startswith(_CROLE_PREFIX):
            return (
                f"{step_id}_output",
                f"{step_id}_model",
                f"{step_id}_provider",
            )
        return _AGENT_STATE_KEYS.get(step_id, ("", "", ""))

    def extract(self, step_id: str, state: PipelineState) -> StepOutput:
        """Build a :class:`StepOutput` from the current pipeline state.

        Args:
            step_id: The agent/step identifier.
            state: Current pipeline state dict.

        Returns:
            A :class:`StepOutput` with message, model, and provider fields.
        """
        out_k, mod_k, prov_k = self.keys_for(step_id)
        if not out_k:
            logger.warning("StepOutputExtractor.extract: unknown agent %r", step_id)
            return StepOutput(message="", model="", provider="")

        msg = str(state.get(out_k, "") or "")
        model = str(state.get(mod_k, "") or "") if mod_k else ""
        provider = str(state.get(prov_k, "") or "") if prov_k else ""
        return StepOutput(message=msg, model=model, provider=provider)

    def emit_completed(self, agent: str, state: PipelineState) -> dict[str, Any]:
        """Build the completion event dict for *agent*.

        Args:
            agent: Agent/step name.
            state: Current pipeline state dict.

        Returns:
            Dict with ``agent``, ``status``, ``message``, and optionally
            ``model`` / ``provider`` keys.
        """
        out = self.extract(agent, state)
        event: dict[str, Any] = {
            "agent": agent,
            "status": "completed",
            "message": out.message,
        }
        if out.model:
            event["model"] = out.model
        if out.provider:
            event["provider"] = out.provider
        # §10.3-2: Surface cache vs live source in SSE events
        if out.model == "cache" or out.provider == "cache":
            event["source"] = "cache"
        return event


# ---------------------------------------------------------------------------
# Module-level helpers — thin wrappers kept for callers that use them directly
# ---------------------------------------------------------------------------

_default_extractor = StepOutputExtractor()


def primary_output_for_step(step_id: str, state: PipelineState) -> str:
    """Primary text output for a step (same as the stream completed event)."""
    completed_event = _default_extractor.emit_completed(step_id, state)
    return str(completed_event.get("message", ""))


def task_store_agent_label(
    state: PipelineState,
    pipeline_steps: Optional[list[str]] = None,
) -> str:
    """Agent label for TaskStore: aligned with whichever field the final text was taken from."""
    if pipeline_steps:
        return pipeline_steps[-1]
    if (state.get("qa_human_output") or "").strip():
        return "human_qa"
    if (state.get("qa_output") or "").strip():
        return "qa"
    return "qa"


def final_pipeline_user_message(
    state: PipelineState,
    pipeline_steps: Optional[list[str]] = None,
) -> str:
    """Text to return to the client: last step of a custom pipeline, or legacy QA output."""
    if pipeline_steps:
        last = pipeline_steps[-1]
        return primary_output_for_step(last, state).strip() or state.get("input", "").strip()
    return (
        state.get("qa_human_output", "")
        or state.get("qa_output", "")
        or state.get("input", "")
    ).strip()
