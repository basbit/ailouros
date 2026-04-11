"""AgentFactory domain port (L-2).

Rules (INV-7): domain layer — stdlib + typing only.
No infrastructure agent imports here.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class AgentFactoryPort(ABC):
    """Abstraction over agent construction.

    Application nodes call create() instead of directly instantiating
    infrastructure agent classes. This allows swapping implementations
    for testing and future model routing without touching node logic.
    """

    @abstractmethod
    def create(self, role: str, **kwargs: Any) -> Any:
        """Create an agent for the given role.

        Args:
            role: Agent role name (e.g. "reviewer", "pm", "dev", "qa", "ba", "arch").
            **kwargs: Role-specific configuration (model, agent_config, etc.).

        Returns:
            An agent instance that has a .run() or equivalent method.

        Raises:
            KeyError: if role is not registered in this factory.
        """
