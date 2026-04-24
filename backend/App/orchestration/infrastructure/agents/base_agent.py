
from __future__ import annotations

import json
import logging
import os
import queue
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from backend.App.integrations.infrastructure.llm.client import ask_model
from backend.App.integrations.infrastructure.llm.prompt_size import estimate_chat_request_size, format_size_hint_ru
from backend.App.integrations.infrastructure.llm.config import LMSTUDIO_BASE_URL, OLLAMA_BASE_URL, SWARM_MODEL_CLOUD_DEFAULT
from backend.App.integrations.infrastructure.model_discovery import load_models_config
from backend.App.orchestration.infrastructure.agents.role_model_policy import build_roles, planning_model_roles, planning_roles
from backend.App.orchestration.infrastructure.agents.model_resolver import (
    resolve_model as _resolve_model_new,
    resolve_base_url as _resolve_base_url_new,
)
from backend.App.orchestration.infrastructure.agents.prompt_loader import (
    PromptLoader,
    _strip_skill_frontmatter,
)
from backend.App.orchestration.infrastructure.agents.llm_backend_selector import (
    LLMBackendConfig,
    LLMBackendSelector,
)
from backend.App.shared.infrastructure.model_routing import detect_provider

logger = logging.getLogger(__name__)

__all__ = [
    "BaseAgent",
    "effective_cloud_provider",
    "load_prompt",
    "resolve_agent_model",
    "resolve_default_environment",
    "AgentModelResolver",
    "PromptLoader",
    "LLMBackendConfig",
    "LLMBackendSelector",
]


def resolve_default_environment() -> str:
    env = os.getenv("SWARM_DEFAULT_ENVIRONMENT", "").strip()
    if env:
        return env
    return "ollama"


class AgentModelResolver:

    @staticmethod
    def resolve_model(role: str, role_default_model: str) -> str:
        return _resolve_model_new(role, role_default_model)

    @staticmethod
    def resolve_base_url(role: str) -> str | None:
        return _resolve_base_url_new(role)


def effective_cloud_provider(
    remote_provider: Optional[str],
    environment: str,
    model_for_infer: str,
) -> str:
    """Thin shim over :func:`shared.infrastructure.model_routing.detect_provider`.

    Preserves the legacy positional signature used across orchestration.
    """
    return detect_provider(
        model_for_infer,
        remote_provider=remote_provider,
        environment=environment,
    )


PROJECT_ROOT = Path(os.environ.get("SWARM_PROJECT_ROOT", "")).resolve() if os.environ.get("SWARM_PROJECT_ROOT") else Path(__file__).resolve().parents[5]
PROMPTS_DIR = Path(os.environ.get("SWARM_PROMPTS_DIR", "")).resolve() if os.environ.get("SWARM_PROMPTS_DIR") else PROJECT_ROOT / "config" / "prompts"


def load_prompt(prompt_relative_path: str, fallback: str) -> str:
    import logging
    _log = logging.getLogger(__name__)

    rel = prompt_relative_path.strip().lstrip("/")
    if not rel:
        return fallback

    overrides_path = PROMPTS_DIR / "overrides" / rel
    if overrides_path.is_file():
        loaded = overrides_path.read_text(encoding="utf-8").strip()
        if loaded:
            _log.debug("Prompt resolved: path=%s source=overrides", rel)
            return _strip_skill_frontmatter(loaded)

    upstream_path = PROMPTS_DIR / "upstream" / rel
    if upstream_path.is_file():
        loaded = upstream_path.read_text(encoding="utf-8").strip()
        if loaded:
            _log.debug("Prompt resolved: path=%s source=upstream", rel)
            return _strip_skill_frontmatter(loaded)

    direct_path = PROMPTS_DIR / rel
    if direct_path.is_file():
        loaded = direct_path.read_text(encoding="utf-8").strip()
        if loaded:
            _log.debug("Prompt resolved: path=%s source=direct", rel)
            return _strip_skill_frontmatter(loaded)

    _log.debug("Prompt not found: path=%s — using fallback", rel)
    return fallback


def resolve_agent_model(role: str, role_default_model: str = "") -> str:
    role_key = role.upper()
    workspace_root = os.getenv("SWARM_WORKSPACE_ROOT", "").strip()
    if workspace_root:
        try:
            models_config = load_models_config(workspace_root)
        except Exception:
            models_config = None
        role_cfg = ((models_config or {}).get("roles") or {}).get(role.lower())
        if isinstance(role_cfg, dict):
            configured_model = str(role_cfg.get("model_id") or "").strip()
            if configured_model:
                return configured_model

    route_specific = os.getenv(f"SWARM_ROUTE_{role_key}")
    route = route_specific.lower() if route_specific else ""
    _route_planning_roles = planning_roles()
    _model_planning_roles = planning_model_roles()
    if not route:
        if role_key in _route_planning_roles:
            route = os.getenv("SWARM_ROUTE_PLANNING", "").lower()
        elif role_key in build_roles():
            route = os.getenv("SWARM_ROUTE_BUILD", "").lower()
    if not route:
        route = os.getenv("SWARM_ROUTE_DEFAULT", "local").lower()

    if route == "cloud":
        specific_cloud = os.getenv(f"SWARM_MODEL_CLOUD_{role_key}")
        if specific_cloud:
            return specific_cloud.strip()

        cloud_ba_arch = os.getenv("SWARM_MODEL_CLOUD_BA_ARCH", "").strip()
        if (
            role_key in {
                "BA",
                "ARCH",
                "REVIEWER",
                "STACK_REVIEWER",
                "REFACTOR_PLAN",
                "CODE_DIAGRAM",
                "DOC_GEN",
            }
            and cloud_ba_arch
        ):
            return cloud_ba_arch

        if role_key in _model_planning_roles:
            cloud_planning = os.getenv("SWARM_MODEL_CLOUD_PLANNING", "").strip()
            if cloud_planning:
                return cloud_planning
        if role_key in build_roles():
            cloud_build = os.getenv("SWARM_MODEL_CLOUD_BUILD", "").strip()
            if cloud_build:
                return cloud_build

        return os.getenv("SWARM_MODEL_CLOUD", SWARM_MODEL_CLOUD_DEFAULT).strip() or SWARM_MODEL_CLOUD_DEFAULT

    specific = os.getenv(f"SWARM_MODEL_{role_key}")
    if specific:
        return specific.strip()

    ba_arch = os.getenv("SWARM_MODEL_BA_ARCH", "").strip()
    if role_key in {
        "BA",
        "ARCH",
        "REVIEWER",
        "STACK_REVIEWER",
        "REFACTOR_PLAN",
        "CODE_DIAGRAM",
        "DOC_GEN",
    } and ba_arch:
        return ba_arch

    if role_key in _model_planning_roles:
        planning = os.getenv("SWARM_MODEL_PLANNING", "").strip()
        if planning:
            return planning

    if role_key in build_roles():
        build = os.getenv("SWARM_MODEL_BUILD", "").strip()
        if build:
            return build

    global_model = os.getenv("SWARM_MODEL", "").strip()
    if global_model:
        return global_model

    _all_known_roles = _route_planning_roles | _model_planning_roles | build_roles()
    if role_key not in _all_known_roles:
        for env_key in ("SWARM_MODEL_BUILD", "SWARM_MODEL_PLANNING"):
            group_model = os.getenv(env_key, "").strip()
            if group_model:
                logger.warning(
                    "resolve_agent_model(%s): role not in known groups — "
                    "falling back to %s=%s. Set SWARM_MODEL or SWARM_MODEL_%s to fix.",
                    role, env_key, group_model, role_key,
                )
                return group_model

    if role_default_model:
        logger.warning(
            "resolve_agent_model(%s): no SWARM_MODEL or SWARM_MODEL_%s set — "
            "falling back to deprecated hardcoded default %r. "
            "Set SWARM_MODEL in .env to fix this warning.",
            role, role_key, role_default_model,
        )
        return role_default_model
    raise ValueError(
        f"No model configured for role {role!r}. "
        f"Set SWARM_MODEL or SWARM_MODEL_{role_key} in your environment."
    )


def provider_from_model(model: str) -> str:
    model_l = model.lower()
    if model_l.startswith("claude") or model_l.startswith("anthropic/"):
        return "cloud:anthropic"
    return "local:ollama"


def _local_base_url_from_environment(environment: str) -> tuple[str, str]:
    env_key = (environment or "").lower()
    if env_key in {"lmstudio", "lm_studio"}:
        return (
            os.getenv("LMSTUDIO_BASE_URL", LMSTUDIO_BASE_URL),
            os.getenv("LMSTUDIO_API_KEY", "lm-studio"),
        )
    return (
        os.getenv("OPENAI_BASE_URL", OLLAMA_BASE_URL),
        os.getenv("OPENAI_API_KEY", "ollama"),
    )


def _has_unclosed_xml_tags(text: str) -> bool:
    all_opens = re.findall(r'<([a-zA-Z_][a-zA-Z0-9_]*)(?:\s[^>]*)?>', text)
    self_closing = set(re.findall(r'<([a-zA-Z_][a-zA-Z0-9_]*)(?:\s[^>]*)?\s*/>', text))
    opens = [t for t in all_opens if t not in self_closing]
    closes = re.findall(r'</([a-zA-Z_][a-zA-Z0-9_]*)>', text)
    counts: dict[str, int] = {}
    for tag in opens:
        counts[tag] = counts.get(tag, 0) + 1
    for tag in closes:
        if tag in counts:
            counts[tag] -= 1
    return any(v > 0 for v in counts.values())


@dataclass
class BaseAgent:
    role: str
    system_prompt: str
    model: str
    environment: str = ""
    used_model: str = ""
    used_provider: str = ""
    remote_provider: Optional[str] = None  # см. remote_presets / UI
    remote_api_key: Optional[str] = None
    remote_base_url: Optional[str] = None
    max_tokens: int = 0
    last_usage: dict = field(default_factory=dict)
    system_prompt_extra: str = ""
    truncation_retries: int = 0

    def effective_system_prompt(self) -> str:
        extra = (self.system_prompt_extra or "").strip()
        if not extra:
            return self.system_prompt
        return f"{self.system_prompt.rstrip()}\n\n### Agent skills (injected)\n\n{extra}"

    def run(self, user_input: str, *, _progress_queue: Any = None) -> str:
        self.truncation_retries = 0
        messages = [
            {"role": "system", "content": self.effective_system_prompt()},
            {"role": "user", "content": user_input},
        ]

        model = self.model
        req_size = estimate_chat_request_size(messages)

        est_chars = req_size.chars_total
        est_tokens = est_chars // 3
        logger.debug(
            "[%s] estimated input: %d chars (~%d tokens)",
            self.role,
            est_chars,
            est_tokens,
        )

        ctx_limit_str = os.getenv("SWARM_LLM_CONTEXT_TOKENS", "").strip()
        if ctx_limit_str:
            try:
                ctx_limit = int(ctx_limit_str)
                threshold_80 = int(ctx_limit * 0.8)
                if est_tokens > threshold_80:
                    logger.warning(
                        "[%s] estimated input ~%d tokens exceeds 80%% of "
                        "SWARM_LLM_CONTEXT_TOKENS=%d (threshold=%d). "
                        "Consider lowering SWARM_WORKSPACE_MAX_BYTES or setting "
                        "SWARM_INPUT_MAX_CHARS. %s",
                        self.role,
                        est_tokens,
                        ctx_limit,
                        threshold_80,
                        format_size_hint_ru(req_size, model),
                    )
            except ValueError:
                pass

        selector = LLMBackendSelector()
        _effective_env = self.environment or resolve_default_environment()
        cfg = selector.select(
            role=self.role,
            model=model,
            environment=_effective_env,
            remote_provider=self.remote_provider,
            remote_api_key=self.remote_api_key,
            remote_base_url=self.remote_base_url,
            max_tokens=self.max_tokens,
        )
        ask_kwargs = selector.ask_kwargs(cfg)

        if cfg.llm_route == "anthropic":
            _tgt = cfg.anthropic_base_url or "anthropic_sdk_default"
        else:
            _tgt = cfg.base_url or "?"
        logger.info(
            "BaseAgent.run: role=%s model=%s environment=%r target=%r user_chars=%d",
            self.role,
            model,
            self.environment,
            _tgt[:200],
            len(user_input),
        )

        llm_response, usage = ask_model(messages=messages, model=model, **ask_kwargs)
        self.last_usage = usage
        try:
            from backend.App.integrations.infrastructure.llm.client import _accumulate_thread_usage
            _accumulate_thread_usage(usage)
        except Exception:
            logger.debug("Failed to accumulate thread usage", exc_info=True)
        self.used_model = model
        self.used_provider = cfg.provider_label or f"local:{resolve_default_environment()}"

        _max_truncation_retries = int(os.getenv("SWARM_TRUNCATION_MAX_RETRIES", "2"))
        accumulated = llm_response
        for _retry_n in range(_max_truncation_retries):
            if not _has_unclosed_xml_tags(accumulated):
                break
            logger.warning(
                "[%s] output_truncated: unclosed XML tags detected on attempt %d/%d — "
                "retrying with CONTINUE prompt. role=%s model=%s",
                self.role, _retry_n + 1, _max_truncation_retries, self.role, model,
            )
            self.truncation_retries += 1
            if isinstance(_progress_queue, queue.Queue):
                try:
                    _progress_queue.put(json.dumps({
                        "_event_type": "output_truncated",
                        "role": self.role,
                        "attempt": _retry_n + 1,
                        "message": f"[{self.role}] output truncated — resuming (attempt {_retry_n + 1}/{_max_truncation_retries})",
                    }))
                except Exception:
                    pass
            messages.append({"role": "assistant", "content": accumulated})
            messages.append({"role": "user", "content": "[CONTINUE FROM WHERE YOU LEFT OFF]"})
            continuation, cont_usage = ask_model(messages=messages, model=model, **ask_kwargs)
            accumulated = accumulated + "\n" + continuation
            try:
                self.last_usage = {
                    "input_tokens": (self.last_usage.get("input_tokens") or 0) + (cont_usage.get("input_tokens") or 0),
                    "output_tokens": (self.last_usage.get("output_tokens") or 0) + (cont_usage.get("output_tokens") or 0),
                }
                _accumulate_thread_usage(cont_usage)
            except Exception:
                pass
        else:
            if _has_unclosed_xml_tags(accumulated):
                logger.error(
                    "[%s] output_truncated: still truncated after %d retries — "
                    "returning partial output. role=%s",
                    self.role, _max_truncation_retries, self.role,
                )

        return accumulated
