"""AgenticBaseAgent — BaseAgent subclass with a tool execution loop.

§12.10 — Enables agent-as-tool pattern, DialogueLoop, and wiki tools.

Supports:
- Anthropic tool_use (native message format)
- OpenAI function_calling (via compatible API)
- Internal registered tools (callables)

Environment:
    SWARM_AGENT_MAX_TOOL_ROUNDS (int, default 10) — max tool-call iterations.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from backend.App.integrations.infrastructure.llm.client import ask_model
from backend.App.orchestration.infrastructure.agents.base_agent import BaseAgent

logger = logging.getLogger(__name__)

_MAX_TOOL_ROUNDS = int(os.getenv("SWARM_AGENT_MAX_TOOL_ROUNDS", "10"))


@dataclass
class AgenticBaseAgent(BaseAgent):
    """BaseAgent with an agentic tool-execution loop.

    Register tools via :meth:`register_tool`.  On each LLM round, if the
    response contains ``tool_use`` / ``function_call`` blocks, the registered
    callable is invoked and its return value is fed back as a tool result.
    The loop continues until the LLM returns plain text or *max_tool_rounds*
    is exhausted.

    Usage::

        agent = AgenticBaseAgent(
            role="researcher",
            system_prompt="You are a researcher...",
            model="claude-sonnet-4-6",
        )
        agent.register_tool("web_search", my_search_fn)
        result = agent.run("Find information about X")
    """

    # Tool registry: name → callable(input_dict) → str
    _tools: dict[str, Callable[[dict[str, Any]], str]] = field(
        default_factory=dict, init=False, repr=False
    )
    # Tool schemas for LLM (Anthropic / OpenAI format)
    _tool_schemas: list[dict[str, Any]] = field(
        default_factory=list, init=False, repr=False
    )
    # Rounds used in the last run()
    tool_rounds_used: int = field(default=0, init=False, repr=False)

    def register_tool(
        self,
        name: str,
        fn: Callable[[dict[str, Any]], str],
        *,
        description: str = "",
        input_schema: dict[str, Any] | None = None,
    ) -> None:
        """Register a callable as a tool the agent can invoke.

        Args:
            name: Tool name (must match what the LLM calls).
            fn: Callable receiving a dict of inputs, returning a string result.
            description: Human-readable description for the LLM.
            input_schema: JSON Schema dict for the tool's input parameters.
        """
        self._tools[name] = fn
        schema: dict[str, Any] = {
            "name": name,
            "description": description or name,
            "input_schema": input_schema or {"type": "object", "properties": {}},
        }
        # Replace existing schema if already registered
        self._tool_schemas = [s for s in self._tool_schemas if s["name"] != name]
        self._tool_schemas.append(schema)

    def run(self, user_input: str, *, _progress_queue: Any = None) -> str:
        """Run with agentic tool loop.

        1. Call LLM with system + user + registered tool schemas.
        2. If response contains tool_use / function_call → execute tool → append result.
        3. Repeat until LLM returns plain text (no tool calls) or max_tool_rounds.
        4. Return the final text response.
        """
        self.truncation_retries = 0
        self.tool_rounds_used = 0

        if not self._tool_schemas:
            # No tools registered — fall back to BaseAgent.run() directly
            return super().run(user_input, _progress_queue=_progress_queue)

        from backend.App.orchestration.infrastructure.agents.base_agent import (
            resolve_default_environment,
        )
        from backend.App.orchestration.infrastructure.agents.llm_backend_selector import (
            LLMBackendSelector,
        )

        selector = LLMBackendSelector()
        _effective_env = self.environment or resolve_default_environment()
        cfg = selector.select(
            role=self.role,
            model=self.model,
            environment=_effective_env,
            remote_provider=self.remote_provider,
            remote_api_key=self.remote_api_key,
            remote_base_url=self.remote_base_url,
            max_tokens=self.max_tokens,
        )
        ask_kwargs = selector.ask_kwargs(cfg)

        # Build initial message list
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.effective_system_prompt()},
            {"role": "user", "content": user_input},
        ]

        # Inject tool schemas (Anthropic native format)
        ask_kwargs["tools"] = self._tool_schemas

        max_rounds = int(os.getenv("SWARM_AGENT_MAX_TOOL_ROUNDS", str(_MAX_TOOL_ROUNDS)))
        final_text = ""

        for _round in range(max_rounds + 1):
            self.tool_rounds_used = _round
            logger.debug(
                "AgenticBaseAgent.run: role=%s round=%d/%d",
                self.role, _round, max_rounds,
            )
            llm_response, usage = ask_model(messages=messages, model=self.model, **ask_kwargs)
            self.last_usage = usage
            try:
                from backend.App.integrations.infrastructure.llm.client import _accumulate_thread_usage
                _accumulate_thread_usage(usage)
            except Exception:
                pass
            self.used_model = self.model
            self.used_provider = cfg.provider_label or f"local:{resolve_default_environment()}"

            # Check if response contains tool_use blocks (Anthropic) or function_call
            tool_calls = _extract_tool_calls(llm_response)
            if not tool_calls:
                # Plain text response — done
                final_text = llm_response if isinstance(llm_response, str) else str(llm_response)
                break

            # Execute tool calls and append results
            messages.append({"role": "assistant", "content": llm_response})
            for tc in tool_calls:
                tool_name = tc.get("name", "")
                tool_input = tc.get("input") or {}
                tool_use_id = tc.get("id", "")
                result_str = self._execute_tool(tool_name, tool_input, _progress_queue)
                # Append tool result in Anthropic format
                messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": result_str,
                    }],
                })
        else:
            logger.warning(
                "AgenticBaseAgent.run: max_tool_rounds=%d exhausted for role=%s — "
                "returning last LLM response as plain text",
                max_rounds, self.role,
            )
            final_text = llm_response if isinstance(llm_response, str) else str(llm_response)

        return final_text

    def _execute_tool(
        self,
        name: str,
        input_dict: dict[str, Any],
        progress_queue: Any,
    ) -> str:
        """Execute a registered tool and return the result as a string."""
        import queue as _q
        fn = self._tools.get(name)
        if fn is None:
            logger.warning("AgenticBaseAgent: unknown tool %r — returning error", name)
            return f"Error: tool '{name}' is not registered"
        try:
            if isinstance(progress_queue, _q.Queue):
                import json as _json
                try:
                    progress_queue.put(_json.dumps({
                        "_event_type": "tool_call",
                        "agent": self.role,
                        "tool": name,
                        "message": f"[{self.role}] calling tool: {name}",
                    }))
                except Exception:
                    pass
            result = fn(input_dict)
            return str(result)
        except Exception as exc:
            logger.warning("AgenticBaseAgent: tool %r raised %s", name, exc)
            return f"Error executing tool '{name}': {exc}"


def _extract_tool_calls(response: Any) -> list[dict[str, Any]]:
    """Extract tool_use blocks from an Anthropic-style response.

    Returns a list of dicts with keys: id, name, input.
    Returns an empty list if the response is plain text or contains no tool calls.
    """
    import json as _json

    if isinstance(response, str):
        return []
    # Anthropic SDK returns a list of content blocks when tools are used
    if isinstance(response, list):
        calls = []
        for block in response:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                calls.append({
                    "id": block.get("id", ""),
                    "name": block.get("name", ""),
                    "input": block.get("input") or {},
                })
            elif hasattr(block, "type") and getattr(block, "type", "") == "tool_use":
                calls.append({
                    "id": getattr(block, "id", ""),
                    "name": getattr(block, "name", ""),
                    "input": getattr(block, "input", {}),
                })
        return calls
    # OpenAI-style function_call (dict with "choices")
    if isinstance(response, dict):
        choices = response.get("choices") or []
        calls = []
        for choice in choices:
            msg = (choice or {}).get("message") or {}
            tool_calls = msg.get("tool_calls") or []
            for tc in tool_calls:
                fn = (tc or {}).get("function") or {}
                try:
                    input_dict = _json.loads(fn.get("arguments") or "{}")
                except Exception:
                    input_dict = {}
                calls.append({
                    "id": tc.get("id", ""),
                    "name": fn.get("name", ""),
                    "input": input_dict,
                })
        return calls
    return []
