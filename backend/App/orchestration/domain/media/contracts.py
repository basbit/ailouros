from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class MediaBudget:
    max_cost_usd: float
    max_attempts: int
    license_policy: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MediaRequest:
    kind: str
    prompt: str
    target_path: str
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    voice: str | None = None
    extra: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MediaArtifact:
    relative_path: str
    kind: str
    bytes_size: int
    provider: str
    license: str
    cost_usd: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MediaProviderPort(Protocol):
    name: str

    def supports(self, kind: str) -> bool:
        ...

    def estimate_cost(self, request: MediaRequest) -> float:
        ...

    def generate(self, request: MediaRequest) -> MediaArtifact:
        ...
