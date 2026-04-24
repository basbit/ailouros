from __future__ import annotations

import logging
import os
import time
import threading
from typing import Any, Optional

from openai import OpenAI

from backend.App.orchestration.infrastructure.agents.base_agent import (
    _local_base_url_from_environment,
    effective_cloud_provider,
)
from backend.App.integrations.infrastructure.llm.remote_presets import uses_anthropic_sdk
from backend.App.integrations.infrastructure.llm.client import make_openai_client
from backend.App.integrations.infrastructure.llm.remote_presets import resolve_openai_compat_base_url
from backend.App.integrations.infrastructure.mcp.stdio.session import MCPPool, load_mcp_server_defs
from backend.App.integrations.infrastructure.mcp.openai_loop.config import (
    _model_context_size_tokens,
    _mcp_fallback_allow,
)
from backend.App.integrations.infrastructure.mcp.openai_loop.context_manager import (
    _compute_user_content_budget,
    compute_user_content_budget_from_env,
)
from backend.App.integrations.infrastructure.mcp.openai_loop.tool_loop import (
    MCPToolLoop,
    _mcp_serialize_acquire_timeout_sec,
)

logger = logging.getLogger(__name__)

__all__ = [
    "run_with_mcp_tools_openai_compat",
    "_model_context_size_tokens",
    "_mcp_fallback_allow",
    "MCPToolLoop",
    "_mcp_serialize_acquire_timeout_sec",
]


def _build_openai_client_for_env(
    environment: str,
    model: str,
    *,
    remote_provider: Optional[str],
    remote_api_key: Optional[str],
    remote_base_url: Optional[str],
) -> tuple[OpenAI, str]:
    env_key = (environment or "").lower()
    if env_key in {"lmstudio", "lm_studio", "ollama", ""}:
        base_url, api_key = _local_base_url_from_environment(environment)
        return (
            make_openai_client(base_url=base_url, api_key=api_key),
            f"local:{env_key or 'ollama'}",
        )

    if env_key in {"cloud", "anthropic"}:
        provider_id = effective_cloud_provider(remote_provider, environment, model)
        if uses_anthropic_sdk(provider_id):
            raise ValueError(
                "MCP tool loop: native Anthropic SDK is not supported; "
                "an OpenAI-compatible endpoint is required (remote_api.base_url + provider)."
            )
        resolved_base_url = resolve_openai_compat_base_url(provider_id, remote_base_url)
        api_key_resolved = (remote_api_key or "").strip() or (os.getenv("OPENAI_API_KEY", "") or "").strip()
        if not api_key_resolved:
            api_key_resolved = "ollama"
        return make_openai_client(base_url=resolved_base_url, api_key=api_key_resolved), f"cloud:{provider_id}"

    base_url, api_key = _local_base_url_from_environment("ollama")
    return make_openai_client(base_url=base_url, api_key=api_key), "local:ollama"


def run_with_mcp_tools_openai_compat(
    *,
    system_prompt: str,
    user_content: str,
    model: str,
    environment: str = "ollama",
    remote_provider: Optional[str] = None,
    remote_api_key: Optional[str] = None,
    remote_base_url: Optional[str] = None,
    mcp_cfg: Any,
    temperature: float = 0.2,
    max_rounds: int = 8,
    cancel_event: Optional[threading.Event] = None,
    readonly_tools: bool = False,
) -> tuple[str, str, str]:
    defs = load_mcp_server_defs(mcp_cfg)
    if not defs:
        raise ValueError("mcp_cfg: no valid servers found")

    client, prov_label = _build_openai_client_for_env(
        environment,
        model,
        remote_provider=remote_provider,
        remote_api_key=remote_api_key,
        remote_base_url=remote_base_url,
    )

    _web_search_enabled = bool(os.getenv("_WEB_SEARCH_ENABLED", ""))
    _ddg_enabled = bool(os.getenv("_DDG_SEARCH_ENABLED", ""))
    _fetch_page_enabled = bool(os.getenv("_FETCH_PAGE_ENABLED", ""))
    _workspace_root = os.getenv("SWARM_WORKSPACE_ROOT", "").strip()

    pool_start_time = time.monotonic()
    with MCPPool(defs, cancel_event=cancel_event) as pool:
        tools = pool.openai_tools(readonly=readonly_tools)

        if os.getenv("SWARM_MCP_FILTER_DEPRECATED_TOOLS", "1").strip() not in ("0", "false", "no", "off"):
            _before = len(tools)
            tools = [
                t for t in tools
                if "DEPRECATED" not in (t.get("function", {}).get("description", "") or "")
            ]
            _removed = _before - len(tools)
            if _removed:
                logger.info("MCP: filtered %d deprecated tool(s) from tool list", _removed)

        if _web_search_enabled:
            from backend.App.integrations.infrastructure.mcp.web_search.web_search_router import (
                web_search_mcp_tool_definition,
                web_search_available,
            )
            if web_search_available():
                existing_tool_names = {
                    t.get("function", {}).get("name", "") for t in tools
                }
                if "web_search" not in existing_tool_names:
                    tools.append(web_search_mcp_tool_definition())
                    logger.info("MCP: web_search tool injected (provider router)")

        if _ddg_enabled:
            from backend.App.integrations.infrastructure.mcp.web_search.ddg_search import (
                ddg_search_mcp_tool_definition,
                ddg_search_available,
            )
            if ddg_search_available():
                existing_tool_names = {
                    t.get("function", {}).get("name", "") for t in tools
                }
                if "web_search" not in existing_tool_names:
                    tools.append(ddg_search_mcp_tool_definition())
                    logger.info("MCP: DDG web_search tool injected (no provider keys, DDG available)")
            else:
                logger.warning(
                    "MCP: DDG search enabled but duckduckgo-search package not installed. "
                    "Web search will not be available."
                )

        if _fetch_page_enabled:
            from backend.App.integrations.infrastructure.mcp.web_search.fetch_page import (
                fetch_page_tool_definition,
                fetch_page_available,
            )
            if fetch_page_available():
                existing_tool_names = {
                    t.get("function", {}).get("name", "") for t in tools
                }
                if "fetch_page" not in existing_tool_names:
                    tools.append(fetch_page_tool_definition())
                    logger.info("MCP: fetch_page tool injected")

        if _workspace_root:
            from backend.App.integrations.infrastructure.mcp.evidence_tools import (
                evidence_tools_available,
                evidence_tools_definitions,
            )
            if evidence_tools_available(_workspace_root):
                existing_tool_names = {
                    t.get("function", {}).get("name", "") for t in tools
                }
                for tool in evidence_tools_definitions():
                    name = tool.get("function", {}).get("name", "")
                    if name and name not in existing_tool_names:
                        tools.append(tool)
                logger.info("MCP: local evidence tools injected for workspace=%s", _workspace_root)

            from backend.App.integrations.infrastructure.mcp.wiki_tools import (
                wiki_tools_available,
                wiki_tools_definitions,
            )
            if wiki_tools_available(_workspace_root):
                existing_tool_names = {
                    t.get("function", {}).get("name", "") for t in tools
                }
                for tool in wiki_tools_definitions():
                    name = tool.get("function", {}).get("name", "")
                    if name and name not in existing_tool_names:
                        tools.append(tool)
                logger.info("MCP: wiki tools injected for workspace=%s", _workspace_root)

        if not tools:
            logger.warning("MCP: tools/list is empty — falling back to plain agent.run")
            raise RuntimeError("MCP: tools/list is empty — check your servers")

        pool_ready_elapsed_ms = (time.monotonic() - pool_start_time) * 1000
        logger.info(
            "MCP: phase=ready prov=%s model=%s tool_count=%d elapsed_ms=%.0f",
            prov_label, model, len(tools), pool_ready_elapsed_ms,
        )

        from backend.App.integrations.infrastructure.llm.context_size_resolver import resolve_context_size
        _ctx_tokens = resolve_context_size(model, environment)
        if _ctx_tokens > 0:
            _chars_per_tok = 3
            _tools_chars = sum(
                len(str(t.get("function", {}).get("description") or ""))
                + len(str(t.get("function", {}).get("parameters") or ""))
                for t in tools
            )
            _sys_tokens = len(system_prompt) // _chars_per_tok
            _tools_tokens = _tools_chars // _chars_per_tok
            _min_user_tokens = 300
            _reserve = 512
            _max_sys_tokens = _ctx_tokens - _tools_tokens - _reserve - _min_user_tokens
            if _sys_tokens > _max_sys_tokens > 0:
                _new_len = _max_sys_tokens * _chars_per_tok
                logger.warning(
                    "MCP: system_prompt too large (%d chars, ~%d tokens) for model context "
                    "(%d tokens) — trimming to %d chars. Consider a shorter prompt or larger model.",
                    len(system_prompt), _sys_tokens, _ctx_tokens, _new_len,
                )
                system_prompt = system_prompt[:_new_len] + "\n…[system prompt trimmed to fit context]"

        effective_user_content = user_content
        _reserve_raw = os.getenv("SWARM_MODEL_CONTEXT_RESERVE_TOKENS", "512").strip()
        _reserve_tokens = int(_reserve_raw) if _reserve_raw.isdigit() else 512
        user_budget = _compute_user_content_budget(
            system_prompt,
            tools,
            model_context_size_tokens=_ctx_tokens,
            model_context_reserve_tokens=_reserve_tokens,
        ) or compute_user_content_budget_from_env(system_prompt, tools)
        if user_budget > 0 and len(effective_user_content) > user_budget:
            logger.warning(
                "MCP: user_content truncated from %d to %d chars "
                "(SWARM_MODEL_CONTEXT_SIZE=%d tokens, budget=%d chars). "
                "Increase SWARM_MODEL_CONTEXT_RESERVE_TOKENS or raise model n_ctx.",
                len(effective_user_content), user_budget,
                _model_context_size_tokens(), user_budget,
            )
            effective_user_content = (
                effective_user_content[:user_budget]
                + "\n…[user_content truncated — set SWARM_MODEL_CONTEXT_SIZE and "
                "SWARM_MODEL_CONTEXT_RESERVE_TOKENS to adjust]"
            )

        effective_user_content = (
            effective_user_content
            + "\n\nAfter using tools to read and analyse files, write your complete "
            "output as text. Always finish with a full written response."
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": effective_user_content},
        ]

        loop = MCPToolLoop(
            client=client,
            pool=pool,
            model=model,
            prov_label=prov_label,
            cancel_event=cancel_event,
            web_search_enabled=_web_search_enabled,
            ddg_enabled=_ddg_enabled,
            fetch_page_enabled=_fetch_page_enabled,
        )
        result = loop.run(
            messages=messages,
            tools=tools,
            user_content=user_content,
            max_rounds=max_rounds,
            temperature=temperature,
        )
        _last_mcp_write_count.count = getattr(loop, '_last_mcp_write_count', 0)
        _last_mcp_write_count.actions = getattr(loop, '_last_mcp_write_actions', [])
        _last_mcp_telemetry.tool_call_rounds = getattr(loop, '_last_tool_call_rounds', 0)
        _last_mcp_telemetry.tool_parser_failures = getattr(loop, '_last_tool_parser_failures', 0)
        _last_mcp_telemetry.files_read_count = getattr(loop, '_last_files_read_count', 0)
        _last_mcp_telemetry.file_read_cache_hits = getattr(loop, '_last_file_read_cache_hits', 0)
        _last_mcp_telemetry.file_read_cache_misses = getattr(loop, '_last_file_read_cache_misses', 0)
        _last_mcp_telemetry.time_to_first_tool = getattr(loop, '_last_time_to_first_tool', None)
        _last_mcp_telemetry.time_after_last_tool_until_finish = getattr(loop, '_last_time_after_last_tool_until_finish', None)
        return result


_last_mcp_write_count = threading.local()
_last_mcp_write_count.count = 0
_last_mcp_write_count.actions = []

_last_mcp_telemetry = threading.local()
_last_mcp_telemetry.tool_call_rounds = 0
_last_mcp_telemetry.tool_parser_failures = 0
_last_mcp_telemetry.files_read_count = 0
_last_mcp_telemetry.file_read_cache_hits = 0
_last_mcp_telemetry.file_read_cache_misses = 0
_last_mcp_telemetry.time_to_first_tool = None
_last_mcp_telemetry.time_after_last_tool_until_finish = None
