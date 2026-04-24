from __future__ import annotations

import json

from backend.App.integrations.infrastructure.mcp.evidence_tools import (
    evidence_tools_available,
    evidence_tools_definitions,
    find_class_definition,
    find_symbol_usages,
    grep_context,
)


def test_evidence_tools_available(tmp_path):
    assert evidence_tools_available(str(tmp_path)) is True
    assert evidence_tools_available("") is False


def test_evidence_tools_definitions_names():
    names = [tool["function"]["name"] for tool in evidence_tools_definitions()]
    assert names == ["grep_context", "find_class_definition", "find_symbol_usages"]


def test_grep_context_returns_hits(tmp_path):
    target = tmp_path / "src" / "service.py"
    target.parent.mkdir(parents=True)
    target.write_text("alpha\nneedle here\nomega\n", encoding="utf-8")

    result = json.loads(grep_context(str(tmp_path), query="needle"))

    assert result["hits"][0]["path"] == "src/service.py"
    assert "needle here" in result["hits"][0]["excerpt"]


def test_find_class_definition_uses_code_analysis_entities(tmp_path):
    target = tmp_path / "src" / "service.py"
    target.parent.mkdir(parents=True)
    target.write_text("class OrderService:\n    pass\n", encoding="utf-8")

    result = json.loads(find_class_definition(str(tmp_path), symbol="OrderService"))

    assert result["hits"][0]["path"] == "src/service.py"
    assert result["hits"][0]["kind"] == "class"
    assert "OrderService" in result["hits"][0]["excerpt"]


def test_find_symbol_usages_returns_word_boundary_hits(tmp_path):
    target = tmp_path / "src" / "service.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        "class OrderService:\n"
        "    pass\n\n"
        "def build():\n"
        "    return OrderService()\n",
        encoding="utf-8",
    )

    result = json.loads(find_symbol_usages(str(tmp_path), symbol="OrderService", max_hits=5))

    assert len(result["hits"]) >= 2
    assert all(hit["path"] == "src/service.py" for hit in result["hits"])
