"""Anti-drift tests for runtime config boundaries in orchestration domain."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text(encoding="utf-8")


def test_approval_policy_domain_has_no_env_reads() -> None:
    content = _read("backend/App/orchestration/domain/approval_policy.py")
    assert "os.getenv" not in content
    assert "os.environ" not in content


def test_contract_validator_domain_has_no_env_reads() -> None:
    content = _read("backend/App/orchestration/domain/contract_validator.py")
    assert "os.getenv" not in content
    assert "os.environ" not in content


def test_ports_domain_has_no_env_reads() -> None:
    content = _read("backend/App/orchestration/domain/ports.py")
    assert "os.getenv" not in content
    assert "os.environ" not in content
