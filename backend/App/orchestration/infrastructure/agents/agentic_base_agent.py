from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from backend.App.integrations.infrastructure.llm.client import ask_model
from backend.App.orchestration.infrastructure.agents.base_agent import BaseAgent
from backend.App.shared.infrastructure.message_formatting import to_openai_tool_schemas
from backend.App.shared.infrastructure.tool_call_parser import parse_tool_call_args

logger = logging.getLogger(__name__)

_MAX_TOOL_ROUNDS = int(os.getenv("SWARM_AGENT_MAX_TOOL_ROUNDS", "10"))


@dataclass
class AgenticBaseAgent(BaseAgent):

    _tools: dict[str, Callable[[dict[str, Any]], str]] = field(
        default_factory=dict, init=False, repr=False
    )
    _tool_schemas: list[dict[str, Any]] = field(
        default_factory=list, init=False, repr=False
    )
    tool_rounds_used: int = field(default=0, init=False, repr=False)

    def register_tool(
        self,
        name: str,
        fn: Callable[[dict[str, Any]], str],
        *,
        description: str = "",
        input_schema: dict[str, Any] | None = None,
    ) -> None:
        self._tools[name] = fn
        schema: dict[str, Any] = {
            "name": name,
            "description": description or name,
            "input_schema": input_schema or {"type": "object", "properties": {}},
        }
        self._tool_schemas = [s for s in self._tool_schemas if s["name"] != name]
        self._tool_schemas.append(schema)

    def run(self, user_input: str, *, _progress_queue: Any = None) -> str:
        self.truncation_retries = 0
        self.tool_rounds_used = 0

        if not self._tool_schemas:
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

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.effective_system_prompt()},
            {"role": "user", "content": user_input},
        ]

        max_rounds = int(os.getenv("SWARM_AGENT_MAX_TOOL_ROUNDS", str(_MAX_TOOL_ROUNDS)))

        if cfg.llm_route != "anthropic":
            return self._run_openai_tool_loop(cfg, messages, max_rounds, _progress_queue)

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

            tool_calls = _extract_tool_calls(llm_response)
            if not tool_calls:
                final_text = llm_response if isinstance(llm_response, str) else str(llm_response)
                break

            messages.append({"role": "assistant", "content": llm_response})
            for tc in tool_calls:
                tool_name = tc.get("name", "")
                tool_input = tc.get("input") or {}
                tool_use_id = tc.get("id", "")
                result_str = self._execute_tool(tool_name, tool_input, _progress_queue)
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
        from backend.App.integrations.infrastructure.llm.client import (
            make_openai_client,
            merge_openai_compat_max_tokens,
            _accumulate_thread_usage,
        )

        openai_tools = to_openai_tool_schemas(self._tool_schemas)

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
            for tc in tool_calls:
                tool_name = tc.function.name
                # Canonical parser handles nested/double-encoded JSON arg values
                # that Qwen/DeepSeek/Gemini sometimes emit. Failures silently
                # collapse to ``{}`` just like before — the agent loop keeps
                # moving and the tool receives an empty-args call.
                tool_input = parse_tool_call_args(tc.function.arguments)
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
    import json as _json

    if isinstance(response, str):
        return []
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
