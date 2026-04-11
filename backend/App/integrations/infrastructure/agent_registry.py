"""Реестр ролей: JSON с дефолтами и флагами enabled (мердж в agent_config).

Путь: env ``SWARM_AGENT_REGISTRY_PATH`` или ``<project>/config/agent_registry.json``.
Запрос клиента перекрывает значения из реестра (deep merge: реестр сначала, потом request).
Роли с ``enabled: false`` удаляют секцию из итогового agent_config (кроме reviewer/human
если заданы в запросе явно).
"""

from __future__ import annotations

import copy
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[4]  # backend/App/integrations/infrastructure -> project root
_DEFAULT_PATH = _PROJECT_ROOT / "config" / "agent_registry.json"
_CACHE: tuple[float, dict[str, Any]] = (0.0, {})
_TTL = 30.0


def _registry_path() -> Path:
    raw = os.getenv("SWARM_AGENT_REGISTRY_PATH", "").strip()
    if raw:
        p = Path(raw).expanduser()
        return p if p.is_absolute() else _PROJECT_ROOT / p
    return _DEFAULT_PATH


def load_registry_raw() -> dict[str, Any]:
    global _CACHE
    path = _registry_path()
    try:
        mtime = path.stat().st_mtime
    except OSError as exc:
        logger.debug("agent_registry: registry file not found or inaccessible: %s", exc)
        return {}
    if _CACHE[0] == mtime and _CACHE[1] is not None:
        return copy.deepcopy(_CACHE[1])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("agent_registry: skip (%s)", exc)
        return {}
    if not isinstance(data, dict):
        return {}
    _CACHE = (mtime, copy.deepcopy(data))
    return copy.deepcopy(data)


def merge_agent_config(request_config: Optional[dict[str, Any]]) -> dict[str, Any]:
    raw = load_registry_raw()
    if not raw:
        return copy.deepcopy(request_config or {})

    defaults = raw.get("defaults")
    if not isinstance(defaults, dict):
        defaults = {}

    roles_meta = raw.get("roles")
    if not isinstance(roles_meta, dict):
        roles_meta = {}

    base: dict[str, Any] = {}
    for role, frag in defaults.items():
        if isinstance(frag, dict):
            base[role] = copy.deepcopy(frag)

    for role, meta in roles_meta.items():
        if not isinstance(meta, dict):
            continue
        if meta.get("enabled") is False:
            base.pop(role, None)
            continue
        ov = meta.get("config")
        if isinstance(ov, dict):
            cur = base.setdefault(role, {})
            if isinstance(cur, dict):
                merged = copy.deepcopy(cur)
                merged.update(copy.deepcopy(ov))
                base[role] = merged

    req = copy.deepcopy(request_config or {})
    for role, frag in req.items():
        if not isinstance(frag, dict):
            base[role] = frag
            continue
        if role not in base or not isinstance(base.get(role), dict):
            base[role] = copy.deepcopy(frag)
        else:
            cur = copy.deepcopy(base[role])
            cur.update(frag)
            base[role] = cur
    return base
