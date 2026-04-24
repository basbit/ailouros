from __future__ import annotations

import os

from backend.App.orchestration.domain.ports import ShellApprovalPolicyPort


class DefaultShellApprovalPolicy(ShellApprovalPolicyPort):

    def is_allowed(self, command: str, allowlist: list[str]) -> bool:
        if not allowlist:
            return False
        stripped = command.strip()
        return any(stripped == p or stripped.startswith(p) for p in allowlist)

    def max_timeout_sec(self) -> int:
        raw = os.getenv("SWARM_SHELL_APPROVAL_TIMEOUT_SEC", "300").strip()
        try:
            value = int(raw)
            return value if value > 0 else 300
        except ValueError:
            return 300
