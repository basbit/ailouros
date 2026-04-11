"""Local execution environment adapter — R1.2.

Wraps the existing sandbox_exec infrastructure to conform to
ExecutionEnvironmentPort, adding manifest-based policy enforcement.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from backend.App.orchestration.domain.execution_env import (
    EnvironmentManifest,
    EnvironmentSnapshot,
    ExecutionResult,
    ExecutionTarget,
    SandboxProfile,
)
from backend.App.orchestration.domain.ports import ExecutionEnvironmentPort

_PROFILE_READONLY_BLOCKLIST = frozenset(["rm", "mv", "cp", "write", "truncate", "tee"])


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class LocalExecutionEnvironment(ExecutionEnvironmentPort):
    """Executes commands on the local host, enforcing manifest policies."""

    def execute(
        self,
        command: str,
        manifest: EnvironmentManifest,
        *,
        timeout_sec: int = 300,
    ) -> ExecutionResult:
        import time
        from backend.App.orchestration.infrastructure.sandbox_exec import run_in_sandbox

        env_id = str(uuid.uuid4())[:8]

        # Policy: read-only profile blocks write commands
        if manifest.profile == SandboxProfile.READ_ONLY:
            cmd_base = command.strip().split()[0] if command.strip() else ""
            if cmd_base in _PROFILE_READONLY_BLOCKLIST:
                return ExecutionResult(
                    stdout="",
                    stderr=f"[Policy] Command '{cmd_base}' blocked by read_only profile",
                    exit_code=1,
                    elapsed_sec=0.0,
                    environment_id=env_id,
                    command=command,
                )

        started = time.monotonic()
        timeout = min(timeout_sec, int(manifest.resource_limits.get("timeout_sec", timeout_sec)))
        result = run_in_sandbox(command, timeout_sec=timeout)
        elapsed = time.monotonic() - started

        return ExecutionResult(
            stdout=result.get("stdout", ""),
            stderr=result.get("stderr", ""),
            exit_code=int(result.get("exit_code", 0)),
            elapsed_sec=round(elapsed, 3),
            environment_id=env_id,
            command=command,
        )

    def snapshot(self, manifest: EnvironmentManifest) -> EnvironmentSnapshot:
        return EnvironmentSnapshot(
            environment_id=str(uuid.uuid4()),
            manifest=manifest,
            created_at=_now_iso(),
            metadata={"target": ExecutionTarget.LOCAL, "host": True},
        )
