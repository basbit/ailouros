from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from backend.App.shared.domain.ports import ObservabilityPort  # noqa: F401


class DocumentationFetchPort(ABC):
    @abstractmethod
    def fetch(self, url: str, *, max_chars: int = 50_000) -> str:
        ...


class RemoteModelRegistryPort(ABC):
    @abstractmethod
    def list_models(self, provider: str) -> list[str]:
        ...


class PromptTemplateRepositoryPort(ABC):
    @abstractmethod
    def get_template(self, name: str) -> str:
        ...


class SkillRepositoryPort(ABC):
    @abstractmethod
    def get_skill(self, skill_id: str) -> str:
        ...


class LLMCachePort(ABC):
    @abstractmethod
    def make_key(self, messages: list[dict[str, Any]], model: str, temperature: float) -> str:
        ...

    @abstractmethod
    def get(self, key: str) -> tuple[str, dict[str, Any]] | None:
        ...

    @abstractmethod
    def set(self, key: str, text: str, usage: dict[str, Any]) -> None:
        ...
