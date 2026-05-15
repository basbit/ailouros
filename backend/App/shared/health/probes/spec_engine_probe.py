from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable, Optional

from backend.App.shared.health.probe import ProbeResult


class SpecEngineProbe:
    subsystem: str = "spec_engine"

    def __init__(
        self,
        workspace_getter: Optional[Callable[[], str]] = None,
    ) -> None:
        self._workspace_getter = workspace_getter

    def _default_workspace(self) -> str:
        return (os.getenv("SWARM_WORKSPACE_ROOT") or "").strip()

    def probe(self) -> ProbeResult:
        start = time.perf_counter()
        raw = (self._workspace_getter or self._default_workspace)()
        metadata: dict[str, str] = {"workspace_root": raw}

        if not raw:
            elapsed = (time.perf_counter() - start) * 1000.0
            return ProbeResult(
                subsystem=self.subsystem,
                status="disabled",
                latency_ms=elapsed,
                detail="SWARM_WORKSPACE_ROOT not set",
                metadata=metadata,
            )
        root = Path(raw).expanduser()
        try:
            resolved = root.resolve()
        except OSError as exc:
            elapsed = (time.perf_counter() - start) * 1000.0
            return ProbeResult(
                subsystem=self.subsystem,
                status="error",
                latency_ms=elapsed,
                detail=f"resolve failed: {exc}",
                metadata=metadata,
            )

        specs_dir = resolved / ".swarm" / "specs"
        metadata["specs_root"] = str(specs_dir)

        if not resolved.is_dir():
            elapsed = (time.perf_counter() - start) * 1000.0
            return ProbeResult(
                subsystem=self.subsystem,
                status="error",
                latency_ms=elapsed,
                detail=f"workspace root not a directory: {resolved}",
                metadata=metadata,
            )
        if not specs_dir.exists():
            elapsed = (time.perf_counter() - start) * 1000.0
            return ProbeResult(
                subsystem=self.subsystem,
                status="degraded",
                latency_ms=elapsed,
                detail=f"specs directory missing: {specs_dir}",
                metadata={**metadata, "spec_count": "0"},
            )
        if not os.access(specs_dir, os.R_OK):
            elapsed = (time.perf_counter() - start) * 1000.0
            return ProbeResult(
                subsystem=self.subsystem,
                status="error",
                latency_ms=elapsed,
                detail=f"specs directory not readable: {specs_dir}",
                metadata=metadata,
            )

        try:
            specs = list(specs_dir.glob("**/*.md"))
        except OSError as exc:
            elapsed = (time.perf_counter() - start) * 1000.0
            return ProbeResult(
                subsystem=self.subsystem,
                status="error",
                latency_ms=elapsed,
                detail=f"glob failed: {exc}",
                metadata=metadata,
            )

        elapsed = (time.perf_counter() - start) * 1000.0
        metadata["spec_count"] = str(len(specs))
        return ProbeResult(
            subsystem=self.subsystem,
            status="ok",
            latency_ms=elapsed,
            detail=f"{len(specs)} spec(s) under {specs_dir}",
            metadata=metadata,
        )


__all__ = ["SpecEngineProbe"]
