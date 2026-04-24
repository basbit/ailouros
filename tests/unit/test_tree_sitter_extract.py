"""Tree-sitter извлечение (если установлен language-pack)."""

import pytest

from backend.App.workspace.infrastructure.code_analysis.tree_sitter_extract import _pack_available, extract_with_tree_sitter


def test_extract_disabled_skips_tree_sitter():
    out = extract_with_tree_sitter("class A: pass", "m.py", "python", disabled=True)
    assert out is None


@pytest.mark.skipif(not _pack_available(), reason="tree-sitter-language-pack not installed")
def test_extract_python_finds_def():
    src = "class A:\n    pass\n\ndef foo():\n    pass\n"
    out = extract_with_tree_sitter(src, "m.py", "python")
    assert out is not None
    kinds = {e["kind"] for e in out["entities"]}
    assert "class" in kinds
    assert "function" in kinds
    names = {e["name"] for e in out["entities"]}
    assert "A" in names and "foo" in names
