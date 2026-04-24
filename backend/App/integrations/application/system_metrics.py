from __future__ import annotations

import os
from typing import Any, Optional

_psutil: Optional[Any] = None
try:
    import psutil as _psutil_import
    _psutil = _psutil_import
except Exception:  # pragma: no cover
    pass


def metrics_payload() -> dict[str, Any]:
    data: dict[str, Any] = {"provider": "local"}
    try:
        if _psutil is None:
            data["loadavg"] = os.getloadavg()
            return data
        data["cpu_percent"] = _psutil.cpu_percent(interval=0.1)
        vm = _psutil.virtual_memory()
        data["memory_percent"] = vm.percent
        data["memory_used_gb"] = round(vm.used / (1024**3), 2)
    except Exception as exc:  # pragma: no cover
        data["error"] = str(exc)
    return data


def task_snapshot(task_store: Any, task_id: Optional[str]) -> Optional[dict[str, Any]]:
    if not task_id:
        return None
    try:
        return task_store.get_task(task_id)
    except KeyError:
        return {"error": "not_found", "task_id": task_id}


def build_live_tick_payload(task_store: Any, task_id: Optional[str]) -> dict[str, Any]:
    from backend.App.workspace.infrastructure.workspace_io import (
        command_exec_allowed,
        workspace_write_allowed,
    )
    return {
        "type": "tick",
        "metrics": metrics_payload(),
        "task": task_snapshot(task_store, task_id),
        "capabilities": {
            "workspace_write": workspace_write_allowed(),
            "command_exec": command_exec_allowed(),
        },
    }
