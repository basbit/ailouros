from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from backend.App.integrations.application.system_metrics import build_live_tick_payload

logger = logging.getLogger(__name__)


async def handle_ws_ui(websocket: WebSocket, task_store: Any) -> None:
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
                payload = await asyncio.to_thread(
                    build_live_tick_payload, task_store, subscribed["task_id"],
                )
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
