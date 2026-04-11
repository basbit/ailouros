"""Статический анализ workspace (без LLM)."""

from pathlib import Path

from backend.App.workspace.infrastructure.code_analysis.scan import analyze_workspace, analysis_to_json


def test_analyze_workspace_respects_tree_sitter_disabled(tmp_path):
    (tmp_path / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    payload = analyze_workspace(tmp_path, tree_sitter_disabled=True)
    assert payload.get("stats", {}).get("tree_sitter_files", 0) == 0
    for f in payload.get("files") or []:
        assert not (f.get("tree_sitter") or {}).get("enabled")


def test_analyze_workspace_this_repo_has_python():
    root = Path(__file__).resolve().parents[1]
    payload = analyze_workspace(root, languages_filter=["python"])
    assert payload.get("schema") == "swarm_code_analysis/v1"
    assert "relation_graph" in payload
    assert payload.get("relation_graph", {}).get("schema") == "swarm_relation_graph/v1"
    assert payload.get("stats", {}).get("scanned_files", 0) >= 1
    paths = {f.get("path") for f in payload.get("files") or []}
    assert any(p and p.endswith("pipeline_graph.py") for p in paths)


def test_analysis_to_json_roundtrip_keys():
    payload = {"schema": "swarm_code_analysis/v1", "files": [], "stats": {}}
    s = analysis_to_json(payload)
    assert "swarm_code_analysis" in s
