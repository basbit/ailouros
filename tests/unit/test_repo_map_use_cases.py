from __future__ import annotations

import pytest
from pathlib import Path

from backend.App.repomap.application.use_cases import build_repo_map, serve_for_codegen
from backend.App.repomap.domain.repo_map import RepoMap
from backend.App.repomap.infrastructure.treesitter_extractor import _ts_available


pytestmark = pytest.mark.skipif(
    not _ts_available(), reason="tree-sitter-language-pack not installed"
)


def test_build_repo_map_returns_repo_map(tmp_path: Path):
    (tmp_path / "main.py").write_text("def run(): pass\n", encoding="utf-8")
    result = build_repo_map(tmp_path)
    assert isinstance(result, RepoMap)
    assert len(result.entries) >= 1


def test_build_repo_map_empty_workspace(tmp_path: Path):
    result = build_repo_map(tmp_path)
    assert isinstance(result, RepoMap)
    assert result.entries == ()


def test_build_repo_map_entries_sorted_by_rank(tmp_path: Path):
    for i in range(4):
        (tmp_path / f"mod{i}.py").write_text(f"def fn{i}(): pass\n", encoding="utf-8")
    result = build_repo_map(tmp_path)
    ranks = [e.rank for e in result.entries]
    assert ranks == sorted(ranks, reverse=True)


def test_build_repo_map_with_focus_biases_connected_files(tmp_path: Path):
    (tmp_path / "core.py").write_text("def core_fn(): pass\n", encoding="utf-8")
    (tmp_path / "caller.py").write_text(
        "from core import core_fn\ncore_fn()\n", encoding="utf-8"
    )
    (tmp_path / "unrelated.py").write_text("def other(): pass\n", encoding="utf-8")

    result_no_focus = build_repo_map(tmp_path)
    result_focused = build_repo_map(tmp_path, focus_path=tmp_path / "caller.py")

    assert isinstance(result_no_focus, RepoMap)
    assert isinstance(result_focused, RepoMap)
    assert len(result_focused.entries) >= 1


def test_serve_for_codegen_returns_string(tmp_path: Path):
    (tmp_path / "app.py").write_text("def start(): pass\n", encoding="utf-8")
    result = serve_for_codegen(tmp_path, None, max_tokens=512)
    assert isinstance(result, str)
    assert len(result) > 0


def test_serve_for_codegen_respects_max_tokens(tmp_path: Path):
    for i in range(20):
        (tmp_path / f"big_module_{i}.py").write_text(
            "\n".join(f"def func_{j}(): pass" for j in range(50)) + "\n",
            encoding="utf-8",
        )
    result = serve_for_codegen(tmp_path, None, max_tokens=100)
    assert len(result) <= 100 * 6


def test_serve_for_codegen_empty_workspace_returns_message(tmp_path: Path):
    result = serve_for_codegen(tmp_path, None, max_tokens=512)
    assert "no source files" in result
