"""Infrastructure: concrete shell approval policy.

Provides ``DefaultShellApprovalPolicy`` — the production implementation of
``ShellApprovalPolicyPort``.  Uses ``os.getenv`` to allow the hard timeout to
be tuned at runtime without code changes.
"""
from __future__ import annotations

import os

from backend.App.orchestration.domain.ports import ShellApprovalPolicyPort


class DefaultShellApprovalPolicy(ShellApprovalPolicyPort):
    """Concrete shell approval policy backed by environment configuration.

    The timeout is read from ``SWARM_SHELL_APPROVAL_TIMEOUT_SEC`` (default 300).
    Allowlist matching uses exact prefix semantics: a pattern ``"git "`` matches
    any command that starts with ``"git "``.
    """

    def is_allowed(self, command: str, allowlist: list[str]) -> bool:
        """Return True if *command* matches any pattern in *allowlist*.

        An empty *allowlist* allows nothing.

        Args:
            command: Full shell command string to evaluate.
            allowlist: List of approved command prefixes.

        Returns:
            True if the command is approved, False otherwise.
        """
        if not allowlist:
            return False
        stripped = command.strip()
        return any(stripped == p or stripped.startswith(p) for p in allowlist)

    def max_timeout_sec(self) -> int:
        """Return the maximum allowed timeout in seconds for shell commands.

        Reads ``SWARM_SHELL_APPROVAL_TIMEOUT_SEC`` from the environment.
        Falls back to 300 seconds if the variable is absent or invalid.

        Returns:
            Hard upper bound on execution time in seconds.
        """
        raw = os.getenv("SWARM_SHELL_APPROVAL_TIMEOUT_SEC", "300").strip()
        try:
            value = int(raw)
            return value if value > 0 else 300
        except ValueError:
            return 300
