"""Reviewer agent — LLM review after each worker step."""

from __future__ import annotations

from typing import Optional

from backend.App.orchestration.infrastructure.agents.base_agent import BaseAgent, load_prompt, resolve_agent_model, resolve_default_environment


class ReviewerAgent(BaseAgent):
    def __init__(
        self,
        *,
        system_prompt_path_override: Optional[str] = None,
        model_override: Optional[str] = None,
        environment_override: Optional[str] = None,
        remote_provider: Optional[str] = None,
        remote_api_key: Optional[str] = None,
        remote_base_url: Optional[str] = None,
        max_output_tokens: int = 0,
        system_prompt_extra: str = "",
    ) -> None:
        fallback = (
            "You are a reviewer. Assess the artifact for task fit, completeness, and risks. "
            "End with a single line: VERDICT: OK or VERDICT: NEEDS_WORK.\n\n"
            "When issuing VERDICT: NEEDS_WORK you MUST include a structured defect block "
            "immediately after the verdict line:\n"
            "VERDICT: NEEDS_WORK\n"
            "<defect_report>\n"
            "  <defect id=\"D1\" severity=\"P0|P1|P2\">\n"
            "    <description>what is wrong</description>\n"
            "    <remediation>what to fix</remediation>\n"
            "  </defect>\n"
            "</defect_report>\n"
            "Use severity P0 for blocking issues, P1 for major issues, P2 for minor issues. "
            "Include one <defect> element per distinct issue found."
        )
        prompt_path = system_prompt_path_override or "specialized/specialized-reviewer.md"
        super().__init__(
            role="REVIEWER",
            system_prompt=load_prompt(prompt_path, fallback),
            model=model_override or resolve_agent_model("REVIEWER"),
            environment=environment_override or resolve_default_environment(),
            remote_provider=remote_provider,
            remote_api_key=remote_api_key,
            remote_base_url=remote_base_url,
            max_tokens=max_output_tokens,
            system_prompt_extra=system_prompt_extra,
        )
