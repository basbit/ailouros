from __future__ import annotations

from pathlib import Path

import pytest

from backend.App.spec.infrastructure import repomap_adapter as adapter_mod
from backend.App.spec.infrastructure.repomap_adapter import (
    RepoMapUnavailableError,
    TreeSitterRepoMapAdapter,
)


def test_adapter_delegates_to_serve_for_codegen(tmp_path: Path, monkeypatch):
    calls: list[tuple[Path, Path | None, int]] = []

    def fake_serve(workspace_root, focus_path, *, max_tokens):
        calls.append((workspace_root, focus_path, max_tokens))
        return "STUB_RENDER"

    import backend.App.repomap.application.use_cases as uc
    monkeypatch.setattr(uc, "serve_for_codegen", fake_serve)

    adapter = TreeSitterRepoMapAdapter()
    out = adapter.serve(tmp_path, tmp_path / "src" / "x.py", max_tokens=512)
    assert out == "STUB_RENDER"
    assert len(calls) == 1
    assert calls[0][0] == tmp_path
    assert calls[0][1] == tmp_path / "src" / "x.py"
    assert calls[0][2] == 512


def test_adapter_translates_extraction_error_to_unavailable(tmp_path: Path, monkeypatch):
    from backend.App.repomap.infrastructure.treesitter_extractor import (
        RepoMapExtractionError,
    )

    def boom(workspace_root, focus_path, *, max_tokens):
        raise RepoMapExtractionError("tree-sitter not installed")

    import backend.App.repomap.application.use_cases as uc
    monkeypatch.setattr(uc, "serve_for_codegen", boom)

    adapter = TreeSitterRepoMapAdapter()
    with pytest.raises(RepoMapUnavailableError, match="tree-sitter-language-pack"):
        adapter.serve(tmp_path, None, max_tokens=128)


def test_adapter_focus_path_optional(tmp_path: Path, monkeypatch):
    received: dict[str, object] = {}

    def fake_serve(workspace_root, focus_path, *, max_tokens):
        received["focus"] = focus_path
        return ""

    import backend.App.repomap.application.use_cases as uc
    monkeypatch.setattr(uc, "serve_for_codegen", fake_serve)

    TreeSitterRepoMapAdapter().serve(tmp_path, None, max_tokens=64)
    assert received["focus"] is None


def test_adapter_module_exposes_error_class():
    assert hasattr(adapter_mod, "RepoMapUnavailableError")
    assert issubclass(adapter_mod.RepoMapUnavailableError, RuntimeError)
