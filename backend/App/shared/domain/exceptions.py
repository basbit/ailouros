"""Root exception hierarchy used across domains.

Everything inherits from :class:`SwarmError`. Domain-layer failures extend
:class:`DomainError`; infrastructure-layer failures extend
:class:`InfrastructureError`. This lets callers distinguish "business-rule
violation" from "transport / storage / 3rd-party-API blew up".

Per-domain exceptions (``PipelineCancelled``, ``HumanGateTimeout``,
``ContractViolation``, ``InvalidTaskTransitionError``, ``ToolUnavailableError``,
…) continue to live in their respective domain packages — they just inherit
from the canonical bases declared here.
"""

from __future__ import annotations

__all__ = [
    "SwarmError",
    "DomainError",
    "InfrastructureError",
    "ConcurrentUpdateError",
]


class SwarmError(Exception):
    """Base class for every exception raised by the swarm codebase."""


class DomainError(SwarmError):
    """Business-rule / invariant-violation errors raised from domain layers.

    Examples: invalid task state transitions, malformed agent contracts,
    human-approval gates that timed out.
    """


class InfrastructureError(SwarmError):
    """Errors originating from the infrastructure layer.

    Examples: concurrent-update conflicts in storage, circuit breaker
    tripping for an external tool, transport / network failures.
    """


class ConcurrentUpdateError(InfrastructureError):
    """Raised when an optimistic-lock retry loop exhausts its budget.

    Moved from a bare ``Exception`` subclass under ``SwarmError`` into the
    infrastructure branch — that's where task stores, Redis WATCH retries,
    and similar concurrency guards live.
    """
