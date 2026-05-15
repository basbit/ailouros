from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from pathlib import Path
from threading import RLock
from typing import Any

logger = logging.getLogger(__name__)

_OVERRIDES: dict[str, Any] = {}
_OVERRIDES_LOCK = RLock()

_SETTINGS_CACHE: dict[Path, tuple[int, dict[str, Any]]] = {}
_CACHE_LOCK = RLock()

_DEPRECATED_WARNED: set[str] = set()
_WARNED_LOCK = RLock()
_WORKSPACE_SETTINGS_DIR = ".swarm"
_WORKSPACE_SETTINGS_FILE = "settings.json"


def settings_path_override() -> str:

    return os.environ.get("SWARM_SETTINGS_PATH", "").strip()


def _resolve_settings_paths(workspace_root: str | Path | None) -> list[Path]:

    paths: list[Path] = []
    override = settings_path_override()
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_file():
            paths.append(candidate)
        else:
            raise FileNotFoundError(
                "settings resolution failed: operation=resolve_settings_paths "
                f"input=SWARM_SETTINGS_PATH={override!r} expected=existing file "
                "actual=file missing"
            )
    if workspace_root:
        try:
            workspace_settings_path = _workspace_settings_path(workspace_root)
            if (
                workspace_settings_path.is_file()
                and workspace_settings_path not in paths
            ):
                paths.append(workspace_settings_path)
        except (OSError, TypeError, ValueError) as exc:
            raise ValueError(
                "settings resolution failed: operation=resolve_project_settings_path "
                f"workspace_root={workspace_root!r} expected=valid workspace root "
                f"actual={exc}"
            ) from exc
    return paths


def _workspace_settings_path(workspace_root: str | Path) -> Path:
    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"workspace_root is not a directory: {root}")
    candidate = (root / _WORKSPACE_SETTINGS_DIR / _WORKSPACE_SETTINGS_FILE).resolve(
        strict=False
    )
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"workspace settings path escapes workspace root: {candidate}"
        ) from exc
    return candidate


def _load_cached(path: Path) -> dict[str, Any]:
    try:
        mtime = path.stat().st_mtime_ns
    except OSError as exc:
        raise OSError(
            "settings resolution failed: operation=stat_settings_file "
            f"path={path!s} expected=readable file actual={exc}"
        ) from exc
    with _CACHE_LOCK:
        cached = _SETTINGS_CACHE.get(path)
        if cached and cached[0] == mtime:
            return cached[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise OSError(
            "settings resolution failed: operation=read_settings_file "
            f"path={path!s} expected=utf-8 JSON file actual={exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            "settings resolution failed: operation=parse_settings_file "
            f"path={path!s} expected=valid JSON object actual={exc}"
        ) from exc
    if not isinstance(data, dict):
        raise ValueError(
            "settings resolution failed: operation=parse_settings_file "
            f"path={path!s} expected=JSON object actual={type(data).__name__}"
        )
    with _CACHE_LOCK:
        _SETTINGS_CACHE[path] = (mtime, data)

    return data


def _walk_dotted(data: Mapping[str, Any], dotted: str) -> tuple[bool, Any]:

    parts = [part for part in dotted.split(".") if part]
    current_value: Any = data
    for part in parts:
        if not isinstance(current_value, Mapping) or part not in current_value:
            return (False, None)
        current_value = current_value[part]
    return (True, current_value)


def _default_env_key(key: str) -> str:
    return "SWARM_" + key.upper().replace(".", "_").replace("-", "_")


def _warn_deprecated_env(env_key: str, settings_key: str) -> None:
    with _WARNED_LOCK:
        if env_key in _DEPRECATED_WARNED:
            return
        _DEPRECATED_WARNED.add(env_key)
    logger.warning(
        "settings_resolver: env var %s is deprecated — settings.json key %r already "
        "carries this value; remove the env var to silence this warning.",
        env_key,
        settings_key,
    )


def set_override(key: str, value: Any) -> None:

    with _OVERRIDES_LOCK:
        _OVERRIDES[key] = value


def clear_overrides_for_tests() -> None:

    with _OVERRIDES_LOCK:
        _OVERRIDES.clear()


def invalidate_settings_cache(path: str | Path | None = None) -> None:
    with _CACHE_LOCK:
        if path is None:
            _SETTINGS_CACHE.clear()
            return
        resolved_path = Path(path).expanduser().resolve(strict=False)
        _SETTINGS_CACHE.pop(resolved_path, None)


def reset_state_for_tests() -> None:

    clear_overrides_for_tests()
    invalidate_settings_cache()
    with _WARNED_LOCK:
        _DEPRECATED_WARNED.clear()


def get_setting(
    key: str,
    *,
    workspace_root: str | Path | None = None,
    env_key: str | None = None,
    default: Any = None,
) -> Any:

    with _OVERRIDES_LOCK:
        if key in _OVERRIDES:
            return _OVERRIDES[key]

    settings_value: Any = None
    settings_found = False
    for path in _resolve_settings_paths(workspace_root):
        data = _load_cached(path)
        ok, value = _walk_dotted(data, key)
        if ok:
            settings_value = value
            settings_found = True
            break

    resolved_env_key = env_key or _default_env_key(key)
    env_raw = os.environ.get(resolved_env_key)
    env_present = env_raw is not None and env_raw.strip() != ""

    if settings_found:
        if env_present:
            _warn_deprecated_env(resolved_env_key, key)
        return settings_value
    if env_present:
        return env_raw

    return default


def get_setting_bool(
    key: str,
    *,
    workspace_root: str | Path | None = None,
    env_key: str | None = None,
    default: bool = False,
) -> bool:

    raw = get_setting(key, workspace_root=workspace_root, env_key=env_key, default=None)
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    text = str(raw).strip().lower()
    return text in {"1", "true", "yes", "on"}


def get_setting_int(
    key: str,
    *,
    workspace_root: str | Path | None = None,
    env_key: str | None = None,
    default: int = 0,
) -> int:

    raw_value = get_setting(
        key, workspace_root=workspace_root, env_key=env_key, default=None
    )
    if raw_value is None:
        return default
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        raise ValueError(
            "settings resolution failed: operation=parse_int_setting "
            f"key={key!r} expected=integer actual={raw_value!r}"
        )


__all__ = [
    "clear_overrides_for_tests",
    "get_setting",
    "get_setting_bool",
    "get_setting_int",
    "invalidate_settings_cache",
    "reset_state_for_tests",
    "set_override",
    "settings_path_override",
]
