"""PromptLoader — loads agent system prompts from a prompts directory.

Extracted from ``base_agent.py``.  ``prompts_dir`` is injected so there is
no ``Path(__file__).parents[N]`` inside the class.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _strip_skill_frontmatter(text: str) -> str:
    """If the file starts with YAML frontmatter ``---``, return body after second ``---``."""
    raw = text.strip()
    if not raw.startswith("---"):
        return text.strip()
    parts = raw.split("---", 2)
    if len(parts) >= 3:
        return parts[2].strip()
    return text.strip()


class PromptLoader:
    """Load agent system prompts from a configurable directory.

    Resolution order: overrides/ → upstream/ → direct → fallback.

    Args:
        prompts_dir: Absolute path to the directory that contains prompt files
            (e.g. ``<project>/config/prompts``).
    """

    def __init__(self, prompts_dir: Path) -> None:
        self._prompts_dir = prompts_dir

    def load(self, role_relative_path: str, fallback: str = "") -> str:
        """Read a prompt file with overrides→upstream→direct→fallback resolution.

        Args:
            role_relative_path: Path fragment relative to ``prompts_dir``,
                e.g. ``"engineering/engineering-senior-developer.md"``.
            fallback: Returned when the file is missing or empty.

        Returns:
            Stripped prompt text (YAML frontmatter stripped if present),
            or *fallback*.
        """
        rel = (role_relative_path or "").strip().lstrip("/")
        if not rel:
            return fallback

        # 1) overrides
        overrides_path = self._prompts_dir / "overrides" / rel
        loaded = self._try_read(overrides_path)
        if loaded:
            logger.debug("PromptLoader: resolved path=%s source=overrides", rel)
            return loaded

        # 2) upstream (submodule)
        upstream_path = self._prompts_dir / "upstream" / rel
        loaded = self._try_read(upstream_path)
        if loaded:
            logger.debug("PromptLoader: resolved path=%s source=upstream", rel)
            return loaded

        # 3) direct (flat layout fallback)
        direct_path = self._prompts_dir / rel
        loaded = self._try_read(direct_path)
        if loaded:
            logger.debug("PromptLoader: resolved path=%s source=direct", rel)
            return loaded

        logger.debug("PromptLoader: not found path=%s — using fallback", rel)
        return fallback

    @staticmethod
    def _try_read(path: Path) -> str:
        """Try to read and strip a prompt file. Returns empty string on failure."""
        if not path.is_file():
            return ""
        try:
            text = path.read_text(encoding="utf-8").strip()
            if text:
                return _strip_skill_frontmatter(text)
        except OSError as exc:
            logger.warning("PromptLoader: failed to read prompt file %s: %s", path, exc)
        return ""
