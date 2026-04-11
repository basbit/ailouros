"""Integrations domain ports.

Rules (INV-7): this module MUST NOT import fastapi, redis, httpx, openai,
anthropic, langgraph, or subprocess. Only stdlib + typing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import AbstractContextManager
from typing import Any, Optional


class DocumentationFetchPort(ABC):
    """Abstraction over remote documentation retrieval."""

    @abstractmethod
    def fetch(self, url: str, *, max_chars: int = 50_000) -> str:
        """Fetch documentation from *url*, truncating at *max_chars*.

        Returns:
            The fetched content as plain text.

        Raises:
            RuntimeError: if the fetch fails and no fallback is available.
        """


class RemoteModelRegistryPort(ABC):
    """Abstraction over remote model provider registries."""

    @abstractmethod
    def list_models(self, provider: str) -> list[str]:
        """Return available model IDs for *provider*.

        Args:
            provider: Provider name (e.g. "ollama", "lmstudio", "openai").

        Returns:
            List of model identifier strings.
        """


class ObservabilityPort(ABC):
    """Abstraction over metrics and tracing infrastructure."""

    @abstractmethod
    def record_metric(
        self,
        name: str,
        value: float,
        tags: Optional[dict[str, str]] = None,
    ) -> None:
        """Record a numeric metric.

        Args:
            name: Metric name (e.g. "pipeline.step.latency_ms").
            value: Numeric value.
            tags: Optional key-value labels (e.g. {"agent": "pm", "step": "ba"}).
        """

    @abstractmethod
    def trace_step(self, step_id: str, data: dict[str, Any]) -> None:
        """Emit a structured trace event for a pipeline step.

        Args:
            step_id: Unique identifier for the step being traced.
            data: Arbitrary key-value data to include in the trace event.
        """

    @abstractmethod
    def step_span_ctx(self, step_id: str, state: dict[str, Any]) -> AbstractContextManager[None]:
        """Return a context manager that wraps a pipeline step span.

        Args:
            step_id: Unique identifier for the step being spanned.
            state: Current pipeline state dict passed to the span.
        """


class PromptTemplateRepositoryPort(ABC):
    """Abstraction over prompt template storage."""

    @abstractmethod
    def get_template(self, name: str) -> str:
        """Return the prompt template for *name*.

        Args:
            name: Template name (e.g. "pm_system", "dev_task").

        Returns:
            Template string (may contain placeholders like {goal}).

        Raises:
            KeyError: if the template does not exist.
        """


class SkillRepositoryPort(ABC):
    """Abstraction over agent skill storage."""

    @abstractmethod
    def get_skill(self, skill_id: str) -> str:
        """Return the skill definition for *skill_id*.

        Args:
            skill_id: Unique skill identifier.

        Returns:
            Skill content as a string (e.g. Markdown or JSON).

        Raises:
            KeyError: if the skill does not exist.
        """


class LLMCachePort(ABC):
    """Abstract port for LLM response caching.

    Infrastructure provides concrete implementations (e.g. Redis, in-memory).
    The domain only depends on this interface (INV-7).
    """

    @abstractmethod
    def make_key(self, messages: list[dict[str, Any]], model: str, temperature: float) -> str:
        """Derive a deterministic cache key from the request inputs.

        Args:
            messages: List of chat message dicts (role + content).
            model: Model identifier string.
            temperature: Sampling temperature.

        Returns:
            A string cache key suitable for use with ``get`` and ``set``.
        """
        ...

    @abstractmethod
    def get(self, key: str) -> tuple[str, dict[str, Any]] | None:
        """Return the cached ``(text, usage)`` pair or ``None`` on cache miss.

        Args:
            key: Cache key produced by ``make_key``.

        Returns:
            Tuple of (response_text, usage_dict) if cached, else ``None``.
        """
        ...

    @abstractmethod
    def set(self, key: str, text: str, usage: dict[str, Any]) -> None:
        """Store a response in the cache.

        Args:
            key: Cache key produced by ``make_key``.
            text: LLM response text.
            usage: Usage metadata dict (e.g. token counts).
        """
        ...
