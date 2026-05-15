from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from backend.App.integrations.domain.structured_output import (
    StructuredOutputError,
    parse_structured,
)
from backend.App.shared.domain.exceptions import DomainError


class _Plan(BaseModel):
    title: str
    steps: list[str] = Field(default_factory=list)
    priority: int


class _Nested(BaseModel):
    name: str


class _Outer(BaseModel):
    inner: _Nested
    count: int


def test_structured_output_error_is_domain_error() -> None:
    exc = StructuredOutputError(
        model_name="X",
        attempt=2,
        validation_errors=("a.b: bad",),
        last_response_excerpt="excerpt",
    )
    assert isinstance(exc, DomainError)
    assert exc.attempt == 2
    assert exc.validation_errors == ("a.b: bad",)
    assert "X" in str(exc)


def test_parse_structured_happy_path() -> None:
    raw = '{"title": "t", "steps": ["a", "b"], "priority": 1}'
    plan = parse_structured(raw, _Plan)
    assert isinstance(plan, _Plan)
    assert plan.title == "t"
    assert plan.priority == 1


def test_parse_structured_strips_json_fence() -> None:
    raw = '```json\n{"title": "t", "steps": [], "priority": 0}\n```'
    plan = parse_structured(raw, _Plan)
    assert plan.title == "t"


def test_parse_structured_strips_bare_fence() -> None:
    raw = '```\n{"title": "t", "steps": [], "priority": 0}\n```'
    plan = parse_structured(raw, _Plan)
    assert plan.priority == 0


def test_parse_structured_json_syntax_error_raises() -> None:
    with pytest.raises(StructuredOutputError) as ei:
        parse_structured("{not json}", _Plan)
    msg = "; ".join(ei.value.validation_errors)
    assert "json_decode_error" in msg or "JSON" in msg


def test_parse_structured_pydantic_error_contains_field_path() -> None:
    raw = '{"title": "t", "steps": [], "priority": "not-int"}'
    with pytest.raises(StructuredOutputError) as ei:
        parse_structured(raw, _Plan)
    paths = [e.split(":", 1)[0] for e in ei.value.validation_errors]
    assert "priority" in paths


def test_parse_structured_nested_field_path() -> None:
    raw = '{"inner": {"name": 42}, "count": 1}'
    with pytest.raises(StructuredOutputError) as ei:
        parse_structured(raw, _Outer)
    joined = " | ".join(ei.value.validation_errors)
    assert "inner.name" in joined


def test_parse_structured_empty_response_raises() -> None:
    with pytest.raises(StructuredOutputError) as ei:
        parse_structured("", _Plan)
    assert any("empty" in e or "<root>" in e for e in ei.value.validation_errors)


def test_parse_structured_excerpt_is_truncated() -> None:
    raw = "x" * 2000
    with pytest.raises(StructuredOutputError) as ei:
        parse_structured(raw, _Plan)
    assert len(ei.value.last_response_excerpt) <= 512
