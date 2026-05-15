from __future__ import annotations

from pathlib import Path

import pytest

from backend.App.spec.domain.spec_document import (
    SpecDocument,
    SpecFrontmatter,
    render_spec,
)
from backend.App.spec.infrastructure.spec_graph_adapter import (
    FilesystemSpecGraphAdapter,
)
from backend.App.spec.infrastructure.spec_repository_fs import SpecNotFoundError


def _write_spec(
    workspace_root: Path,
    spec_id: str,
    *,
    depends_on: tuple[str, ...] = (),
    targets: tuple[str, ...] = ("src/x.py",),
) -> None:
    body = (
        "\n## Purpose\n\nfor tests.\n\n"
        "## Public Contract\n\ndef fn() -> None: ...\n\n"
        "## Behaviour\n\nnoop.\n\n"
    )
    frontmatter = SpecFrontmatter(
        spec_id=spec_id,
        version=1,
        status="draft",
        privacy="internal",
        codegen_targets=targets,
        depends_on=depends_on,
    )
    document = SpecDocument(frontmatter=frontmatter, body=body, sections=())
    parts = spec_id.split("/")
    spec_dir = workspace_root / ".swarm" / "specs"
    spec_dir.mkdir(parents=True, exist_ok=True)
    if len(parts) > 1:
        (spec_dir / Path(*parts[:-1])).mkdir(parents=True, exist_ok=True)
    (spec_dir / (spec_id + ".md")).write_text(render_spec(document), encoding="utf-8")


def test_adapter_walks_ancestors(tmp_path: Path):
    _write_spec(tmp_path, "a/leaf", depends_on=("a/middle",))
    _write_spec(tmp_path, "a/middle", depends_on=("a/root",))
    _write_spec(tmp_path, "a/root")

    adapter = FilesystemSpecGraphAdapter()
    direct = adapter.ancestors(tmp_path, "a/leaf", depth=1)
    assert direct == ("a/middle",)
    deep = adapter.ancestors(tmp_path, "a/leaf", depth=2)
    assert set(deep) == {"a/middle", "a/root"}


def test_adapter_loads_spec_document(tmp_path: Path):
    _write_spec(tmp_path, "x/y")
    adapter = FilesystemSpecGraphAdapter()
    doc = adapter.load_spec(tmp_path, "x/y")
    assert doc.frontmatter.spec_id == "x/y"
    assert doc.section("Public Contract").strip().startswith("def fn")


def test_adapter_load_missing_propagates(tmp_path: Path):
    _write_spec(tmp_path, "x/y")
    adapter = FilesystemSpecGraphAdapter()
    with pytest.raises(SpecNotFoundError):
        adapter.load_spec(tmp_path, "x/missing")


def test_adapter_ancestors_empty_when_no_deps(tmp_path: Path):
    _write_spec(tmp_path, "solo")
    adapter = FilesystemSpecGraphAdapter()
    assert adapter.ancestors(tmp_path, "solo", depth=2) == ()
