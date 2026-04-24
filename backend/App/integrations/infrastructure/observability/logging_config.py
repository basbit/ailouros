from __future__ import annotations

import contextvars
import json
import logging
import os
import time
from contextvars import ContextVar

_task_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("task_id", default="")
_step_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("step", default="")
_request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def set_task_id(task_id: str) -> None:
    _task_id_ctx.set(task_id)


def get_task_id() -> str:
    return _task_id_ctx.get()


def set_step(step: str) -> None:
    _step_ctx.set(step)


def get_step() -> str:
    return _step_ctx.get()


def set_request_id(rid: str) -> None:
    _request_id_var.set(rid)


def get_request_id() -> str:
    return _request_id_var.get()


class StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()

        entry: dict = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": message,
        }

        task_id = get_task_id()
        if task_id:
            entry["task_id"] = task_id

        step = get_step()
        if step:
            entry["step"] = step

        request_id = get_request_id()
        if request_id:
            entry["request_id"] = request_id

        if record.exc_info:
            entry["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(entry)


def configure_logging(level: str | None = None, json_logs: bool | None = None) -> None:
    if level is None:
        level = os.environ.get("LOG_LEVEL", "INFO")

    if json_logs is None:
        json_logs = os.environ.get("LOG_JSON", "1") not in ("0", "false", "False", "no")

    numeric_level = logging.getLevelName(level.upper())
    if not isinstance(numeric_level, int):
        numeric_level = logging.INFO

    root_logger = logging.getLogger()

    if root_logger.handlers:
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

    handler = logging.StreamHandler()
    if json_logs:
        handler.setFormatter(StructuredFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )

    root_logger.addHandler(handler)
    root_logger.setLevel(numeric_level)


__all__ = [
    "configure_logging",
    "set_task_id",
    "get_task_id",
    "set_step",
    "get_step",
    "set_request_id",
    "get_request_id",
]
