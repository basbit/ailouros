from __future__ import annotations

import json
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from backend.App.spec.application.sketch_codegen import (
    LLMResponseParseError,
    SketchCodegenError,
    run_sketch_codegen,
)

_SKETCH = """\
class PasswordHasher:
    def hash(self, plain: str) -> str:
        ...

    def verify(self, plain: str, expected: str) -> bool:
        ...
"""

_NO_HOLES_SKETCH = """\
def already_done() -> int:
    return 42
"""


@dataclass(frozen=True)
class _FakeRequest:
    spec_id: str = "test/sketch"
    model_name: str = "stub"
    seed: int = 0


def _llm_returning(payload: dict) -> MagicMock:
    client = MagicMock()
    client.generate.return_value = json.dumps(payload)
    return client


def test_happy_path_fills_holes():
    client = _llm_returning({
        "PasswordHasher.hash": "    return plain + '_hashed'",
        "PasswordHasher.verify": "    return plain + '_hashed' == expected",
    })
    outcome = run_sketch_codegen(_FakeRequest(), _SKETCH, llm_client=client)
    assert outcome.holes_filled == 2
    assert "PasswordHasher.hash" in outcome.hole_qualnames
    assert "PasswordHasher.verify" in outcome.hole_qualnames
    assert "..." not in outcome.filled_source


def test_filled_source_contains_implementation():
    client = _llm_returning({
        "PasswordHasher.hash": "    return plain + '_hashed'",
        "PasswordHasher.verify": "    return plain + '_hashed' == expected",
    })
    outcome = run_sketch_codegen(_FakeRequest(), _SKETCH, llm_client=client)
    assert "_hashed" in outcome.filled_source


def test_no_holes_raises():
    client = _llm_returning({})
    with pytest.raises(SketchCodegenError, match="No holes"):
        run_sketch_codegen(_FakeRequest(), _NO_HOLES_SKETCH, llm_client=client)


def test_signature_mutation_raises():
    mutated_response = json.dumps({
        "PasswordHasher.hash": "    return plain",
        "PasswordHasher.verify": "    return True",
        "PasswordHasher.extra_method": "    pass",
    })
    client = MagicMock()
    client.generate.return_value = mutated_response
    with pytest.raises(SketchCodegenError):
        run_sketch_codegen(_FakeRequest(), _SKETCH, llm_client=client)


def test_non_json_response_raises():
    client = MagicMock()
    client.generate.return_value = "not valid json at all"
    with pytest.raises(LLMResponseParseError):
        run_sketch_codegen(_FakeRequest(), _SKETCH, llm_client=client)


def test_llm_error_raises_codegen_error():
    client = MagicMock()
    client.generate.side_effect = RuntimeError("connection refused")
    with pytest.raises(SketchCodegenError, match="LLM call failed"):
        run_sketch_codegen(_FakeRequest(), _SKETCH, llm_client=client)


def test_outcome_hole_qualnames_is_tuple():
    client = _llm_returning({
        "PasswordHasher.hash": "    return plain",
        "PasswordHasher.verify": "    return True",
    })
    outcome = run_sketch_codegen(_FakeRequest(), _SKETCH, llm_client=client)
    assert isinstance(outcome.hole_qualnames, tuple)
