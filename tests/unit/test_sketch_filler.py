from __future__ import annotations

import pytest

from backend.App.spec.domain.sketch_filler import (
    HoleNotFoundError,
    NoFilledBodyError,
    apply_filled_bodies,
)

_SIMPLE_SKETCH = """\
def greet(name: str) -> str:
    ...
"""

_CLASS_SKETCH = """\
class PasswordHasher:
    def hash(self, plain: str) -> str:
        ...

    def verify(self, plain: str, expected: str) -> bool:
        ...
"""

_MULTI_HOLE_SKETCH = """\
def alpha() -> int:
    ...

def beta() -> str:
    ...
"""


def test_apply_simple_body():
    result = apply_filled_bodies(_SIMPLE_SKETCH, {"greet": "    return f'Hello, {name}'"})
    assert "return f'Hello, {name}'" in result
    assert "..." not in result


def test_signature_preserved():
    result = apply_filled_bodies(_SIMPLE_SKETCH, {"greet": "    return 'hi'"})
    assert "def greet(name: str) -> str:" in result


def test_class_method_filled():
    result = apply_filled_bodies(
        _CLASS_SKETCH,
        {"PasswordHasher.hash": "    return plain + '_hashed'"},
    )
    assert "return plain + '_hashed'" in result
    assert "..." in result or "pass" in result or "verify" in result


def test_indentation_preserved():
    result = apply_filled_bodies(
        _CLASS_SKETCH,
        {"PasswordHasher.hash": "return plain"},
    )
    lines = result.splitlines()
    hash_body_lines = [
        ln for ln in lines
        if "return plain" in ln
    ]
    assert hash_body_lines, "body line not found"
    assert hash_body_lines[0].startswith("        "), "expected 8-space indent inside class method"


def test_missing_qualname_raises():
    with pytest.raises(HoleNotFoundError):
        apply_filled_bodies(_SIMPLE_SKETCH, {"nonexistent.func": "    pass"})


def test_empty_filled_bodies_raises():
    with pytest.raises(NoFilledBodyError):
        apply_filled_bodies(_SIMPLE_SKETCH, {})


def test_multiple_holes_filled():
    result = apply_filled_bodies(
        _MULTI_HOLE_SKETCH,
        {"alpha": "    return 1", "beta": "    return 'b'"},
    )
    assert "return 1" in result
    assert "return 'b'" in result
    assert "..." not in result
