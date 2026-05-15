from __future__ import annotations

from pathlib import Path
from typing import Optional

from backend.App.spec.domain.ports import RepoMapPort


class RepoMapUnavailableError(RuntimeError):
    pass


class TreeSitterRepoMapAdapter(RepoMapPort):
    def serve(
        self,
        workspace_root: Path,
        focus_path: Optional[Path],
        *,
        max_tokens: int,
    ) -> str:
        try:
            from backend.App.repomap.application.use_cases import serve_for_codegen
        except ImportError as exc:
            raise RepoMapUnavailableError(
                "RepoMap requires tree-sitter language packs. "
                "Install with: pip install tree-sitter-language-pack"
            ) from exc

        try:
            from backend.App.repomap.infrastructure.treesitter_extractor import (
                RepoMapExtractionError,
            )
        except ImportError as exc:
            raise RepoMapUnavailableError(
                "RepoMap requires tree-sitter language packs. "
                "Install with: pip install tree-sitter-language-pack"
            ) from exc

        try:
            return serve_for_codegen(
                workspace_root,
                focus_path,
                max_tokens=max_tokens,
            )
        except RepoMapExtractionError as exc:
            raise RepoMapUnavailableError(
                "RepoMap requires tree-sitter language packs. "
                "Install with: pip install tree-sitter-language-pack. "
                f"Underlying error: {exc}"
            ) from exc
        except (ImportError, ModuleNotFoundError) as exc:
            raise RepoMapUnavailableError(
                "RepoMap requires tree-sitter language packs. "
                "Install with: pip install tree-sitter-language-pack"
            ) from exc


__all__ = ["RepoMapUnavailableError", "TreeSitterRepoMapAdapter"]
