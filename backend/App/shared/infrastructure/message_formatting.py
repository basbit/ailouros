"""Conversion helpers between ``{role, content}`` messages and provider APIs.

Two independent copies of these converters used to exist:

  * ``integrations/infrastructure/llm/providers.py`` — Anthropic message layout
    (separate ``system`` string + typed content parts).
  * ``orchestration/infrastructure/agents/agentic_base_agent.py`` — OpenAI
    tool-schema wrapping (``{"type": "function", "function": {...}}``).

The tool_loop also wraps tool schemas for the OpenAI Chat Completions endpoint
— that wrapper is kept inline there because it carries Gemini-specific
``model_extra`` preservation, but the unwrapped helper here remains
source-of-truth for the standard layout.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

__all__ = [
    "to_anthropic_messages",
    "to_openai_tool_schemas",
]


def to_anthropic_messages(
    messages: Iterable[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Split a flat OpenAI-style ``[{role, content}, …]`` list into Anthropic shape.

    Returns ``(system_prompt, chat_messages)`` where:
      * ``system_prompt`` concatenates every ``role == "system"`` content,
        separated by two newlines.
      * ``chat_messages`` keeps the remaining entries but rewrites roles to
        ``"assistant" | "user"`` and wraps content as
        ``[{"type": "text", "text": ...}]`` — Anthropic's typed-parts shape.
    """
    system_parts: list[str] = []
    chat: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content", "")
        if role == "system":
            if content:
                system_parts.append(str(content))
            continue
        normalised_role = "assistant" if role == "assistant" else "user"
        chat.append(
            {
                "role": normalised_role,
                "content": [{"type": "text", "text": str(content)}],
            }
        )
    system_prompt = "\n\n".join(system_parts).strip()
    return system_prompt, chat


def to_openai_tool_schemas(
    schemas: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Wrap internal ``{name, description, input_schema}`` tool schemas for OpenAI.

    Matches the format expected by ``tools=[{"type":"function","function":{...}}]``
    in the OpenAI Chat Completions API and compatible endpoints.
    """
    result: list[dict[str, Any]] = []
    for schema in schemas:
        result.append(
            {
                "type": "function",
                "function": {
                    "name": schema["name"],
                    "description": schema.get("description", ""),
                    "parameters": schema.get(
                        "input_schema", {"type": "object", "properties": {}}
                    ),
                },
            }
        )
    return result
