from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.App.spec.application.codegen import CodegenRequest
from backend.App.spec.application.n_best_codegen import run_n_best_codegen
from backend.App.spec.domain.candidate_selection import NoCandidatePassedError
from backend.App.spec.domain.ports import VerificationFinding
from backend.App.spec.domain.spec_document import SpecDocument, SpecFrontmatter, render_spec

_SPEC_BODY = (
    "\n## Purpose\n\nSort items.\n\n"
    "## Public Contract\n\ndef sort_items(items: list) -> list: ...\n\n"
    "## Behaviour\n\nReturn sorted list.\n\n"
    "## Examples\n\n```python\nsort_items([3,1,2]) -> [1,2,3]\n```\n"
)

_TARGET = "src/sorting/sort_items.py"


def _write_spec(workspace_root: Path, complexity: str = "high") -> None:
    frontmatter = SpecFrontmatter(
        spec_id="sorting/sort_items",
        version=1,
        status="draft",
        privacy="internal",
        codegen_targets=(_TARGET,),
        complexity=complexity,  # type: ignore[arg-type]
    )
    document = SpecDocument(frontmatter=frontmatter, body=_SPEC_BODY, sections=())
    specs_dir = workspace_root / ".swarm" / "specs" / "sorting"
    specs_dir.mkdir(parents=True, exist_ok=True)
    (specs_dir / "sort_items.md").write_text(render_spec(document), encoding="utf-8")


def _stub_client(response: str) -> MagicMock:
    client = MagicMock()
    client.generate.return_value = response
    return client


class _SelectiveVerifier:
    kind = "selective"

    def __init__(self, bad_texts: set[str]) -> None:
        self._bad = bad_texts

    def verify(
        self, workspace_root: Path, written_files: tuple[str, ...]
    ) -> tuple[VerificationFinding, ...]:
        for rel in written_files:
            fp = workspace_root / rel
            if fp.is_file() and fp.read_text() in self._bad:
                return (
                    VerificationFinding(
                        verifier_kind="selective",
                        severity="error",
                        file_path=rel,
                        line=1,
                        message="bad candidate",
                        rule=None,
                    ),
                )
        return ()


def test_picks_passing_candidate(tmp_path: Path) -> None:
    _write_spec(tmp_path)
    good_text = "def sort_items(items):\n    return sorted(items)\n"
    bad_text = "SYNTAX ERROR !!!\n"
    responses = [bad_text, bad_text, good_text]
    call_count = 0

    def _gen(prompt: str, *, model: str, seed: int) -> str:
        nonlocal call_count
        r = responses[call_count % len(responses)]
        call_count += 1
        return r

    client = MagicMock()
    client.generate.side_effect = _gen

    verifier = _SelectiveVerifier({bad_text})
    outcome = run_n_best_codegen(
        tmp_path,
        CodegenRequest(spec_id="sorting/sort_items"),
        llm_client=client,
        verifiers=(verifier,),
        n=3,
    )
    assert _TARGET in outcome.written_files
    written = (tmp_path / _TARGET).read_text()
    assert written == good_text


def test_parallel_execution_calls_llm_n_times(tmp_path: Path) -> None:
    _write_spec(tmp_path)
    client = _stub_client("def sort_items(items): return sorted(items)\n")
    run_n_best_codegen(
        tmp_path,
        CodegenRequest(spec_id="sorting/sort_items"),
        llm_client=client,
        n=3,
    )
    assert client.generate.call_count == 3


def test_all_failed_raises_no_candidate_passed(tmp_path: Path) -> None:
    _write_spec(tmp_path)
    bad_text = "BAD\n"
    client = _stub_client(bad_text)
    verifier = _SelectiveVerifier({bad_text})
    with pytest.raises(NoCandidatePassedError):
        run_n_best_codegen(
            tmp_path,
            CodegenRequest(spec_id="sorting/sort_items"),
            llm_client=client,
            verifiers=(verifier,),
            n=3,
        )


def test_no_verifiers_picks_first_candidate(tmp_path: Path) -> None:
    _write_spec(tmp_path)
    client = _stub_client("def sort_items(items): return sorted(items)\n")
    outcome = run_n_best_codegen(
        tmp_path,
        CodegenRequest(spec_id="sorting/sort_items"),
        llm_client=client,
        verifiers=(),
        n=3,
    )
    assert outcome.spec_id == "sorting/sort_items"
    assert _TARGET in outcome.written_files


def test_written_file_contains_best_candidate_text(tmp_path: Path) -> None:
    _write_spec(tmp_path)
    good = "def sort_items(items): return list(sorted(items))\n"
    bad = "INVALID\n"
    idx = [0]

    def _gen(prompt: str, *, model: str, seed: int) -> str:
        val = bad if idx[0] < 2 else good
        idx[0] += 1
        return val

    client = MagicMock()
    client.generate.side_effect = _gen
    verifier = _SelectiveVerifier({bad})
    run_n_best_codegen(
        tmp_path,
        CodegenRequest(spec_id="sorting/sort_items"),
        llm_client=client,
        verifiers=(verifier,),
        n=3,
    )
    assert (tmp_path / _TARGET).read_text() == good


def test_sidecar_written_alongside_target(tmp_path: Path) -> None:
    _write_spec(tmp_path)
    client = _stub_client("def sort_items(items): return sorted(items)\n")
    outcome = run_n_best_codegen(
        tmp_path,
        CodegenRequest(spec_id="sorting/sort_items"),
        llm_client=client,
        n=2,
    )
    assert len(outcome.sidecar_paths) > 0


def test_n_equals_one_still_works(tmp_path: Path) -> None:
    _write_spec(tmp_path)
    client = _stub_client("def sort_items(items): return sorted(items)\n")
    outcome = run_n_best_codegen(
        tmp_path,
        CodegenRequest(spec_id="sorting/sort_items"),
        llm_client=client,
        n=1,
    )
    assert outcome.spec_id == "sorting/sort_items"
