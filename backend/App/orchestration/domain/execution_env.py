
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ExecutionTarget(str, Enum):
    LOCAL = "local"
    DOCKER = "docker"
    REMOTE = "remote"


class SandboxProfile(str, Enum):
    READ_ONLY = "read_only"
    CODE_EDIT = "code_edit"
    VERIFICATION = "verification"
    INTERNET = "internet"
    FULL = "full"


@dataclass
class EnvironmentManifest:
    target: ExecutionTarget = ExecutionTarget.LOCAL
    profile: SandboxProfile = SandboxProfile.CODE_EDIT
    tools: list[str] = field(default_factory=list)
    runtimes: list[str] = field(default_factory=list)
    resource_limits: dict[str, Any] = field(default_factory=dict)
    workspace_root: str | None = None
    network_allowed: bool = False
    ttl_seconds: int = 3600


@dataclass
class ExecutionResult:
    stdout: str
    stderr: str
    exit_code: int
    elapsed_sec: float
    environment_id: str
    command: str


@dataclass
class EnvironmentSnapshot:
    environment_id: str
    manifest: EnvironmentManifest
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)
