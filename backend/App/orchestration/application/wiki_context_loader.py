"""Load wiki context from .swarm/wiki/ for injection into agent prompts.

Reads index.md (overview) plus any articles relevant to the current task.
Returns a compact text block — empty string when wiki doesn't exist yet.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Maximum chars to inject per article
_MAX_INDEX_CHARS = 2000
_MAX_ARTICLE_CHARS = 1500
_MAX_TOTAL_CHARS = 8000

# Priority sub-directories to scan for relevant articles (pipeline-generated + user-created)
_PRIORITY_DIRS = (
    "architecture", "planning", "development", "analysis",
    "documentation", "qa", "devops",
    "features", "api", "agents",
)


def _read_file(path: Path, max_chars: int) -> str:
    try:
        text = path.read_text(encoding="utf-8").strip()
        if len(text) > max_chars:
            text = text[:max_chars] + "\n…[truncated]"
        return text
    except OSError:
        return ""


def load_wiki_context(workspace_root: str | Path) -> str:
    """Return wiki memory block for *workspace_root*/.swarm/wiki/.

    Reads index.md then up to a few architecture/feature articles.
    Returns empty string if wiki doesn't exist or is empty.
    """
    wiki_root = Path(workspace_root) / ".swarm" / "wiki"
    if not wiki_root.exists():
        return ""

    parts: list[str] = []
    total = 0

    # 1. index.md — high-level overview
    index_path = wiki_root / "index.md"
    if index_path.exists():
        content = _read_file(index_path, _MAX_INDEX_CHARS)
        if content:
            parts.append(f"## index\n{content}")
            total += len(content)

    # 2. Priority articles from architecture/, features/, api/
    for sub in _PRIORITY_DIRS:
        if total >= _MAX_TOTAL_CHARS:
            break
        sub_dir = wiki_root / sub
        if not sub_dir.is_dir():
            continue
        for md in sorted(sub_dir.glob("*.md"))[:4]:
            if total >= _MAX_TOTAL_CHARS:
                break
            content = _read_file(md, _MAX_ARTICLE_CHARS)
            if content:
                rel = md.relative_to(wiki_root).with_suffix("").as_posix()
                parts.append(f"## {rel}\n{content}")
                total += len(content)

    if not parts:
        return ""

    logger.debug("wiki_context_loader: loaded %d chars from %s", total, wiki_root)
    return "\n\n".join(parts)
