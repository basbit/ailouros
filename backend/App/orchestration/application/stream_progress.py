"""Thread-safe progress emitter for pipeline streaming.

Extracted from ``nodes/_shared.py`` — no FastAPI imports, no filesystem operations.
"""

from __future__ import annotations

import logging
import queue as _queue
from typing import Optional

logger = logging.getLogger(__name__)


def emit_progress(q: Optional[_queue.Queue[str]], message: str) -> None:
    """Put *message* onto *q* if it is not None.

    Thread-safe: uses ``put_nowait`` so the emitter never blocks a pipeline node.
    No-op when *q* is None.
    """
    if q is None:
        return
    if not hasattr(q, "put_nowait"):
        return
    try:
        q.put_nowait(message)
    except Exception as exc:
        logger.debug("emit_progress: queue full or closed, dropping message: %s", exc)
