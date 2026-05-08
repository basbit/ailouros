from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from backend.App.orchestration.domain.scenarios.errors import ScenarioRegistryError

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_PATH = _PROJECT_ROOT / "config" / "scenarios"


def _scenarios_path(override: Path | None) -> Path:
    if override is not None:
        return override
    raw = os.getenv("SWARM_SCENARIOS_PATH", "").strip()
    if raw:
        p = Path(raw).expanduser()
        return p if p.is_absolute() else _PROJECT_ROOT / p
    return _DEFAULT_PATH


class ScenarioFileLoader:
    def __init__(self, path: Path | None = None) -> None:
        self._path = _scenarios_path(path)

    def load_all(self) -> list[tuple[Path, dict[str, Any]]]:
        if not self._path.exists():
            logger.info("scenario_loader: directory not found: %s", self._path)
            return []
        results: list[tuple[Path, dict[str, Any]]] = []
        for file in sorted(self._path.glob("*.json")):
            if file.name.startswith("__"):
                continue
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ScenarioRegistryError(
                    f"Invalid scenario JSON in {file.name}: {exc}"
                ) from exc
            results.append((file, data))
        return results
