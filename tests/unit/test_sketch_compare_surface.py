from __future__ import annotations

from backend.App.spec.domain.sketch_holes import compare_public_surface

_BASE = """\
class Hasher:
    def hash(self, plain: str) -> str:
        ...

def top_func(x: int) -> bool:
    ...
"""

_RENAMED_METHOD = """\
class Hasher:
    def hash_renamed(self, plain: str) -> str:
        return plain

def top_func(x: int) -> bool:
    return True
"""

_ADDED_METHOD = """\
class Hasher:
    def hash(self, plain: str) -> str:
        return plain

    def extra(self) -> None:
        pass

def top_func(x: int) -> bool:
    return True
"""

_IDENTICAL_FILLED = """\
class Hasher:
    def hash(self, plain: str) -> str:
        return plain

def top_func(x: int) -> bool:
    return True
"""

_ADDED_TOP_FUNC = """\
class Hasher:
    def hash(self, plain: str) -> str:
        return plain

def top_func(x: int) -> bool:
    return True

def new_func() -> None:
    pass
"""


def test_preserved_empty_diff():
    diffs = compare_public_surface(_BASE, _IDENTICAL_FILLED)
    assert diffs == ()


def test_renamed_method_detected():
    diffs = compare_public_surface(_BASE, _RENAMED_METHOD)
    assert len(diffs) > 0
    messages = " ".join(diffs)
    assert "hash" in messages


def test_added_method_detected():
    diffs = compare_public_surface(_BASE, _ADDED_METHOD)
    assert len(diffs) > 0
    messages = " ".join(diffs)
    assert "extra" in messages


def test_added_top_function_detected():
    diffs = compare_public_surface(_BASE, _ADDED_TOP_FUNC)
    assert len(diffs) > 0
    messages = " ".join(diffs)
    assert "new_func" in messages


def test_returns_tuple():
    diffs = compare_public_surface(_BASE, _IDENTICAL_FILLED)
    assert isinstance(diffs, tuple)
