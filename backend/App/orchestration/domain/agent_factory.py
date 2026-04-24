from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class AgentFactoryPort(ABC):

    @abstractmethod
    def create(self, role: str, **kwargs: Any) -> Any:
        ...
