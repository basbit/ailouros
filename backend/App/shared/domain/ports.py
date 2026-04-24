"""Cross-cutting ports declared at the ``shared`` boundary.

Only truly cross-cutting contracts live here. Domain-specific ports (e.g.
``TaskStorePort``) belong inside their owning domain (``tasks/domain/ports``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import AbstractContextManager
from typing import Any, Optional

__all__ = ["ObservabilityPort"]


class ObservabilityPort(ABC):
    @abstractmethod
    def record_metric(
        self,
        name: str,
        value: float,
        tags: Optional[dict[str, str]] = None,
    ) -> None:
        pass

    @abstractmethod
    def trace_step(self, step_id: str, data: dict[str, Any]) -> None:
        pass

    @abstractmethod
    def step_span_ctx(
        self, step_id: str, state: dict[str, Any]
    ) -> AbstractContextManager[None]:
        pass
