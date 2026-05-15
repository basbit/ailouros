from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from backend.App.shared.domain.exceptions import InfrastructureError

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class ToolUnavailableError(InfrastructureError, RuntimeError):

    def __init__(self, tool_name: str) -> None:
        super().__init__(f"Circuit breaker OPEN for tool '{tool_name}': call blocked")
        self.tool_name = tool_name


@dataclass
class CircuitBreaker:
    name: str
    failure_threshold: int = 3
    recovery_timeout_sec: float = 60.0
    _state: CircuitState = field(default=CircuitState.CLOSED, init=False, repr=False)
    _failure_count: int = field(default=0, init=False, repr=False)
    _last_failure_time: float = field(default=0.0, init=False, repr=False)
    _last_success_time: float = field(default=0.0, init=False, repr=False)

    @property
    def state(self) -> CircuitState:
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
        old = self._state
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if self._state == CircuitState.HALF_OPEN:
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
        s = self.state
        if s == CircuitState.CLOSED:
            return True
        if s == CircuitState.HALF_OPEN:
            return True
        return False


class CircuitBreakerRegistry:
    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout_sec: float = 60.0,
    ) -> None:
        self._breakers: dict[str, CircuitBreaker] = {}
        self._threshold = failure_threshold
        self._timeout = recovery_timeout_sec

    def get(self, tool_name: str) -> CircuitBreaker:
        if tool_name not in self._breakers:
            self._breakers[tool_name] = CircuitBreaker(
                name=tool_name,
                failure_threshold=self._threshold,
                recovery_timeout_sec=self._timeout,
            )
        return self._breakers[tool_name]

    def get_all_states(self) -> dict[str, str]:
        return {name: cb.state.value for name, cb in self._breakers.items()}


def _is_enabled() -> bool:
    return os.getenv("SWARM_CIRCUIT_BREAKER", "0").strip() == "1"


_registry: Optional[CircuitBreakerRegistry] = None


def get_registry() -> CircuitBreakerRegistry:
    global _registry
    if _registry is None:
        threshold = int(os.getenv("SWARM_CIRCUIT_BREAKER_FAILURE_THRESHOLD", "3"))
        recovery = float(os.getenv("SWARM_CIRCUIT_BREAKER_RECOVERY_SECONDS", "60"))
        _registry = CircuitBreakerRegistry(
            failure_threshold=threshold,
            recovery_timeout_sec=recovery,
        )
    return _registry
