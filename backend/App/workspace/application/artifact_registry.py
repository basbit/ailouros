from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ArtifactEntry:
    agent: str
    path: str
    purpose: str
    step_index: int


class WorkspaceArtifactRegistry:

    def __init__(self, workspace_root: str = "") -> None:
        self._entries: list[ArtifactEntry] = []
        self._step_index: int = 0
        self._workspace_root = workspace_root

    def register(self, agent: str, path: str, purpose: str = "") -> None:
        entry = ArtifactEntry(
            agent=agent,
            path=path,
            purpose=purpose or path,
            step_index=self._step_index,
        )
        self._entries.append(entry)
        logger.debug("ArtifactRegistry: registered %s/%s — %s", agent, path, purpose)

    def register_many(self, agent: str, paths: list[str], purpose: str = "") -> None:
        for path in paths:
            self.register(agent, path, purpose)

    def advance_step(self) -> None:
        self._step_index += 1

    def query(self, path_prefix: str = "") -> list[ArtifactEntry]:
        if not path_prefix:
            return list(self._entries)
        return [e for e in self._entries if e.path.startswith(path_prefix)]

    def query_by_agent(self, agent: str) -> list[ArtifactEntry]:
        return [e for e in self._entries if e.agent == agent]

    def get_diff_since(self, agent: str) -> str:
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


_REGISTRY_KEY = "_artifact_registry"


def get_artifact_registry(state: Any) -> WorkspaceArtifactRegistry:
    reg = state.get(_REGISTRY_KEY)
    if not isinstance(reg, WorkspaceArtifactRegistry):
        workspace_root = str(state.get("workspace_root") or "")
        reg = WorkspaceArtifactRegistry(workspace_root=workspace_root)
        state[_REGISTRY_KEY] = reg
    return reg


def register_step_artifacts(state: Any, agent: str, paths: list[str], purpose: str = "") -> None:
    registry = get_artifact_registry(state)
    registry.register_many(agent, paths, purpose=purpose)
    registry.advance_step()
