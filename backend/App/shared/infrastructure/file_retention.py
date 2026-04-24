from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from backend.App.shared.application.settings_resolver import get_setting_int

_SECONDS_PER_DAY = 86400


async def cleanup_old_directories(
    root: Path,
    *,
    ttl_setting_key: str,
    ttl_env_key: str,
    default_ttl_days: int,
    logger: logging.Logger,
) -> int:
    ttl_days = get_setting_int(
        ttl_setting_key,
        env_key=ttl_env_key,
        default=default_ttl_days,
    )
    if ttl_days <= 0:
        return 0

    cutoff = time.time() - ttl_days * _SECONDS_PER_DAY
    removed = 0
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
        except OSError as error:
            logger.warning(
                "Failed to stat directory %s during retention cleanup: %s", entry, error
            )
    if removed:
        logger.info("Cleaned up %d old directories under %s", removed, root)
    return removed
