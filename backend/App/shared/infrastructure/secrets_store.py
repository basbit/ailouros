from __future__ import annotations

import json
import os
import stat
import threading
from pathlib import Path
from typing import Optional

_lock = threading.RLock()
_DEFAULT_DIR = Path.home() / ".swarm"
_DEFAULT_FILE = "secrets.json"


def _resolve_path() -> Path:
    override = (os.getenv("SWARM_SECRETS_PATH") or "").strip()
    if override:
        candidate = Path(override).expanduser()
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        return candidate.resolve()
    return (_DEFAULT_DIR / _DEFAULT_FILE).resolve()


def _read_secrets() -> dict[str, str]:
    path = _resolve_path()
    if not path.is_file():
        return {}
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(
            f"secrets file {path} must contain a JSON object, got {type(data).__name__}"
        )
    cleaned: dict[str, str] = {}
    for key, value in data.items():
        if isinstance(key, str) and isinstance(value, str) and key.strip():
            cleaned[key.strip()] = value
    return cleaned


def _write_secrets(values: dict[str, str]) -> None:
    path = _resolve_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(values, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(tmp, path)
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def load_secret(name: str) -> Optional[str]:
    name = (name or "").strip()
    if not name:
        return None
    with _lock:
        values = _read_secrets()
    raw = values.get(name)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def save_secret(name: str, value: str) -> None:
    name = (name or "").strip()
    if not name:
        raise ValueError("secret name required")
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"secret value for {name!r} must be a non-empty string")
    with _lock:
        values = _read_secrets()
        values[name] = value.strip()
        _write_secrets(values)


def delete_secret(name: str) -> bool:
    name = (name or "").strip()
    if not name:
        return False
    with _lock:
        values = _read_secrets()
        if name not in values:
            return False
        values.pop(name, None)
        _write_secrets(values)
        return True


def list_secret_names() -> list[str]:
    with _lock:
        values = _read_secrets()
    return sorted(values.keys())


__all__ = [
    "delete_secret",
    "list_secret_names",
    "load_secret",
    "save_secret",
]
