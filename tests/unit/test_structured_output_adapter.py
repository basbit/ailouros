from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from backend.App.integrations.domain.structured_output import StructuredOutputError
from backend.App.integrations.infrastructure.llm.structured_output_adapter import (
    apply_schema_to_request,
    extract_structured_response,
    register_provider,
    supported_providers,
)


class _Out(BaseModel):
    name: str
    age: int


def _base_kwargs() -> dict:
    return {
        "messages": [
            {"role": "system", "content": "you are helpful"},
            {"role": "user", "content": "do the thing"},
        ],
        "temperature": 0.2,
    }


def test_supported_providers_includes_all_four() -> None:
    names = supported_providers()
    for expected in ("anthropic", "openai", "ollama", "litellm"):
        assert expected in names


def test_augment_anthropic_sets_tool_choice() -> None:
    out = apply_schema_to_request("anthropic", _base_kwargs(), _Out)
    assert "tools" in out
    assert out["tools"][0]["name"].startswith("emit_")
    assert out["tool_choice"]["type"] == "tool"
    assert out["tool_choice"]["name"] == out["tools"][0]["name"]
    assert out["_structured_output_tool_name"] == out["tools"][0]["name"]


def test_augment_openai_sets_response_format_json_schema() -> None:
    out = apply_schema_to_request("openai", _base_kwargs(), _Out)
    rf = out["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["name"] == "_Out"
    assert "properties" in rf["json_schema"]["schema"]
    assert rf["json_schema"]["strict"] is True


def test_augment_ollama_sets_format_json_and_appends_instruction() -> None:
    out = apply_schema_to_request("ollama", _base_kwargs(), _Out)
    assert out["extra_body"]["format"] == "json"
    assert out["response_format"]["type"] == "json_object"
    system_msg = out["messages"][0]
    assert system_msg["role"] == "system"
    assert "JSON schema" in system_msg["content"]


def test_augment_ollama_inserts_system_when_missing() -> None:
    kwargs = {"messages": [{"role": "user", "content": "hi"}], "temperature": 0.0}
    out = apply_schema_to_request("ollama", kwargs, _Out)
    assert out["messages"][0]["role"] == "system"
    assert out["messages"][1]["role"] == "user"


def test_augment_litellm_sets_response_format_and_instruction() -> None:
    out = apply_schema_to_request("litellm", _base_kwargs(), _Out)
    assert out["response_format"]["type"] == "json_schema"
    assert "JSON schema" in out["messages"][0]["content"]


def test_augment_unknown_provider_falls_back_to_prompt_instruction() -> None:
    out = apply_schema_to_request("future-unknown", _base_kwargs(), _Out)
    assert "JSON schema" in out["messages"][0]["content"]
    assert "response_format" not in out


def test_extract_openai_parses_json_text() -> None:
    obj = extract_structured_response("openai", '{"name":"x","age":3}', _Out)
    assert isinstance(obj, _Out)
    assert obj.age == 3


def test_extract_ollama_parses_json_text() -> None:
    obj = extract_structured_response("ollama", '{"name":"y","age":7}', _Out)
    assert obj.name == "y"


def test_extract_litellm_parses_with_fence() -> None:
    obj = extract_structured_response(
        "litellm", '```json\n{"name":"a","age":1}\n```', _Out
    )
    assert obj.name == "a"


def test_extract_anthropic_from_tool_input_dict() -> None:
    raw = {"tool_input": {"name": "z", "age": 9}}
    obj = extract_structured_response("anthropic", raw, _Out)
    assert obj.age == 9


def test_extract_anthropic_falls_back_to_text() -> None:
    obj = extract_structured_response("anthropic", '{"name":"q","age":5}', _Out)
    assert obj.name == "q"


def test_extract_invalid_json_raises_structured_error() -> None:
    with pytest.raises(StructuredOutputError):
        extract_structured_response("openai", "not json", _Out)


def test_extract_pydantic_violation_raises_with_field_path() -> None:
    with pytest.raises(StructuredOutputError) as ei:
        extract_structured_response("openai", '{"name":"a","age":"bad"}', _Out)
    joined = " | ".join(ei.value.validation_errors)
    assert "age" in joined


def test_register_provider_for_ocp() -> None:
    def _aug(rk, schema):
        out = dict(rk)
        out["custom_marker"] = schema.__name__
        return out

    def _ext(raw, schema):
        return schema.model_validate(json.loads(raw))

    register_provider("custom-test", _aug, _ext)
    assert "custom-test" in supported_providers()
    out = apply_schema_to_request("custom-test", _base_kwargs(), _Out)
    assert out["custom_marker"] == "_Out"
    obj = extract_structured_response("custom-test", '{"name":"n","age":1}', _Out)
    assert isinstance(obj, _Out)
