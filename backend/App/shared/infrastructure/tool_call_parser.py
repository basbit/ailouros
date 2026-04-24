"""Robust parsing of LLM tool-call arguments.

LLMs (especially Qwen/DeepSeek/Gemini on OpenAI-compatible endpoints) often
emit ``tool_calls[*].function.arguments`` as a JSON *string* with nested
JSON-encoded values — e.g. ``{"patches": "[{...}, {...}]"}`` — which has to be
unwrapped before handing off to the tool implementation.

Previously this logic existed twice:

  * ``integrations/.../openai_loop/tool_loop.py`` — full recursive-unwrap
    implementation (canonical).
  * ``orchestration/infrastructure/agents/agentic_base_agent.py`` — simpler
    ``json.loads`` only, which silently dropped nested-dict arguments.

This module hosts the canonical implementation so every tool-call path uses
identical semantics.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["parse_tool_call_args"]


def parse_tool_call_args(args_str: str | None) -> dict[str, Any]:
    """Parse a tool-call ``arguments`` JSON string into a dict.

    - Empty / ``None`` input → ``{}``.
    - Malformed JSON → ``{}`` (caller is expected to log; we never raise).
    - Non-dict top-level JSON (e.g. a raw list) → ``{}``.
    - For every string value that *looks* like JSON (starts with ``[`` or
      ``{``), attempts ``json.loads`` and replaces the string with the parsed
      value if it is itself a list or dict. String remains untouched on parse
      failure.

    This last step matters because OpenAI-compatible servers sometimes
    double-encode nested structures — the first ``json.loads`` gives you a
    dict with JSON *strings* inside, rather than a fully-decoded dict.
    """
    raw = (args_str or "").strip()
    if not raw:
        return {}
    try:
        parsed: Any = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}

    for key, value in list(parsed.items()):
        if (
            isinstance(value, str)
            and value
            and value[0] in ("[", "{")
        ):
            try:
                nested = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(nested, (list, dict)):
                parsed[key] = nested
    return parsed
