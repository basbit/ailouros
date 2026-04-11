"""Orchestration domain ports (interfaces).

Rules (INV-7): this module MUST NOT import fastapi, redis, httpx, openai,
anthropic, langgraph, or subprocess. Only stdlib + typing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

# Re-export TaskId, TaskStatus, and TaskStorePort so orchestration layer can
# import them here. Canonical definitions live in tasks BC.
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


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

class AgentRole(str, Enum):
    """Built-in agent role identifiers — domain value object.

    For dynamic/custom roles, use :class:`RoleRegistry` instead of extending
    this enum. The registry accepts any string role id and is the recommended
    way to check role validity at runtime.
    """
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
    """Dynamic role registry — single source of truth for valid role ids.

    Pre-populated with built-in :class:`AgentRole` members. Custom roles are
    added at startup via :meth:`register`.

    Usage::

        registry = get_role_registry()
        assert registry.is_valid("pm")        # built-in
        assert registry.is_valid("designer")  # custom (if registered)
        all_ids = registry.all_roles()
    """

    def __init__(self, *, custom_roles: list[str] | None = None) -> None:
        self._roles: dict[str, dict[str, Any]] = {}
        # Seed with built-in roles
        for member in AgentRole:
            self._roles[member.value] = {"builtin": True}
        for role_id in custom_roles or []:
            rid = role_id.strip().lower()
            if rid and rid not in self._roles:
                self._roles[rid] = {"builtin": False}

    def register(self, role_id: str, *, meta: dict[str, Any] | None = None) -> None:
        """Register a custom role at runtime."""
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
    """Return the global RoleRegistry singleton."""
    global _role_registry
    if _role_registry is None:
        _role_registry = RoleRegistry()
    return _role_registry


@dataclass
class AgentOutput:
    """Result from an agent role execution — domain value object."""
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


# ---------------------------------------------------------------------------
# Domain policies
# ---------------------------------------------------------------------------

class ShellApprovalPolicyPort(ABC):
    """Abstract domain port for shell command approval policy.

    Infrastructure provides a concrete implementation; the domain only
    depends on this interface (INV-7).
    """

    @abstractmethod
    def is_allowed(self, command: str, allowlist: list[str]) -> bool:
        """Return True if *command* is permitted by *allowlist*.

        Args:
            command: Full shell command string to evaluate.
            allowlist: List of approved command prefixes.

        Returns:
            True if the command is approved, False otherwise.
        """
        ...

    @abstractmethod
    def max_timeout_sec(self) -> int:
        """Return the maximum allowed timeout in seconds for shell commands.

        Returns:
            Hard upper bound on execution time (in seconds).
        """
        ...


class _DefaultShellApprovalPolicy(ShellApprovalPolicyPort):
    """Default domain-level concrete policy (no infrastructure imports).

    Uses a hardcoded 300-second timeout.  Infrastructure subclasses
    (e.g. ``DefaultShellApprovalPolicy`` in ``shell_policy.py``) override
    ``max_timeout_sec`` to read from the environment.
    """

    def is_allowed(self, command: str, allowlist: list[str]) -> bool:
        if not allowlist:
            return False
        stripped = command.strip()
        return any(stripped == p or stripped.startswith(p) for p in allowlist)

    def max_timeout_sec(self) -> int:
        return 300


# Backward-compatibility alias: preserves the ability to instantiate
# ShellApprovalPolicy() in existing code and tests without changes.
ShellApprovalPolicy = _DefaultShellApprovalPolicy


# ---------------------------------------------------------------------------
# Ports
# ---------------------------------------------------------------------------

class ToolsRuntimePort(ABC):
    """Abstraction over MCP tool-calling runtime."""

    @abstractmethod
    def list_tools(self) -> list[ToolSchema]: ...

    @abstractmethod
    def call_tool(self, name: str, args: dict[str, Any]) -> ToolResult: ...


class ShellExecutorPort(ABC):
    """Abstraction over controlled shell command execution."""

    @abstractmethod
    def execute(self, cmd: str, *, timeout_sec: int = 300) -> ShellResult: ...


class AgentRolePort(ABC):
    """Port for agent role execution. Infrastructure implements this for each LLM role."""

    @abstractmethod
    def execute(self, state: dict, context: dict) -> AgentOutput:
        """Execute this agent role with given pipeline state and context."""
        ...

    @property
    @abstractmethod
    def role(self) -> str:
        """The role id this adapter implements (AgentRole value or custom string)."""
        ...


# ---------------------------------------------------------------------------
# Protocol ports (for type-checking without ABC inheritance)
# ---------------------------------------------------------------------------

@runtime_checkable
class LLMBackend(Protocol):
    """Synchronous LLM chat completion.

    Returned tuple: ``(text, usage_dict)`` where ``usage_dict`` contains at
    minimum ``input_tokens``, ``output_tokens``, and ``model`` keys.
    """

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
    """A single-turn agent that takes a user prompt and returns text.

    The ``run`` method is the minimal surface needed by the pipeline nodes.
    """

    role: str
    model: str
    used_model: str
    used_provider: str

    def run(self, user_input: str) -> str:
        ...

    def effective_system_prompt(self) -> str:
        ...


# ---------------------------------------------------------------------------
# R1.4 — Session trace ports
# ---------------------------------------------------------------------------


class TraceCollectorPort(ABC):
    """Port for recording and retrieving session trace events."""

    @abstractmethod
    def record(self, event: TraceEvent) -> None:
        """Append a trace event to the session."""
        ...

    @abstractmethod
    def get_session(self, session_id: str) -> TraceSession | None:
        """Return the full trace session or None if not found."""
        ...


# ---------------------------------------------------------------------------
# R1.1 — Durable session store ports
# ---------------------------------------------------------------------------


class SessionStorePort(ABC):
    """Port for persisting and retrieving durable agent sessions."""

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


# ---------------------------------------------------------------------------
# R1.2 — Execution environment ports
# ---------------------------------------------------------------------------


class ExecutionEnvironmentPort(ABC):
    """Port for running commands in a managed execution environment."""

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


# ---------------------------------------------------------------------------
# R1.3 — Vault / credential ports
# ---------------------------------------------------------------------------


class VaultPort(ABC):
    """Port for storing and retrieving scoped credentials."""

    @abstractmethod
    def get(self, credential_id: str, accessed_by: str) -> Credential | None: ...

    @abstractmethod
    def store(self, credential: Credential) -> None: ...

    @abstractmethod
    def revoke(self, credential_id: str, revoked_by: str) -> None: ...

    @abstractmethod
    def audit_log(self, credential_id: str) -> list[CredentialAuditEntry]: ...


# ---------------------------------------------------------------------------
# R1.5 — Agent delegation ports
# ---------------------------------------------------------------------------


class AgentDelegationPort(ABC):
    """Port for spawning and joining delegated sub-agent tasks."""

    @abstractmethod
    def delegate(self, request: DelegationRequest) -> DelegationBranch: ...

    @abstractmethod
    def join(self, branch_id: str, *, timeout_sec: int = 300) -> DelegationResult: ...

    @abstractmethod
    def cancel(self, branch_id: str) -> None: ...
