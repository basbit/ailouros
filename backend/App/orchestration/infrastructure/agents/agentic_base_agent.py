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

        For OpenAI-compatible backends (llm_route=="openai", e.g. Gemini) tool schemas
        are converted from Anthropic format to OpenAI function-calling format and the
        response tool_calls are handled natively via the OpenAI client.
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

        max_rounds = int(os.getenv("SWARM_AGENT_MAX_TOOL_ROUNDS", str(_MAX_TOOL_ROUNDS)))

        # Any non-Anthropic backend here is OpenAI-compatible (local or cloud),
        # so tool schemas must use the OpenAI ``type=function`` format.
        if cfg.llm_route != "anthropic":
            return self._run_openai_tool_loop(cfg, messages, max_rounds, _progress_queue)

        # ── Anthropic / local path ────────────────────────────────────────────
        # Inject tool schemas in Anthropic native format
        ask_kwargs["tools"] = self._tool_schemas
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

    def _run_openai_tool_loop(
        self,
        cfg: Any,
        messages: list[dict[str, Any]],
        max_rounds: int,
        _progress_queue: Any,
    ) -> str:
        """Tool loop for OpenAI-compatible backends (Gemini, etc.).

        Converts Anthropic-format tool schemas to OpenAI function-calling format,
        calls the OpenAI client directly so that ``tool_calls`` in the response are
        accessible, and appends tool results in the OpenAI ``role=tool`` format.
        """
        import json as _json
        from backend.App.integrations.infrastructure.llm.client import (
            make_openai_client,
            merge_openai_compat_max_tokens,
            _accumulate_thread_usage,
        )

        # Convert Anthropic-format schemas → OpenAI function-calling format
        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": s["name"],
                    "description": s.get("description", ""),
                    "parameters": s.get("input_schema", {"type": "object", "properties": {}}),
                },
            }
            for s in self._tool_schemas
        ]

        client = make_openai_client(base_url=cfg.base_url, api_key=cfg.api_key)
        final_text = ""
        msg = None  # set each iteration; referenced in the `else` branch

        for _round in range(max_rounds + 1):
            self.tool_rounds_used = _round
            logger.debug(
                "AgenticBaseAgent._run_openai_tool_loop: role=%s round=%d/%d",
                self.role, _round, max_rounds,
            )
            create_kwargs: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "tools": openai_tools,
                "tool_choice": "auto",
            }
            if cfg.max_tokens > 0:
                create_kwargs["max_tokens"] = cfg.max_tokens
            create_kwargs = merge_openai_compat_max_tokens(
                create_kwargs, base_url=cfg.base_url
            )

            response = client.chat.completions.create(**create_kwargs)
            usage_obj = response.usage
            usage = {
                "input_tokens": getattr(usage_obj, "prompt_tokens", None) or 0,
                "output_tokens": getattr(usage_obj, "completion_tokens", None) or 0,
                "model": self.model,
                "cached": False,
            }
            self.last_usage = usage
            try:
                _accumulate_thread_usage(usage)
            except Exception:
                pass
            self.used_model = self.model
            self.used_provider = cfg.provider_label or "cloud:openai_compat"

            msg = response.choices[0].message
            tool_calls = msg.tool_calls or []

            if not tool_calls:
                final_text = msg.content or ""
                break

            # Build per-tool-call dicts; preserve Gemini thought_signature from
            # model_extra so the next round is accepted by the API.
            # Detection uses both the provider label (set by LLMBackendSelector) and
            # the base URL (fallback for custom endpoints that don't match the label).
            _is_gemini = "gemini" in (cfg.provider_label or "").lower() or \
                "generativelanguage.googleapis.com" in (cfg.base_url or "")
            _tc_list: list[dict[str, Any]] = []
            for tc in tool_calls:
                tc_dict: dict[str, Any] = {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments or "{}",
                    },
                }
                if _is_gemini:
                    tc_extra = getattr(tc, "model_extra", None)
                    if tc_extra and isinstance(tc_extra, dict):
                        for _ek, _ev in tc_extra.items():
                            if _ek not in tc_dict:
                                tc_dict[_ek] = _ev
                _tc_list.append(tc_dict)

            # Append assistant message with tool_calls (OpenAI format).
            # For Gemini: also echo back the top-level thought from model_extra.
            _assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": _tc_list,
            }
            if _is_gemini:
                _msg_extra = getattr(msg, "model_extra", None)
                if _msg_extra and isinstance(_msg_extra, dict):
                    for _ek, _ev in _msg_extra.items():
                        if _ek not in _assistant_msg:
                            _assistant_msg[_ek] = _ev
            messages.append(_assistant_msg)
            # Execute each tool and append result in OpenAI ``role=tool`` format
            for tc in tool_calls:
                tool_name = tc.function.name
                try:
                    tool_input = _json.loads(tc.function.arguments or "{}")
                except Exception as _parse_err:
                    logger.warning(
                        "AgenticBaseAgent._run_openai_tool_loop: could not parse tool "
                        "arguments for %r — raw=%r error=%s; calling with empty input",
                        tool_name, tc.function.arguments, _parse_err,
                    )
                    tool_input = {}
                result_str = self._execute_tool(tool_name, tool_input, _progress_queue)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                })
        else:
            logger.warning(
                "AgenticBaseAgent._run_openai_tool_loop: max_tool_rounds=%d exhausted "
                "for role=%s — returning last response as plain text",
                max_rounds, self.role,
            )
            if msg is not None:
                final_text = msg.content or ""

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
