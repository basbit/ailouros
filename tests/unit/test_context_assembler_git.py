from __future__ import annotations

from pathlib import Path
from typing import Optional

import pytest

from backend.App.spec.application.context_assembler import (
    ENV_GIT_DISABLED,
    CodegenContextAssembler,
)
from backend.App.spec.domain.ports import BlameLine, CommitEntry
from backend.App.spec.domain.spec_document import (
    SpecDocument,
    SpecFrontmatter,
    parse_spec,
    render_spec,
)
from backend.App.spec.infrastructure.git_history_adapter import GitFileUnknownError


def _make_document(
    spec_id: str,
    *,
    targets: tuple[str, ...] = ("src/feature.py",),
) -> SpecDocument:
    body = (
        "\n## Purpose\n\nfor tests.\n\n"
        "## Public Contract\n\ndef fn() -> None: ...\n\n"
        "## Behaviour\n\nReturns nothing.\n\n"
        "## Examples\n\nnone\n"
    )
    frontmatter = SpecFrontmatter(
        spec_id=spec_id,
        version=1,
        status="draft",
        privacy="internal",
        codegen_targets=targets,
        depends_on=(),
    )
    rendered = render_spec(SpecDocument(frontmatter=frontmatter, body=body, sections=()))
    return parse_spec(rendered)


class _StubRepoMap:
    def serve(self, workspace_root: Path, focus_path: Optional[Path], *, max_tokens: int) -> str:
        return "REPO_MAP"


class _StubSpecGraph:
    def ancestors(self, workspace_root: Path, spec_id: str, *, depth: int) -> tuple[str, ...]:
        return ()

    def load_spec(self, workspace_root: Path, spec_id: str) -> SpecDocument:
        raise KeyError(spec_id)


_CANNED_COMMITS = (
    CommitEntry(sha="a" * 40, author="Alice", date_iso="2024-01-10T00:00:00Z", subject="fix bug"),
    CommitEntry(sha="b" * 40, author="Bob", date_iso="2024-01-09T00:00:00Z", subject="add feature"),
)
_CANNED_BLAME = (
    BlameLine(sha="a" * 40, author="Alice", date_iso="2024-01-10T00:00:00Z", line_no=1, line_text="x = 1"),
    BlameLine(sha="b" * 40, author="Bob", date_iso="2024-01-09T00:00:00Z", line_no=2, line_text="y = 2"),
)


class _StubGitPort:
    def __init__(
        self,
        commits: tuple[CommitEntry, ...] = _CANNED_COMMITS,
        blame: tuple[BlameLine, ...] = _CANNED_BLAME,
        *,
        unknown_files: tuple[str, ...] = (),
    ) -> None:
        self.commits = commits
        self.blame = blame
        self.unknown_files = unknown_files
        self.recent_calls: list[tuple[str, int]] = []
        self.blame_calls: list[tuple[str, int, int]] = []

    def recent_commits(
        self,
        workspace_root: str | Path,
        relative_path: str | Path,
        *,
        limit: int = 10,
    ) -> tuple[CommitEntry, ...]:
        rel = str(relative_path)
        self.recent_calls.append((rel, limit))
        if rel in self.unknown_files:
            raise GitFileUnknownError(f"not tracked: {rel}")
        return self.commits

    def blame_range(
        self,
        workspace_root: str | Path,
        relative_path: str | Path,
        *,
        start_line: int,
        end_line: int,
    ) -> tuple[BlameLine, ...]:
        rel = str(relative_path)
        self.blame_calls.append((rel, start_line, end_line))
        if rel in self.unknown_files:
            raise GitFileUnknownError(f"not tracked: {rel}")
        return self.blame


def test_git_history_block_present(tmp_path: Path):
    doc = _make_document("a/b", targets=("src/feature.py",))
    git_port = _StubGitPort()
    assembler = CodegenContextAssembler(
        repo_map=_StubRepoMap(), spec_graph=_StubSpecGraph(), git_history_port=git_port
    )
    out = assembler.assemble(tmp_path, doc)

    assert "## [git history]" in out


def test_git_history_block_contains_commits(tmp_path: Path):
    doc = _make_document("a/b")
    git_port = _StubGitPort()
    assembler = CodegenContextAssembler(
        repo_map=_StubRepoMap(), spec_graph=_StubSpecGraph(), git_history_port=git_port
    )
    out = assembler.assemble(tmp_path, doc)

    assert "fix bug" in out
    assert "add feature" in out
    assert "Alice" in out


def test_git_history_block_contains_blame(tmp_path: Path):
    doc = _make_document("a/b")
    git_port = _StubGitPort()
    assembler = CodegenContextAssembler(
        repo_map=_StubRepoMap(), spec_graph=_StubSpecGraph(), git_history_port=git_port
    )
    out = assembler.assemble(tmp_path, doc)

    assert "x = 1" in out
    assert "y = 2" in out


def test_no_git_port_omits_section(tmp_path: Path):
    doc = _make_document("a/b")
    assembler = CodegenContextAssembler(
        repo_map=_StubRepoMap(), spec_graph=_StubSpecGraph()
    )
    out = assembler.assemble(tmp_path, doc)

    assert "## [git history]" not in out


def test_git_context_env_kill_switch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(ENV_GIT_DISABLED, "1")
    doc = _make_document("a/b")
    git_port = _StubGitPort()
    assembler = CodegenContextAssembler(
        repo_map=_StubRepoMap(), spec_graph=_StubSpecGraph(), git_history_port=git_port
    )
    out = assembler.assemble(tmp_path, doc)

    assert "## [git history]" not in out
    assert git_port.recent_calls == []


def test_untracked_file_produces_unknown_finding(tmp_path: Path):
    doc = _make_document("a/b", targets=("untracked.py",))
    git_port = _StubGitPort(unknown_files=("untracked.py",))
    assembler = CodegenContextAssembler(
        repo_map=_StubRepoMap(), spec_graph=_StubSpecGraph(), git_history_port=git_port
    )
    out = assembler.assemble(tmp_path, doc)

    assert "git_unknown_file" in out
    assert "## [git history]" in out


def test_no_targets_omits_git_section(tmp_path: Path):
    doc = _make_document("a/b", targets=())
    git_port = _StubGitPort()
    assembler = CodegenContextAssembler(
        repo_map=_StubRepoMap(), spec_graph=_StubSpecGraph(), git_history_port=git_port
    )
    out = assembler.assemble(tmp_path, doc)

    assert "## [git history]" not in out
    assert git_port.recent_calls == []


def test_git_section_appears_after_repo_map(tmp_path: Path):
    doc = _make_document("a/b")
    git_port = _StubGitPort()
    assembler = CodegenContextAssembler(
        repo_map=_StubRepoMap(), spec_graph=_StubSpecGraph(), git_history_port=git_port
    )
    out = assembler.assemble(tmp_path, doc)

    assert out.index("## [repo map]") < out.index("## [git history]")


def test_blame_called_with_start_line_1(tmp_path: Path):
    doc = _make_document("a/b", targets=("src/feature.py",))
    git_port = _StubGitPort()
    assembler = CodegenContextAssembler(
        repo_map=_StubRepoMap(), spec_graph=_StubSpecGraph(), git_history_port=git_port
    )
    assembler.assemble(tmp_path, doc)

    assert git_port.blame_calls
    assert git_port.blame_calls[0][1] == 1


def test_git_commit_limit_forwarded(tmp_path: Path):
    doc = _make_document("a/b", targets=("src/feature.py",))
    git_port = _StubGitPort()
    assembler = CodegenContextAssembler(
        repo_map=_StubRepoMap(), spec_graph=_StubSpecGraph(), git_history_port=git_port
    )
    assembler.assemble(tmp_path, doc, git_commit_limit=3)

    assert git_port.recent_calls[0][1] == 3


def test_multiple_targets_each_get_git_section(tmp_path: Path):
    doc = _make_document("a/b", targets=("src/a.py", "src/b.py"))
    git_port = _StubGitPort()
    assembler = CodegenContextAssembler(
        repo_map=_StubRepoMap(), spec_graph=_StubSpecGraph(), git_history_port=git_port
    )
    out = assembler.assemble(tmp_path, doc)

    assert "src/a.py" in out
    assert "src/b.py" in out
    assert len(git_port.recent_calls) == 2


def test_partially_untracked_targets(tmp_path: Path):
    doc = _make_document("a/b", targets=("tracked.py", "new.py"))
    git_port = _StubGitPort(unknown_files=("new.py",))
    assembler = CodegenContextAssembler(
        repo_map=_StubRepoMap(), spec_graph=_StubSpecGraph(), git_history_port=git_port
    )
    out = assembler.assemble(tmp_path, doc)

    assert "fix bug" in out
    assert "git_unknown_file" in out
