"""Tests for K-8: Circuit Breaker for Flaky Tools."""
from __future__ import annotations

import time

from backend.App.integrations.infrastructure.mcp.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerRegistry,
    CircuitState,
    ToolUnavailableError,
)


def test_initial_state_closed():
    cb = CircuitBreaker(name="test_tool")
    assert cb.state == CircuitState.CLOSED
    assert cb.allow_request() is True


def test_opens_after_threshold_failures():
    cb = CircuitBreaker(name="flaky_tool", failure_threshold=3)
    assert cb.state == CircuitState.CLOSED
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED  # threshold not yet reached
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    assert cb.allow_request() is False


def test_does_not_open_before_threshold():
    cb = CircuitBreaker(name="tool_a", failure_threshold=5)
    for _ in range(4):
        cb.record_failure()
    assert cb.state == CircuitState.CLOSED


def test_half_open_after_recovery_timeout():
    cb = CircuitBreaker(name="tool_b", failure_threshold=2, recovery_timeout_sec=0.05)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    # Wait for recovery window
    time.sleep(0.1)
    assert cb.state == CircuitState.HALF_OPEN
    assert cb.allow_request() is True  # probe allowed


def test_half_open_does_not_transition_before_recovery():
    cb = CircuitBreaker(name="tool_c", failure_threshold=2, recovery_timeout_sec=60.0)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    # Not enough time passed
    assert cb.state == CircuitState.OPEN


def test_closes_on_success_after_half_open():
    cb = CircuitBreaker(name="tool_d", failure_threshold=2, recovery_timeout_sec=0.05)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    time.sleep(0.1)
    assert cb.state == CircuitState.HALF_OPEN
    cb.record_success()
    assert cb.state == CircuitState.CLOSED
    assert cb.allow_request() is True


def test_failure_in_half_open_reopens():
    cb = CircuitBreaker(name="tool_e", failure_threshold=2, recovery_timeout_sec=0.05)
    cb.record_failure()
    cb.record_failure()
    time.sleep(0.1)
    assert cb.state == CircuitState.HALF_OPEN
    cb.record_failure()
    # After failure in HALF_OPEN, failure count exceeded threshold again
    assert cb.state == CircuitState.OPEN


def test_success_resets_failure_count():
    cb = CircuitBreaker(name="tool_f", failure_threshold=3)
    cb.record_failure()
    cb.record_failure()
    assert cb._failure_count == 2
    cb.record_success()
    assert cb._failure_count == 0
    assert cb.state == CircuitState.CLOSED


def test_registry_per_tool_isolation():
    registry = CircuitBreakerRegistry(failure_threshold=2)
    cb_a = registry.get("tool_alpha")
    cb_b = registry.get("tool_beta")

    # Open circuit for tool_alpha only
    cb_a.record_failure()
    cb_a.record_failure()
    assert cb_a.state == CircuitState.OPEN

    # tool_beta unaffected
    assert cb_b.state == CircuitState.CLOSED
    assert cb_b.allow_request() is True


def test_registry_returns_same_instance():
    registry = CircuitBreakerRegistry()
    cb1 = registry.get("some_tool")
    cb2 = registry.get("some_tool")
    assert cb1 is cb2


def test_registry_get_all_states():
    registry = CircuitBreakerRegistry(failure_threshold=1)
    registry.get("tool_x")
    registry.get("tool_y").record_failure()
    states = registry.get_all_states()
    assert states["tool_x"] == CircuitState.CLOSED.value
    assert states["tool_y"] == CircuitState.OPEN.value


def test_tool_unavailable_error():
    err = ToolUnavailableError("broken_tool")
    assert "broken_tool" in str(err)
    assert err.tool_name == "broken_tool"
