from __future__ import annotations

from typing import Any, Callable

from pydantic import BaseModel

from backend.App.integrations.domain.structured_output import parse_structured

ProviderName = str

_SCHEMA_INSTRUCTION_PREFIX = (
    "Your output MUST be a single JSON object that conforms to this JSON schema. "
    "Return only the JSON, no prose, no markdown fences:\n"
)

_AugmentFn = Callable[[dict[str, Any], type[BaseModel]], dict[str, Any]]
_ExtractFn = Callable[[Any, type[BaseModel]], BaseModel]


def _schema_json(schema: type[BaseModel]) -> dict[str, Any]:
    return schema.model_json_schema()


def _append_schema_instruction(
    request_kwargs: dict[str, Any], schema: type[BaseModel]
) -> dict[str, Any]:
    messages = list(request_kwargs.get("messages") or [])
    schema_json = _schema_json(schema)
    instruction = _SCHEMA_INSTRUCTION_PREFIX + str(schema_json)
    new_messages: list[dict[str, Any]] = []
    appended = False
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content", "")
        if role == "system" and not appended:
            merged = (content or "").rstrip() + "\n\n" + instruction
            new_messages.append({**msg, "content": merged})
            appended = True
        else:
            new_messages.append(dict(msg))
    if not appended:
        new_messages.insert(0, {"role": "system", "content": instruction})
    out = dict(request_kwargs)
    out["messages"] = new_messages
    return out


def _augment_anthropic(
    request_kwargs: dict[str, Any], schema: type[BaseModel]
) -> dict[str, Any]:
    tool_name = f"emit_{schema.__name__.lower()}"
    schema_json = _schema_json(schema)
    tool = {
        "name": tool_name,
        "description": f"Emit a single {schema.__name__} JSON object.",
        "input_schema": schema_json,
    }
    out = dict(request_kwargs)
    existing_tools = list(out.get("tools") or [])
    existing_tools.append(tool)
    out["tools"] = existing_tools
    out["tool_choice"] = {"type": "tool", "name": tool_name}
    out["_structured_output_tool_name"] = tool_name
    return out


def _augment_openai(
    request_kwargs: dict[str, Any], schema: type[BaseModel]
) -> dict[str, Any]:
    schema_json = _schema_json(schema)
    out = dict(request_kwargs)
    out["response_format"] = {
        "type": "json_schema",
        "json_schema": {
            "name": schema.__name__,
            "schema": schema_json,
            "strict": True,
        },
    }
    return out


def _augment_ollama(
    request_kwargs: dict[str, Any], schema: type[BaseModel]
) -> dict[str, Any]:
    augmented = _append_schema_instruction(request_kwargs, schema)
    extra_body = dict(augmented.get("extra_body") or {})
    extra_body["format"] = "json"
    augmented["extra_body"] = extra_body
    augmented["response_format"] = {"type": "json_object"}
    return augmented


def _augment_litellm(
    request_kwargs: dict[str, Any], schema: type[BaseModel]
) -> dict[str, Any]:
    schema_json = _schema_json(schema)
    out = _append_schema_instruction(request_kwargs, schema)
    out["response_format"] = {
        "type": "json_schema",
        "json_schema": {
            "name": schema.__name__,
            "schema": schema_json,
            "strict": True,
        },
    }
    return out


_AUGMENT_REGISTRY: dict[ProviderName, _AugmentFn] = {
    "anthropic": _augment_anthropic,
    "openai": _augment_openai,
    "ollama": _augment_ollama,
    "litellm": _augment_litellm,
}


def _extract_anthropic(raw_response: Any, schema: type[BaseModel]) -> BaseModel:
    if isinstance(raw_response, dict) and "tool_input" in raw_response:
        return schema.model_validate(raw_response["tool_input"])
    text = raw_response if isinstance(raw_response, str) else str(raw_response or "")
    return parse_structured(text, schema)


def _extract_openai(raw_response: Any, schema: type[BaseModel]) -> BaseModel:
    text = raw_response if isinstance(raw_response, str) else str(raw_response or "")
    return parse_structured(text, schema)


def _extract_ollama(raw_response: Any, schema: type[BaseModel]) -> BaseModel:
    text = raw_response if isinstance(raw_response, str) else str(raw_response or "")
    return parse_structured(text, schema)


def _extract_litellm(raw_response: Any, schema: type[BaseModel]) -> BaseModel:
    text = raw_response if isinstance(raw_response, str) else str(raw_response or "")
    return parse_structured(text, schema)


_EXTRACT_REGISTRY: dict[ProviderName, _ExtractFn] = {
    "anthropic": _extract_anthropic,
    "openai": _extract_openai,
    "ollama": _extract_ollama,
    "litellm": _extract_litellm,
}


def register_provider(
    provider_name: ProviderName,
    augment: _AugmentFn,
    extract: _ExtractFn,
) -> None:
    _AUGMENT_REGISTRY[provider_name] = augment
    _EXTRACT_REGISTRY[provider_name] = extract


def supported_providers() -> tuple[ProviderName, ...]:
    return tuple(sorted(_AUGMENT_REGISTRY.keys()))


def apply_schema_to_request(
    provider_name: ProviderName,
    request_kwargs: dict[str, Any],
    schema: type[BaseModel],
) -> dict[str, Any]:
    fn = _AUGMENT_REGISTRY.get(provider_name)
    if fn is None:
        return _append_schema_instruction(request_kwargs, schema)
    return fn(request_kwargs, schema)


def extract_structured_response(
    provider_name: ProviderName,
    raw_response: Any,
    schema: type[BaseModel],
) -> BaseModel:
    fn = _EXTRACT_REGISTRY.get(provider_name)
    if fn is None:
        text = raw_response if isinstance(raw_response, str) else str(raw_response or "")
        return parse_structured(text, schema)
    return fn(raw_response, schema)


__all__ = [
    "ProviderName",
    "apply_schema_to_request",
    "extract_structured_response",
    "register_provider",
    "supported_providers",
]
