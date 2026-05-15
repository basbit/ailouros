from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from backend.App.shared.infrastructure.json_file_io import read_json_file, write_json_file

logger = logging.getLogger(__name__)

_MASKED = "***"

_SECRET_ENV_KEYS: dict[str, str] = {
    "tavily_api_key": "TAVILY_API_KEY",
    "exa_api_key": "EXA_API_KEY",
    "scrapingdog_api_key": "SCRAPINGDOG_API_KEY",
}

_AUTOMATION_BOOL_FIELDS: tuple[str, ...] = (
    "swarm_self_verify",
    "swarm_auto_retry",
    "swarm_deep_planning",
    "swarm_background_agent",
    "swarm_dream_enabled",
    "swarm_quality_gate",
    "swarm_auto_plan",
)

_AUTOMATION_STR_FIELDS: tuple[str, ...] = (
    "swarm_self_verify_model",
    "swarm_self_verify_provider",
    "swarm_auto_approve",
    "swarm_auto_approve_timeout",
    "swarm_max_step_retries",
    "swarm_deep_planning_model",
    "swarm_deep_planning_provider",
    "swarm_background_agent_model",
    "swarm_background_agent_provider",
    "swarm_background_watch_paths",
    "swarm_planner_model",
    "swarm_planner_provider",
)


def _settings_file_path() -> Path:
    override = os.environ.get("SWARM_USER_SETTINGS_FILE", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    root = os.environ.get("SWARM_PROJECT_ROOT", "").strip()
    base = Path(root).resolve() if root else Path(__file__).resolve().parents[5]
    return base / "var" / "user_settings.json"


def _read_persisted() -> dict[str, Any]:
    path = _settings_file_path()
    try:
        data = read_json_file(path)
    except FileNotFoundError:
        return {}
    except OSError as exc:
        logger.warning("user_settings: failed to read %s: %s", path, exc)
        return {}
    except json.JSONDecodeError as exc:
        logger.warning("user_settings: malformed JSON in %s: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _write_persisted(data: dict[str, Any]) -> None:
    path = _settings_file_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json_file(path, data, sort_keys=True)
    except OSError as exc:
        logger.warning("user_settings: failed to write %s: %s", path, exc)


def _normalized_automation(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in _AUTOMATION_BOOL_FIELDS:
        out[key] = bool(raw.get(key, False))
    for key in _AUTOMATION_STR_FIELDS:
        value = raw.get(key, "")
        out[key] = str(value) if value is not None else ""
    return out


def masked_user_settings() -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, env_var in _SECRET_ENV_KEYS.items():
        result[key] = _MASKED if os.environ.get(env_var) else ""
    persisted = _read_persisted()
    result.update(_normalized_automation(persisted))
    return result


def update_user_settings(data: dict[str, Any]) -> dict[str, Any]:
    updated: list[str] = []

    for key, env_var in _SECRET_ENV_KEYS.items():
        value = data.get(key, "")
        if isinstance(value, str) and value.strip() and value != _MASKED:
            os.environ[env_var] = value.strip()
            updated.append(key)

    persisted = _read_persisted()
    persisted_changed = False
    for key in _AUTOMATION_BOOL_FIELDS:
        if key in data:
            new_val = bool(data[key])
            if persisted.get(key) != new_val:
                persisted[key] = new_val
                updated.append(key)
                persisted_changed = True
    for key in _AUTOMATION_STR_FIELDS:
        if key in data:
            raw_val = data[key]
            new_str_val = str(raw_val).strip() if raw_val is not None else ""
            if persisted.get(key, "") != new_str_val:
                persisted[key] = new_str_val
                updated.append(key)
                persisted_changed = True

    if persisted_changed:
        _write_persisted(persisted)

    logger.info("user_settings: updated keys: %s", updated)
    return {"status": "ok", "updated": updated}


def get_persisted_automation_settings() -> dict[str, Any]:
    return _normalized_automation(_read_persisted())


def load_and_apply_user_settings() -> None:
    pass
