from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Optional, Protocol

if TYPE_CHECKING:
    from backend.App.spec.domain.spec_document import SpecDocument


@dataclass(frozen=True)
class CommitEntry:
    sha: str
    author: str
    date_iso: str
    subject: str


@dataclass(frozen=True)
class BlameLine:
    sha: str
    author: str
    date_iso: str
    line_no: int
    line_text: str


class GitHistoryPort(Protocol):
    def recent_commits(
        self,
        workspace_root: str | Path,
        relative_path: str | Path,
        *,
        limit: int = 10,
    ) -> tuple[CommitEntry, ...]:
        ...

    def blame_range(
        self,
        workspace_root: str | Path,
        relative_path: str | Path,
        *,
        start_line: int,
        end_line: int,
    ) -> tuple[BlameLine, ...]:
        ...


class LLMClient(Protocol):
    def generate(
        self,
        prompt: str,
        *,
        model: str,
        seed: int,
    ) -> str:
        ...


@dataclass(frozen=True)
class VerificationFinding:
    verifier_kind: str
    severity: Literal["error", "warning"]
    file_path: str
    line: int | None
    message: str
    rule: str | None


class CodeVerifier(Protocol):
    kind: str

    def verify(
        self,
        workspace_root: Path,
        written_files: tuple[str, ...],
    ) -> tuple[VerificationFinding, ...]:
        ...


class RepoMapPort(Protocol):
    def serve(
        self,
        workspace_root: Path,
        focus_path: Optional[Path],
        *,
        max_tokens: int,
    ) -> str:
        ...


class SpecGraphPort(Protocol):
    def ancestors(
        self,
        workspace_root: Path,
        spec_id: str,
        *,
        depth: int,
    ) -> tuple[str, ...]:
        ...

    def load_spec(
        self,
        workspace_root: Path,
        spec_id: str,
    ) -> "SpecDocument":
        ...


__all__ = [
    "BlameLine",
    "CodeVerifier",
    "CommitEntry",
    "GitHistoryPort",
    "LLMClient",
    "RepoMapPort",
    "SpecGraphPort",
    "VerificationFinding",
]
