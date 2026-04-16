"""MCPToolLoop — stateless LLM → tool-dispatch → append loop.

Extracted from ``loop.py``.  ``loop.py`` keeps ``run_with_mcp_tools_openai_compat``
as a thin adapter that builds ``MCPToolLoop`` and calls ``run()``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
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

logger = logging.getLogger(__name__)

_mcp_serialize_lock = threading.Lock()

# Track models that never produce tool_calls (after 2 attempts → skip tools for them)
_NO_TOOL_MODELS: dict[str, int] = {}  # model → consecutive no-tool-call count
_NO_TOOL_THRESHOLD = 2

_THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_think_tags(text: str) -> str:
    """Remove <think>...</think> reasoning blocks (Qwen3, DeepSeek-R1, etc.)."""
    return _THINK_TAG_RE.sub("", text).strip()


def _detect_truncated_xml(text: str) -> list[str]:
    """Return list of tag names that appear to be unclosed in text."""
    from collections import Counter
    opened: list[str] = re.findall(r"<(swarm_file|swarm_patch|swarm_shell)\b[^>]*>", text)
    closed: list[str] = re.findall(r"</(swarm_file|swarm_patch|swarm_shell)>", text)
    diff = Counter(opened) - Counter(closed)
    return [tag for tag, count in diff.items() if count > 0]


_TEXT_TOOL_CALL_RE = re.compile(
    r"<tool_call>\s*<function=(\w+)>(.*?)</function>\s*</tool_call>",
    re.DOTALL,
)
_TEXT_PARAM_RE = re.compile(r"<parameter=(\w+)>\s*(.*?)\s*</parameter>", re.DOTALL)

# BUG-F9: gpt-oss-20b emits pseudo-tool-calls as text when tools are disabled.
# Format: <|start|>assistant<|channel|>commentary to=functions.TOOL_NAME<|constrain|>json<|message|>{JSON}
_GPT_OSS_TOOL_RE = re.compile(
    r"<\|start\|>assistant<\|channel\|>commentary\s+to=functions\.(\w+)"
    r".*?<\|message\|>(\{.+\})",
    re.DOTALL,
)

# Parser failure: LM Studio failed to generate a valid tool-call token sequence.
# These markers appear when the token constraint engine fails mid-generation.
_TOOL_PARSER_FAILURE_RE = re.compile(
    r"<\|constr|"            # token constraint failure
    r"<\|channel\|>|"        # partial gpt-oss tool call
    r"<\|message\|>$|"       # message token at end of string (truncated)
    r"^to=functions\.\w+$",  # only a function reference, nothing else
    re.IGNORECASE | re.MULTILINE,
)

# P0-7: Broader tool-leak detection for partial/malformed tool call text.
# Matches when a model writes a tool call as plain text instead of using
# the structured tool_calls API (e.g. "to=functions.workspace_list_directory").
_TOOL_LEAK_RE = re.compile(
    r"^to=functions\.[\w.]+|"             # gpt-oss partial: to=functions.name
    r"Action:\s*\w+\[|"                   # ReAct format: Action: tool_name[
    r"```tool_code\b|"                    # tool_code blocks
    r"\bfunction_call\s*\(",              # function_call(...)
    re.IGNORECASE | re.MULTILINE,
)

# Control tokens that local models (gpt-oss, LM Studio) may leak into text output.
_CONTROL_TOKEN_RE = re.compile(
    r"<\|(?:start|end|channel|constrain|message|im_start|im_end)\|>",
)


def sanitize_control_tokens(text: str) -> str:
    """Strip model control tokens that leaked into plain-text output.

    Local models (gpt-oss-20b via LM Studio) sometimes emit internal scaffolding
    tokens like ``<|start|>``, ``<|channel|>``, ``<|constrain|>`` in their text
    response.  This function removes them so downstream consumers get clean text.
    """
    if not text or not _CONTROL_TOKEN_RE.search(text):
        return text
    logger.warning(
        "sanitize_control_tokens: stripping leaked control tokens from output (%d chars). "
        "Preview: %r",
        len(text), text[:120],
    )
    cleaned = _CONTROL_TOKEN_RE.sub("", text)
    # Also strip leftover fragments like "assistant", "commentary to=functions.xxx",
    # "json" that follow control tokens.
    cleaned = re.sub(
        r"(?:^|\n)(?:assistant|commentary\s+to=functions\.\w+|json)\s*(?:\n|$)",
        "\n",
        cleaned,
    )
    return cleaned.strip()


def _mcp_write_action_from_tool_call(
    tool_name: str,
    args: dict[str, Any],
) -> Optional[dict[str, str]]:
    normalized_name = (tool_name.split("__", 1)[-1] if "__" in tool_name else tool_name).lower()
    raw_path = str(args.get("path") or "").strip()
    if not raw_path:
        return None
    path_obj = Path(raw_path)
    path_label = raw_path.replace("\\", "/")
    existed_before = path_obj.exists() if path_obj.is_absolute() else False
    if normalized_name == "edit_file":
        return {"path": path_label, "mode": "patch_edit" if existed_before else "patch_create"}
    if normalized_name == "write_file":
        return {"path": path_label, "mode": "overwrite_file" if existed_before else "create_file"}
    if normalized_name == "move_file":
        return {"path": path_label, "mode": "move_file"}
    if normalized_name == "create_directory":
        return {"path": path_label, "mode": "create_directory"}
    return None


def _parse_text_tool_calls(text: str) -> list:
    """Parse text-based tool calls emitted by models that lack native function calling.

    Matches two formats:

    1. Qwen/DeepSeek XML:
       ``<tool_call><function=name><parameter=key>val</parameter></function></tool_call>``

    2. gpt-oss-20b pseudo-tool-calls (BUG-F9):
       ``<|start|>assistant<|channel|>commentary to=functions.name<|constrain|>json<|message|>{...}``

    Returns list of objects mimicking OpenAI tool_call format (SimpleNamespace with
    id, function.name, function.arguments).
    """
    from types import SimpleNamespace
    results: list[SimpleNamespace] = []
    # Format 1: Qwen/DeepSeek XML
    for m in _TEXT_TOOL_CALL_RE.finditer(text):
        fn_name = m.group(1)
        fn_body = m.group(2)
        params: dict[str, str] = {}
        for pm in _TEXT_PARAM_RE.finditer(fn_body):
            params[pm.group(1)] = pm.group(2)
        tc = SimpleNamespace(
            id=f"textcall_{fn_name}_{len(results)}",
            type="function",
            function=SimpleNamespace(
                name=fn_name,
                arguments=json.dumps(params),
            ),
        )
        results.append(tc)
    # Format 2: gpt-oss-20b pseudo-tool-calls (BUG-F9)
    for m in _GPT_OSS_TOOL_RE.finditer(text):
        fn_name = m.group(1)
        raw_json = m.group(2)
        try:
            args = json.loads(raw_json)
            if not isinstance(args, dict):
                continue
        except (json.JSONDecodeError, ValueError):
            continue
        tc = SimpleNamespace(
            id=f"gptoss_{fn_name}_{len(results)}",
            type="function",
            function=SimpleNamespace(
                name=fn_name,
                arguments=json.dumps(args),
            ),
        )
        results.append(tc)
    return results


def _normalize_text_tool_names(
    parsed_calls: list,
    available_tools: list[dict[str, Any]],
) -> list:
    """Fix tool names from text-parsed calls to match available MCP tool names.

    Models writing tool calls as text (e.g. in reasoning_content) often use
    single-underscore names like ``workspace_read_file`` instead of the
    MCP-prefixed ``workspace__read_file``.  This builds a lookup from the
    available tools list and corrects the names in-place.
    """
    if not parsed_calls or not available_tools:
        return parsed_calls
    name_map: dict[str, str] = {}
    for tool in available_tools:
        fn = tool.get("function", {})
        correct_name = fn.get("name", "")
        if "__" in correct_name:
            simplified = correct_name.replace("__", "_", 1)
            name_map[simplified] = correct_name
    for tc in parsed_calls:
        old_name = tc.function.name
        if "__" not in old_name and old_name in name_map:
            tc.function.name = name_map[old_name]
            logger.debug(
                "MCP: normalized text-parsed tool name %r → %r",
                old_name, name_map[old_name],
            )
    return parsed_calls


def _mcp_serialize_acquire_timeout_sec() -> Optional[float]:
    env_value = os.getenv("SWARM_MCP_SERIALIZE_ACQUIRE_TIMEOUT_SEC", "").strip()
    if not env_value:
        return None
    try:
        parsed_float = float(env_value)
        return parsed_float if parsed_float > 0 else None
    except ValueError:
        return None


@contextlib.contextmanager
def _mcp_global_lock_acquire():
    if os.getenv("SWARM_MCP_SERIALIZE_CALLS", "0").strip().lower() not in (
        "1",
        "true",
        "yes",
        "on",
    ):
        yield
        return
    tmo = _mcp_serialize_acquire_timeout_sec()
    if tmo is None:
        _mcp_serialize_lock.acquire()
        try:
            yield
        finally:
            _mcp_serialize_lock.release()
        return
    if not _mcp_serialize_lock.acquire(timeout=tmo):
        raise RuntimeError(
            f"SWARM_MCP_SERIALIZE_CALLS: could not acquire lock within {tmo}s "
            "(another MCP+LLM run in progress). Increase the timeout or disable serialisation."
        )
    try:
        yield
    finally:
        _mcp_serialize_lock.release()


class MCPToolLoop:
    """Encapsulates the LLM call → tool dispatch → message-append loop.

    Constructor:
        client: OpenAI-compatible client instance.
        pool: MCPPool (already entered as context manager).
        model: model name string.
        prov_label: human-readable provider label (e.g. ``"local:ollama"``).
        cancel_event: optional threading.Event; checked before each round.

    The caller is responsible for entering/exiting the MCPPool context manager.
    """

    _TOOL_INTENT_PATTERNS = (
        "let's read", "let me read", "we need", "i'll read", "i need to read",
        "list root", "list directory", "read file", "check file",
    )

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
        """Execute web search via the Tavily/Exa/ScrapingDog router."""
        from backend.App.integrations.infrastructure.mcp.web_search.web_search_router import (
            web_search,
        )
        query = args.get("query", "")
        if not query:
            return "ERROR: 'query' parameter is required for web_search"
        try:
            results = web_search(query, max_results=5)
        except RuntimeError as exc:
            return f"ERROR: {exc}"
        except Exception as exc:
            return f"ERROR: web search failed — {exc}"
        if not results:
            return f"No results found for: {query}"
        lines = []
        for r in results:
            lines.append(f"**{r.get('title', '')}**")
            lines.append(f"URL: {r.get('href', '')}")
            lines.append(r.get("body", ""))
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _handle_ddg_search(args: dict[str, Any]) -> str:
        """Execute DuckDuckGo search locally and return formatted results."""
        from backend.App.integrations.infrastructure.mcp.web_search.ddg_search import (
            ddg_search,
            ddg_search_available,
        )
        if not ddg_search_available():
            return (
                "ERROR: DuckDuckGo search unavailable — "
                "package 'duckduckgo-search' is not installed. "
                "Set SWARM_TAVILY_API_KEY, SWARM_EXA_API_KEY, or SWARM_SCRAPINGDOG_API_KEY "
                "to use the multi-provider web search router instead."
            )
        query = args.get("query", "")
        if not query:
            return "ERROR: 'query' parameter is required for web_search"
        results = ddg_search(query, max_results=5)
        if not results:
            return f"No results found for: {query}"
        lines = []
        for r in results:
            lines.append(f"**{r.get('title', '')}**")
            lines.append(f"URL: {r.get('href', '')}")
            lines.append(r.get("body", ""))
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _handle_fetch_page(args: dict[str, Any]) -> str:
        """Fetch a URL and return text content."""
        from backend.App.integrations.infrastructure.mcp.web_search.fetch_page import (
            fetch_page,
        )
        url = args.get("url", "")
        return fetch_page(url)

    @staticmethod
    def _handle_local_evidence_tool(name: str, args: dict[str, Any]) -> str:
        from backend.App.integrations.infrastructure.mcp.evidence_tools import (
            find_class_definition,
            find_symbol_usages,
            grep_context,
        )
        workspace_root = os.getenv("SWARM_WORKSPACE_ROOT", "").strip()
        if not workspace_root:
            return "ERROR: SWARM_WORKSPACE_ROOT is not set; local evidence tools are unavailable."
        if name == "grep_context":
            return grep_context(
                workspace_root,
                query=str(args.get("query") or ""),
                globs=args.get("globs") if isinstance(args.get("globs"), list) else None,
                max_hits=int(args.get("max_hits") or 5),
            )
        if name == "find_class_definition":
            return find_class_definition(
                workspace_root,
                symbol=str(args.get("symbol") or ""),
            )
        if name == "find_symbol_usages":
            return find_symbol_usages(
                workspace_root,
                symbol=str(args.get("symbol") or ""),
                max_hits=int(args.get("max_hits") or 10),
            )
        return f"ERROR: unsupported local evidence tool: {name}"

    @staticmethod
    def _handle_wiki_tool(name: str, args: dict[str, Any]) -> str:
        """Dispatch wiki_search / wiki_read / wiki_write locally."""
        from backend.App.integrations.infrastructure.mcp.wiki_tools import handle_wiki_tool_call
        workspace_root = os.getenv("SWARM_WORKSPACE_ROOT", "").strip()
        if not workspace_root:
            return "ERROR: SWARM_WORKSPACE_ROOT is not set; wiki tools are unavailable."
        return handle_wiki_tool_call(name, args, workspace_root)

    def run(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        user_content: str,
        max_rounds: int = 8,
        temperature: float = 0.2,
    ) -> tuple[str, str, str]:
        """Execute the tool-call loop.

        Args:
            messages: Initial message list (system + user already prepared).
            tools: OpenAI-format tool schemas from MCPPool.
            user_content: Original (pre-modified) user content — used only for
                error/log messages and length reporting.
            max_rounds: Maximum number of tool-call rounds.
            temperature: Sampling temperature.

        Returns:
            (final_text, model, prov_label)
        """
        model = self._model
        client = self._client
        cancel_event = self._cancel_event

        final_text = ""
        _ctx_budget = _mcp_max_context_chars()
        _retry_count = 0
        _max_retries = _mcp_max_retry_count()
        _history_compress_threshold = _mcp_history_compress_after_rounds()
        _tool_call_rounds = 0
        _tool_reminder_sent = False
        _force_text_round = False  # set after reminder injection to disable tool_choice
        _mcp_write_count = 0  # track actual MCP write/edit/create tool calls
        _mcp_write_actions: list[dict[str, str]] = []
        _tool_parser_failures = 0
        _files_read_count = 0  # track read_file / read_text_file / read_multiple_files calls
        _file_read_cache: dict[str, str] = {}  # per-invocation read cache (not shared across calls)
        _file_read_cache_hits = 0  # observability: count cache hits per agent.run()
        _file_read_cache_misses = 0  # observability: count cache misses per agent.run()
        _time_to_first_tool: float | None = None
        _time_last_tool: float | None = None
        _loop_start_time = time.monotonic()

        # Read-tool name fragments used for _files_read_count tracking.
        _READ_TOOL_FRAGMENTS = ("read_file", "read_text_file", "read_multiple")

        # EC-5: fresh start for each agent call — don't inherit blacklist from prior subtasks
        _NO_TOOL_MODELS.pop(model, None)

        # §10.7-4: Track LLM↔tool message count via ContractValidator
        _cv_task_id: str = ""
        try:
            from backend.App.orchestration.domain.contract_validator import get_validator as _get_cv
            # Use thread-local or model as proxy task_id for per-loop tracking
            import threading
            _cv_task_id = f"mcp_loop_{threading.current_thread().ident}_{id(messages)}"
            _cv = _get_cv()
            try:
                _cv.register_task(_cv_task_id, "mcp_tool_loop")
            except Exception:
                pass  # already registered
        except ImportError:
            _cv = None

        # Pre-flight: estimate total prompt chars and trim if >70% of model context
        _model_ctx = _model_context_size_tokens()
        if _model_ctx > 0:
            _chars_per_token = 4  # rough estimate
            _max_prompt_chars = int(_model_ctx * _chars_per_token * 0.7)
            _total_chars = sum(len(str(m.get("content", ""))) for m in messages)
            _tools_chars = sum(len(json.dumps(t)) for t in tools) if tools else 0
            if _total_chars + _tools_chars > _max_prompt_chars:
                user_msg_idx = next(
                    (i for i, m in enumerate(messages) if m.get("role") == "user"), None
                )
                if user_msg_idx is not None:
                    old_content = messages[user_msg_idx].get("content") or ""
                    trim_to = max(500, _max_prompt_chars - _tools_chars - 2000)
                    if len(old_content) > trim_to:
                        messages[user_msg_idx] = {
                            **messages[user_msg_idx],
                            "content": old_content[:trim_to]
                            + "\n…[pre-flight: content trimmed to fit model context]",
                        }
                        logger.info(
                            "MCP: pre-flight trim user_content from %d to %d chars "
                            "(model_ctx=%d tokens, est prompt=%d chars)",
                            len(old_content), trim_to, _model_ctx,
                            _total_chars + _tools_chars,
                        )

        for _ in range(max_rounds):
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("MCP: cancelled (pipeline cancel)")
            if _history_compress_threshold > 0:
                messages = _compress_tool_history(messages, _history_compress_threshold)
            messages = _truncate_oldest_tool_results(messages, _ctx_budget)

            # Hard budget check: ensure total (messages + tools) fits model context
            if _model_ctx > 0:
                _chars_per_tok = 3
                _tools_est = sum(len(json.dumps(t)) for t in tools) if tools else 0
                _msgs_est = sum(len(str(m.get("content", ""))) for m in messages)
                _total_est = _msgs_est + _tools_est
                _max_chars = _model_ctx * _chars_per_tok
                if _total_est > _max_chars:
                    # Truncate the longest user/tool message to fit
                    _over = _total_est - _max_chars + 500  # 500 safety margin
                    for _mi in range(len(messages) - 1, -1, -1):
                        _mc = messages[_mi].get("content") or ""
                        if isinstance(_mc, str) and len(_mc) > 1000 and messages[_mi].get("role") in ("user", "tool"):
                            _cut = max(500, len(_mc) - _over)
                            messages[_mi] = {**messages[_mi], "content": _mc[:_cut] + "\n…[trimmed to fit context]"}
                            logger.warning(
                                "MCP: trimmed message[%d] (%s) from %d to %d chars to fit model context (%d tokens)",
                                _mi, messages[_mi].get("role"), len(_mc), _cut, _model_ctx,
                            )
                            break

            base_url_str = str(getattr(client, "base_url", "") or "")
            # After a "write your conclusion" reminder, disable tool calls so the model
            # is forced to produce text rather than another tool-call round.
            _tool_choice = "none" if _force_text_round else "auto"
            _force_text_round = False
            # Skip tools entirely: (a) models known to never use them,
            # (b) force-text round (tool_choice=none) — no point sending schema
            _skip_tools = (
                _NO_TOOL_MODELS.get(model, 0) >= _NO_TOOL_THRESHOLD
                or _tool_choice == "none"
            )
            _effective_tools = [] if _skip_tools else tools
            create_kw_dict: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
            }
            if _effective_tools:
                create_kw_dict["tools"] = _effective_tools
                create_kw_dict["tool_choice"] = _tool_choice
            create_kw = merge_openai_compat_max_tokens(create_kw_dict, base_url=base_url_str)
            try:
                resp = client.chat.completions.create(**create_kw)
            except Exception as llm_exc:
                _exc_str = str(llm_exc).lower()
                _is_channel_error = "channel error" in _exc_str or "channel closed" in _exc_str
                _is_model_crash = "model has crashed" in _exc_str or "exit code: null" in _exc_str
                _is_context_overflow = "tokens to keep" in _exc_str or (
                    "context" in _exc_str and "length" in _exc_str
                )
                if (
                    (_is_channel_error or _is_model_crash)
                    and _retry_count < _max_retries
                ):
                    _retry_count += 1
                    user_msg_index = next(
                        (i for i, m in enumerate(messages) if m.get("role") == "user"), None
                    )
                    if user_msg_index is not None:
                        old_content = messages[user_msg_index].get("content") or ""
                        ratio = _mcp_retry_truncate_ratio()
                        new_len = max(100, int(len(old_content) * ratio))
                        messages[user_msg_index] = {
                            **messages[user_msg_index],
                            "content": old_content[:new_len]
                            + f"\n…[retry {_retry_count}/{_max_retries}: content truncated — channel error recovery]",
                        }
                    logger.warning(
                        "MCP: Channel Error / model crash (model=%s) — retry %d/%d "
                        "with truncated context. Error: %s",
                        model, _retry_count, _max_retries, llm_exc,
                    )
                    continue
                if (
                    _is_context_overflow
                    and _retry_count < _max_retries
                    and _mcp_retry_on_context_overflow()
                ):
                    user_msg_index = next(
                        (i for i, m in enumerate(messages) if m.get("role") == "user"), None
                    )
                    if user_msg_index is not None:
                        old_content = messages[user_msg_index].get("content") or ""
                        ratio = _mcp_retry_truncate_ratio()
                        new_len = max(100, int(len(old_content) * ratio))
                        _retry_count += 1
                        messages[user_msg_index] = {
                            **messages[user_msg_index],
                            "content": old_content[:new_len]
                            + f"\n…[retry {_retry_count}/{_max_retries}: content truncated to {ratio:.0%} — context overflow]",
                        }
                        logger.warning(
                            "MCP: context overflow (model=%s) — retry %d/%d, user_content "
                            "truncated from %d to %d chars (SWARM_MCP_RETRY_TRUNCATE_RATIO=%.2f). "
                            "Set SWARM_MCP_RETRY_ON_CONTEXT_OVERFLOW=0 to disable.",
                            model, _retry_count, _max_retries, len(old_content), new_len, ratio,
                        )
                        continue
                if _is_context_overflow:
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
                        model, _retry_count, _max_retries, llm_exc,
                    )
                raise
            if not resp.choices:
                raise ValueError(f"MCP: LLM returned empty choices (model={model})")
            choice = resp.choices[0]
            msg = choice.message
            raw_content = msg.content or ""
            # Strip <think>...</think> blocks (Qwen3, DeepSeek-R1 etc.) before
            # checking for actual text — models can produce thinking-only responses.
            content_text = _strip_think_tags(raw_content)
            if content_text:
                final_text = content_text
            elif raw_content and not content_text:
                # Model produced only a think block — log and keep final_text unchanged
                logger.debug(
                    "MCP: model %r produced thinking-only response (%d chars), no text output yet",
                    model, len(raw_content),
                )

            # FIX 10.2: Detect truncated XML output (unclosed swarm_file/patch/shell tags)
            if content_text:
                _truncated_tags = _detect_truncated_xml(content_text)
                if _truncated_tags:
                    # Check if the loop will continue (rounds remaining)
                    _rounds_used = sum(
                        1 for m in messages if m.get("role") == "assistant"
                    )
                    _rounds_remaining = max_rounds - _rounds_used - 1
                    if _rounds_remaining > 0 and _retry_count < _max_retries:
                        logger.warning(
                            "[TRUNCATION] Unclosed tags detected: %s — injecting continuation prompt",
                            _truncated_tags,
                        )
                        messages.append({
                            "role": "assistant",
                            "content": content_text,
                        })
                        messages.append({
                            "role": "user",
                            "content": (
                                f"Your previous response was truncated. "
                                f"The following tags were left unclosed: {_truncated_tags}. "
                                "Please continue from where you left off and close all open tags."
                            ),
                        })
                        final_text = ""
                        continue
                    else:
                        logger.warning(
                            "[TRUNCATION_DETECTED_AT_END] Unclosed tags %s detected but no retries remaining — "
                            "continuing with partial output.",
                            _truncated_tags,
                        )

            tool_calls = getattr(msg, "tool_calls", None) or []
            # Parse text-based tool calls from content (models that write
            # <tool_call><function=name><parameter=key>val... as text)
            if not tool_calls and content_text:
                parsed = _parse_text_tool_calls(content_text)
                if parsed:
                    parsed = _normalize_text_tool_names(parsed, tools)
                    tool_calls = parsed
                    logger.info(
                        "MCP: parsed %d text-based tool call(s) from model %r content",
                        len(parsed), model,
                    )
            # Fallback: check reasoning_content (Qwen3, DeepSeek-R1, etc. put
            # tool calls inside the reasoning/thinking field instead of the
            # structured tool_calls array after receiving tool results).
            if not tool_calls:
                _raw_reasoning = getattr(msg, "reasoning_content", None)
                reasoning = _raw_reasoning if isinstance(_raw_reasoning, str) else ""
                if reasoning:
                    parsed = _parse_text_tool_calls(reasoning)
                    if parsed:
                        parsed = _normalize_text_tool_names(parsed, tools)
                        tool_calls = parsed
                        # Use the reasoning text (minus tool_call tags) as content
                        # so the assistant message has meaningful content for the
                        # conversation history.
                        _reasoning_text = _TEXT_TOOL_CALL_RE.sub("", reasoning).strip()
                        if _reasoning_text and not content_text:
                            raw_content = _reasoning_text
                        logger.info(
                            "MCP: parsed %d text-based tool call(s) from model %r "
                            "reasoning_content (model placed tool calls in thinking block)",
                            len(parsed), model,
                        )
            # P0: Parser failure detection — LM Studio failed to generate a valid tool-call.
            # Signals: empty tool_calls AND content contains tokenizer artifacts.
            _is_parser_failure = (
                not tool_calls
                and tools
                and not _skip_tools
                and raw_content
                and _TOOL_PARSER_FAILURE_RE.search(raw_content)
            )
            if _is_parser_failure:
                _tool_parser_failures += 1
                logger.warning(
                    "MCP: tool-call parser failure detected (round=%d, failures=%d, model=%r) — "
                    "stripping tool history and switching to tool-free synthesis. Content: %r",
                    _tool_call_rounds, _tool_parser_failures, model, raw_content[:120],
                )
                # Strip accumulated tool result messages to start clean
                messages = [m for m in messages if m.get("role") != "tool"]
                # Remove any assistant messages that were pure tool_calls (no content)
                messages = [
                    m for m in messages
                    if not (m.get("role") == "assistant" and not m.get("content") and m.get("tool_calls"))
                ]
                messages.append({
                    "role": "user",
                    "content": (
                        "Your tool call could not be processed due to a serialization error. "
                        "Please provide your best answer directly as text, without calling any tools. "
                        "Use your knowledge and the information already provided in the conversation."
                    ),
                })
                _force_text_round = True
                final_text = ""  # reset to get fresh response
                continue

            if not tool_calls and tools and not _skip_tools:
                # Track models that never produce tool_calls
                _NO_TOOL_MODELS[model] = _NO_TOOL_MODELS.get(model, 0) + 1
                if _NO_TOOL_MODELS[model] == _NO_TOOL_THRESHOLD:
                    logger.info(
                        "MCP: model %r returned empty tool_calls %d times — "
                        "will skip tools for this model to save context tokens",
                        model, _NO_TOOL_THRESHOLD,
                    )
            elif tool_calls:
                _NO_TOOL_MODELS.pop(model, None)  # reset on success
            if not tool_calls:
                if (
                    not _tool_reminder_sent
                    and _tool_call_rounds == 0
                    and final_text
                    and any(p in final_text.lower() for p in self._TOOL_INTENT_PATTERNS)
                ):
                    _tool_reminder_sent = True
                    messages.append({
                        "role": "user",
                        "content": (
                            "REMINDER: To read files use the tool_calls API (function calling), "
                            "not plain text. Call the `read_file` or `list_directory` tool now "
                            "using the structured function calling format."
                        ),
                    })
                    final_text = ""  # reset so we get a fresh response
                    continue
                # P0-7: Tool leak detection — model wrote tool call as text
                if (
                    not _tool_reminder_sent
                    and _tool_call_rounds == 0
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
                    _tool_reminder_sent = True
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
                    _force_text_round = True
                    continue
                break

            _tool_call_rounds += 1
            if _time_to_first_tool is None:
                _time_to_first_tool = time.monotonic() - _loop_start_time
            _time_last_tool = time.monotonic()
            messages.append(
                {
                    "role": "assistant",
                    "content": raw_content or msg.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments or "{}",
                            },
                        }
                        for tc in tool_calls
                    ],
                }
            )

            for tc in tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                    if not isinstance(args, dict):
                        args = {}
                except json.JSONDecodeError:
                    args = {}
                # FIX: Local LLMs (Qwen/DeepSeek) often stringify JSON
                # arrays/objects inside tool-call arguments, e.g.
                #   {"paths": "[\"a.txt\",\"b.txt\"]"}  instead of
                #   {"paths": ["a.txt","b.txt"]}
                # Auto-parse such values so MCP tools receive correct types.
                for _ak, _av in list(args.items()):
                    if isinstance(_av, str) and _av and _av[0] in ("[", "{"):
                        try:
                            _parsed = json.loads(_av)
                            if isinstance(_parsed, (list, dict)):
                                args[_ak] = _parsed
                        except (json.JSONDecodeError, ValueError):
                            pass
                # Track MCP write/edit/create calls and read-file calls
                _name_lower = (name.split("__", 1)[-1] if "__" in name else name).lower()
                if any(w in _name_lower for w in ("write", "edit", "create", "move")):
                    _mcp_write_count += 1
                    action = _mcp_write_action_from_tool_call(name, args)
                    if action is not None:
                        _mcp_write_actions.append(action)
                if any(r in _name_lower for r in _READ_TOOL_FRAGMENTS):
                    _files_read_count += 1
                # FIX 10.4: Check per-invocation read cache for deterministic read tools.
                # Only cache tools whose names suggest deterministic reads (not writes/searches).
                _is_cacheable_read = any(
                    kw in _name_lower
                    for kw in ("read_file", "get_file", "fetch_file", "read_text_file", "read_multiple")
                )
                _cache_key = f"{name}:{json.dumps(args, sort_keys=True)}" if _is_cacheable_read else ""
                if _is_cacheable_read and _cache_key in _file_read_cache:
                    result = _file_read_cache[_cache_key]
                    _file_read_cache_hits += 1
                    logger.debug(
                        "file_read_cache HIT tool=%r path=%r result_len=%d",
                        name,
                        args.get("path") or args.get("file_path") or "?",
                        len(result),
                    )
                else:
                    if _is_cacheable_read:
                        _file_read_cache_misses += 1
                    # Builtin tools: handle locally without MCP dispatch
                    if name == "web_search" and self._web_search_enabled:
                        result = self._handle_web_search(args)
                    elif name == "web_search" and self._ddg_enabled:
                        result = self._handle_ddg_search(args)
                    elif name == "fetch_page" and self._fetch_page_enabled:
                        result = self._handle_fetch_page(args)
                    elif name in {"grep_context", "find_class_definition", "find_symbol_usages"}:
                        result = self._handle_local_evidence_tool(name, args)
                    elif name in {"wiki_search", "wiki_read", "wiki_write"}:
                        result = self._handle_wiki_tool(name, args)
                    else:
                        with _mcp_global_lock_acquire():
                            try:
                                result = self._pool.dispatch_tool(name, args, cancel_event=cancel_event)
                            except Exception as e:
                                result = f"tool error: {e}"
                    # Store in cache only for cacheable read tools (and only successful results)
                    if _is_cacheable_read and not result.startswith("tool error:"):
                        _file_read_cache[_cache_key] = result
                # EC-2: Inject recovery hint for path errors
                if "access denied" in result.lower() or "outside allowed" in result.lower():
                    _ws_root = getattr(self._pool, '_workspace_root', None) or ""
                    if _ws_root:
                        _bad_path = args.get("path", "")
                        result += (
                            f"\n\nHINT: Use absolute path starting with: {_ws_root}/"
                            f"\nExample: {_ws_root}/{_bad_path}"
                            "\nCall the tool again with the corrected absolute path."
                        )
                _result_limit = _mcp_tool_result_max_chars()
                if len(result) > _result_limit:
                    logger.warning(
                        "MCP tool %r result truncated from %d to %d chars "
                        "(SWARM_MCP_TOOL_RESULT_MAX_CHARS=%d)",
                        name, len(result), _result_limit, _result_limit,
                    )
                    result = (
                        result[:_result_limit]
                        + "\n[…result truncated — set SWARM_MCP_TOOL_RESULT_MAX_CHARS to adjust]"
                    )

                # Untrusted content isolation: quarantine + wrap external tool results
                try:
                    from backend.App.orchestration.application.untrusted_content import (
                        is_external_tool,
                        wrap_untrusted,
                        QuarantineAgent,
                    )
                    if is_external_tool(name):
                        _quarantine = QuarantineAgent(state=getattr(self, "_pipeline_state", None) or {})
                        result = _quarantine.summarize(result, source=name)
                        result = wrap_untrusted(result, source=name)
                except Exception as _uc_exc:
                    logger.debug("untrusted_content: isolation step skipped: %s", _uc_exc)

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    }
                )

            # §10.7-4: Track message count in ContractValidator
            if _cv and _cv_task_id:
                _cv._message_counts[_cv_task_id] = _cv._message_counts.get(_cv_task_id, 0) + len(tool_calls) + 1

            # BUG-F2: Only inject conclusion after enough tool rounds.
            # Models need multiple rounds (read → read → write) before producing text.
            _min_rounds = int(os.getenv("SWARM_MCP_MIN_TOOL_ROUNDS", "6"))
            if not final_text and _tool_call_rounds >= _min_rounds:
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
                _force_text_round = True

        if _tool_call_rounds == 0 and final_text and len(final_text) < 400:
            logger.warning(
                "MCP: model issued 0 structured tool_calls but produced short output (%d chars) "
                "— model '%s' may not support function calling. "
                "Output preview: %r. "
                "Use a model with native tool-use support (Claude, GPT-4, Qwen-72B+) "
                "or set workspace_context_mode=inline to pre-load files.",
                len(final_text), model, final_text[:120],
            )
        # If model returned completely empty on the FIRST call (round 0, no text,
        # no tools) — most likely the tools schema overflowed the context window.
        # Retry once without tools to give the model a chance to produce text.
        if not final_text and _tool_call_rounds == 0:
            logger.warning(
                "MCP: model %r returned empty on first call (0 tool rounds, 0 text). "
                "Likely context overflow from tools schema. "
                "Retrying WITHOUT tools (text-only mode).",
                model,
            )
            # Keep only system + user messages, strip any tool-related content
            _text_only_msgs = [m for m in messages if m.get("role") in ("system", "user")]
            try:
                _no_tool_kw: dict[str, Any] = {
                    "model": model,
                    "messages": _text_only_msgs,
                    "temperature": temperature,
                }
                _no_tool_kw = merge_openai_compat_max_tokens(
                    _no_tool_kw, base_url=str(getattr(client, "base_url", "") or ""),
                )
                _no_tool_resp = client.chat.completions.create(**_no_tool_kw)
                if _no_tool_resp.choices:
                    _no_tool_text = (_no_tool_resp.choices[0].message.content or "").strip()
                    if _no_tool_text:
                        final_text = _strip_think_tags(_no_tool_text) or _no_tool_text
                        logger.info(
                            "MCP: text-only retry succeeded for model %r (%d chars).",
                            model, len(final_text),
                        )
            except Exception as _nt_exc:
                logger.warning("MCP: text-only retry also failed: %s", _nt_exc)

        # If model used tools but never wrote text, give it one last chance
        # with tools disabled — force a text response.
        if not final_text and _tool_call_rounds > 0:
            logger.warning(
                "MCP: model %r produced 0 text after %d tool_call round(s) — "
                "forcing one text-only round before giving up.",
                model, _tool_call_rounds,
            )
            messages.append({
                "role": "user",
                "content": (
                    "You have completed your tool calls. Now write your full response "
                    "as text based on what you learned from the tools. "
                    "Do not call any more tools."
                ),
            })
            try:
                _force_kw: dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                }
                _force_kw = merge_openai_compat_max_tokens(
                    _force_kw, base_url=str(getattr(client, "base_url", "") or ""),
                )
                _force_resp = client.chat.completions.create(**_force_kw)
                if _force_resp.choices:
                    _force_text = (_force_resp.choices[0].message.content or "").strip()
                    if _force_text:
                        final_text = _strip_think_tags(_force_text) or _force_text
            except Exception as _force_exc:
                logger.warning("MCP: forced text round failed: %s", _force_exc)

        if not final_text:
            logger.error(
                "MCP: model returned empty text after %d tool_call round(s). "
                "model=%s prompt_chars=%d — possible context overflow or unsupported function calling. "
                "Hints: set SWARM_MCP_TOOL_RESULT_MAX_CHARS or SWARM_MODEL_CONTEXT_SIZE.",
                _tool_call_rounds, model, len(user_content),
            )
            raise RuntimeError(
                f"MCP: model returned empty text after {_tool_call_rounds} tool_call round(s). "
                f"model={model} prompt_chars={len(user_content)} — "
                "possible context overflow or model ignoring output instruction."
            )
        self._last_mcp_write_count = _mcp_write_count
        self._last_mcp_write_actions = list(_mcp_write_actions)
        self._last_tool_call_rounds = _tool_call_rounds
        self._last_tool_parser_failures = _tool_parser_failures
        self._last_files_read_count = _files_read_count
        self._last_file_read_cache_hits = _file_read_cache_hits
        self._last_file_read_cache_misses = _file_read_cache_misses
        if _file_read_cache_hits or _file_read_cache_misses:
            _total = _file_read_cache_hits + _file_read_cache_misses
            logger.info(
                "file_read_cache summary: hits=%d misses=%d total=%d hit_rate=%.1f%%",
                _file_read_cache_hits,
                _file_read_cache_misses,
                _total,
                100.0 * _file_read_cache_hits / _total,
            )
        self._last_time_to_first_tool = _time_to_first_tool
        _finish_time = time.monotonic()
        self._last_time_after_last_tool_until_finish = (
            (_finish_time - _time_last_tool) if _time_last_tool is not None else None
        )
        # §10.7-4: Cleanup — remove ephemeral task from ContractValidator
        if _cv and _cv_task_id:
            _cv._task_states.pop(_cv_task_id, None)
            _cv._seen_ids.pop(_cv_task_id, None)
            _cv._message_counts.pop(_cv_task_id, None)
            _cv._active_tasks.discard(_cv_task_id)
        return final_text, model, self._prov_label
