
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _strip_skill_frontmatter(text: str) -> str:
    raw = text.strip()
    if not raw.startswith("---"):
        return text.strip()
    parts = raw.split("---", 2)
    if len(parts) >= 3:
        return parts[2].strip()
    return text.strip()


class PromptLoader:

    def __init__(self, prompts_dir: Path) -> None:
        self._prompts_dir = prompts_dir

    def load(self, role_relative_path: str, fallback: str = "") -> str:
        rel = (role_relative_path or "").strip().lstrip("/")
        if not rel:
            return fallback

        overrides_path = self._prompts_dir / "overrides" / rel
        loaded = self._try_read(overrides_path)
        if loaded:
            logger.debug("PromptLoader: resolved path=%s source=overrides", rel)
            return loaded

        upstream_path = self._prompts_dir / "upstream" / rel
        loaded = self._try_read(upstream_path)
        if loaded:
            logger.debug("PromptLoader: resolved path=%s source=upstream", rel)
            return loaded

        direct_path = self._prompts_dir / rel
        loaded = self._try_read(direct_path)
        if loaded:
            logger.debug("PromptLoader: resolved path=%s source=direct", rel)
            return loaded

        logger.debug("PromptLoader: not found path=%s — using fallback", rel)
        return fallback

    @staticmethod
    def _try_read(path: Path) -> str:
        if not path.is_file():
            return ""
        try:
            text = path.read_text(encoding="utf-8").strip()
            if text:
                return _strip_skill_frontmatter(text)
        except OSError as exc:
            logger.warning("PromptLoader: failed to read prompt file %s: %s", path, exc)
        return ""
