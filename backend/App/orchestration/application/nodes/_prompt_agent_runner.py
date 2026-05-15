from __future__ import annotations

import logging
import threading
from typing import Any, Optional, cast

from backend.App.orchestration.infrastructure.agents.base_agent import BaseAgent
from backend.App.orchestration.domain.exceptions import PipelineCancelled
from backend.App.shared.domain.exceptions import OperationCancelled
from backend.App.orchestration.application.agents.agent_runner import (
    run_agent_with_boundary as _canonical_run_agent_with_boundary,
    validate_agent_boundary as _canonical_validate_agent_boundary,
)
from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState

logger = logging.getLogger(__name__)


def _swarm_block(state: PipelineState) -> dict[str, Any]:
    agent_config = state.get("agent_config") or {}
    swarm_section = agent_config.get("swarm")
    return swarm_section if isinstance(swarm_section, dict) else {}


def run_agent_with_optional_mcp(
    agent: BaseAgent,
    prompt: str,
    state: PipelineState,
    *,
    readonly_tools: bool = False,
    max_tool_rounds: Optional[int] = None,
) -> tuple[str, str, str]:
    agent_config = state.get("agent_config") or {}
    swarm_section = _swarm_block(state)
    if swarm_section.get("skip_mcp_tools"):
        output = _canonical_run_agent_with_boundary(state, agent, prompt)
        return output, agent.used_model, agent.used_provider
    mcp = agent_config.get("mcp")
    cancel_ev: Optional[threading.Event] = cast(
        Optional[threading.Event], state.get("_pipeline_cancel_event"),
    )
    if isinstance(mcp, dict) and mcp.get("servers"):
        try:
            from backend.App.integrations.infrastructure.mcp.openai_loop.loop import (
                run_with_mcp_tools_openai_compat,
            )
            output, used_model, used_provider = run_with_mcp_tools_openai_compat(
                system_prompt=agent.effective_system_prompt(),
                user_content=prompt,
                model=agent.model,
                environment=agent.environment,
                remote_provider=agent.remote_provider,
                remote_api_key=agent.remote_api_key,
                remote_base_url=agent.remote_base_url,
                mcp_cfg=mcp,
                cancel_event=cancel_ev,
                readonly_tools=readonly_tools,
                **(
                    {"max_rounds": max_tool_rounds}
                    if max_tool_rounds is not None
                    else {}
                ),
            )
            _canonical_validate_agent_boundary(state, agent, prompt, output)
            return output, used_model, used_provider
        except PipelineCancelled:
            raise
        except OperationCancelled as exc:
            raise PipelineCancelled(detail=str(exc)) from exc
        except Exception as exc:
            return _retry_or_fallback_after_mcp_error(
                state=state,
                agent=agent,
                prompt=prompt,
                mcp=mcp,
                cancel_event=cancel_ev,
                readonly_tools=readonly_tools,
                max_tool_rounds=max_tool_rounds,
                exc=exc,
            )
    output = _canonical_run_agent_with_boundary(state, agent, prompt)
    if not output or not output.strip():
        logger.warning(
            "agent.run returned empty output for role=%s model=%s",
            agent.role, agent.model,
        )
    return output, agent.used_model, agent.used_provider


def _retry_or_fallback_after_mcp_error(
    *,
    state: PipelineState,
    agent: BaseAgent,
    prompt: str,
    mcp: dict[str, Any],
    cancel_event: Optional[threading.Event],
    readonly_tools: bool,
    max_tool_rounds: Optional[int],
    exc: Exception,
) -> tuple[str, str, str]:
    from backend.App.integrations.infrastructure.mcp.openai_loop.loop import (
        _mcp_fallback_allow,
        run_with_mcp_tools_openai_compat,
    )
    error_text = str(exc).lower()
    if "anthropic sdk is not supported" in error_text:
        logger.info(
            "MCP: Anthropic SDK detected for role=%s — skipping MCP tool loop, "
            "using plain agent.run instead.",
            agent.role,
        )
    else:
        is_context_overflow = (
            "tokens to keep" in error_text
            or ("context" in error_text and "length" in error_text)
            or "channel error" in error_text
            or "model has crashed" in error_text
        )
        if is_context_overflow and agent.remote_provider and agent.remote_api_key:
            logger.warning(
                "MCP: local model failed (role=%s model=%s) — retrying with remote "
                "profile (provider=%s). Error: %s",
                agent.role, agent.model, agent.remote_provider, exc,
            )
            try:
                return run_with_mcp_tools_openai_compat(
                    system_prompt=agent.effective_system_prompt(),
                    user_content=prompt,
                    model=agent.model,
                    environment="cloud",
                    remote_provider=agent.remote_provider,
                    remote_api_key=agent.remote_api_key,
                    remote_base_url=agent.remote_base_url,
                    mcp_cfg=mcp,
                    cancel_event=cancel_event,
                    readonly_tools=readonly_tools,
                    **(
                        {"max_rounds": max_tool_rounds}
                        if max_tool_rounds is not None
                        else {}
                    ),
                )
            except Exception as remote_exc:
                logger.error(
                    "MCP: remote retry also failed (role=%s provider=%s): %s",
                    agent.role, agent.remote_provider, remote_exc,
                )
                raise remote_exc from exc

        if not _mcp_fallback_allow():
            logger.error(
                "MCP tool-call loop failed for role=%s. "
                "Set SWARM_MCP_FALLBACK_ALLOW=1 to allow plain agent.run fallback. "
                "Error: %s",
                agent.role,
                exc,
                exc_info=True,
            )
            raise
        logger.warning(
            "MCP tool-call loop failed for role=%s; "
            "SWARM_MCP_FALLBACK_ALLOW=1 — продолжаем без инструментов. "
            "Error: %s",
            agent.role,
            exc,
            exc_info=True,
        )
    output = _canonical_run_agent_with_boundary(state, agent, prompt)
    return output, agent.used_model, agent.used_provider


def validate_agent_boundary(
    state: PipelineState,
    agent: BaseAgent,
    prompt: str,
    output: str,
) -> None:
    _canonical_validate_agent_boundary(state, agent, prompt, output)


def run_agent_with_boundary(
    state: PipelineState,
    agent: BaseAgent,
    prompt: str,
) -> str:
    return _canonical_run_agent_with_boundary(state, agent, prompt)


__all__ = (
    "run_agent_with_boundary",
    "run_agent_with_optional_mcp",
    "validate_agent_boundary",
)
