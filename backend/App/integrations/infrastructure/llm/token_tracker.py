from __future__ import annotations

import contextlib
import threading
from typing import Any, Optional


class ThreadTokenTracker:
    def __init__(self) -> None:
        self._local: threading.local = threading.local()

    def reset(self) -> None:
        self._local.input_tokens = 0
        self._local.output_tokens = 0

    def accumulate(self, usage: dict[str, Any]) -> None:
        if not hasattr(self._local, "input_tokens"):
            self.reset()
        self._local.input_tokens += int(usage.get("input_tokens") or 0)
        self._local.output_tokens += int(usage.get("output_tokens") or 0)

    def get_and_reset(self) -> dict[str, int]:
        input_tokens = getattr(self._local, "input_tokens", 0)
        output_tokens = getattr(self._local, "output_tokens", 0)
        self.reset()
        return {"input_tokens": input_tokens, "output_tokens": output_tokens}

    def get_current(self) -> Optional[dict[str, int]]:
        if not hasattr(self._local, "input_tokens"):
            return None
        return {
            "input_tokens": self._local.input_tokens,
            "output_tokens": self._local.output_tokens,
        }

    @contextlib.contextmanager
    def thread_usage_tracking(self):
        self.reset()
        try:
            yield
        finally:
            pass


_default_tracker = ThreadTokenTracker()


def reset_thread_usage() -> None:
    _default_tracker.reset()


def get_and_reset_thread_usage() -> dict[str, int]:
    return _default_tracker.get_and_reset()


def _accumulate_thread_usage(usage: dict[str, Any]) -> None:
    _default_tracker.accumulate(usage)


@contextlib.contextmanager
def thread_usage_tracking():
    with _default_tracker.thread_usage_tracking():
        yield
