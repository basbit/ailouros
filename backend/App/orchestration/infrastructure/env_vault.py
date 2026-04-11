"""Env-backed vault adapter — R1.3.

Reads credentials from environment variables. Production systems should
replace this with a real vault (HashiCorp Vault, AWS Secrets Manager, etc.)
while keeping the VaultPort interface unchanged.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone

from backend.App.orchestration.domain.credentials import (
    Credential,
    CredentialAuditEntry,
    CredentialRef,
    CredentialScope,
)
from backend.App.orchestration.domain.ports import VaultPort

__all__ = [
    "EnvVaultAdapter",
    "Credential",
    "CredentialAuditEntry",
    "CredentialRef",
    "CredentialScope",
    "VaultPort",
]

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class EnvVaultAdapter(VaultPort):
    """Reads secrets from environment variables and keeps an in-memory audit log.

    Credential IDs map to env var names (uppercased, dashes → underscores).
    Example: credential_id="openai-api-key" → env var "OPENAI_API_KEY".
    """

    def __init__(self) -> None:
        self._overrides: dict[str, Credential] = {}   # runtime-stored credentials
        self._revoked: set[str] = set()
        self._audit: list[CredentialAuditEntry] = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def get(self, credential_id: str, accessed_by: str) -> Credential | None:
        with self._lock:
            if credential_id in self._revoked:
                logger.warning("Vault: credential '%s' is revoked", credential_id)
                return None

            # Check runtime overrides first
            if credential_id in self._overrides:
                cred = self._overrides[credential_id]
                if cred.is_expired():
                    logger.warning("Vault: credential '%s' expired", credential_id)
                    return None
                self._record_audit(credential_id, accessed_by, "read")
                return cred

            # Fall back to environment variable
            env_key = credential_id.upper().replace("-", "_").replace(".", "_")
            value = os.environ.get(env_key, "")
            if not value:
                return None

            cred = Credential(
                credential_id=credential_id,
                scope=CredentialScope.GLOBAL,
                target="env",
                secret_value=value,
                created_at=_now_iso(),
                description=f"From env var {env_key}",
            )
            self._record_audit(credential_id, accessed_by, "read")
            return cred

    def store(self, credential: Credential) -> None:
        with self._lock:
            self._overrides[credential.credential_id] = credential
            self._record_audit(credential.credential_id, "system", "store")

    def revoke(self, credential_id: str, revoked_by: str) -> None:
        with self._lock:
            self._revoked.add(credential_id)
            self._overrides.pop(credential_id, None)
            self._record_audit(credential_id, revoked_by, "revoke")

    def audit_log(self, credential_id: str) -> list[CredentialAuditEntry]:
        with self._lock:
            return [e for e in self._audit if e.credential_id == credential_id]

    # ------------------------------------------------------------------
    def _record_audit(self, credential_id: str, accessed_by: str, action: str) -> None:
        entry = CredentialAuditEntry(
            credential_id=credential_id,
            accessed_by=accessed_by,
            accessed_at=_now_iso(),
            action=action,
        )
        self._audit.append(entry)
        logger.debug("Vault audit: %s %s by %s", action, credential_id, accessed_by)
