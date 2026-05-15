from __future__ import annotations

from pathlib import Path
from typing import Optional

import pytest

from backend.App.spec.application.context_assembler import (
    ENV_DISABLED,
    CodegenContextAssembler,
)
from backend.App.spec.domain.spec_document import (
    SpecDocument,
    SpecFrontmatter,
    parse_spec,
    render_spec,
)


def _make_document(
    spec_id: str,
    *,
    contract: str = "def fn() -> None: ...",
    behaviour: str = "Returns nothing.",
    depends_on: tuple[str, ...] = (),
    targets: tuple[str, ...] = ("src/feature.py",),
) -> SpecDocument:
    body = (
        "\n## Purpose\n\nfor tests.\n\n"
        f"## Public Contract\n\n{contract}\n\n"
        f"## Behaviour\n\n{behaviour}\n\n"
        "## Examples\n\nnone\n"
    )
    frontmatter = SpecFrontmatter(
        spec_id=spec_id,
        version=1,
        status="draft",
        privacy="internal",
        codegen_targets=targets,
        depends_on=depends_on,
    )
    rendered = render_spec(SpecDocument(frontmatter=frontmatter, body=body, sections=()))
    return parse_spec(rendered)


class _StubRepoMap:
    def __init__(self, output: str = "REPO_MAP_TEXT") -> None:
        self.output = output
        self.calls: list[tuple[Path, Optional[Path], int]] = []

    def serve(
        self,
        workspace_root: Path,
        focus_path: Optional[Path],
        *,
        max_tokens: int,
    ) -> str:
        self.calls.append((workspace_root, focus_path, max_tokens))
        return self.output


class _StubSpecGraph:
    def __init__(
        self,
        ancestors_map: Optional[dict[str, tuple[str, ...]]] = None,
        documents: Optional[dict[str, SpecDocument]] = None,
        load_error: Optional[Exception] = None,
    ) -> None:
        self.ancestors_map = ancestors_map or {}
        self.documents = documents or {}
        self.load_error = load_error
        self.ancestor_calls: list[tuple[str, int]] = []
        self.load_calls: list[str] = []

    def ancestors(
        self,
        workspace_root: Path,
        spec_id: str,
        *,
        depth: int,
    ) -> tuple[str, ...]:
        self.ancestor_calls.append((spec_id, depth))
        return self.ancestors_map.get(spec_id, ())

    def load_spec(self, workspace_root: Path, spec_id: str) -> SpecDocument:
        self.load_calls.append(spec_id)
        if self.load_error is not None:
            raise self.load_error
        if spec_id not in self.documents:
            raise KeyError(f"missing stub doc {spec_id}")
        return self.documents[spec_id]


def test_assemble_happy_path(tmp_path: Path):
    base = _make_document("module/feature", depends_on=("module/ancestor",))
    ancestor = _make_document(
        "module/ancestor",
        contract="def helper(x: int) -> int: ...",
        behaviour="adds one.",
    )
    repo = _StubRepoMap("MAPTEXT")
    graph = _StubSpecGraph(
        ancestors_map={"module/feature": ("module/ancestor",)},
        documents={"module/ancestor": ancestor},
    )
    assembler = CodegenContextAssembler(repo_map=repo, spec_graph=graph)
    out = assembler.assemble(tmp_path, base)
    assert "[ancestor specs]" in out
    assert "module/ancestor" in out
    assert "def helper(x: int) -> int: ..." in out
    assert "adds one." in out
    assert "[repo map]" in out
    assert "MAPTEXT" in out


def test_ancestor_renders_only_contract_and_behaviour(tmp_path: Path):
    base = _make_document("a/b", depends_on=("a/c",))
    ancestor = _make_document(
        "a/c",
        contract="def c() -> None: ...",
        behaviour="does c.",
    )
    graph = _StubSpecGraph(
        ancestors_map={"a/b": ("a/c",)},
        documents={"a/c": ancestor},
    )
    repo = _StubRepoMap()
    out = CodegenContextAssembler(repo_map=repo, spec_graph=graph).assemble(tmp_path, base)
    assert "Public Contract" in out
    assert "Behaviour" in out
    assert "for tests." not in out
    assert "Examples" not in out or "## [" in out
    assert "## Purpose" not in out


def test_repomap_appended_after_ancestors(tmp_path: Path):
    base = _make_document("a/b", depends_on=("a/c",))
    ancestor = _make_document("a/c")
    graph = _StubSpecGraph(
        ancestors_map={"a/b": ("a/c",)},
        documents={"a/c": ancestor},
    )
    repo = _StubRepoMap("MAPTEXT_TRAILING")
    out = CodegenContextAssembler(repo_map=repo, spec_graph=graph).assemble(tmp_path, base)
    idx_anc = out.index("[ancestor specs]")
    idx_repo = out.index("[repo map]")
    assert idx_anc < idx_repo
    assert "MAPTEXT_TRAILING" in out


def test_budget_passed_through(tmp_path: Path):
    base = _make_document("a/b")
    repo = _StubRepoMap()
    graph = _StubSpecGraph()
    CodegenContextAssembler(repo_map=repo, spec_graph=graph).assemble(
        tmp_path, base, repomap_token_budget=1024
    )
    assert repo.calls
    assert repo.calls[0][2] == 1024


def test_focus_path_is_first_codegen_target(tmp_path: Path):
    base = _make_document("a/b", targets=("src/first.py", "src/second.py"))
    repo = _StubRepoMap()
    graph = _StubSpecGraph()
    CodegenContextAssembler(repo_map=repo, spec_graph=graph).assemble(tmp_path, base)
    focus = repo.calls[0][1]
    assert focus is not None
    assert focus.name == "first.py"


def test_no_targets_focus_is_none(tmp_path: Path):
    base = _make_document("a/b", targets=())
    repo = _StubRepoMap()
    graph = _StubSpecGraph()
    CodegenContextAssembler(repo_map=repo, spec_graph=graph).assemble(tmp_path, base)
    assert repo.calls[0][1] is None


def test_missing_ancestor_propagates(tmp_path: Path):
    base = _make_document("a/b", depends_on=("a/missing",))
    err = RuntimeError("ancestor not loadable")
    graph = _StubSpecGraph(
        ancestors_map={"a/b": ("a/missing",)},
        load_error=err,
    )
    repo = _StubRepoMap()
    with pytest.raises(RuntimeError, match="ancestor not loadable"):
        CodegenContextAssembler(repo_map=repo, spec_graph=graph).assemble(tmp_path, base)


def test_env_kill_switch_returns_empty(tmp_path: Path, monkeypatch):
    monkeypatch.setenv(ENV_DISABLED, "1")
    base = _make_document("a/b", depends_on=("a/c",))
    repo = _StubRepoMap()
    graph = _StubSpecGraph()
    out = CodegenContextAssembler(repo_map=repo, spec_graph=graph).assemble(tmp_path, base)
    assert out == ""
    assert repo.calls == []
    assert graph.ancestor_calls == []


def test_ancestor_depth_passed(tmp_path: Path):
    base = _make_document("a/b")
    repo = _StubRepoMap()
    graph = _StubSpecGraph()
    CodegenContextAssembler(repo_map=repo, spec_graph=graph).assemble(
        tmp_path, base, ancestor_depth=5
    )
    assert graph.ancestor_calls[0] == ("a/b", 5)


def test_no_ancestors_section_says_none(tmp_path: Path):
    base = _make_document("a/b")
    repo = _StubRepoMap()
    graph = _StubSpecGraph()
    out = CodegenContextAssembler(repo_map=repo, spec_graph=graph).assemble(tmp_path, base)
    assert "(none)" in out
