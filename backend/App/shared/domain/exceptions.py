
from __future__ import annotations

__all__ = [
    "SwarmError",
    "DomainError",
    "InfrastructureError",
    "ConcurrentUpdateError",
    "PrivacyTierViolation",
    "OperationCancelled",
]


class SwarmError(Exception):
    pass


class DomainError(SwarmError):
    pass


class InfrastructureError(SwarmError):
    pass


class ConcurrentUpdateError(InfrastructureError):
    pass


class OperationCancelled(SwarmError):
    def __init__(self, source: str, detail: str = "") -> None:
        message = f"operation cancelled by {source}"
        if detail:
            message = f"{message}: {detail}"
        super().__init__(message)
        self.source = source
        self.detail = detail


class PrivacyTierViolation(DomainError):
    def __init__(self, *, tier: str, provider: str, remediation: str) -> None:
        super().__init__(
            f"privacy tier '{tier}' violated by provider '{provider}': {remediation}"
        )
        self.tier = tier
        self.provider = provider
        self.remediation = remediation
