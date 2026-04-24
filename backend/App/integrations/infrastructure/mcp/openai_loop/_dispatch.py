from __future__ import annotations

import contextlib as _contextlib
import json
import logging
import os
import re
import threading
from collections import Counter
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_mcp_serialize_lock = threading.Lock()

_NO_TOOL_MODELS: dict[str, int] = {}
_NO_TOOL_THRESHOLD = 2

_THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

_TEXT_TOOL_CALL_RE = re.compile(
    r"<tool_call>\s*<function=(\w+)>(.*?)</function>\s*</tool_call>",
    re.DOTALL,
)
_TEXT_PARAM_RE = re.compile(r"<parameter=(\w+)>\s*(.*?)\s*</parameter>", re.DOTALL)

_GPT_OSS_TOOL_RE = re.compile(
    r"<\|start\|>assistant<\|channel\|>commentary\s+to=functions\.(\w+)"
    r".*?<\|message\|>(\{.+\})",
    re.DOTALL,
)

_TOOL_PARSER_FAILURE_RE = re.compile(
    r"<\|constr|"
    r"<\|channel\|>|"
    r"<\|message\|>$|"
    r"^to=functions\.\w+$",
    re.IGNORECASE | re.MULTILINE,
)

_TOOL_LEAK_RE = re.compile(
    r"^to=functions\.[\w.]+|"
    r"Action:\s*\w+\[|"
    r"```tool_code\b|"
    r"\bfunction_call\s*\(|"
    r"<\|tool_call\>|"
    r"^call:\s*\w+__\w+",
    re.IGNORECASE | re.MULTILINE,
)

_CONTROL_TOKEN_RE = re.compile(
    r"<\|(?:start|end|channel|constrain|message|im_start|im_end)\|>",
)


def strip_think_tags(text: str) -> str:
    return _THINK_TAG_RE.sub("", text).strip()


def detect_truncated_xml(text: str) -> list[str]:
    opened: list[str] = re.findall(r"<(swarm_file|swarm_patch|swarm_shell)\b[^>]*>", text)
    closed: list[str] = re.findall(r"</(swarm_file|swarm_patch|swarm_shell)>", text)
    diff = Counter(opened) - Counter(closed)
    return [tag for tag, count in diff.items() if count > 0]


def sanitize_control_tokens(text: str) -> str:
    if not text or not _CONTROL_TOKEN_RE.search(text):
        return text
    logger.warning(
        "sanitize_control_tokens: stripping leaked control tokens from output (%d chars). "
        "Preview: %r",
        len(text), text[:120],
    )
    cleaned = _CONTROL_TOKEN_RE.sub("", text)
    cleaned = re.sub(
        r"(?:^|\n)(?:assistant|commentary\s+to=functions\.\w+|json)\s*(?:\n|$)",
        "\n",
        cleaned,
    )
    return cleaned.strip()


def mcp_write_action_from_tool_call(
    tool_name: str,
    args: dict[str, Any],
) -> Optional[dict[str, str]]:
    normalized_name = (tool_name.split("__", 1)[-1] if "__" in tool_name else tool_name).lower()
    raw_path = str(args.get("path") or "").strip()
    if not raw_path:
        return None
    path_object = Path(raw_path)
    path_label = raw_path.replace("\\", "/")
    existed_before = path_object.exists() if path_object.is_absolute() else False
    if normalized_name == "edit_file":
        return {"path": path_label, "mode": "patch_edit" if existed_before else "patch_create"}
    if normalized_name == "write_file":
        return {"path": path_label, "mode": "overwrite_file" if existed_before else "create_file"}
    if normalized_name == "move_file":
        return {"path": path_label, "mode": "move_file"}
    if normalized_name == "create_directory":
        return {"path": path_label, "mode": "create_directory"}
    return None


def parse_text_tool_calls(text: str) -> list:
    from types import SimpleNamespace
    results: list[SimpleNamespace] = []
    for match in _TEXT_TOOL_CALL_RE.finditer(text):
        function_name = match.group(1)
        function_body = match.group(2)
        params: dict[str, str] = {}
        for param_match in _TEXT_PARAM_RE.finditer(function_body):
            params[param_match.group(1)] = param_match.group(2)
        tool_call = SimpleNamespace(
            id=f"textcall_{function_name}_{len(results)}",
            type="function",
            function=SimpleNamespace(
                name=function_name,
                arguments=json.dumps(params),
            ),
        )
        results.append(tool_call)
    for match in _GPT_OSS_TOOL_RE.finditer(text):
        function_name = match.group(1)
        raw_json = match.group(2)
        try:
            args = json.loads(raw_json)
            if not isinstance(args, dict):
                continue
        except (json.JSONDecodeError, ValueError):
            continue
        tool_call = SimpleNamespace(
            id=f"gptoss_{function_name}_{len(results)}",
            type="function",
            function=SimpleNamespace(
                name=function_name,
                arguments=json.dumps(args),
            ),
        )
        results.append(tool_call)
    return results


def normalize_text_tool_names(
    parsed_calls: list,
    available_tools: list[dict[str, Any]],
) -> list:
    if not parsed_calls or not available_tools:
        return parsed_calls
    name_map: dict[str, str] = {}
    for tool in available_tools:
        function = tool.get("function", {})
        correct_name = function.get("name", "")
        if "__" in correct_name:
            simplified = correct_name.replace("__", "_", 1)
            name_map[simplified] = correct_name
    for tool_call in parsed_calls:
        old_name = tool_call.function.name
        if "__" not in old_name and old_name in name_map:
            tool_call.function.name = name_map[old_name]
            logger.debug(
                "MCP: normalized text-parsed tool name %r → %r",
                old_name, name_map[old_name],
            )
    return parsed_calls


def mcp_serialize_acquire_timeout_sec() -> Optional[float]:
    env_value = os.getenv("SWARM_MCP_SERIALIZE_ACQUIRE_TIMEOUT_SEC", "").strip()
    if not env_value:
        return None
    try:
        parsed_float = float(env_value)
        return parsed_float if parsed_float > 0 else None
    except ValueError:
        return None


@_contextlib.contextmanager
def mcp_global_lock_acquire():
    if os.getenv("SWARM_MCP_SERIALIZE_CALLS", "0").strip().lower() not in (
        "1",
        "true",
        "yes",
        "on",
    ):
        yield
        return
    timeout = mcp_serialize_acquire_timeout_sec()
    if timeout is None:
        _mcp_serialize_lock.acquire()
        try:
            yield
        finally:
            _mcp_serialize_lock.release()
        return
    if not _mcp_serialize_lock.acquire(timeout=timeout):
        raise RuntimeError(
            f"SWARM_MCP_SERIALIZE_CALLS: could not acquire lock within {timeout}s "
            "(another MCP+LLM run in progress). Increase the timeout or disable serialisation."
        )
    try:
        yield
    finally:
        _mcp_serialize_lock.release()
