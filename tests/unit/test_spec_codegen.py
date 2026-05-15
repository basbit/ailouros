from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.App.spec.application.codegen import (
    CodegenError,
    CodegenOutcome,
    CodegenRequest,
    NoLLMClientConfigured,
    run_codegen,
)
from backend.App.spec.domain.spec_document import (
    SpecDocument,
    SpecFrontmatter,
    render_spec,
)
from backend.App.spec.infrastructure.sidecar_store import read_sidecar

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
    (workspace_root / ".swarm" / "specs" / "auth" / "login.md").write_text(
        render_spec(document), encoding="utf-8"
    )


def _stub_client(response: str = "# generated code\n") -> MagicMock:
    client = MagicMock()
    client.generate.return_value = response
    return client


def test_codegen_writes_file(tmp_path: Path):
    _write_spec(tmp_path)
    client = _stub_client()
    outcome = run_codegen(tmp_path, CodegenRequest(spec_id="auth/login"), llm_client=client)
    target = tmp_path / _TARGET
    assert target.is_file()
    assert outcome.spec_id == "auth/login"
    assert _TARGET in outcome.written_files


def test_codegen_writes_sidecar(tmp_path: Path):
    _write_spec(tmp_path)
    client = _stub_client()
    outcome = run_codegen(tmp_path, CodegenRequest(spec_id="auth/login"), llm_client=client)
    target = tmp_path / _TARGET
    sidecar = read_sidecar(target, tmp_path)
    assert sidecar.spec_id == "auth/login"
    assert sidecar.spec_hash != ""
    assert len(outcome.sidecar_paths) == 1


def test_codegen_outcome_fields(tmp_path: Path):
    _write_spec(tmp_path)
    client = _stub_client("# hello\n")
    request = CodegenRequest(spec_id="auth/login", model_name="my-model", seed=7)
    outcome = run_codegen(tmp_path, request, llm_client=client)
    assert isinstance(outcome, CodegenOutcome)
    assert outcome.retry_count == 0


def test_codegen_passes_model_and_seed_to_client(tmp_path: Path):
    _write_spec(tmp_path)
    client = _stub_client()
    run_codegen(
        tmp_path,
        CodegenRequest(spec_id="auth/login", model_name="test-model", seed=123),
        llm_client=client,
    )
    client.generate.assert_called_once()
    _, kwargs = client.generate.call_args
    assert kwargs["model"] == "test-model"
    assert kwargs["seed"] == 123


def test_codegen_no_targets_raises(tmp_path: Path):
    frontmatter = SpecFrontmatter(
        spec_id="auth/empty",
        version=1,
        status="draft",
        privacy="internal",
        codegen_targets=(),
    )
    document = SpecDocument(frontmatter=frontmatter, body=_SPEC_BODY, sections=())
    specs_dir = tmp_path / ".swarm" / "specs" / "auth"
    specs_dir.mkdir(parents=True, exist_ok=True)
    (specs_dir / "empty.md").write_text(render_spec(document), encoding="utf-8")
    with pytest.raises(CodegenError, match="codegen_targets"):
        run_codegen(tmp_path, CodegenRequest(spec_id="auth/empty"), llm_client=_stub_client())


def test_codegen_no_client_no_stub_env_raises(tmp_path: Path, monkeypatch):
    _write_spec(tmp_path)
    monkeypatch.delenv("SWARM_SPEC_CODEGEN_STUB", raising=False)
    with pytest.raises(NoLLMClientConfigured):
        run_codegen(tmp_path, CodegenRequest(spec_id="auth/login"))


def test_codegen_stub_env_used_when_no_client(tmp_path: Path, monkeypatch):
    _write_spec(tmp_path)
    monkeypatch.setenv("SWARM_SPEC_CODEGEN_STUB", "1")
    outcome = run_codegen(tmp_path, CodegenRequest(spec_id="auth/login"))
    assert _TARGET in outcome.written_files


def test_codegen_preserves_keep_regions(tmp_path: Path):
    _write_spec(tmp_path)
    target = tmp_path / _TARGET
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "# old header\n"
        "# @spec-keep begin manual-impl\n"
        "def my_custom_function(): return 99\n"
        "# @spec-keep end\n",
        encoding="utf-8",
    )
    client = _stub_client("# generated code\ndef login(): pass\n")
    run_codegen(tmp_path, CodegenRequest(spec_id="auth/login"), llm_client=client)
    content = target.read_text(encoding="utf-8")
    assert "my_custom_function" in content
    assert "return 99" in content


def test_codegen_llm_failure_raises(tmp_path: Path):
    _write_spec(tmp_path)
    client = MagicMock()
    client.generate.side_effect = RuntimeError("LLM unavailable")
    with pytest.raises(CodegenError, match="LLM generation failed"):
        run_codegen(tmp_path, CodegenRequest(spec_id="auth/login"), llm_client=client)


def test_codegen_multiple_targets(tmp_path: Path):
    frontmatter = SpecFrontmatter(
        spec_id="auth/multi",
        version=1,
        status="draft",
        privacy="internal",
        codegen_targets=("src/a.py", "src/b.py"),
    )
    document = SpecDocument(frontmatter=frontmatter, body=_SPEC_BODY, sections=())
    specs_dir = tmp_path / ".swarm" / "specs" / "auth"
    specs_dir.mkdir(parents=True, exist_ok=True)
    (specs_dir / "multi.md").write_text(render_spec(document), encoding="utf-8")
    outcome = run_codegen(
        tmp_path,
        CodegenRequest(spec_id="auth/multi"),
        llm_client=_stub_client(),
    )
    assert set(outcome.written_files) == {"src/a.py", "src/b.py"}
    assert len(outcome.sidecar_paths) == 2


def test_codegen_sidecar_spec_hash_matches_document(tmp_path: Path):
    _write_spec(tmp_path)
    from backend.App.spec.infrastructure.spec_repository_fs import FilesystemSpecRepository
    repo = FilesystemSpecRepository(tmp_path)
    doc = repo.load("auth/login")
    expected_hash = doc.codegen_hash()
    run_codegen(tmp_path, CodegenRequest(spec_id="auth/login"), llm_client=_stub_client())
    sidecar = read_sidecar(tmp_path / _TARGET, tmp_path)
    assert sidecar.spec_hash == expected_hash


def test_codegen_creates_parent_dirs(tmp_path: Path):
    _write_spec(tmp_path, target="deep/nested/dir/file.py")
    run_codegen(
        tmp_path,
        CodegenRequest(spec_id="auth/login"),
        llm_client=_stub_client(),
    )
    assert (tmp_path / "deep" / "nested" / "dir" / "file.py").is_file()


def test_codegen_retry_on_transient_failure(tmp_path: Path):
    _write_spec(tmp_path)
    client = MagicMock()
    client.generate.side_effect = [RuntimeError("transient"), RuntimeError("transient"), "# ok\n"]
    outcome = run_codegen(tmp_path, CodegenRequest(spec_id="auth/login"), llm_client=client)
    assert client.generate.call_count == 3
    assert outcome.retry_count == 2


def test_codegen_fails_after_max_retries(tmp_path: Path):
    _write_spec(tmp_path)
    client = MagicMock()
    client.generate.side_effect = RuntimeError("always fails")
    with pytest.raises(CodegenError):
        run_codegen(tmp_path, CodegenRequest(spec_id="auth/login"), llm_client=client)
    assert client.generate.call_count == 3
