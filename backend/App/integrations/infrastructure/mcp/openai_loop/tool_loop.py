from __future__ import annotations

import json
import logging
import os
import time
import threading
from typing import Any, Optional

from openai import OpenAI

from backend.App.integrations.infrastructure.llm.client import merge_openai_compat_max_tokens
from backend.App.integrations.infrastructure.mcp.stdio.session import MCPPool
from backend.App.integrations.infrastructure.mcp.openai_loop.config import (
    _mcp_tool_result_max_chars,
    _mcp_max_context_chars,
    _mcp_max_retry_count,
    _mcp_retry_on_context_overflow,
    _mcp_retry_truncate_ratio,
    _mcp_history_compress_after_rounds,
    _model_context_size_tokens,
)
from backend.App.integrations.infrastructure.mcp.openai_loop.context_manager import (
    _truncate_oldest_tool_results,
    _compress_tool_history,
)
from backend.App.integrations.infrastructure.mcp.openai_loop._dispatch import (
    strip_think_tags,
    detect_truncated_xml,
    parse_text_tool_calls,
    normalize_text_tool_names,
    mcp_write_action_from_tool_call,
    mcp_global_lock_acquire,
    _NO_TOOL_MODELS,
    _NO_TOOL_THRESHOLD,
    _TOOL_PARSER_FAILURE_RE,
    _TOOL_LEAK_RE,
    mcp_serialize_acquire_timeout_sec,
)
from backend.App.integrations.infrastructure.mcp.openai_loop._telemetry import LoopTelemetry
from backend.App.shared.infrastructure.tool_call_parser import parse_tool_call_args

logger = logging.getLogger(__name__)

_READ_TOOL_FRAGMENTS = ("read_file", "read_text_file", "read_multiple")
_TOOL_INTENT_PATTERNS = (
    "let's read", "let me read", "we need", "i'll read", "i need to read",
    "list root", "list directory", "read file", "check file",
)


def _mcp_serialize_acquire_timeout_sec() -> Optional[float]:
    return mcp_serialize_acquire_timeout_sec()


class MCPToolLoop:

    _TOOL_INTENT_PATTERNS = _TOOL_INTENT_PATTERNS

    def __init__(
        self,
        client: OpenAI,
        pool: MCPPool,
        model: str,
        prov_label: str,
        cancel_event: Optional[threading.Event] = None,
        web_search_enabled: bool = False,
        ddg_enabled: bool = False,
        fetch_page_enabled: bool = False,
    ) -> None:
        self._client = client
        self._pool = pool
        self._model = model
        self._prov_label = prov_label
        self._cancel_event = cancel_event
        self._web_search_enabled = web_search_enabled
        self._ddg_enabled = ddg_enabled
        self._fetch_page_enabled = fetch_page_enabled

    @staticmethod
    def _handle_web_search(args: dict[str, Any]) -> str:
        from backend.App.integrations.infrastructure.mcp.openai_loop._tool_handlers import (
            handle_web_search,
        )
        return handle_web_search(args)

    @staticmethod
    def _handle_ddg_search(args: dict[str, Any]) -> str:
        from backend.App.integrations.infrastructure.mcp.openai_loop._tool_handlers import (
            handle_ddg_search,
        )
        return handle_ddg_search(args)

    @staticmethod
    def _handle_fetch_page(args: dict[str, Any]) -> str:
        from backend.App.integrations.infrastructure.mcp.openai_loop._tool_handlers import (
            handle_fetch_page,
        )
        return handle_fetch_page(args)

    @staticmethod
    def _handle_local_evidence_tool(name: str, args: dict[str, Any]) -> str:
        from backend.App.integrations.infrastructure.mcp.openai_loop._tool_handlers import (
            handle_local_evidence_tool,
        )
        return handle_local_evidence_tool(name, args)

    @staticmethod
    def _handle_wiki_tool(name: str, args: dict[str, Any]) -> str:
        from backend.App.integrations.infrastructure.mcp.openai_loop._tool_handlers import (
            handle_wiki_tool,
        )
        return handle_wiki_tool(name, args)

    _PREFLIGHT_CHARS_PER_TOKEN = 4
    _HARD_BUDGET_CHARS_PER_TOKEN = 3

    def _preflight_trim_messages(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model_context_size: int,
    ) -> list[dict[str, Any]]:
        if model_context_size <= 0:
            return messages
        max_prompt_chars = int(
            model_context_size * self._PREFLIGHT_CHARS_PER_TOKEN * 0.7
        )
        total_chars = sum(len(str(message.get("content", ""))) for message in messages)
        tools_chars = sum(len(json.dumps(tool)) for tool in tools) if tools else 0
        if total_chars + tools_chars <= max_prompt_chars:
            return messages
        user_message_index = next(
            (i for i, message in enumerate(messages) if message.get("role") == "user"), None
        )
        if user_message_index is None:
            return messages
        old_content = messages[user_message_index].get("content") or ""
        trim_to = max(500, max_prompt_chars - tools_chars - 2000)
        if len(old_content) <= trim_to:
            return messages
        messages = list(messages)
        messages[user_message_index] = {
            **messages[user_message_index],
            "content": old_content[:trim_to]
            + "\n…[pre-flight: content trimmed to fit model context]",
        }
        logger.info(
            "MCP: pre-flight trim user_content from %d to %d chars "
            "(model_ctx=%d tokens, est prompt=%d chars)",
            len(old_content), trim_to, model_context_size,
            total_chars + tools_chars,
        )
        return messages

    def _hard_budget_trim(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model_context_size: int,
    ) -> list[dict[str, Any]]:
        if model_context_size <= 0:
            return messages
        tools_estimate = sum(len(json.dumps(tool)) for tool in tools) if tools else 0
        messages_estimate = sum(len(str(message.get("content", ""))) for message in messages)
        total_estimate = messages_estimate + tools_estimate
        max_chars = model_context_size * self._HARD_BUDGET_CHARS_PER_TOKEN
        if total_estimate <= max_chars:
            return messages
        over = total_estimate - max_chars + 500
        for index in range(len(messages) - 1, -1, -1):
            message_content = messages[index].get("content") or ""
            if isinstance(message_content, str) and len(message_content) > 1000 and messages[index].get("role") in ("user", "tool"):
                cut = max(500, len(message_content) - over)
                messages = list(messages)
                messages[index] = {**messages[index], "content": message_content[:cut] + "\n…[trimmed to fit context]"}
                logger.warning(
                    "MCP: trimmed message[%d] (%s) from %d to %d chars to fit model context (%d tokens)",
                    index, messages[index].get("role"), len(message_content), cut, model_context_size,
                )
                break
        return messages

    def _dispatch_tool_call(
        self,
        name: str,
        args: dict[str, Any],
        cancel_event: Optional[threading.Event],
    ) -> str:
        if name == "web_search" and self._web_search_enabled:
            return self._handle_web_search(args)
        if name == "web_search" and self._ddg_enabled:
            return self._handle_ddg_search(args)
        if name == "fetch_page" and self._fetch_page_enabled:
            return self._handle_fetch_page(args)
        if name in {"grep_context", "find_class_definition", "find_symbol_usages"}:
            return self._handle_local_evidence_tool(name, args)
        if name in {"wiki_search", "wiki_read", "wiki_write"}:
            return self._handle_wiki_tool(name, args)
        with mcp_global_lock_acquire():
            try:
                return self._pool.dispatch_tool(name, args, cancel_event=cancel_event)
            except Exception as exc:
                return f"tool error: {exc}"

    def run(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        user_content: str,
        max_rounds: int = 8,
        temperature: float = 0.2,
    ) -> tuple[str, str, str]:
        model = self._model
        client = self._client
        cancel_event = self._cancel_event

        final_text = ""
        context_budget = _mcp_max_context_chars()
        retry_count = 0
        max_retries = _mcp_max_retry_count()
        history_compress_threshold = _mcp_history_compress_after_rounds()
        telemetry = LoopTelemetry()
        tool_reminder_sent = False
        force_text_round = False
        file_read_cache: dict[str, str] = {}

        _NO_TOOL_MODELS.pop(model, None)

        contract_validator = None
        contract_validator_task_id: str = ""
        try:
            from backend.App.orchestration.domain.contract_validator import get_validator as get_contract_validator
            contract_validator_task_id = f"mcp_loop_{threading.current_thread().ident}_{id(messages)}"
            contract_validator = get_contract_validator()
            try:
                contract_validator.register_task(contract_validator_task_id, "mcp_tool_loop")
            except Exception:
                logger.debug("contract_validator.register_task: task already registered")
        except ImportError:
            contract_validator = None

        model_context_size = _model_context_size_tokens()
        messages = self._preflight_trim_messages(messages, tools, model_context_size)

        from backend.App.shared.domain.exceptions import OperationCancelled

        for _ in range(max_rounds):
            if cancel_event is not None and cancel_event.is_set():
                raise OperationCancelled(source="mcp", detail="tool-loop")
            if history_compress_threshold > 0:
                messages = _compress_tool_history(messages, history_compress_threshold)
            messages = _truncate_oldest_tool_results(messages, context_budget)
            messages = self._hard_budget_trim(messages, tools, model_context_size)

            base_url_str = str(getattr(client, "base_url", "") or "")
            tool_choice = "none" if force_text_round else "auto"
            force_text_round = False
            skip_tools = (
                _NO_TOOL_MODELS.get(model, 0) >= _NO_TOOL_THRESHOLD
                or tool_choice == "none"
            )
            effective_tools = [] if skip_tools else tools
            create_kwargs: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
            }
            if effective_tools:
                create_kwargs["tools"] = effective_tools
                create_kwargs["tool_choice"] = tool_choice
            create_kwargs = merge_openai_compat_max_tokens(create_kwargs, base_url=base_url_str)
            try:
                response = client.chat.completions.create(**create_kwargs)
            except Exception as llm_exception:
                exception_str = str(llm_exception).lower()
                is_channel_error = "channel error" in exception_str or "channel closed" in exception_str
                is_model_crash = "model has crashed" in exception_str or "exit code: null" in exception_str
                is_context_overflow = "tokens to keep" in exception_str or (
                    "context" in exception_str and "length" in exception_str
                )
                if (is_channel_error or is_model_crash) and retry_count < max_retries:
                    retry_count += 1
                    user_message_index = next(
                        (i for i, message in enumerate(messages) if message.get("role") == "user"), None
                    )
                    if user_message_index is not None:
                        old_content = messages[user_message_index].get("content") or ""
                        ratio = _mcp_retry_truncate_ratio()
                        new_len = max(100, int(len(old_content) * ratio))
                        messages = list(messages)
                        messages[user_message_index] = {
                            **messages[user_message_index],
                            "content": old_content[:new_len]
                            + f"\n…[retry {retry_count}/{max_retries}: content truncated — channel error recovery]",
                        }
                    logger.warning(
                        "MCP: Channel Error / model crash (model=%s) — retry %d/%d "
                        "with truncated context. Error: %s",
                        model, retry_count, max_retries, llm_exception,
                    )
                    continue
                if is_context_overflow and retry_count < max_retries and _mcp_retry_on_context_overflow():
                    user_message_index = next(
                        (i for i, message in enumerate(messages) if message.get("role") == "user"), None
                    )
                    if user_message_index is not None:
                        old_content = messages[user_message_index].get("content") or ""
                        ratio = _mcp_retry_truncate_ratio()
                        new_len = max(100, int(len(old_content) * ratio))
                        retry_count += 1
                        messages = list(messages)
                        messages[user_message_index] = {
                            **messages[user_message_index],
                            "content": old_content[:new_len]
                            + f"\n…[retry {retry_count}/{max_retries}: content truncated to {ratio:.0%} — context overflow]",
                        }
                        logger.warning(
                            "MCP: context overflow (model=%s) — retry %d/%d, user_content "
                            "truncated from %d to %d chars (SWARM_MCP_RETRY_TRUNCATE_RATIO=%.2f). "
                            "Set SWARM_MCP_RETRY_ON_CONTEXT_OVERFLOW=0 to disable.",
                            model, retry_count, max_retries, len(old_content), new_len, ratio,
                        )
                        continue
                if is_context_overflow:
                    logger.error(
                        "MCP: LLM rejected request — context window too small for model=%s "
                        "(exhausted %d/%d retries). "
                        "Hints: "
                        "(1) Increase model context in LMStudio/Ollama (n_ctx / context length). "
                        "(2) Set SWARM_MCP_TOOL_RESULT_MAX_CHARS=8000 to truncate file reads earlier. "
                        "(3) Planning roles (PM/BA/Arch) automatically use readonly_tools=True — "
                        "if you are using a custom role, ensure readonly_tools is passed. "
                        "(4) Set SWARM_MODEL_CONTEXT_SIZE=<n_ctx> to auto-trim user_content before sending. "
                        "(5) Set SWARM_MCP_COMPACT_TOOLS=1 to truncate tool descriptions. "
                        "(6) Increase SWARM_MCP_MAX_RETRY_COUNT (default 3) for more truncation steps. "
                        "(7) Set remote_profile in agent_config for this role to use a cloud model "
                        "with larger context (e.g. Gemini, GPT-4). "
                        "Error: %s",
                        model, retry_count, max_retries, llm_exception,
                    )
                raise

            if not response.choices:
                raise ValueError(f"MCP: LLM returned empty choices (model={model})")
            choice = response.choices[0]
            message = choice.message
            raw_content = message.content or ""
            content_text = strip_think_tags(raw_content)
            if content_text:
                final_text = content_text
            elif raw_content and not content_text:
                logger.debug(
                    "MCP: model %r produced thinking-only response (%d chars), no text output yet",
                    model, len(raw_content),
                )

            if content_text:
                truncated_tags = detect_truncated_xml(content_text)
                if truncated_tags:
                    rounds_used = sum(1 for message in messages if message.get("role") == "assistant")
                    rounds_remaining = max_rounds - rounds_used - 1
                    if rounds_remaining > 0 and retry_count < max_retries:
                        logger.warning(
                            "[TRUNCATION] Unclosed tags detected: %s — injecting continuation prompt",
                            truncated_tags,
                        )
                        messages = list(messages)
                        messages.append({"role": "assistant", "content": content_text})
                        messages.append({
                            "role": "user",
                            "content": (
                                f"Your previous response was truncated. "
                                f"The following tags were left unclosed: {truncated_tags}. "
                                "Please continue from where you left off and close all open tags."
                            ),
                        })
                        final_text = ""
                        continue
                    else:
                        logger.warning(
                            "[TRUNCATION_DETECTED_AT_END] Unclosed tags %s detected but no retries remaining — "
                            "continuing with partial output.",
                            truncated_tags,
                        )

            tool_calls = getattr(message, "tool_calls", None) or []
            if not tool_calls and content_text:
                parsed = parse_text_tool_calls(content_text)
                if parsed:
                    parsed = normalize_text_tool_names(parsed, tools)
                    tool_calls = parsed
                    logger.info(
                        "MCP: parsed %d text-based tool call(s) from model %r content",
                        len(parsed), model,
                    )
            if not tool_calls:
                raw_reasoning = getattr(message, "reasoning_content", None)
                reasoning = raw_reasoning if isinstance(raw_reasoning, str) else ""
                if reasoning:
                    parsed = parse_text_tool_calls(reasoning)
                    if parsed:
                        from backend.App.integrations.infrastructure.mcp.openai_loop._dispatch import _TEXT_TOOL_CALL_RE as _TTCRE
                        parsed = normalize_text_tool_names(parsed, tools)
                        tool_calls = parsed
                        reasoning_text = _TTCRE.sub("", reasoning).strip()
                        if reasoning_text and not content_text:
                            raw_content = reasoning_text
                        logger.info(
                            "MCP: parsed %d text-based tool call(s) from model %r "
                            "reasoning_content (model placed tool calls in thinking block)",
                            len(parsed), model,
                        )

            is_parser_failure = (
                not tool_calls
                and tools
                and not skip_tools
                and raw_content
                and _TOOL_PARSER_FAILURE_RE.search(raw_content)
            )
            if is_parser_failure:
                telemetry.tool_parser_failures += 1
                logger.warning(
                    "MCP: tool-call parser failure detected (round=%d, failures=%d, model=%r) — "
                    "stripping tool history and switching to tool-free synthesis. Content: %r",
                    telemetry.tool_call_rounds, telemetry.tool_parser_failures, model, raw_content[:120],
                )
                messages = [message for message in messages if message.get("role") != "tool"]
                messages = [
                    message for message in messages
                    if not (message.get("role") == "assistant" and not message.get("content") and message.get("tool_calls"))
                ]
                messages.append({
                    "role": "user",
                    "content": (
                        "Your tool call could not be processed due to a serialization error. "
                        "Please provide your best answer directly as text, without calling any tools. "
                        "Use your knowledge and the information already provided in the conversation."
                    ),
                })
                force_text_round = True
                final_text = ""
                continue

            if not tool_calls and tools and not skip_tools:
                _NO_TOOL_MODELS[model] = _NO_TOOL_MODELS.get(model, 0) + 1
                if _NO_TOOL_MODELS[model] == _NO_TOOL_THRESHOLD:
                    logger.info(
                        "MCP: model %r returned empty tool_calls %d times — "
                        "will skip tools for this model to save context tokens",
                        model, _NO_TOOL_THRESHOLD,
                    )
            elif tool_calls:
                _NO_TOOL_MODELS.pop(model, None)

            if not tool_calls:
                if (
                    not tool_reminder_sent
                    and telemetry.tool_call_rounds == 0
                    and final_text
                    and any(pattern in final_text.lower() for pattern in self._TOOL_INTENT_PATTERNS)
                ):
                    tool_reminder_sent = True
                    messages = list(messages)
                    messages.append({
                        "role": "user",
                        "content": (
                            "REMINDER: To read files use the tool_calls API (function calling), "
                            "not plain text. Call the `read_file` or `list_directory` tool now "
                            "using the structured function calling format."
                        ),
                    })
                    final_text = ""
                    continue
                if (
                    not tool_reminder_sent
                    and telemetry.tool_call_rounds == 0
                    and final_text
                    and len(final_text.strip()) < 200
                    and _TOOL_LEAK_RE.search(final_text)
                ):
                    logger.warning(
                        "MCP: tool leak detected in model %r output — "
                        "model wrote tool call as text instead of structured tool_calls. "
                        "Retrying without tools. Preview: %r",
                        model, final_text[:200],
                    )
                    tool_reminder_sent = True
                    messages = list(messages)
                    messages.append({
                        "role": "user",
                        "content": (
                            "Your previous response looks like a malformed tool call "
                            "(e.g. 'to=functions.xxx'). "
                            "Do NOT try to call tools as text. "
                            "Write your complete response directly as plain text/markdown."
                        ),
                    })
                    final_text = ""
                    force_text_round = True
                    continue
                break

            telemetry.tool_call_rounds += 1
            if telemetry.time_to_first_tool is None:
                telemetry.time_to_first_tool = time.monotonic() - telemetry.loop_start_time
            telemetry.time_last_tool = time.monotonic()

            is_gemini = "generativelanguage.googleapis.com" in base_url_str
            tool_calls_list: list[dict[str, Any]] = []
            for tool_call in tool_calls:
                tool_call_dict: dict[str, Any] = {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments or "{}",
                    },
                }
                if is_gemini:
                    tool_call_extra = getattr(tool_call, "model_extra", None)
                    if tool_call_extra and isinstance(tool_call_extra, dict):
                        for extra_key, extra_value in tool_call_extra.items():
                            if extra_key not in tool_call_dict:
                                tool_call_dict[extra_key] = extra_value
                tool_calls_list.append(tool_call_dict)

            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": raw_content or message.content,
                "tool_calls": tool_calls_list,
            }
            if is_gemini:
                message_extra = getattr(message, "model_extra", None)
                if message_extra and isinstance(message_extra, dict):
                    for extra_key, extra_value in message_extra.items():
                        if extra_key not in assistant_message:
                            assistant_message[extra_key] = extra_value
            messages = list(messages)
            messages.append(assistant_message)

            for tool_call in tool_calls:
                name = tool_call.function.name
                args = parse_tool_call_args(tool_call.function.arguments)

                name_lower = (name.split("__", 1)[-1] if "__" in name else name).lower()
                if any(write_kw in name_lower for write_kw in ("write", "edit", "create", "move")):
                    telemetry.mcp_write_count += 1
                    action = mcp_write_action_from_tool_call(name, args)
                    if action is not None:
                        telemetry.mcp_write_actions.append(action)
                if any(read_fragment in name_lower for read_fragment in _READ_TOOL_FRAGMENTS):
                    telemetry.files_read_count += 1

                is_cacheable_read = any(
                    keyword in name_lower
                    for keyword in ("read_file", "get_file", "fetch_file", "read_text_file", "read_multiple")
                )
                cache_key = f"{name}:{json.dumps(args, sort_keys=True)}" if is_cacheable_read else ""
                if is_cacheable_read and cache_key in file_read_cache:
                    result = file_read_cache[cache_key]
                    telemetry.file_read_cache_hits += 1
                    logger.debug(
                        "file_read_cache HIT tool=%r path=%r result_len=%d",
                        name,
                        args.get("path") or args.get("file_path") or "?",
                        len(result),
                    )
                else:
                    if is_cacheable_read:
                        telemetry.file_read_cache_misses += 1
                    result = self._dispatch_tool_call(name, args, cancel_event)
                    if is_cacheable_read and not result.startswith("tool error:"):
                        file_read_cache[cache_key] = result

                if "access denied" in result.lower() or "outside allowed" in result.lower():
                    workspace_root = getattr(self._pool, '_workspace_root', None) or ""
                    if workspace_root:
                        bad_path = args.get("path", "")
                        result += (
                            f"\n\nHINT: Use absolute path starting with: {workspace_root}/"
                            f"\nExample: {workspace_root}/{bad_path}"
                            "\nCall the tool again with the corrected absolute path."
                        )

                result_limit = _mcp_tool_result_max_chars()
                if len(result) > result_limit:
                    logger.warning(
                        "MCP tool %r result truncated from %d to %d chars "
                        "(SWARM_MCP_TOOL_RESULT_MAX_CHARS=%d)",
                        name, len(result), result_limit, result_limit,
                    )
                    result = (
                        result[:result_limit]
                        + "\n[…result truncated — set SWARM_MCP_TOOL_RESULT_MAX_CHARS to adjust]"
                    )

                try:
                    from backend.App.orchestration.application.enforcement.untrusted_content import (
                        is_external_tool,
                        wrap_untrusted,
                        QuarantineAgent,
                    )
                    if is_external_tool(name):
                        quarantine = QuarantineAgent(state=getattr(self, "_pipeline_state", None) or {})
                        result = quarantine.summarize(result, source=name)
                        result = wrap_untrusted(result, source=name)
                except Exception as untrusted_content_exception:
                    logger.debug("untrusted_content: isolation step skipped: %s", untrusted_content_exception)

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result,
                    }
                )

            if contract_validator and contract_validator_task_id:
                contract_validator._message_counts[contract_validator_task_id] = (
                    contract_validator._message_counts.get(contract_validator_task_id, 0) + len(tool_calls) + 1
                )

            min_rounds = int(os.getenv("SWARM_MCP_MIN_TOOL_ROUNDS", "6"))
            if not final_text and telemetry.tool_call_rounds >= min_rounds:
                messages = list(messages)
                messages.append({
                    "role": "user",
                    "content": (
                        "Tool results received. "
                        "Now write your complete output. "
                        "If you need to create files, use workspace__write_file tool "
                        'or wrap code in <swarm_file path="...">...</swarm_file> tags. '
                        "Then write a brief summary of what you did."
                    ),
                })
                force_text_round = True

        if telemetry.tool_call_rounds == 0 and final_text and len(final_text) < 400:
            logger.warning(
                "MCP: model issued 0 structured tool_calls but produced short output (%d chars) "
                "— model '%s' may not support function calling. "
                "Output preview: %r. "
                "Use a model with native tool-use support (Claude, GPT-4, Qwen-72B+) "
                "or set workspace_context_mode=inline to pre-load files.",
                len(final_text), model, final_text[:120],
            )

        if not final_text and telemetry.tool_call_rounds == 0:
            logger.warning(
                "MCP: model %r returned empty on first call (0 tool rounds, 0 text). "
                "Likely context overflow from tools schema. "
                "Retrying WITHOUT tools (text-only mode).",
                model,
            )
            text_only_messages = [message for message in messages if message.get("role") in ("system", "user")]
            try:
                no_tool_kwargs: dict[str, Any] = {
                    "model": model,
                    "messages": text_only_messages,
                    "temperature": temperature,
                }
                no_tool_kwargs = merge_openai_compat_max_tokens(
                    no_tool_kwargs, base_url=str(getattr(client, "base_url", "") or ""),
                )
                no_tool_response = client.chat.completions.create(**no_tool_kwargs)
                if no_tool_response.choices:
                    no_tool_text = (no_tool_response.choices[0].message.content or "").strip()
                    if no_tool_text:
                        final_text = strip_think_tags(no_tool_text) or no_tool_text
                        logger.info(
                            "MCP: text-only retry succeeded for model %r (%d chars).",
                            model, len(final_text),
                        )
            except Exception as no_tool_exception:
                logger.warning("MCP: text-only retry also failed: %s", no_tool_exception)

        if not final_text and telemetry.tool_call_rounds > 0:
            logger.warning(
                "MCP: model %r produced 0 text after %d tool_call round(s) — "
                "forcing one text-only round before giving up.",
                model, telemetry.tool_call_rounds,
            )
            messages = list(messages)
            messages.append({
                "role": "user",
                "content": (
                    "You have completed your tool calls. Now write your full response "
                    "as text based on what you learned from the tools. "
                    "Do not call any more tools."
                ),
            })
            try:
                force_kwargs: dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                }
                force_kwargs = merge_openai_compat_max_tokens(
                    force_kwargs, base_url=str(getattr(client, "base_url", "") or ""),
                )
                force_response = client.chat.completions.create(**force_kwargs)
                if force_response.choices:
                    force_text = (force_response.choices[0].message.content or "").strip()
                    if force_text:
                        final_text = strip_think_tags(force_text) or force_text
            except Exception as force_exception:
                logger.warning("MCP: forced text round failed: %s", force_exception)

        if not final_text:
            logger.error(
                "MCP: model returned empty text after %d tool_call round(s). "
                "model=%s prompt_chars=%d — possible context overflow or unsupported function calling. "
                "Hints: set SWARM_MCP_TOOL_RESULT_MAX_CHARS or SWARM_MODEL_CONTEXT_SIZE.",
                telemetry.tool_call_rounds, model, len(user_content),
            )
            raise RuntimeError(
                f"MCP: model returned empty text after {telemetry.tool_call_rounds} tool_call round(s). "
                f"model={model} prompt_chars={len(user_content)} — "
                "possible context overflow or model ignoring output instruction."
            )

        self._last_mcp_write_count = telemetry.mcp_write_count
        self._last_mcp_write_actions = list(telemetry.mcp_write_actions)
        self._last_tool_call_rounds = telemetry.tool_call_rounds
        self._last_tool_parser_failures = telemetry.tool_parser_failures
        self._last_files_read_count = telemetry.files_read_count
        self._last_file_read_cache_hits = telemetry.file_read_cache_hits
        self._last_file_read_cache_misses = telemetry.file_read_cache_misses
        if telemetry.file_read_cache_hits or telemetry.file_read_cache_misses:
            total = telemetry.file_read_cache_hits + telemetry.file_read_cache_misses
            logger.info(
                "file_read_cache summary: hits=%d misses=%d total=%d hit_rate=%.1f%%",
                telemetry.file_read_cache_hits,
                telemetry.file_read_cache_misses,
                total,
                100.0 * telemetry.file_read_cache_hits / total,
            )
        self._last_time_to_first_tool = telemetry.time_to_first_tool
        finish_time = time.monotonic()
        self._last_time_after_last_tool_until_finish = (
            (finish_time - telemetry.time_last_tool) if telemetry.time_last_tool is not None else None
        )

        if contract_validator and contract_validator_task_id:
            contract_validator._task_states.pop(contract_validator_task_id, None)
            contract_validator._seen_ids.pop(contract_validator_task_id, None)
            contract_validator._message_counts.pop(contract_validator_task_id, None)
            contract_validator._active_tasks.discard(contract_validator_task_id)

        return final_text, model, self._prov_label
