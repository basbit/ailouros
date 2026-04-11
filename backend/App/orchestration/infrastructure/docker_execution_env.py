"""Docker execution environment adapter — R1.2.

Runs commands inside an isolated Docker container, applying
manifest-based policy and resource limits.

The container image is resolved from the manifest ``runtimes`` field
(first entry) or falls back to ``SWARM_DOCKER_IMAGE`` env var.

Design goals:
- Zero coupling to sandbox_exec (separate concern)
- Manifest-enforced isolation (READ_ONLY → no write-capable flags)
- Resource limits via Docker flags (--memory, --cpus)
- Graceful fallback: if Docker is not available, raises RuntimeError
  instead of silently downgrading to local execution.
"""

from __future__ import annotations

import logging
import os
import subprocess
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

logger = logging.getLogger(__name__)

_DEFAULT_IMAGE = os.getenv("SWARM_DOCKER_IMAGE", "python:3.12-slim")
_READONLY_BLOCKLIST = frozenset(["rm", "mv", "cp", "write", "truncate", "tee"])


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


class DockerExecutionEnvironment(ExecutionEnvironmentPort):
    """Executes commands inside a Docker container.

    The container is created fresh per ``execute()`` call (``--rm`` flag)
    to ensure clean isolation.  Workspace files can be mounted read-only
    or read-write depending on the manifest profile.
    """

    def __init__(self, default_image: str = _DEFAULT_IMAGE) -> None:
        self._default_image = default_image
        if not _docker_available():
            logger.warning(
                "DockerExecutionEnvironment: Docker daemon not reachable. "
                "execute() calls will raise RuntimeError."
            )

    # ------------------------------------------------------------------
    def execute(
        self,
        command: str,
        manifest: EnvironmentManifest,
        *,
        timeout_sec: int = 300,
    ) -> ExecutionResult:
        import time

        env_id = str(uuid.uuid4())[:8]

        # Policy: read-only profile blocks write commands
        if manifest.profile == SandboxProfile.READ_ONLY:
            cmd_base = command.strip().split()[0] if command.strip() else ""
            if cmd_base in _READONLY_BLOCKLIST:
                return ExecutionResult(
                    stdout="",
                    stderr=f"[Policy] Command '{cmd_base}' blocked by read_only profile",
                    exit_code=1,
                    elapsed_sec=0.0,
                    environment_id=env_id,
                    command=command,
                )

        if not _docker_available():
            raise RuntimeError(
                "DockerExecutionEnvironment: Docker daemon is not available on this host. "
                "Set ExecutionTarget.LOCAL or ensure Docker is running."
            )

        image = (manifest.runtimes and manifest.runtimes[0]) or self._default_image
        effective_timeout = min(timeout_sec, int(manifest.resource_limits.get("timeout_sec", timeout_sec)))

        docker_args = ["docker", "run", "--rm", "--network=none"]

        # Resource limits
        if mem := manifest.resource_limits.get("memory"):
            docker_args += [f"--memory={mem}"]
        if cpus := manifest.resource_limits.get("cpus"):
            docker_args += [f"--cpus={cpus}"]

        # Network
        if manifest.network_allowed:
            docker_args = [a for a in docker_args if a != "--network=none"]

        # Workspace mount
        if manifest.workspace_root:
            mount_mode = "ro" if manifest.profile == SandboxProfile.READ_ONLY else "rw"
            docker_args += ["-v", f"{manifest.workspace_root}:/workspace:{mount_mode}", "-w", "/workspace"]

        docker_args += [image, "sh", "-c", command]

        started = time.monotonic()
        try:
            proc = subprocess.run(
                docker_args,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
            )
            elapsed = time.monotonic() - started
            return ExecutionResult(
                stdout=proc.stdout,
                stderr=proc.stderr,
                exit_code=proc.returncode,
                elapsed_sec=round(elapsed, 3),
                environment_id=env_id,
                command=command,
            )
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - started
            return ExecutionResult(
                stdout="",
                stderr=f"[Docker] Command timed out after {effective_timeout}s",
                exit_code=124,
                elapsed_sec=round(elapsed, 3),
                environment_id=env_id,
                command=command,
            )
        except Exception as exc:
            elapsed = time.monotonic() - started
            logger.exception("DockerExecutionEnvironment.execute failed: %s", exc)
            return ExecutionResult(
                stdout="",
                stderr=f"[Docker] Execution error: {exc}",
                exit_code=1,
                elapsed_sec=round(elapsed, 3),
                environment_id=env_id,
                command=command,
            )

    def snapshot(self, manifest: EnvironmentManifest) -> EnvironmentSnapshot:
        image = (manifest.runtimes and manifest.runtimes[0]) or self._default_image
        return EnvironmentSnapshot(
            environment_id=str(uuid.uuid4()),
            manifest=manifest,
            created_at=_now_iso(),
            metadata={
                "target": ExecutionTarget.DOCKER,
                "image": image,
                "docker_available": _docker_available(),
            },
        )
