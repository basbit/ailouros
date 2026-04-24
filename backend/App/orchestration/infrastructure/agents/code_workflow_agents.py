
from __future__ import annotations

from typing import Optional

from backend.App.orchestration.infrastructure.agents.base_agent import BaseAgent, load_prompt, resolve_agent_model, resolve_default_environment


class DocGenerateAgent(BaseAgent):
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
            "You generate README.md, API.md, and ARCHITECTURE.md fragments from the "
            "code-analysis JSON and any diagrams block in the user message. "
            'Follow the \u201cResponse language\u201d line in the user message when present; '
            "otherwise write in English."
        )
        path = system_prompt_path_override or "specialized/code-doc-generator.md"
        super().__init__(
            role="DOC_GEN",
            system_prompt=load_prompt(path, fallback),
            model=model_override or resolve_agent_model("BA"),
            environment=environment_override or resolve_default_environment(),
            remote_provider=remote_provider,
            remote_api_key=remote_api_key,
            remote_base_url=remote_base_url,
            system_prompt_extra=system_prompt_extra,
        )


class ProblemSpotterAgent(BaseAgent):
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
            "You look for duplication, overloaded functions, and suspicious dependencies "
            "from the code analysis."
        )
        path = system_prompt_path_override or "specialized/code-problem-spotter.md"
        super().__init__(
            role="PROBLEM_SPOTTER",
            system_prompt=load_prompt(path, fallback),
            model=model_override or resolve_agent_model("REVIEWER"),
            environment=environment_override or resolve_default_environment(),
            remote_provider=remote_provider,
            remote_api_key=remote_api_key,
            remote_base_url=remote_base_url,
            system_prompt_extra=system_prompt_extra,
        )


class RefactorPlanAgent(BaseAgent):
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
            "You propose a step-by-step refactoring plan from the problem list and analysis."
        )
        path = system_prompt_path_override or "specialized/code-refactor-planner.md"
        super().__init__(
            role="REFACTOR_PLAN",
            system_prompt=load_prompt(path, fallback),
            model=model_override or resolve_agent_model("ARCH"),
            environment=environment_override or resolve_default_environment(),
            remote_provider=remote_provider,
            remote_api_key=remote_api_key,
            remote_base_url=remote_base_url,
            system_prompt_extra=system_prompt_extra,
        )


class CodeDiagramAgent(BaseAgent):

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
            "You build Mermaid diagrams and briefly describe module interactions from the "
            "analysis."
        )
        path = system_prompt_path_override or "specialized/code-structure-diagram.md"
        super().__init__(
            role="CODE_DIAGRAM",
            system_prompt=load_prompt(path, fallback),
            model=model_override or resolve_agent_model("ARCH"),
            environment=environment_override or resolve_default_environment(),
            remote_provider=remote_provider,
            remote_api_key=remote_api_key,
            remote_base_url=remote_base_url,
            system_prompt_extra=system_prompt_extra,
        )
