from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.App.spec.application.codegen import CodegenRequest, run_codegen
from backend.App.spec.domain.spec_document import (
    SpecComplexity,
    SpecDocument,
    SpecFrontmatter,
    SpecParseError,
    parse_spec,
    render_spec,
)

_SPEC_BODY = (
    "\n## Purpose\n\nCompute scores.\n\n"
    "## Public Contract\n\ndef compute(x: int) -> int: ...\n\n"
    "## Behaviour\n\nReturn doubled value.\n\n"
    "## Examples\n\n```python\ncompute(2) -> 4\n```\n"
)

_TARGET = "src/scoring/compute.py"


def _write_spec(workspace_root: Path, complexity: SpecComplexity = "medium") -> None:
    frontmatter = SpecFrontmatter(
        spec_id="scoring/compute",
        version=1,
        status="draft",
        privacy="internal",
        codegen_targets=(_TARGET,),
        complexity=complexity,
    )
    document = SpecDocument(frontmatter=frontmatter, body=_SPEC_BODY, sections=())
    specs_dir = workspace_root / ".swarm" / "specs" / "scoring"
    specs_dir.mkdir(parents=True, exist_ok=True)
    (specs_dir / "compute.md").write_text(render_spec(document), encoding="utf-8")


def _stub_client(response: str = "def compute(x): return x * 2\n") -> MagicMock:
    client = MagicMock()
    client.generate.return_value = response
    return client


def test_high_complexity_spec_auto_enables_n_best(tmp_path: Path) -> None:
    _write_spec(tmp_path, complexity="high")
    client = _stub_client()
    with patch(
        "backend.App.spec.application.n_best_codegen.run_n_best_codegen",
    ) as mock_n_best:
        mock_n_best.return_value = MagicMock(
            spec_id="scoring/compute",
            written_files=(_TARGET,),
            sidecar_paths=(),
            retry_count=0,
        )
        run_codegen(tmp_path, CodegenRequest(spec_id="scoring/compute"), llm_client=client)
        mock_n_best.assert_called_once()


def test_medium_complexity_spec_does_not_enable_n_best(tmp_path: Path) -> None:
    _write_spec(tmp_path, complexity="medium")
    client = _stub_client()
    with patch(
        "backend.App.spec.application.n_best_codegen.run_n_best_codegen",
    ) as mock_n_best:
        run_codegen(tmp_path, CodegenRequest(spec_id="scoring/compute"), llm_client=client)
        mock_n_best.assert_not_called()


def test_low_complexity_spec_does_not_enable_n_best(tmp_path: Path) -> None:
    _write_spec(tmp_path, complexity="low")
    client = _stub_client()
    with patch(
        "backend.App.spec.application.n_best_codegen.run_n_best_codegen",
    ) as mock_n_best:
        run_codegen(tmp_path, CodegenRequest(spec_id="scoring/compute"), llm_client=client)
        mock_n_best.assert_not_called()


def test_use_n_best_flag_forces_n_best_even_for_low_complexity(tmp_path: Path) -> None:
    _write_spec(tmp_path, complexity="low")
    client = _stub_client()
    with patch(
        "backend.App.spec.application.n_best_codegen.run_n_best_codegen",
    ) as mock_n_best:
        mock_n_best.return_value = MagicMock(
            spec_id="scoring/compute",
            written_files=(_TARGET,),
            sidecar_paths=(),
            retry_count=0,
        )
        run_codegen(
            tmp_path,
            CodegenRequest(spec_id="scoring/compute", use_n_best=True),
            llm_client=client,
        )
        mock_n_best.assert_called_once()


def test_complexity_defaults_to_medium() -> None:
    fm = SpecFrontmatter(spec_id="x")
    assert fm.complexity == "medium"


def test_parse_spec_accepts_high_complexity() -> None:
    text = (
        "---\nspec_id: test/comp\ncomplexity: high\n---\n"
        "\n## Purpose\n\nDoes stuff.\n\n"
        "## Public Contract\n\ndef foo(): pass\n\n"
        "## Behaviour\n\nDoes it.\n"
    )
    doc = parse_spec(text)
    assert doc.frontmatter.complexity == "high"


def test_parse_spec_rejects_invalid_complexity() -> None:
    text = (
        "---\nspec_id: test/comp\ncomplexity: extreme\n---\n"
        "\n## Purpose\n\nDoes stuff.\n\n"
        "## Public Contract\n\ndef foo(): pass\n\n"
        "## Behaviour\n\nDoes it.\n"
    )
    with pytest.raises(SpecParseError, match="invalid complexity"):
        parse_spec(text)


def test_parse_spec_defaults_complexity_to_medium() -> None:
    text = (
        "---\nspec_id: test/comp\n---\n"
        "\n## Purpose\n\nDoes stuff.\n\n"
        "## Public Contract\n\ndef foo(): pass\n\n"
        "## Behaviour\n\nDoes it.\n"
    )
    doc = parse_spec(text)
    assert doc.frontmatter.complexity == "medium"
