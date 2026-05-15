from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.App.spec.application.codegen import CodegenRequest
from backend.App.spec.application.differential_codegen import (
    DifferentialCodegenError,
    DifferentialOutcome,
    run_differential_codegen,
)
from backend.App.spec.domain.spec_document import (
    SpecDocument,
    SpecFrontmatter,
    render_spec,
)

_SPEC_BODY = (
    "\n## Purpose\n\nAuthenticate users.\n\n"
    "## Public Contract\n\ndef login(user: str, pw: str) -> bool: ...\n\n"
    "## Behaviour\n\nVerify credentials.\n\n"
    "## Examples\n\n```python\nlogin('a','b') -> True\n```\n"
)

_TARGET = "src/auth/login.py"


def _write_spec(workspace_root: Path, target: str = _TARGET) -> None:
    frontmatter = SpecFrontmatter(
        spec_id="auth/login",
        version=1,
        status="draft",
        privacy="internal",
        codegen_targets=(target,),
    )
    document = SpecDocument(frontmatter=frontmatter, body=_SPEC_BODY, sections=())
    specs_dir = workspace_root / ".swarm" / "specs" / "auth"
    specs_dir.mkdir(parents=True, exist_ok=True)
    (specs_dir / "login.md").write_text(render_spec(document), encoding="utf-8")


def _stub_client(response: str = "# generated\ndef login(): pass\n") -> MagicMock:
    client = MagicMock()
    client.generate.return_value = response
    return client


def _make_request(seed: int = 0) -> CodegenRequest:
    return CodegenRequest(spec_id="auth/login", model_name="stub", seed=seed)


def test_returns_differential_outcome(tmp_path: Path) -> None:
    _write_spec(tmp_path)
    outcome = run_differential_codegen(
        _make_request(),
        primary_client=_stub_client(),
        alternative_client=_stub_client(),
        primary_model_name="model-a",
        alternative_model_name="model-b",
        workspace_root=str(tmp_path),
    )
    assert isinstance(outcome, DifferentialOutcome)


def test_report_carries_model_names(tmp_path: Path) -> None:
    _write_spec(tmp_path)
    outcome = run_differential_codegen(
        _make_request(),
        primary_client=_stub_client(),
        alternative_client=_stub_client(),
        primary_model_name="gpt-4o",
        alternative_model_name="claude-3",
        workspace_root=str(tmp_path),
    )
    assert outcome.report.model_a == "gpt-4o"
    assert outcome.report.model_b == "claude-3"


def test_primary_written_files_populated(tmp_path: Path) -> None:
    _write_spec(tmp_path)
    outcome = run_differential_codegen(
        _make_request(),
        primary_client=_stub_client(),
        alternative_client=_stub_client(),
        primary_model_name="a",
        alternative_model_name="b",
        workspace_root=str(tmp_path),
    )
    assert len(outcome.primary_outcome.written_files) > 0


def test_alternative_written_files_populated(tmp_path: Path) -> None:
    _write_spec(tmp_path)
    outcome = run_differential_codegen(
        _make_request(),
        primary_client=_stub_client(),
        alternative_client=_stub_client(),
        primary_model_name="a",
        alternative_model_name="b",
        workspace_root=str(tmp_path),
    )
    assert len(outcome.alternative_outcome.written_files) > 0


def test_primary_failure_raises_differential_error(tmp_path: Path) -> None:
    _write_spec(tmp_path)
    failing_client = MagicMock()
    failing_client.generate.side_effect = RuntimeError("primary model timed out")
    with pytest.raises(DifferentialCodegenError) as exc_info:
        run_differential_codegen(
            _make_request(),
            primary_client=failing_client,
            alternative_client=_stub_client(),
            primary_model_name="model-a",
            alternative_model_name="model-b",
            workspace_root=str(tmp_path),
        )
    assert "primary" in str(exc_info.value).lower()
    assert "model-a" in str(exc_info.value)


def test_alternative_failure_raises_differential_error(tmp_path: Path) -> None:
    _write_spec(tmp_path)
    failing_client = MagicMock()
    failing_client.generate.side_effect = RuntimeError("alternative model unavailable")
    with pytest.raises(DifferentialCodegenError) as exc_info:
        run_differential_codegen(
            _make_request(),
            primary_client=_stub_client(),
            alternative_client=failing_client,
            primary_model_name="model-a",
            alternative_model_name="model-b",
            workspace_root=str(tmp_path),
        )
    assert "alternative" in str(exc_info.value).lower()
    assert "model-b" in str(exc_info.value)


def test_both_fail_error_names_both_models(tmp_path: Path) -> None:
    _write_spec(tmp_path)
    failing = MagicMock()
    failing.generate.side_effect = RuntimeError("down")
    with pytest.raises(DifferentialCodegenError) as exc_info:
        run_differential_codegen(
            _make_request(),
            primary_client=failing,
            alternative_client=failing,
            primary_model_name="alpha",
            alternative_model_name="beta",
            workspace_root=str(tmp_path),
        )
    msg = str(exc_info.value)
    assert "alpha" in msg
    assert "beta" in msg


def test_parallel_execution_both_clients_called(tmp_path: Path) -> None:
    _write_spec(tmp_path)
    primary_client = _stub_client("def login(): return True\n")
    alternative_client = _stub_client("def login(): return True\n")
    run_differential_codegen(
        _make_request(),
        primary_client=primary_client,
        alternative_client=alternative_client,
        primary_model_name="a",
        alternative_model_name="b",
        workspace_root=str(tmp_path),
    )
    assert primary_client.generate.call_count >= 1
    assert alternative_client.generate.call_count >= 1


def test_caller_can_choose_primary_outcome(tmp_path: Path) -> None:
    _write_spec(tmp_path)
    outcome = run_differential_codegen(
        _make_request(),
        primary_client=_stub_client(),
        alternative_client=_stub_client(),
        primary_model_name="a",
        alternative_model_name="b",
        workspace_root=str(tmp_path),
    )
    chosen = outcome.primary_outcome
    assert chosen.spec_id == "auth/login"


def test_caller_can_choose_alternative_outcome(tmp_path: Path) -> None:
    _write_spec(tmp_path)
    outcome = run_differential_codegen(
        _make_request(),
        primary_client=_stub_client(),
        alternative_client=_stub_client(),
        primary_model_name="a",
        alternative_model_name="b",
        workspace_root=str(tmp_path),
    )
    chosen = outcome.alternative_outcome
    assert chosen.spec_id == "auth/login"


def test_agreement_ratio_in_report(tmp_path: Path) -> None:
    _write_spec(tmp_path)
    outcome = run_differential_codegen(
        _make_request(),
        primary_client=_stub_client(),
        alternative_client=_stub_client(),
        primary_model_name="a",
        alternative_model_name="b",
        workspace_root=str(tmp_path),
    )
    assert 0.0 <= outcome.report.agreement_ratio <= 1.0
