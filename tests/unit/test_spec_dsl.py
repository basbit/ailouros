from __future__ import annotations

from backend.App.spec.domain.dsl_block import (
    extract_dsl_blocks,
    filter_by_kind,
)
from backend.App.spec.domain.dsl_python_sig import PythonSignatureParser
from backend.App.spec.domain.dsl_registry import (
    DslRegistry,
    make_default_registry,
)


def test_extract_dsl_blocks_finds_dsl_attribute():
    markdown = (
        "intro\n\n"
        "```python {dsl=python-sig}\n"
        "def f(x: int) -> str: ...\n"
        "```\n\n"
        "```python\n"
        "print('not a dsl block')\n"
        "```\n\n"
        "```yaml {dsl=invariants}\n"
        "- a > 0\n"
        "```\n"
    )
    blocks = extract_dsl_blocks(markdown)
    kinds = {block.kind for block in blocks}
    assert kinds == {"python-sig", "invariants"}


def test_filter_by_kind_returns_matching_blocks():
    markdown = (
        "```python {dsl=python-sig}\n"
        "def f(): ...\n"
        "```\n\n"
        "```python {dsl=python-sig}\n"
        "def g(): ...\n"
        "```\n"
    )
    blocks = extract_dsl_blocks(markdown)
    selected = filter_by_kind(blocks, "python-sig")
    assert len(selected) == 2


def test_python_sig_parses_function_signature():
    block = extract_dsl_blocks(
        "```python {dsl=python-sig}\n"
        "def hash_password(plain: str) -> str: ...\n"
        "```\n"
    )[0]
    parser = PythonSignatureParser()
    result = parser.parse(block)
    assert result.findings == ()
    functions = result.payload["functions"]
    assert isinstance(functions, list)
    assert functions[0]["name"] == "hash_password"
    assert functions[0]["returns"] == "str"


def test_python_sig_parses_class_with_methods():
    block = extract_dsl_blocks(
        "```python {dsl=python-sig}\n"
        "class Hasher:\n"
        "    def hash(self, plain: str) -> str: ...\n"
        "    def verify(self, plain: str, expected: str) -> bool: ...\n"
        "```\n"
    )[0]
    result = PythonSignatureParser().parse(block)
    assert result.findings == ()
    classes = result.payload["classes"]
    assert classes[0]["name"] == "Hasher"
    method_names = {method["name"] for method in classes[0]["methods"]}
    assert method_names == {"hash", "verify"}


def test_python_sig_rejects_syntax_error():
    block = extract_dsl_blocks(
        "```python {dsl=python-sig}\n"
        "def broken(\n"
        "```\n"
    )[0]
    result = PythonSignatureParser().parse(block)
    severities = {finding.severity for finding in result.findings}
    assert "error" in severities


def test_python_sig_flags_empty_block():
    block = extract_dsl_blocks(
        "```python {dsl=python-sig}\n"
        "import os\n"
        "```\n"
    )[0]
    result = PythonSignatureParser().parse(block)
    codes = {finding.severity for finding in result.findings}
    assert "error" in codes


def test_registry_dispatch_to_python_sig():
    registry = make_default_registry()
    assert "python-sig" in registry.known_kinds()
    block = extract_dsl_blocks(
        "```python {dsl=python-sig}\n"
        "def f() -> int: ...\n"
        "```\n"
    )[0]
    result = registry.parse(block)
    assert result is not None
    assert result.kind == "python-sig"


def test_registry_returns_none_for_unknown_kind():
    registry = DslRegistry()
    block = extract_dsl_blocks(
        "```yaml {dsl=invariants}\n"
        "- ok\n"
        "```\n"
    )[0]
    assert registry.parse(block) is None


class _BadEmptyParser:
    kind = "  "

    def parse(self, block):
        raise NotImplementedError


def test_registry_rejects_empty_kind():
    registry = DslRegistry()
    try:
        registry.register(_BadEmptyParser())
    except ValueError:
        return
    raise AssertionError("registry should have rejected empty kind")
