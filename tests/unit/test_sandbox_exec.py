"""Tests for backend/App/orchestration/infrastructure/sandbox_exec.py."""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch


from backend.App.orchestration.infrastructure.sandbox_exec import (
    _safe_subprocess_env,
    docker_image,
    e2b_enabled,
    exec_backend,
    run_allowlisted_command,
)


# ---------------------------------------------------------------------------
# _safe_subprocess_env
# ---------------------------------------------------------------------------

def test_safe_subprocess_env_only_allowlisted():
    env = _safe_subprocess_env()
    # All keys must be in the allowlist
    from backend.App.orchestration.infrastructure.sandbox_exec import _SUBPROCESS_ENV_ALLOWLIST
    for key in env:
        assert key in _SUBPROCESS_ENV_ALLOWLIST


def test_safe_subprocess_env_returns_dict():
    env = _safe_subprocess_env()
    assert isinstance(env, dict)


# ---------------------------------------------------------------------------
# exec_backend
# ---------------------------------------------------------------------------

def test_exec_backend_default(monkeypatch):
    monkeypatch.delenv("SWARM_EXEC_BACKEND", raising=False)
    assert exec_backend() == "host"


def test_exec_backend_docker(monkeypatch):
    monkeypatch.setenv("SWARM_EXEC_BACKEND", "docker")
    assert exec_backend() == "docker"


def test_exec_backend_stripped(monkeypatch):
    monkeypatch.setenv("SWARM_EXEC_BACKEND", "  DOCKER  ")
    assert exec_backend() == "docker"


# ---------------------------------------------------------------------------
# docker_image
# ---------------------------------------------------------------------------

def test_docker_image_default(monkeypatch):
    monkeypatch.delenv("SWARM_DOCKER_EXEC_IMAGE", raising=False)
    assert docker_image() == "python:3.11-slim"


def test_docker_image_custom(monkeypatch):
    monkeypatch.setenv("SWARM_DOCKER_EXEC_IMAGE", "ubuntu:22.04")
    assert docker_image() == "ubuntu:22.04"


# ---------------------------------------------------------------------------
# e2b_enabled
# ---------------------------------------------------------------------------

def test_e2b_enabled_not_set(monkeypatch):
    monkeypatch.delenv("SWARM_E2B_API_KEY", raising=False)
    assert e2b_enabled() is False


def test_e2b_enabled_set(monkeypatch):
    monkeypatch.setenv("SWARM_E2B_API_KEY", "some-key-12345")
    assert e2b_enabled() is True


def test_e2b_enabled_empty_string(monkeypatch):
    monkeypatch.setenv("SWARM_E2B_API_KEY", "  ")
    assert e2b_enabled() is False


# ---------------------------------------------------------------------------
# run_allowlisted_command — host backend (default)
# ---------------------------------------------------------------------------

def test_run_allowlisted_host_success(tmp_path, monkeypatch):
    monkeypatch.delenv("SWARM_EXEC_BACKEND", raising=False)

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "hello\n"
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result):
        result = run_allowlisted_command(
            ["echo", "hello"],
            tmp_path,
            timeout_sec=30,
        )

    assert result["returncode"] == 0
    assert result["backend"] == "host"
    assert "hello" in result.get("stdout", "")


def test_run_allowlisted_host_timeout(tmp_path, monkeypatch):
    monkeypatch.delenv("SWARM_EXEC_BACKEND", raising=False)

    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 30)):
        result = run_allowlisted_command(
            ["sleep", "100"],
            tmp_path,
            timeout_sec=1,
        )

    assert result["error"] == "timeout"


def test_run_allowlisted_host_os_error(tmp_path, monkeypatch):
    monkeypatch.delenv("SWARM_EXEC_BACKEND", raising=False)

    with patch("subprocess.run", side_effect=OSError("No such file")):
        result = run_allowlisted_command(
            ["nonexistent_cmd"],
            tmp_path,
            timeout_sec=30,
        )

    assert "No such file" in result["error"]


# ---------------------------------------------------------------------------
# run_allowlisted_command — docker backend
# ---------------------------------------------------------------------------

def test_run_allowlisted_docker_success(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_EXEC_BACKEND", "docker")

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "output\n"
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result):
        result = run_allowlisted_command(
            ["python", "--version"],
            tmp_path,
            timeout_sec=30,
        )

    assert result["backend"] == "docker"
    assert result["returncode"] == 0


def test_run_allowlisted_docker_not_found(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_EXEC_BACKEND", "docker")

    with patch("subprocess.run", side_effect=FileNotFoundError("docker not found")):
        result = run_allowlisted_command(
            ["python", "--version"],
            tmp_path,
            timeout_sec=30,
        )

    assert "docker" in result["error"].lower()


def test_run_allowlisted_docker_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_EXEC_BACKEND", "docker")

    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("docker", 30)):
        result = run_allowlisted_command(
            ["python", "--version"],
            tmp_path,
            timeout_sec=1,
        )

    assert result["error"] == "timeout"


def test_run_allowlisted_container_alias(tmp_path, monkeypatch):
    """'container' is an alias for 'docker' backend."""
    monkeypatch.setenv("SWARM_EXEC_BACKEND", "container")

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = ""
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result):
        result = run_allowlisted_command(
            ["echo", "test"],
            tmp_path,
            timeout_sec=30,
        )

    assert result["backend"] == "docker"


# ---------------------------------------------------------------------------
# run_allowlisted_command — e2b backend
# ---------------------------------------------------------------------------

def test_run_allowlisted_e2b_no_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_EXEC_BACKEND", "e2b")
    monkeypatch.delenv("SWARM_E2B_API_KEY", raising=False)

    result = run_allowlisted_command(
        ["python", "--version"],
        tmp_path,
        timeout_sec=30,
    )

    assert "error" in result
    assert "SWARM_E2B_API_KEY" in result["error"]


def test_run_allowlisted_e2b_with_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_EXEC_BACKEND", "e2b")
    monkeypatch.setenv("SWARM_E2B_API_KEY", "test-key-123")

    result = run_allowlisted_command(
        ["python", "--version"],
        tmp_path,
        timeout_sec=30,
    )

    assert "error" in result
    assert "e2b" in result["error"].lower()


def test_run_allowlisted_e2b_sandbox_alias(tmp_path, monkeypatch):
    """'e2b_sandbox' is treated same as 'e2b'."""
    monkeypatch.setenv("SWARM_EXEC_BACKEND", "e2b_sandbox")
    monkeypatch.delenv("SWARM_E2B_API_KEY", raising=False)

    result = run_allowlisted_command(
        ["ls"],
        tmp_path,
        timeout_sec=30,
    )

    assert "SWARM_E2B_API_KEY" in result["error"]


# ---------------------------------------------------------------------------
# run_allowlisted_command — custom env
# ---------------------------------------------------------------------------

def test_run_allowlisted_custom_env(tmp_path, monkeypatch):
    monkeypatch.delenv("SWARM_EXEC_BACKEND", raising=False)

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = ""
    mock_result.stderr = ""

    captured_env = {}

    def fake_run(argv, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return mock_result

    with patch("subprocess.run", side_effect=fake_run):
        run_allowlisted_command(
            ["echo", "test"],
            tmp_path,
            timeout_sec=30,
            env={"MY_CUSTOM_VAR": "value"},
        )

    assert captured_env.get("MY_CUSTOM_VAR") == "value"
