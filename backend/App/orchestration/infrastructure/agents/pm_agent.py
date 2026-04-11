"""Project manager agent."""

from __future__ import annotations

import logging
import os as _os

from backend.App.orchestration.infrastructure.agents.base_agent import BaseAgent, load_prompt, resolve_agent_model, resolve_default_environment
from typing import Optional

logger = logging.getLogger(__name__)


class PMAgent(BaseAgent):

    def __init__(
        self,
        *,
        system_prompt_path_override: Optional[str] = None,
        model_override: Optional[str] = None,
        environment_override: Optional[str] = None,
        remote_provider: Optional[str] = None,
        remote_api_key: Optional[str] = None,
        remote_base_url: Optional[str] = None,
        system_prompt_extra: str = "",
    ) -> None:
        fallback = (
            "You are an experienced Project Manager. "
            "Rewrite the user's task as a concrete, prioritized list of development steps "
            "with expected outcomes."
        )
        prompt_path = system_prompt_path_override or "project-management/project-manager-senior.md"
        super().__init__(
            role="PM",
            system_prompt=load_prompt(
                prompt_path,
                fallback,
            ),
            model=model_override or resolve_agent_model("PM"),
            environment=environment_override or resolve_default_environment(),
            remote_provider=remote_provider,
            remote_api_key=remote_api_key,
            remote_base_url=remote_base_url,
            system_prompt_extra=system_prompt_extra,
        )

    def run(self, user_input: str) -> str:
        """Run PM agent, optionally prepending deep-planning analysis (K-11/M-11).

        When SWARM_DEEP_PLANNING=1, runs 5-stage DeepPlanner before the LLM call
        and prepends the analysis summary to user_input (INV-4: result is a proposal,
        human gate still follows in the pipeline).
        """
        if _os.getenv("SWARM_DEEP_PLANNING", "0") == "1":
            try:
                from backend.App.orchestration.application.deep_planning import DeepPlanner
                task_id = _os.getenv("SWARM_CURRENT_TASK_ID", "unknown")
                workspace_root = _os.getenv("SWARM_WORKSPACE_ROOT", "")
                plan = DeepPlanner().analyze(
                    task_id=task_id,
                    task_spec=user_input,
                    workspace_root=workspace_root,
                )
                if not plan.error:
                    summary = (
                        f"## Deep Planning Analysis\n\n"
                        f"Scan: {plan.scan_summary[:400]}\n"
                        f"Risks: {len(plan.risks)} identified\n"
                        f"Alternatives: {len(plan.alternatives)}\n"
                        f"Milestones: {len(plan.milestones)}\n"
                        f"Recommended: {plan.recommended_alternative}\n\n"
                    )
                    user_input = summary + user_input
                    logger.info("PMAgent: deep planning prepended (task=%s)", task_id)  # INV-1
                else:
                    logger.warning("PMAgent: deep planning failed (%s), proceeding without it", plan.error)  # INV-1
            except Exception as exc:
                logger.warning("PMAgent: deep planning exception (%s), proceeding without it", exc)  # INV-1
        return super().run(user_input)
