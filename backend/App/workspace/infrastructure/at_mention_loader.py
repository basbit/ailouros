"""AtMentionLoader — reads files referenced via ``@path`` syntax in a prompt.

Extracted from ``tasks.py`` (DECOMP-11).

Pure filesystem operation: no LLM calls, no FastAPI, no pipeline state.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_AT_MENTION_PATTERN = re.compile(r'@([\w./\-]+\.\w+)')
_MAX_TOTAL_CHARS = int(os.environ.get("SWARM_AT_MENTION_MAX_CHARS", "50000"))
_MAX_MENTIONS = int(os.environ.get("SWARM_AT_MENTION_MAX_COUNT", "10"))


def load_at_mentions(prompt: str, workspace_root: str) -> str:
    """Read files referenced via ``@path/to/file.ext`` in ``prompt``.

    Returns a formatted Markdown block suitable for prepending to the assembled
    prompt, or an empty string if no mentions are found or workspace_root is
    empty.

    Args:
        prompt: User prompt text that may contain ``@rel/path`` mentions.
        workspace_root: Absolute (or ``~``-relative) path to workspace root.
            Mention paths are resolved relative to this directory.

    Returns:
        Markdown block starting with ``## Referenced files:`` or ``""``.
    """
    mentions = _AT_MENTION_PATTERN.findall(prompt)
    if not mentions or not workspace_root:
        return ""
    root = Path(workspace_root.strip()).expanduser().resolve()
    blocks: list[str] = []
    seen: set[str] = set()
    total_chars = 0
    for rel in mentions[:_MAX_MENTIONS]:
        if rel in seen:
            continue
        seen.add(rel)
        abs_path = root / rel
        try:
            text = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if total_chars + len(text) > _MAX_TOTAL_CHARS:
            text = text[:_MAX_TOTAL_CHARS - total_chars]
        blocks.append(f"### {rel}\n```\n{text}\n```")
        total_chars += len(text)
        if total_chars >= _MAX_TOTAL_CHARS:
            break
    if not blocks:
        return ""
    return "## Referenced files:\n\n" + "\n\n".join(blocks)


class AtMentionLoader:
    """Class wrapper around :func:`load_at_mentions` for dependency injection."""

    def __init__(self, workspace_root: str) -> None:
        self._workspace_root = workspace_root

    def load(self, prompt: str) -> str:
        """Load ``@mention`` file contents from ``prompt``.

        Returns:
            Markdown block or empty string.
        """
        return load_at_mentions(prompt, self._workspace_root)
