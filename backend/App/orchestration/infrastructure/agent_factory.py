from __future__ import annotations

import logging
from typing import Any

from backend.App.orchestration.domain.agent_factory import AgentFactoryPort

logger = logging.getLogger(__name__)


class ConcreteAgentFactory(AgentFactoryPort):

    def create(self, role: str, **kwargs: Any) -> Any:
        creator = self._registry.get(role)
        if creator is None:
            raise KeyError(
                f"AgentFactory: unknown role '{role}'. Known: {list(self._registry)}"
            )
        logger.debug("AgentFactory: creating agent role=%s", role)
        return creator(**kwargs)

    @property
    def _registry(self) -> dict[str, Any]:
        from backend.App.orchestration.infrastructure.agents.reviewer_agent import ReviewerAgent
        from backend.App.orchestration.infrastructure.agents.pm_agent import PMAgent
        from backend.App.orchestration.infrastructure.agents.dev_agent import DevAgent
        from backend.App.orchestration.infrastructure.agents.qa_agent import QAAgent
        from backend.App.orchestration.infrastructure.agents.ba_agent import BAAgent
        from backend.App.orchestration.infrastructure.agents.arch_agent import ArchitectAgent
        from backend.App.orchestration.infrastructure.agents.devops_agent import DevopsAgent
        from backend.App.orchestration.infrastructure.agents.stack_reviewer_agent import StackReviewerAgent
        from backend.App.orchestration.infrastructure.agents.dev_lead_agent import DevLeadAgent
        return {
            "reviewer": ReviewerAgent,
            "pm": PMAgent,
            "dev": DevAgent,
            "qa": QAAgent,
            "ba": BAAgent,
            "arch": ArchitectAgent,
            "devops": DevopsAgent,
            "stack_reviewer": StackReviewerAgent,
            "dev_lead": DevLeadAgent,
        }
