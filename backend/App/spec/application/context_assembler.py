from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from backend.App.spec.domain.ports import (
    BlameLine,
    CommitEntry,
    GitHistoryPort,
    RepoMapPort,
    SpecGraphPort,
)
from backend.App.spec.domain.spec_document import SpecDocument

ENV_DISABLED = "SWARM_CODEGEN_CONTEXT_DISABLED"
ENV_GIT_DISABLED = "SWARM_CODEGEN_GIT_CONTEXT_DISABLED"
_DEFAULT_REPOMAP_BUDGET = 4096
_DEFAULT_ANCESTOR_DEPTH = 2
_DEFAULT_GIT_COMMIT_LIMIT = 5
_DEFAULT_GIT_BLAME_LINES = 40


def context_disabled() -> bool:
    return os.environ.get(ENV_DISABLED, "").strip() == "1"


def git_context_disabled() -> bool:
    return os.environ.get(ENV_GIT_DISABLED, "").strip() == "1"


def _render_commit_entry(entry: CommitEntry) -> str:
    return f"  {entry.sha[:8]}  {entry.date_iso[:10]}  {entry.author}  {entry.subject}"


def _render_blame_line(bl: BlameLine) -> str:
    return f"  {bl.line_no:>5}  {bl.sha[:8]}  {bl.author[:20]:<20}  {bl.line_text}"


def _render_git_history(
    commits: tuple[CommitEntry, ...],
    blame: tuple[BlameLine, ...],
    relative_path: str | Path,
) -> str:
    parts = [f"### {relative_path}"]
    parts.append("")
    parts.append("#### recent commits")
    if commits:
        parts.extend(_render_commit_entry(c) for c in commits)
    else:
        parts.append("  (no commits)")
    parts.append("")
    parts.append("#### blame (first lines)")
    if blame:
        parts.extend(_render_blame_line(bl) for bl in blame)
    else:
        parts.append("  (empty)")
    return "\n".join(parts)


def _render_ancestor(spec_id: str, document: SpecDocument) -> str:
    contract = document.section("Public Contract").strip()
    behaviour = document.section("Behaviour").strip()
    parts = [f"### Ancestor spec: {spec_id}"]
    parts.append("")
    parts.append("#### Public Contract")
    parts.append(contract if contract else "(empty)")
    parts.append("")
    parts.append("#### Behaviour")
    parts.append(behaviour if behaviour else "(empty)")
    return "\n".join(parts)


@dataclass(frozen=True)
class CodegenContextAssembler:
    repo_map: RepoMapPort
    spec_graph: SpecGraphPort
    git_history_port: Optional[GitHistoryPort] = field(default=None)

    def assemble(
        self,
        workspace_root: str | Path,
        spec_document: SpecDocument,
        *,
        repomap_token_budget: int = _DEFAULT_REPOMAP_BUDGET,
        ancestor_depth: int = _DEFAULT_ANCESTOR_DEPTH,
        git_commit_limit: int = _DEFAULT_GIT_COMMIT_LIMIT,
        git_blame_lines: int = _DEFAULT_GIT_BLAME_LINES,
    ) -> str:
        if context_disabled():
            return ""

        ws_path = Path(workspace_root).expanduser().resolve()
        spec_id = spec_document.frontmatter.spec_id

        ancestor_ids = self.spec_graph.ancestors(
            ws_path, spec_id, depth=ancestor_depth
        )

        ancestor_sections: list[str] = []
        for ancestor_id in ancestor_ids:
            document = self.spec_graph.load_spec(ws_path, ancestor_id)
            ancestor_sections.append(_render_ancestor(ancestor_id, document))

        targets = spec_document.frontmatter.codegen_targets
        focus_path: Optional[Path] = None
        if targets:
            focus_path = ws_path / targets[0]

        repo_map_text = self.repo_map.serve(
            ws_path, focus_path, max_tokens=repomap_token_budget
        )

        blocks: list[str] = []
        if ancestor_sections:
            blocks.append("## [ancestor specs]")
            blocks.append("\n\n".join(ancestor_sections))
        else:
            blocks.append("## [ancestor specs]\n(none)")

        blocks.append("## [repo map]")
        blocks.append(repo_map_text.rstrip("\n"))

        if (
            self.git_history_port is not None
            and targets
            and not git_context_disabled()
        ):
            from backend.App.spec.infrastructure.git_history_adapter import (
                GitFileUnknownError,
            )

            git_sections: list[str] = []
            for target in targets:
                try:
                    commits = self.git_history_port.recent_commits(
                        ws_path, target, limit=git_commit_limit
                    )
                    blame = self.git_history_port.blame_range(
                        ws_path,
                        target,
                        start_line=1,
                        end_line=max(1, git_blame_lines),
                    )
                    git_sections.append(_render_git_history(commits, blame, target))
                except GitFileUnknownError:
                    git_sections.append(
                        f"### {target}\n\n  (git_unknown_file: not tracked by git)"
                    )

            if git_sections:
                blocks.append("## [git history]")
                blocks.append("\n\n".join(git_sections))

        return "\n\n".join(blocks) + "\n"


__all__ = [
    "ENV_DISABLED",
    "ENV_GIT_DISABLED",
    "CodegenContextAssembler",
    "context_disabled",
    "git_context_disabled",
]
