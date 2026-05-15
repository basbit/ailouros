
from __future__ import annotations

import base64
from collections.abc import Iterable
from typing import Any

__all__ = [
    "to_anthropic_messages",
    "to_openai_tool_schemas",
]


def to_anthropic_messages(
    messages: Iterable[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    system_parts: list[str] = []
    chat: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content", "")
        if role == "system":
            text = _text_from_content(content)
            if text:
                system_parts.append(text)
            continue
        normalised_role = "assistant" if role == "assistant" else "user"
        chat.append(
            {
                "role": normalised_role,
                "content": _anthropic_content_parts(content),
            }
        )
    system_prompt = "\n\n".join(system_parts).strip()
    return system_prompt, chat


def _text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                texts.append(str(part.get("text") or ""))
        return "\n".join(text for text in texts if text).strip()
    return str(content) if content else ""


def _anthropic_content_parts(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return [{"type": "text", "text": str(content)}]
    parts: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        if part_type == "text":
            parts.append({"type": "text", "text": str(part.get("text") or "")})
        elif part_type == "image_url":
            source = _anthropic_image_source(part.get("image_url"))
            if source:
                parts.append({"type": "image", "source": source})
    return parts or [{"type": "text", "text": ""}]


def _anthropic_image_source(image_url: Any) -> dict[str, str] | None:
    url = ""
    if isinstance(image_url, dict):
        url = str(image_url.get("url") or "")
    elif image_url:
        url = str(image_url)
    if not url.startswith("data:image/"):
        return None
    header, sep, payload = url.partition(",")
    if not sep:
        return None
    media_type = header.removeprefix("data:").split(";", 1)[0]
    try:
        base64.b64decode(payload, validate=True)
    except Exception:
        return None
    return {"type": "base64", "media_type": media_type, "data": payload}


def to_openai_tool_schemas(
    schemas: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
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
