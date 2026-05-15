from __future__ import annotations

import pytest

from backend.App.spec.domain.sketch_holes import (
    NoHolesFoundError,
    extract_holes,
)

_SIMPLE_ELLIPSIS = """\
def greet(name: str) -> str:
    ...
"""

_SIMPLE_PASS = """\
def greet(name: str) -> str:
    pass
"""

_CLASS_METHODS = """\
class PasswordHasher:
    def hash(self, plain: str) -> str:
        ...

    def verify(self, plain: str, expected: str) -> bool:
        ...
"""

_MIXED = """\
class MyClass:
    def implemented(self) -> int:
        return 42

    def hole_method(self) -> str:
        ...

def top_level_hole() -> None:
    pass
"""

_NO_HOLES = """\
def implemented() -> int:
    return 42
"""

_NESTED = """\
def outer() -> None:
    def inner() -> int:
        ...
"""

_ASYNC_HOLE = """\
async def fetch(url: str) -> bytes:
    ...
"""


def test_simple_ellipsis_detected():
    holes = extract_holes(_SIMPLE_ELLIPSIS)
    assert len(holes) == 1
    assert holes[0].function_name == "greet"
    assert holes[0].qualname == "greet"


def test_simple_pass_detected():
    holes = extract_holes(_SIMPLE_PASS)
    assert len(holes) == 1
    assert holes[0].function_name == "greet"


def test_class_methods_detected():
    holes = extract_holes(_CLASS_METHODS)
    qualnames = {h.qualname for h in holes}
    assert "PasswordHasher.hash" in qualnames
    assert "PasswordHasher.verify" in qualnames
    assert len(holes) == 2


def test_class_method_signatures_preserved():
    holes = extract_holes(_CLASS_METHODS)
    by_q = {h.qualname: h for h in holes}
    assert "str" in by_q["PasswordHasher.hash"].signature
    assert "bool" in by_q["PasswordHasher.verify"].signature


def test_mixed_full_body_and_holes():
    holes = extract_holes(_MIXED)
    qualnames = {h.qualname for h in holes}
    assert "MyClass.hole_method" in qualnames
    assert "top_level_hole" in qualnames
    assert "MyClass.implemented" not in qualnames


def test_no_holes_raises():
    with pytest.raises(NoHolesFoundError):
        extract_holes(_NO_HOLES)


def test_nested_function_hole():
    holes = extract_holes(_NESTED)
    qualnames = {h.qualname for h in holes}
    assert any("inner" in q for q in qualnames)


def test_async_hole_detected():
    holes = extract_holes(_ASYNC_HOLE)
    assert len(holes) == 1
    assert holes[0].function_name == "fetch"


def test_hole_lineno_populated():
    holes = extract_holes(_SIMPLE_ELLIPSIS)
    h = holes[0]
    assert h.body_lineno_start >= 1
    assert h.body_lineno_end >= h.body_lineno_start


def test_hole_qualname_matches_class_name():
    holes = extract_holes(_CLASS_METHODS)
    for h in holes:
        assert h.qualname.startswith("PasswordHasher.")


def test_returns_tuple():
    holes = extract_holes(_SIMPLE_ELLIPSIS)
    assert isinstance(holes, tuple)
