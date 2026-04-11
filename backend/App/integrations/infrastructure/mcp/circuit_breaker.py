"""Circuit breaker for MCP tool calls (K-8).

Implements CLOSED → OPEN → HALF_OPEN state machine per tool.
All state transitions are logged as structured events (INV-1, INV-3).

Config env vars:
    SWARM_CIRCUIT_BREAKER=1                    # 0 = disabled, 1 = enabled
    SWARM_CIRCUIT_BREAKER_FAILURE_THRESHOLD=3
    SWARM_CIRCUIT_BREAKER_RECOVERY_SECONDS=60
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "closed"        # Normal — calls pass through
    OPEN = "open"            # Broken — calls fail fast
    HALF_OPEN = "half_open"  # Testing — one probe call allowed


class ToolUnavailableError(RuntimeError):
    """Raised immediately when a circuit breaker is OPEN (INV-3: explicit error, no hang)."""

    def __init__(self, tool_name: str) -> None:
        super().__init__(f"Circuit breaker OPEN for tool '{tool_name}': call blocked")
        self.tool_name = tool_name


@dataclass
class CircuitBreaker:
    """Per-tool circuit breaker.

    Args:
        name: Tool name used for logging.
        failure_threshold: Number of consecutive failures before opening.
        recovery_timeout_sec: Seconds in OPEN before transitioning to HALF_OPEN.
    """

    name: str
    failure_threshold: int = 3
    recovery_timeout_sec: float = 60.0
    _state: CircuitState = field(default=CircuitState.CLOSED, init=False, repr=False)
    _failure_count: int = field(default=0, init=False, repr=False)
    _last_failure_time: float = field(default=0.0, init=False, repr=False)
    _last_success_time: float = field(default=0.0, init=False, repr=False)

    @property
    def state(self) -> CircuitState:
        """Return current state, automatically transitioning OPEN → HALF_OPEN after recovery."""
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self.recovery_timeout_sec:
                old = self._state
                self._state = CircuitState.HALF_OPEN
                logger.info(
                    "circuit_breaker tool=%s old_state=%s new_state=%s reason=recovery_period_elapsed",
                    self.name, old.value, self._state.value,
                )
        return self._state

    def record_success(self) -> None:
        """Record a successful tool call; transitions OPEN/HALF_OPEN → CLOSED."""
        old = self._state
        self._failure_count = 0
        self._last_success_time = time.monotonic()
        if self._state != CircuitState.CLOSED:
            self._state = CircuitState.CLOSED
            logger.info(
                "circuit_breaker tool=%s old_state=%s new_state=%s reason=success",
                self.name, old.value, self._state.value,
            )

    def record_failure(self) -> None:
        """Record a failed tool call; may transition CLOSED → OPEN or HALF_OPEN → OPEN."""
        old = self._state
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if self._state == CircuitState.HALF_OPEN:
            # Any failure in probe state reopens the circuit immediately
            self._state = CircuitState.OPEN
            logger.warning(
                "circuit_breaker tool=%s old_state=%s new_state=%s reason=probe_failed",
                self.name, old.value, self._state.value,
            )
        elif self._failure_count >= self.failure_threshold and self._state == CircuitState.CLOSED:
            self._state = CircuitState.OPEN
            logger.warning(
                "circuit_breaker tool=%s old_state=%s new_state=%s failure_count=%d reason=threshold_exceeded",
                self.name, old.value, self._state.value, self._failure_count,
            )

    def allow_request(self) -> bool:
        """Return True if a call should be allowed through."""
        s = self.state
        if s == CircuitState.CLOSED:
            return True
        if s == CircuitState.HALF_OPEN:
            return True  # Allow exactly one probe call
        return False  # OPEN — fail fast


class CircuitBreakerRegistry:
    """Registry of per-tool :class:`CircuitBreaker` instances.

    Usage::

        registry = CircuitBreakerRegistry()
        cb = registry.get("read_file")
        if not cb.allow_request():
            raise ToolUnavailableError("read_file")
        try:
            result = call_tool(...)
            cb.record_success()
        except Exception:
            cb.record_failure()
            raise
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout_sec: float = 60.0,
    ) -> None:
        self._breakers: dict[str, CircuitBreaker] = {}
        self._threshold = failure_threshold
        self._timeout = recovery_timeout_sec

    def get(self, tool_name: str) -> CircuitBreaker:
        """Return (or create) the circuit breaker for *tool_name*."""
        if tool_name not in self._breakers:
            self._breakers[tool_name] = CircuitBreaker(
                name=tool_name,
                failure_threshold=self._threshold,
                recovery_timeout_sec=self._timeout,
            )
        return self._breakers[tool_name]

    def get_all_states(self) -> dict[str, str]:
        """Return a snapshot of all breaker states keyed by tool name."""
        return {name: cb.state.value for name, cb in self._breakers.items()}


def _is_enabled() -> bool:
    return os.getenv("SWARM_CIRCUIT_BREAKER", "0").strip() == "1"


# Module-level singleton registry (lazily initialised)
_registry: Optional[CircuitBreakerRegistry] = None


def get_registry() -> CircuitBreakerRegistry:
    """Return the module-level :class:`CircuitBreakerRegistry` singleton."""
    global _registry
    if _registry is None:
        threshold = int(os.getenv("SWARM_CIRCUIT_BREAKER_FAILURE_THRESHOLD", "3"))
        recovery = float(os.getenv("SWARM_CIRCUIT_BREAKER_RECOVERY_SECONDS", "60"))
        _registry = CircuitBreakerRegistry(
            failure_threshold=threshold,
            recovery_timeout_sec=recovery,
        )
    return _registry
