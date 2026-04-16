"""WorkspaceArtifactRegistry — track files produced by each agent (§12.9).

Formalizes workspace artifacts as a shared communication layer:
- Dev registers files it wrote → QA queries "what files need testing?"
- DevOps queries "what deploy artifacts exist?" → gets paths from Dev
- Human gate shows artifact registry as structured list, not raw diff text

Usage::

    registry = WorkspaceArtifactRegistry()
    registry.register("dev", "src/app.py", purpose="application entry point")
    files = registry.query("src/")
    dev_files = registry.query_by_agent("dev")
    diff = registry.get_diff_since("dev")
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ArtifactEntry:
    agent: str       # which agent registered this file
    path: str        # relative path inside workspace
    purpose: str     # human-readable intent
    step_index: int  # pipeline step number at registration time


class WorkspaceArtifactRegistry:
    """Track files produced by each pipeline agent for downstream consumption.

    All entries are stored in-memory and persisted to pipeline state via
    :meth:`to_state_dict` / :meth:`from_state_dict`.
    """

    def __init__(self, workspace_root: str = "") -> None:
        self._entries: list[ArtifactEntry] = []
        self._step_index: int = 0
        self._workspace_root = workspace_root

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, agent: str, path: str, purpose: str = "") -> None:
        """Register a file written by *agent*.

        Args:
            agent: Agent role (e.g. "dev", "devops").
            path: Relative path inside workspace.
            purpose: Brief description of why this file was created/modified.
        """
        entry = ArtifactEntry(
            agent=agent,
            path=path,
            purpose=purpose or path,
            step_index=self._step_index,
        )
        self._entries.append(entry)
        logger.debug("ArtifactRegistry: registered %s/%s — %s", agent, path, purpose)

    def register_many(self, agent: str, paths: list[str], purpose: str = "") -> None:
        """Register multiple files for *agent* at once."""
        for path in paths:
            self.register(agent, path, purpose)

    def advance_step(self) -> None:
        """Call after each pipeline step to track step boundaries."""
        self._step_index += 1

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def query(self, path_prefix: str = "") -> list[ArtifactEntry]:
        """Return entries whose path starts with *path_prefix* (or all if empty)."""
        if not path_prefix:
            return list(self._entries)
        return [e for e in self._entries if e.path.startswith(path_prefix)]

    def query_by_agent(self, agent: str) -> list[ArtifactEntry]:
        """Return all entries registered by *agent*."""
        return [e for e in self._entries if e.agent == agent]

    def get_diff_since(self, agent: str) -> str:
        """Return a ``git diff`` of all files registered by *agent*.

        Falls back to a plain file list if git is unavailable.
        """
        entries = self.query_by_agent(agent)
        if not entries:
            return ""
        paths = [e.path for e in entries]
        workspace = self._workspace_root
        if not workspace:
            return "\n".join(f"- {p}" for p in paths)
        try:
            result = subprocess.run(
                ["git", "diff", "--no-color", "HEAD", "--", *paths],
                cwd=workspace,
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode in (0, 1) and result.stdout.strip():
                return result.stdout
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
        return "\n".join(f"- {p}" for p in paths)

    def to_summary(self) -> str:
        """Return a human-readable summary of all registered artifacts."""
        if not self._entries:
            return "(no artifacts registered)"
        lines = ["## Workspace Artifact Registry\n"]
        by_agent: dict[str, list[ArtifactEntry]] = {}
        for e in self._entries:
            by_agent.setdefault(e.agent, []).append(e)
        for agent, entries in by_agent.items():
            lines.append(f"### {agent}")
            for e in entries:
                lines.append(f"  - `{e.path}` — {e.purpose}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def to_state_dict(self) -> dict[str, Any]:
        return {
            "entries": [
                {"agent": e.agent, "path": e.path, "purpose": e.purpose, "step_index": e.step_index}
                for e in self._entries
            ],
            "step_index": self._step_index,
        }

    @classmethod
    def from_state_dict(cls, data: dict[str, Any], workspace_root: str = "") -> "WorkspaceArtifactRegistry":
        reg = cls(workspace_root=workspace_root)
        reg._step_index = int(data.get("step_index") or 0)
        for raw in data.get("entries") or []:
            reg._entries.append(ArtifactEntry(
                agent=str(raw.get("agent") or ""),
                path=str(raw.get("path") or ""),
                purpose=str(raw.get("purpose") or ""),
                step_index=int(raw.get("step_index") or 0),
            ))
        return reg


# ------------------------------------------------------------------
# State integration helpers
# ------------------------------------------------------------------

_REGISTRY_KEY = "_artifact_registry"


def get_artifact_registry(state: Any) -> WorkspaceArtifactRegistry:
    """Get or create the artifact registry from pipeline *state*."""
    reg = state.get(_REGISTRY_KEY)
    if not isinstance(reg, WorkspaceArtifactRegistry):
        workspace_root = str(state.get("workspace_root") or "")
        reg = WorkspaceArtifactRegistry(workspace_root=workspace_root)
        state[_REGISTRY_KEY] = reg
    return reg


def register_step_artifacts(state: Any, agent: str, paths: list[str], purpose: str = "") -> None:
    """Register written files for *agent* in the pipeline *state* registry."""
    registry = get_artifact_registry(state)
    registry.register_many(agent, paths, purpose=purpose)
    registry.advance_step()
