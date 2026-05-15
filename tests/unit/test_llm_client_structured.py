from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from backend.App.integrations.domain.structured_output import StructuredOutputError
from backend.App.integrations.infrastructure.llm import client as client_mod


class _Plan(BaseModel):
    title: str
    priority: int


@pytest.fixture(autouse=True)
def _force_litellm_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARM_USE_LITELLM", "1")
    monkeypatch.setattr(client_mod, "_litellm_enabled", lambda: True)
    monkeypatch.setattr(client_mod, "cache_enabled", lambda: False)


def _install_responses(monkeypatch: pytest.MonkeyPatch, responses: list[str]) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    def _stub_ask_litellm(**kwargs: Any) -> tuple[str, dict[str, Any]]:
        captured.append(kwargs)
        idx = len(captured) - 1
        text = responses[min(idx, len(responses) - 1)]
        return (text, {"input_tokens": 1, "output_tokens": 1, "model": kwargs.get("model"), "cached": False})

    monkeypatch.setattr(client_mod, "_ask_litellm", _stub_ask_litellm)
    return captured


def test_structured_pass_on_first_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_responses(monkeypatch, ['{"title":"T","priority":1}'])
    text, usage = client_mod.ask_model(
        messages=[{"role": "user", "content": "go"}],
        model="m",
        response_schema=_Plan,
    )
    assert text == '{"title":"T","priority":1}'
    assert usage["structured_output_attempt"] == 1
    assert len(calls) == 1


def test_structured_pass_on_second_attempt_after_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_responses(
        monkeypatch,
        [
            "not json at all",
            '{"title":"T","priority":2}',
        ],
    )
    text, usage = client_mod.ask_model(
        messages=[{"role": "user", "content": "go"}],
        model="m",
        response_schema=_Plan,
    )
    assert usage["structured_output_attempt"] == 2
    assert "T" in text
    assert len(calls) == 2
    second_msgs = calls[1]["messages"]
    user_msg = [m for m in second_msgs if m.get("role") == "user"][-1]
    assert "Previous attempt failed validation" in user_msg["content"]


def test_structured_raises_after_second_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_responses(
        monkeypatch,
        [
            "garbage one",
            "garbage two",
        ],
    )
    with pytest.raises(StructuredOutputError) as ei:
        client_mod.ask_model(
            messages=[{"role": "user", "content": "go"}],
            model="m",
            response_schema=_Plan,
        )
    assert ei.value.attempt == 2
    assert ei.value.model_name == "_Plan"


def test_structured_validation_error_contains_field_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_responses(
        monkeypatch,
        [
            '{"title":"T","priority":"bad"}',
            '{"title":"T","priority":"still-bad"}',
        ],
    )
    with pytest.raises(StructuredOutputError) as ei:
        client_mod.ask_model(
            messages=[{"role": "user", "content": "go"}],
            model="m",
            response_schema=_Plan,
        )
    joined = " | ".join(ei.value.validation_errors)
    assert "priority" in joined


def test_structured_schema_response_format_passes_to_litellm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_responses(monkeypatch, ['{"title":"T","priority":3}'])
    client_mod.ask_model(
        messages=[{"role": "user", "content": "go"}],
        model="m",
        response_schema=_Plan,
    )
    kwargs = calls[0]
    assert kwargs.get("response_format", {}).get("type") == "json_schema"


def test_structured_no_schema_does_not_invoke_structured_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_responses(monkeypatch, ["free form text"])
    text, usage = client_mod.ask_model(
        messages=[{"role": "user", "content": "hi"}],
        model="m",
    )
    assert text == "free form text"
    assert "structured_output_attempt" not in usage
    assert len(calls) == 1
