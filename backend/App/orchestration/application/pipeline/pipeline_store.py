from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.App.paths import APP_ROOT

DEFAULT_PIPELINES_DIR: Path = Path(
    os.getenv("SWARM_PIPELINES_DIR", str(APP_ROOT / "var" / "pipelines"))
).resolve()


@dataclass
class PipelineDefinition:
    name: str
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)


def _pipeline_path(pipeline_id: str, directory: Path) -> Path:
    safe = Path(pipeline_id).name
    return directory / f"{safe}.json"


def _load(pipeline_id: str, directory: Path) -> dict[str, Any]:
    path = _pipeline_path(pipeline_id, directory)
    if not path.is_file():
        raise KeyError(pipeline_id)
    return json.loads(path.read_text(encoding="utf-8"))


def create_pipeline(definition: PipelineDefinition, directory: Path) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    pipeline_id = str(uuid.uuid4())
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    record: dict[str, Any] = {
        "id": pipeline_id,
        "name": definition.name,
        "nodes": definition.nodes,
        "edges": definition.edges,
        "created_at": now,
        "updated_at": now,
    }
    _pipeline_path(pipeline_id, directory).write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return record


def list_pipelines(directory: Path) -> list[dict[str, Any]]:
    if not directory.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            items.append({
                "id": data.get("id", path.stem),
                "name": data.get("name", path.stem),
                "updated_at": data.get("updated_at", ""),
            })
        except (json.JSONDecodeError, OSError):
            continue
    return items


def get_pipeline(pipeline_id: str, directory: Path) -> dict[str, Any]:
    return _load(pipeline_id, directory)


def update_pipeline(
    pipeline_id: str, definition: PipelineDefinition, directory: Path
) -> dict[str, Any]:
    existing = _load(pipeline_id, directory)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    existing.update({
        "name": definition.name,
        "nodes": definition.nodes,
        "edges": definition.edges,
        "updated_at": now,
    })
    _pipeline_path(pipeline_id, directory).write_text(
        json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return existing


def delete_pipeline(pipeline_id: str, directory: Path) -> dict[str, Any]:
    path = _pipeline_path(pipeline_id, directory)
    if not path.is_file():
        raise KeyError(pipeline_id)
    path.unlink()
    return {"ok": True, "id": pipeline_id}
