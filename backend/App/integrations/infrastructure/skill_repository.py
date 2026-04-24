from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Optional

from backend.App.orchestration.infrastructure.agents.base_agent import PROMPTS_DIR, _strip_skill_frontmatter

logger = logging.getLogger(__name__)

__all__ = ["format_role_skills_extra"]


def _normalize_skill_id(raw: str) -> str:
    s = (raw or "").strip().lower()
    s = re.sub(r"[^a-z0-9_-]+", "_", s)
    return s.strip("_")[:64]


def _skill_ids_from_role_cfg(role_cfg: Optional[dict[str, Any]]) -> list[str]:
    if not isinstance(role_cfg, dict):
        return []
    raw = role_cfg.get("skill_ids")
    if raw is None:
        raw = role_cfg.get("skills")
    if isinstance(raw, str):
        parts = re.split(r"[,;\s]+", raw)
    elif isinstance(raw, list):
        parts = [str(x) for x in raw]
    else:
        return []
    out: list[str] = []
    for p in parts:
        nid = _normalize_skill_id(p)
        if nid and nid not in out:
            out.append(nid)
    return out


def _resolve_skill_file(workspace_root: str, rel_path: str) -> Optional[Path]:
    rel = (rel_path or "").strip().replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/"):
        return None
    if workspace_root.strip():
        root = Path(workspace_root).expanduser().resolve()
        candidate = (root / rel).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        return candidate if candidate.is_file() else None
    for sub in ("overrides", "upstream", ""):
        base = PROMPTS_DIR / sub if sub else PROMPTS_DIR
        p = (base / rel).resolve()
        try:
            p.relative_to(base.resolve())
        except ValueError:
            continue
        if p.is_file():
            return p
    return None


def _load_skill_body(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("skill file read failed %s: %s", path, e)
        return ""
    return _strip_skill_frontmatter(text).strip()


def _catalog_by_normalized_keys(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for ck, cv in raw.items():
        nk = _normalize_skill_id(str(ck))
        if nk and isinstance(cv, dict):
            out[nk] = cv
    return out


def format_role_skills_extra(
    agent_config: dict[str, Any],
    role_cfg: Optional[dict[str, Any]],
    *,
    workspace_root: str = "",
) -> str:
    ids = _skill_ids_from_role_cfg(role_cfg)
    if not ids:
        return ""
    catalog = agent_config.get("skills")
    if not isinstance(catalog, dict):
        return ""

    catalog_n = _catalog_by_normalized_keys(catalog)

    chunks: list[str] = []
    for sid in ids:
        entry = catalog_n.get(sid)
        if not isinstance(entry, dict):
            chunks.append(f"### Skill `{sid}`\n[no entry in agent_config.skills]\n")
            continue
        rel = str(entry.get("path") or entry.get("file") or "").strip()
        title = str(entry.get("title") or entry.get("label") or sid).strip()
        if not rel:
            chunks.append(f"### {title}\n[no path defined in skills catalog]\n")
            continue
        path = _resolve_skill_file(workspace_root, rel)
        if not path:
            chunks.append(
                f"### {title}\n[file not found: `{rel}` — check workspace root and path]\n"
            )
            continue
        body = _load_skill_body(path)
        if not body:
            chunks.append(f"### {title}\n[empty or unreadable file `{rel}`]\n")
        else:
            chunks.append(f"### Skill: {title} (`{sid}`)\n\n{body}")

    if not chunks:
        return ""
    return "\n\n".join(chunks)
