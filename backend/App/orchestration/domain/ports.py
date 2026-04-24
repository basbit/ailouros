
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from backend.App.tasks.domain.ports import TaskId, TaskStatus, TaskStorePort
from backend.App.orchestration.domain.trace import TraceEvent, TraceSession
from backend.App.orchestration.domain.session import AgentSession, SessionCheckpoint
from backend.App.orchestration.domain.execution_env import (
    EnvironmentManifest, EnvironmentSnapshot, ExecutionResult,
)
from backend.App.orchestration.domain.credentials import (
    Credential, CredentialAuditEntry, CredentialRef,
)
from backend.App.orchestration.domain.delegation import (
    DelegationBranch, DelegationRequest, DelegationResult,
)

__all__ = [
    "TaskId",
    "TaskStatus",
    "TaskStorePort",
    "TraceEvent",
    "TraceSession",
    "AgentSession",
    "SessionCheckpoint",
    "EnvironmentManifest",
    "EnvironmentSnapshot",
    "ExecutionResult",
    "Credential",
    "CredentialAuditEntry",
    "CredentialRef",
    "DelegationBranch",
    "DelegationRequest",
    "DelegationResult",
]


class AgentRole(str, Enum):
    PM = "pm"
    BA = "ba"
    ARCH = "arch"
    DEV = "dev"
    DEVOPS = "devops"
    QA = "qa"
    REVIEWER = "reviewer"
    HUMAN = "human"
    CUSTOM = "custom"
    DOCUMENTATION = "documentation"


class RoleRegistry:

    def __init__(self, *, custom_roles: list[str] | None = None) -> None:
        self._roles: dict[str, dict[str, Any]] = {}
        for member in AgentRole:
            self._roles[member.value] = {"builtin": True}
        for role_id in custom_roles or []:
            rid = role_id.strip().lower()
            if rid and rid not in self._roles:
                self._roles[rid] = {"builtin": False}

    def register(self, role_id: str, *, meta: dict[str, Any] | None = None) -> None:
        rid = role_id.strip().lower()
        if not rid:
            raise ValueError("role_id must be non-empty")
        self._roles[rid] = {"builtin": False, **(meta or {})}

    def is_valid(self, role_id: str) -> bool:
        return role_id.strip().lower() in self._roles

    def all_roles(self) -> list[str]:
        return sorted(self._roles.keys())

    def builtin_roles(self) -> list[str]:
        return sorted(k for k, v in self._roles.items() if v.get("builtin"))

    def custom_roles(self) -> list[str]:
        return sorted(k for k, v in self._roles.items() if not v.get("builtin"))


_role_registry: RoleRegistry | None = None


def get_role_registry() -> RoleRegistry:
    global _role_registry
    if _role_registry is None:
        _role_registry = RoleRegistry()
    return _role_registry


@dataclass
class AgentOutput:
    role: str
    content: str
    artifacts: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ToolSchema:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class ShellResult:
    returncode: int
    stdout: str
    stderr: str


class ShellApprovalPolicyPort(ABC):

    @abstractmethod
    def is_allowed(self, command: str, allowlist: list[str]) -> bool:
        ...

    @abstractmethod
    def max_timeout_sec(self) -> int:
        ...


class _DefaultShellApprovalPolicy(ShellApprovalPolicyPort):

    def is_allowed(self, command: str, allowlist: list[str]) -> bool:
        if not allowlist:
            return False
        stripped = command.strip()
        return any(stripped == p or stripped.startswith(p) for p in allowlist)

    def max_timeout_sec(self) -> int:
        return 300


ShellApprovalPolicy = _DefaultShellApprovalPolicy


class ToolsRuntimePort(ABC):

    @abstractmethod
    def list_tools(self) -> list[ToolSchema]: ...

    @abstractmethod
    def call_tool(self, name: str, args: dict[str, Any]) -> ToolResult: ...


class ShellExecutorPort(ABC):

    @abstractmethod
    def execute(self, cmd: str, *, timeout_sec: int = 300) -> ShellResult: ...


class AgentRolePort(ABC):

    @abstractmethod
    def execute(self, state: dict, context: dict) -> AgentOutput:
        ...

    @property
    @abstractmethod
    def role(self) -> str:
        ...


@runtime_checkable
class LLMBackend(Protocol):

    def chat(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float = 0.2,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any]]:
        ...


@runtime_checkable
class AgentRunner(Protocol):

    role: str
    model: str
    used_model: str
    used_provider: str

    def run(self, user_input: str) -> str:
        ...

    def effective_system_prompt(self) -> str:
        ...


class TraceCollectorPort(ABC):

    @abstractmethod
    def record(self, event: TraceEvent) -> None:
        ...

    @abstractmethod
    def get_session(self, session_id: str) -> TraceSession | None:
        ...


class SessionStorePort(ABC):

    @abstractmethod
    def save_session(self, session: AgentSession) -> None: ...

    @abstractmethod
    def get_session(self, session_id: str) -> AgentSession | None: ...

    @abstractmethod
    def save_checkpoint(self, checkpoint: SessionCheckpoint) -> None: ...

    @abstractmethod
    def get_latest_checkpoint(self, session_id: str) -> SessionCheckpoint | None: ...

    @abstractmethod
    def list_sessions(self, task_id: str) -> list[AgentSession]: ...


class ExecutionEnvironmentPort(ABC):

    @abstractmethod
    def execute(
        self,
        command: str,
        manifest: EnvironmentManifest,
        *,
        timeout_sec: int = 300,
    ) -> ExecutionResult: ...

    @abstractmethod
    def snapshot(self, manifest: EnvironmentManifest) -> EnvironmentSnapshot: ...


class VaultPort(ABC):

    @abstractmethod
    def get(self, credential_id: str, accessed_by: str) -> Credential | None: ...

    @abstractmethod
    def store(self, credential: Credential) -> None: ...

    @abstractmethod
    def revoke(self, credential_id: str, revoked_by: str) -> None: ...

    @abstractmethod
    def audit_log(self, credential_id: str) -> list[CredentialAuditEntry]: ...


class AgentDelegationPort(ABC):

    @abstractmethod
    def delegate(self, request: DelegationRequest) -> DelegationBranch: ...

    @abstractmethod
    def join(self, branch_id: str, *, timeout_sec: int = 300) -> DelegationResult: ...

    @abstractmethod
    def cancel(self, branch_id: str) -> None: ...
