"""Append-only текстовый лог прогона пайплайна рядом с pipeline.json."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


def _utc_ts_ms() -> str:
    """ISO UTC с миллисекундами — иначе несколько строк за одну секунду выглядят как один момент."""
    d = datetime.now(timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"


def append_task_run_log(task_dir: Path, line: str) -> None:
    """Пишет строку в ``<task_dir>/pipeline_run.log`` (UTF-8)."""
    try:
        task_dir.mkdir(parents=True, exist_ok=True)
        p = task_dir / "pipeline_run.log"
        ts = _utc_ts_ms()
        with p.open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] {line.rstrip()}\n")
    except OSError as exc:
        import logging as _logging
        _logging.getLogger(__name__).debug(
            "Could not write pipeline_run.log in %s: %s", task_dir, exc
        )


__all__ = ["append_task_run_log"]
