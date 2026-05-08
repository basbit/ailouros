
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

from backend.App.integrations.infrastructure.llm.remote_presets import resolve_openai_compat_base_url, uses_anthropic_sdk
from backend.App.integrations.infrastructure.llm.config import LMSTUDIO_BASE_URL, OLLAMA_BASE_URL

logger = logging.getLogger(__name__)


@dataclass
class LLMBackendConfig:

    base_url: str
    api_key: str
    model: str
    llm_route: str = ""  # "anthropic" | "openai" | ""
    anthropic_base_url: str = ""
    anthropic_api_key: str = ""
    max_tokens: int = 0
    provider_label: str = ""  # e.g. "cloud:anthropic", "cloud:gemini", "local:ollama"


def _effective_cloud_provider(
    remote_provider: Optional[str],
    environment: str,
    model_for_infer: str,
) -> str:
    env_key = (environment or "").lower()
    pr = (remote_provider or "").strip().lower()
    if env_key == "anthropic":
        return pr or "anthropic"
    if pr:
        return pr
    m = (model_for_infer or "").strip().lower()
    if m.startswith("gemini"):
        return "gemini"
    if (
        m.startswith("gpt") or m.startswith("o1") or m.startswith("o3")
        or m.startswith("chatgpt") or m.startswith("openai/")
    ):
        return "openai_compatible"
    logger.warning(
        "Unknown cloud model prefix for %r, defaulting to anthropic backend — "
        "set SWARM_REMOTE_PROVIDER to override",
        model_for_infer,
    )
    return "anthropic"


def _local_base_url_from_environment(environment: str) -> tuple[str, str]:
    env_key = (environment or "").lower()
    if env_key in {"lmstudio", "lm_studio"}:
        return (
            os.getenv("LMSTUDIO_BASE_URL", LMSTUDIO_BASE_URL),
            os.getenv("LMSTUDIO_API_KEY", "lm-studio"),
        )
    if env_key in {"local", "llamacpp", "llama_cpp"}:
        return (
            os.getenv("AILOUROS_LLM_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or os.getenv("OPENAI_API_BASE")
            or "http://localhost:8080/v1",
            os.getenv("AILOUROS_LLM_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or "sk-no-key-required",
        )
    return (
        os.getenv("OLLAMA_BASE_URL", OLLAMA_BASE_URL),
        os.getenv("OLLAMA_API_KEY", "ollama"),
    )


class LLMBackendSelector:

    def select(
        self,
        role: str,
        model: str,
        environment: str,
        remote_provider: Optional[str] = None,
        remote_api_key: Optional[str] = None,
        remote_base_url: Optional[str] = None,
        max_tokens: int = 0,
        state: Optional[dict[str, Any]] = None,
    ) -> LLMBackendConfig:
        env_key = (environment or "").lower()
        cfg = LLMBackendConfig(base_url="", api_key="", model=model, max_tokens=max_tokens)

        if env_key in {"local", "llamacpp", "llama_cpp"}:
            base_url, api_key = _local_base_url_from_environment(environment)
            cfg.base_url = base_url
            cfg.api_key = api_key
            cfg.llm_route = "openai"
            cfg.provider_label = "local:llamacpp"

        elif env_key in {"lmstudio", "lm_studio", "ollama", ""}:
            base_url, api_key = _local_base_url_from_environment(environment)
            cfg.base_url = base_url
            cfg.api_key = api_key
            cfg.provider_label = f"local:{env_key or 'ollama'}"

        elif env_key in {"cloud", "anthropic"}:
            prov = _effective_cloud_provider(remote_provider, environment, model)
            if uses_anthropic_sdk(prov):
                cfg.llm_route = "anthropic"
                if remote_api_key:
                    cfg.anthropic_api_key = remote_api_key
                if remote_base_url:
                    cfg.anthropic_base_url = remote_base_url
                cfg.provider_label = "cloud:anthropic"
            else:
                bu = resolve_openai_compat_base_url(prov, remote_base_url)
                ky = (remote_api_key or "").strip()
                if not ky:
                    ky = (os.getenv("OPENAI_API_KEY", "ollama") or "ollama").strip()
                cfg.base_url = bu
                cfg.api_key = ky
                cfg.llm_route = "openai"
                cfg.provider_label = f"cloud:{prov}" if prov else "cloud:openai_compatible"

        return cfg

    def ask_kwargs(self, cfg: LLMBackendConfig) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if cfg.llm_route == "anthropic":
            if cfg.anthropic_api_key:
                kwargs["anthropic_api_key"] = cfg.anthropic_api_key
            if cfg.anthropic_base_url:
                kwargs["anthropic_base_url"] = cfg.anthropic_base_url
            kwargs["llm_route"] = "anthropic"
        elif cfg.llm_route == "openai":
            kwargs["base_url"] = cfg.base_url
            kwargs["api_key"] = cfg.api_key
            kwargs["llm_route"] = "openai"
        else:
            if cfg.base_url:
                kwargs["base_url"] = cfg.base_url
            if cfg.api_key:
                kwargs["api_key"] = cfg.api_key
        if cfg.max_tokens > 0:
            kwargs["max_tokens"] = cfg.max_tokens
        return kwargs
