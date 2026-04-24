
from __future__ import annotations

from typing import Optional

from backend.App.orchestration.infrastructure.agents.base_agent import BaseAgent, load_prompt, resolve_agent_model, resolve_default_environment


class AICitationStrategistAgent(BaseAgent):
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
            "You are an AI Citation Strategist. Audit and improve brand visibility "
            "across AI recommendation engines (ChatGPT, Claude, Gemini, Perplexity) "
            "through Answer Engine Optimization (AEO) and Generative Engine Optimization (GEO)."
        )
        prompt_path = system_prompt_path_override or "marketing/marketing-ai-citation-strategist.md"
        super().__init__(
            role="AI_CITATION_STRATEGIST",
            system_prompt=load_prompt(prompt_path, fallback),
            model=model_override or resolve_agent_model("AI_CITATION_STRATEGIST"),
            environment=environment_override or resolve_default_environment(),
            remote_provider=remote_provider,
            remote_api_key=remote_api_key,
            remote_base_url=remote_base_url,
            system_prompt_extra=system_prompt_extra,
        )
