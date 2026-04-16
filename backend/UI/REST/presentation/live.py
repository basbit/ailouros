"""Host metrics and WebSocket /ws/ui handler.

Canonical location: backend/UI/REST/presentation/live.py.
``orchestrator/live.py`` is kept as a re-export shim for backward compatibility.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Optional

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from backend.App.workspace.infrastructure.workspace_io import command_exec_allowed, workspace_write_allowed

logger = logging.getLogger(__name__)

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


async def handle_ws_ui(websocket: WebSocket, task_store: Any) -> None:
    """Metrics + task snapshot subscription."""
    await websocket.accept()
    subscribed: dict[str, Any] = {"task_id": None}
    stop = asyncio.Event()

    async def recv_loop() -> None:
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("cmd") == "subscribe":
                    subscribed["task_id"] = msg.get("task_id")
        except WebSocketDisconnect:
            pass
        finally:
            stop.set()

    async def pump_loop() -> None:
        while not stop.is_set():
            try:

                def _build_tick_payload() -> dict[str, Any]:
                    return {
                        "type": "tick",
                        "metrics": metrics_payload(),
                        "task": task_snapshot(task_store, subscribed["task_id"]),
                        "capabilities": {
                            "workspace_write": workspace_write_allowed(),
                            "command_exec": command_exec_allowed(),
                        },
                    }

                # Redis/psutil/cpu_percent(interval=...) -- don't block event loop,
                # otherwise SSE chat/completions and other awaits hang for seconds.
                payload = await asyncio.to_thread(_build_tick_payload)
                await websocket.send_json(payload)
            except WebSocketDisconnect:
                stop.set()
                return
            except Exception as exc:
                logger.warning("ws/ui pump_loop error: %s", exc)
                stop.set()
                return
            await asyncio.sleep(1.0)

    await asyncio.gather(recv_loop(), pump_loop())
