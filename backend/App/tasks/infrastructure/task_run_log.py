from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _utc_timestamp_ms() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def append_task_run_log(task_dir: Path, line: str) -> None:
    """Append a timestamped line to the pipeline run log for the given task directory."""
    try:
        task_dir.mkdir(parents=True, exist_ok=True)
        log_path = task_dir / "pipeline_run.log"
        timestamp = _utc_timestamp_ms()
        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(f"[{timestamp}] {line.rstrip()}\n")
    except OSError as exc:
        logger.warning(
            "Could not write pipeline_run.log in %s: %s", task_dir, exc
        )


__all__ = ["append_task_run_log"]
