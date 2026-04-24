
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class CredentialScope(str, Enum):
    GLOBAL = "global"
    WORKSPACE = "workspace"
    PROJECT = "project"
    ROLE = "role"
    TOOL = "tool"


@dataclass
class CredentialRef:
    credential_id: str
    scope: CredentialScope
    target: str            # e.g. tool name, role id, project id
    description: str = ""


@dataclass
class CredentialAuditEntry:
    credential_id: str
    accessed_by: str       # agent role or system component
    accessed_at: str       # ISO-8601
    action: str            # "read" | "inject" | "revoke"
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class Credential:
    credential_id: str
    scope: CredentialScope
    target: str
    secret_value: str      # encrypted at rest in production vaults
    created_at: str
    expires_at: str | None = None
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.fromisoformat(self.expires_at) < datetime.now(tz=timezone.utc)
