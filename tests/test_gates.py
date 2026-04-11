"""Tests for verification gates."""

from __future__ import annotations

from pathlib import Path

from backend.App.orchestration.domain.gates import (
    VERIFICATION_RULESET_VERSION,
    DevManifest,
    run_consistency_gate,
    run_diff_risk_gate,
    run_spec_gate,
    run_stub_gate,
)


def test_spec_gate_rejects_incomplete_verification_command(tmp_path: Path) -> None:
    manifest = DevManifest(
        changed_files=["app.py"],
        verification_commands=[{"command": "pytest"}],
    )
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")

    result = run_spec_gate(str(tmp_path), manifest=manifest)

    assert result.passed is False
    assert any(err.get("error") == "INVALID_VERIFICATION_COMMAND" for err in result.errors)


def test_spec_gate_rejects_missing_must_exist_file(tmp_path: Path) -> None:
    manifest = DevManifest(changed_files=["app.py"])
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")

    result = run_spec_gate(
        str(tmp_path),
        manifest=manifest,
        must_exist_files=["src/service.py"],
    )

    assert result.passed is False
    assert any(
        err.get("file") == "src/service.py" and err.get("expected") == "file exists (required by spec)"
        for err in result.errors
    )


def test_spec_gate_accepts_absolute_must_exist_path_within_workspace(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    service = src_dir / "service.py"
    service.write_text("print('ok')\n", encoding="utf-8")

    result = run_spec_gate(
        str(tmp_path),
        manifest=DevManifest(changed_files=["src/service.py"]),
        must_exist_files=[str(service.resolve())],
    )

    assert result.passed is True


def test_spec_gate_normalizes_relative_paths_with_dot_prefix(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    service = src_dir / "service.py"
    service.write_text("print('ok')\n", encoding="utf-8")

    result = run_spec_gate(
        str(tmp_path),
        manifest=DevManifest(changed_files=["./src/service.py", "src/service.py"]),
        must_exist_files=["./src/service.py"],
    )

    assert result.passed is True
    assert result.details["manifest_files_checked"] == 1
    assert result.details["must_exist_checked"] == 1


def test_gate_result_to_dict_includes_ruleset_version(tmp_path: Path) -> None:
    result = run_spec_gate(str(tmp_path), manifest=DevManifest())

    assert result.to_dict()["details"]["verification_ruleset_version"] == VERIFICATION_RULESET_VERSION


def test_diff_risk_gate_requires_test_or_justification(tmp_path: Path) -> None:
    manifest = DevManifest(deleted_files=["old.py"])

    result = run_diff_risk_gate(str(tmp_path), manifest=manifest)

    assert result.passed is False
    assert any(err.get("warning") == "FILE_DELETED" for err in result.errors)


def test_diff_risk_gate_allows_deletion_with_test_command(tmp_path: Path) -> None:
    manifest = DevManifest(
        deleted_files=["old.py"],
        verification_commands=[{"command": "pytest tests/test_old.py", "expected": "passes"}],
    )

    result = run_diff_risk_gate(str(tmp_path), manifest=manifest)

    assert result.passed is True


def test_diff_risk_gate_rejects_full_rewrite_without_justification(tmp_path: Path) -> None:
    manifest = DevManifest(changed_files=["app.py"])

    result = run_diff_risk_gate(
        str(tmp_path),
        manifest=manifest,
        workspace_writes={"write_actions": [{"path": "app.py", "mode": "overwrite_file"}]},
    )

    assert result.passed is False
    assert any(err.get("error") == "FULL_FILE_REWRITE_REQUIRES_JUSTIFICATION" for err in result.errors)


def test_diff_risk_gate_allows_full_rewrite_with_structured_justification(tmp_path: Path) -> None:
    manifest = DevManifest(
        changed_files=["app.py"],
        rewrite_justifications=[{"path": "app.py", "reason": "Legacy file replaced end-to-end to remove dead branch drift"}],
    )

    result = run_diff_risk_gate(
        str(tmp_path),
        manifest=manifest,
        workspace_writes={"write_actions": [{"path": "app.py", "mode": "overwrite_file"}]},
    )

    assert result.passed is True


def test_diff_risk_gate_allows_overwrite_for_file_created_in_same_cycle(tmp_path: Path) -> None:
    manifest = DevManifest(new_files=["app.py"], changed_files=["app.py"])

    result = run_diff_risk_gate(
        str(tmp_path),
        manifest=manifest,
        workspace_writes={"write_actions": [{"path": "app.py", "mode": "overwrite_file"}]},
    )

    assert result.passed is True


def test_diff_risk_gate_allows_create_then_overwrite_in_same_write_actions(tmp_path: Path) -> None:
    manifest = DevManifest(changed_files=["app.py"])

    result = run_diff_risk_gate(
        str(tmp_path),
        manifest=manifest,
        workspace_writes={
            "write_actions": [
                {"path": "app.py", "mode": "create_file"},
                {"path": "app.py", "mode": "overwrite_file"},
            ]
        },
    )

    assert result.passed is True


def test_stub_gate_checks_only_declared_production_paths(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    tests_dir = tmp_path / "tests"
    src_dir.mkdir()
    tests_dir.mkdir()
    (src_dir / "service.py").write_text("def load_data():\n    return []\n", encoding="utf-8")
    (tests_dir / "test_service.py").write_text("# TODO: add coverage\n", encoding="utf-8")

    result = run_stub_gate(
        str(tmp_path),
        changed_files=["src/service.py", "tests/test_service.py"],
        production_paths=["src"],
    )

    assert result.passed is False
    assert len(result.errors) == 1
    assert result.errors[0]["file"] == "src/service.py"


def test_stub_gate_respects_structured_placeholder_allow_list(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "generated.py").write_text("# TODO: generated later\n", encoding="utf-8")

    result = run_stub_gate(
        str(tmp_path),
        changed_files=["src/generated.py"],
        production_paths=["src"],
        allow_list=[
            {"path": "src/generated.py", "pattern": r"\bTODO\b", "reason": "generated scaffold allowed by task"}
        ],
    )

    assert result.passed is True


def test_consistency_gate_rejects_php_namespace_path_mismatch(tmp_path: Path) -> None:
    backend_dir = tmp_path / "backend"
    src_dir = backend_dir / "src" / "Service"
    src_dir.mkdir(parents=True)
    (backend_dir / "composer.json").write_text(
        '{"autoload":{"psr-4":{"App\\\\":"src/"}}}',
        encoding="utf-8",
    )
    (src_dir / "Example.php").write_text(
        "<?php\nnamespace App\\Wrong;\nfinal class Example {}\n",
        encoding="utf-8",
    )

    result = run_consistency_gate(str(tmp_path), manifest=DevManifest(), spec_symbols=[])

    assert result.passed is False
    assert any(err.get("error") == "AUTOLOAD_NAMESPACE_PATH_MISMATCH" for err in result.errors)
