"""ThreadTokenTracker: thread-local token usage accumulator.

Extracted from client.py.  The tracker allows the pipeline graph to collect
usage from all agent.run() calls within one step without changing node
signatures or adding pipeline state keys.
"""
from __future__ import annotations

import contextlib
import threading
from typing import Any, Optional


class ThreadTokenTracker:
    """Thread-local accumulator for LLM token usage within a pipeline step.

    Usage::

        tracker = ThreadTokenTracker()
        with tracker.thread_usage_tracking():
            # ... LLM calls happen here ...
            tracker.accumulate(usage_dict)
        usage = tracker.get_and_reset()
    """

    def __init__(self) -> None:
        self._local: threading.local = threading.local()

    def reset(self) -> None:
        """Reset the per-thread counters to zero."""
        self._local.input_tokens = 0
        self._local.output_tokens = 0

    def accumulate(self, usage: dict[str, Any]) -> None:
        """Add *usage* token counts to the running thread total.

        Args:
            usage: Dict with optional ``input_tokens`` and ``output_tokens`` keys.
        """
        if not hasattr(self._local, "input_tokens"):
            self.reset()
        self._local.input_tokens += int(usage.get("input_tokens") or 0)
        self._local.output_tokens += int(usage.get("output_tokens") or 0)

    def get_and_reset(self) -> dict[str, int]:
        """Read accumulated tokens and reset. Call after a step completes.

        Returns:
            Dict with ``input_tokens`` and ``output_tokens`` keys.
        """
        input_tokens = getattr(self._local, "input_tokens", 0)
        output_tokens = getattr(self._local, "output_tokens", 0)
        self.reset()
        return {"input_tokens": input_tokens, "output_tokens": output_tokens}

    def get_current(self) -> Optional[dict[str, int]]:
        """Return the current accumulated counts without resetting.

        Returns:
            Dict with ``input_tokens`` / ``output_tokens``, or ``None`` if
            tracking was never started on this thread.
        """
        if not hasattr(self._local, "input_tokens"):
            return None
        return {
            "input_tokens": self._local.input_tokens,
            "output_tokens": self._local.output_tokens,
        }

    @contextlib.contextmanager
    def thread_usage_tracking(self):
        """Context manager: reset on entry; caller reads via get_and_reset() on exit."""
        self.reset()
        try:
            yield
        finally:
            pass  # Caller calls get_and_reset() after the with-block if needed


# ---------------------------------------------------------------------------
# Module-level singleton (backward compat with client.py callers)
# ---------------------------------------------------------------------------

_default_tracker = ThreadTokenTracker()


def reset_thread_usage() -> None:
    """Reset the token accumulator before a pipeline step starts."""
    _default_tracker.reset()


def get_and_reset_thread_usage() -> dict[str, int]:
    """Read accumulated tokens and reset. Call after a step completes."""
    return _default_tracker.get_and_reset()


def _accumulate_thread_usage(usage: dict[str, Any]) -> None:
    _default_tracker.accumulate(usage)


@contextlib.contextmanager
def thread_usage_tracking():
    """Context manager that resets thread-local token usage on entry.

    .. deprecated::
        Use :class:`ThreadTokenTracker`.thread_usage_tracking() directly.
    """
    with _default_tracker.thread_usage_tracking():
        yield
