from __future__ import annotations

from pathlib import Path
from typing import Optional

from backend.App.spec.application.codegen import CodegenRequest, run_codegen
from backend.App.spec.application.context_assembler import ENV_DISABLED
from backend.App.spec.domain.spec_document import (
    SpecDocument,
    SpecFrontmatter,
    render_spec,
)


class _CapturingLLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, prompt: str, *, model: str, seed: int) -> str:
        self.prompts.append(prompt)
        return "# generated\n"


class _StubRepoMap:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[tuple[Path, Optional[Path], int]] = []

    def serve(self, workspace_root, focus_path, *, max_tokens):
        self.calls.append((workspace_root, focus_path, max_tokens))
        return self.text


class _StubSpecGraph:
    def __init__(self, ancestors_map, documents):
        self.ancestors_map = ancestors_map
        self.documents = documents

    def ancestors(self, workspace_root, spec_id, *, depth):
        return self.ancestors_map.get(spec_id, ())

    def load_spec(self, workspace_root, spec_id):
        return self.documents[spec_id]


def _write_spec(
    workspace_root: Path,
    spec_id: str,
    *,
    depends_on: tuple[str, ...] = (),
    targets: tuple[str, ...] = ("src/feature.py",),
) -> SpecDocument:
    body = (
        "\n## Purpose\n\np.\n\n"
        "## Public Contract\n\ndef feature() -> None: ...\n\n"
        "## Behaviour\n\nbehaves.\n\n"
        "## Examples\n\nexample\n"
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
    spec_dir = workspace_root / ".swarm" / "specs"
    parts = spec_id.split("/")
    if len(parts) > 1:
        (spec_dir / Path(*parts[:-1])).mkdir(parents=True, exist_ok=True)
    else:
        spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / (spec_id + ".md")).write_text(render_spec(document), encoding="utf-8")
    return document


def _ancestor_doc() -> SpecDocument:
    body = (
        "\n## Purpose\n\nanc-purpose\n\n"
        "## Public Contract\n\nANCESTOR_CONTRACT\n\n"
        "## Behaviour\n\nANCESTOR_BEHAVIOUR\n\n"
    )
    fm = SpecFrontmatter(
        spec_id="lib/ancestor",
        version=1,
        status="draft",
        privacy="internal",
        codegen_targets=("src/anc.py",),
    )
    from backend.App.spec.domain.spec_document import parse_spec
    return parse_spec(render_spec(SpecDocument(frontmatter=fm, body=body, sections=())))


def test_run_codegen_prepends_assembled_context(tmp_path: Path):
    _write_spec(tmp_path, "app/feature", depends_on=("lib/ancestor",))
    repo = _StubRepoMap("REPO_MAP_BODY")
    graph = _StubSpecGraph(
        ancestors_map={"app/feature": ("lib/ancestor",)},
        documents={"lib/ancestor": _ancestor_doc()},
    )
    llm = _CapturingLLM()

    run_codegen(
        tmp_path,
        CodegenRequest(spec_id="app/feature"),
        llm_client=llm,
        repo_map_port=repo,
        spec_graph_port=graph,
    )
    assert len(llm.prompts) == 1
    prompt = llm.prompts[0]
    assert "[ancestor specs]" in prompt
    assert "ANCESTOR_CONTRACT" in prompt
    assert "ANCESTOR_BEHAVIOUR" in prompt
    assert "[repo map]" in prompt
    assert "REPO_MAP_BODY" in prompt
    idx_anc = prompt.index("[ancestor specs]")
    idx_repo = prompt.index("[repo map]")
    idx_contract = prompt.index("# Codegen target:")
    assert idx_anc < idx_repo < idx_contract


def test_kill_switch_skips_context(tmp_path: Path, monkeypatch):
    monkeypatch.setenv(ENV_DISABLED, "1")
    _write_spec(tmp_path, "app/feature", depends_on=("lib/ancestor",))
    repo = _StubRepoMap("REPO_MAP_BODY")
    graph = _StubSpecGraph(
        ancestors_map={"app/feature": ("lib/ancestor",)},
        documents={"lib/ancestor": _ancestor_doc()},
    )
    llm = _CapturingLLM()
    run_codegen(
        tmp_path,
        CodegenRequest(spec_id="app/feature"),
        llm_client=llm,
        repo_map_port=repo,
        spec_graph_port=graph,
    )
    prompt = llm.prompts[0]
    assert "[ancestor specs]" not in prompt
    assert "[repo map]" not in prompt
    assert repo.calls == []


def test_run_codegen_focus_path_is_first_target(tmp_path: Path):
    _write_spec(
        tmp_path,
        "app/multi",
        targets=("src/first_target.py", "src/second.py"),
    )
    repo = _StubRepoMap("MAP")
    graph = _StubSpecGraph({}, {})
    llm = _CapturingLLM()
    run_codegen(
        tmp_path,
        CodegenRequest(spec_id="app/multi"),
        llm_client=llm,
        repo_map_port=repo,
        spec_graph_port=graph,
    )
    assert repo.calls
    focus = repo.calls[0][1]
    assert focus is not None
    assert focus.name == "first_target.py"
