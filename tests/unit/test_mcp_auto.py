"""Автоконфиг MCP."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.App.shared.infrastructure.env_flags import is_truthy_env as _truthy_env
from backend.App.integrations.infrastructure.mcp.auto.auto import (
    _ensure_mcp_filesystem_bin,
    _load_mcp_config_file,
    apply_auto_mcp_to_agent_config,
)


def test_apply_auto_mcp_no_workspace_noop():
    ac = apply_auto_mcp_to_agent_config({}, workspace_root="")
    assert "mcp" not in ac or not (ac.get("mcp") or {}).get("servers")


# ---------------------------------------------------------------------------
# _truthy_env
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("val,expected", [
    ("1", True),
    ("true", True),
    ("yes", True),
    ("on", True),
    ("0", False),
    ("false", False),
    ("", False),
])
def test_truthy_env(monkeypatch, val, expected):
    monkeypatch.setenv("TEST_MCP_ENV_VAR", val)
    assert _truthy_env("TEST_MCP_ENV_VAR") == expected


def test_truthy_env_missing_default(monkeypatch):
    monkeypatch.delenv("TEST_MCP_ENV_VAR", raising=False)
    assert _truthy_env("TEST_MCP_ENV_VAR", True) is True


# ---------------------------------------------------------------------------
# _load_mcp_config_file
# ---------------------------------------------------------------------------

def test_load_mcp_config_file_valid(tmp_path):
    cfg = {"servers": [{"name": "ws"}]}
    f = tmp_path / "mcp.json"
    f.write_text(json.dumps(cfg))
    result = _load_mcp_config_file(str(f))
    assert result == cfg


def test_load_mcp_config_file_not_found(tmp_path):
    result = _load_mcp_config_file(str(tmp_path / "missing.json"))
    assert result is None


def test_load_mcp_config_file_invalid_json(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text("not json!")
    result = _load_mcp_config_file(str(f))
    assert result is None


def test_load_mcp_config_file_not_dict(tmp_path):
    f = tmp_path / "arr.json"
    f.write_text('["a", "b"]')
    result = _load_mcp_config_file(str(f))
    assert result is None


# ---------------------------------------------------------------------------
# apply_auto_mcp_to_agent_config
# ---------------------------------------------------------------------------

def test_apply_auto_mcp_existing_servers(tmp_path):
    ac = {"mcp": {"servers": [{"name": "existing"}]}}
    result = apply_auto_mcp_to_agent_config(ac, workspace_root=str(tmp_path))
    assert result["mcp"]["servers"][0]["name"] == "existing"


def test_apply_auto_mcp_disabled_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_MCP_AUTO", "0")
    monkeypatch.delenv("SWARM_MCP_CONFIG", raising=False)
    result = apply_auto_mcp_to_agent_config({}, workspace_root=str(tmp_path))
    assert not result.get("mcp", {}).get("servers")


def test_apply_auto_mcp_disabled_swarm_key(tmp_path, monkeypatch):
    monkeypatch.delenv("SWARM_MCP_CONFIG", raising=False)
    ac = {"swarm": {"mcp_auto": False}}
    result = apply_auto_mcp_to_agent_config(ac, workspace_root=str(tmp_path))
    assert not result.get("mcp", {}).get("servers")


def test_apply_auto_mcp_with_bin(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_MCP_AUTO", "1")
    monkeypatch.delenv("SWARM_MCP_CONFIG", raising=False)
    with patch(
        "backend.App.integrations.infrastructure.mcp.auto.auto._ensure_mcp_filesystem_bin",
        return_value="/usr/local/bin/mcp-server-filesystem",
    ):
        result = apply_auto_mcp_to_agent_config({}, workspace_root=str(tmp_path))
    servers = result.get("mcp", {}).get("servers", [])
    assert len(servers) == 1
    assert servers[0]["name"] == "workspace"
    assert servers[0]["command"] == "/usr/local/bin/mcp-server-filesystem"


def test_apply_auto_mcp_fallback_to_npx(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_MCP_AUTO", "1")
    monkeypatch.delenv("SWARM_MCP_CONFIG", raising=False)
    with patch(
        "backend.App.integrations.infrastructure.mcp.auto.auto._ensure_mcp_filesystem_bin",
        return_value=None,
    ), patch(
        "backend.App.integrations.infrastructure.mcp.auto.auto.shutil.which",
        return_value="/usr/bin/npx",
    ):
        result = apply_auto_mcp_to_agent_config({}, workspace_root=str(tmp_path))
    servers = result.get("mcp", {}).get("servers", [])
    assert len(servers) == 1
    assert servers[0]["command"] == "/usr/bin/npx"


def test_apply_auto_mcp_no_bin_no_npx(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_MCP_AUTO", "1")
    monkeypatch.delenv("SWARM_MCP_CONFIG", raising=False)
    with patch(
        "backend.App.integrations.infrastructure.mcp.auto.auto._ensure_mcp_filesystem_bin",
        return_value=None,
    ), patch(
        "backend.App.integrations.infrastructure.mcp.auto.auto.shutil.which",
        return_value=None,
    ):
        result = apply_auto_mcp_to_agent_config({}, workspace_root=str(tmp_path))
    assert not result.get("mcp", {}).get("servers")


def test_apply_auto_mcp_from_config_file(tmp_path, monkeypatch):
    cfg_file = tmp_path / "mcp.json"
    cfg = {"servers": [{"name": "custom", "command": "my-mcp"}]}
    cfg_file.write_text(json.dumps(cfg))
    monkeypatch.setenv("SWARM_MCP_CONFIG", str(cfg_file))
    result = apply_auto_mcp_to_agent_config({}, workspace_root=str(tmp_path))
    assert result["mcp"]["servers"][0]["name"] == "custom"


def test_apply_auto_mcp_nonexistent_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_MCP_AUTO", "1")
    monkeypatch.delenv("SWARM_MCP_CONFIG", raising=False)
    result = apply_auto_mcp_to_agent_config(
        {}, workspace_root=str(tmp_path / "nonexistent")
    )
    assert not result.get("mcp", {}).get("servers")


def test_apply_auto_mcp_does_not_mutate_input(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_MCP_AUTO", "0")
    monkeypatch.delenv("SWARM_MCP_CONFIG", raising=False)
    ac = {"swarm": {"some": "config"}}
    original = {"swarm": {"some": "config"}}
    apply_auto_mcp_to_agent_config(ac, workspace_root=str(tmp_path))
    assert ac == original


def test_apply_auto_mcp_respects_explicit_servers():
    ac = apply_auto_mcp_to_agent_config(
        {"mcp": {"servers": [{"name": "x", "command": "true", "args": []}]}},
        workspace_root="/tmp",
    )
    assert ac["mcp"]["servers"][0]["name"] == "x"


def test_apply_auto_mcp_coerces_cursor_mcp_servers():
    ac = apply_auto_mcp_to_agent_config(
        {
            "mcp": {
                "mcpServers": {
                    "a": {"command": "true", "args": []},
                },
            },
        },
        workspace_root="/tmp",
    )
    assert ac["mcp"]["servers"][0]["name"] == "a"
    assert ac["mcp"]["servers"][0]["command"] == "true"


def test_apply_auto_mcp_injects_when_flag_and_npx(monkeypatch, tmp_path):
    monkeypatch.setenv("SWARM_MCP_AUTO", "1")
    monkeypatch.setattr("backend.App.integrations.infrastructure.mcp.auto.auto.shutil.which", lambda _: "/fake/npx")
    root = tmp_path / "proj"
    root.mkdir()
    ac = apply_auto_mcp_to_agent_config({}, workspace_root=str(root))
    assert ac.get("mcp", {}).get("servers")
    assert ac["mcp"]["servers"][0]["name"] == "workspace"


def test_apply_auto_mcp_swarm_mcp_auto_false_overrides_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SWARM_MCP_AUTO", "1")
    monkeypatch.setattr("backend.App.integrations.infrastructure.mcp.auto.auto.shutil.which", lambda _: "/fake/npx")
    root = tmp_path / "proj"
    root.mkdir()
    ac = apply_auto_mcp_to_agent_config(
        {"swarm": {"mcp_auto": False}},
        workspace_root=str(root),
    )
    assert not (ac.get("mcp") or {}).get("servers")


# ---------------------------------------------------------------------------
# _ensure_mcp_filesystem_bin
# ---------------------------------------------------------------------------


def test_ensure_mcp_bin_global_found():
    """Global binary found via shutil.which → returned immediately."""
    with patch(
        "backend.App.integrations.infrastructure.mcp.auto.auto.shutil.which",
        return_value="/usr/local/bin/mcp-server-filesystem",
    ):
        result = _ensure_mcp_filesystem_bin()
    assert result == "/usr/local/bin/mcp-server-filesystem"


def test_ensure_mcp_bin_local_cached(tmp_path):
    """Local binary exists (from previous install) → returned immediately."""
    fake_bin = tmp_path / "node_modules" / ".bin" / "mcp-server-filesystem"
    fake_bin.parent.mkdir(parents=True)
    fake_bin.touch()

    with patch(
        "backend.App.integrations.infrastructure.mcp.auto.auto.shutil.which",
        return_value=None,  # No global binary
    ), patch(
        "backend.App.integrations.infrastructure.mcp.auto.auto._local_bin_path",
        return_value=fake_bin,
    ):
        result = _ensure_mcp_filesystem_bin()
    assert result == str(fake_bin)


def test_ensure_mcp_bin_npm_not_found():
    """No global binary, no local cache, npm not found → None."""
    with patch(
        "backend.App.integrations.infrastructure.mcp.auto.auto.shutil.which",
        return_value=None,
    ), patch(
        "backend.App.integrations.infrastructure.mcp.auto.auto._local_bin_path",
        return_value=Path("/nonexistent/path"),
    ):
        result = _ensure_mcp_filesystem_bin()
    assert result is None


def test_ensure_mcp_bin_npm_install_success(tmp_path):
    """npm install succeeds → local binary returned."""
    fake_bin = tmp_path / "bin" / "mcp-server-filesystem"
    fake_bin.parent.mkdir(parents=True)
    fake_bin.touch()

    mock_result = MagicMock()
    mock_result.returncode = 0

    def fake_which(name):
        if name == "mcp-server-filesystem":
            return None
        if name == "npm":
            return "/usr/bin/npm"
        return None

    with patch(
        "backend.App.integrations.infrastructure.mcp.auto.auto.shutil.which",
        side_effect=fake_which,
    ), patch(
        "backend.App.integrations.infrastructure.mcp.auto.auto._local_bin_path",
        return_value=fake_bin,
    ), patch(
        "subprocess.run",
        return_value=mock_result,
    ):
        result = _ensure_mcp_filesystem_bin()
    assert result == str(fake_bin)


def test_ensure_mcp_bin_npm_install_fails(tmp_path):
    """npm install returns non-zero → None."""
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stderr = b"Error: package not found"

    def fake_which(name):
        if name == "npm":
            return "/usr/bin/npm"
        return None

    with patch(
        "backend.App.integrations.infrastructure.mcp.auto.auto.shutil.which",
        side_effect=fake_which,
    ), patch(
        "backend.App.integrations.infrastructure.mcp.auto.auto._local_bin_path",
        return_value=Path("/nonexistent"),
    ), patch(
        "subprocess.run",
        return_value=mock_result,
    ):
        result = _ensure_mcp_filesystem_bin()
    assert result is None


def test_ensure_mcp_bin_npm_install_timeout(tmp_path):
    """npm install times out → None."""
    import subprocess as sp

    def fake_which(name):
        if name == "npm":
            return "/usr/bin/npm"
        return None

    with patch(
        "backend.App.integrations.infrastructure.mcp.auto.auto.shutil.which",
        side_effect=fake_which,
    ), patch(
        "backend.App.integrations.infrastructure.mcp.auto.auto._local_bin_path",
        return_value=Path("/nonexistent"),
    ), patch(
        "subprocess.run",
        side_effect=sp.TimeoutExpired("npm", 300),
    ):
        result = _ensure_mcp_filesystem_bin()
    assert result is None


def test_ensure_mcp_bin_npm_install_os_error(tmp_path):
    """npm install raises OSError → None."""
    def fake_which(name):
        if name == "npm":
            return "/usr/bin/npm"
        return None

    with patch(
        "backend.App.integrations.infrastructure.mcp.auto.auto.shutil.which",
        side_effect=fake_which,
    ), patch(
        "backend.App.integrations.infrastructure.mcp.auto.auto._local_bin_path",
        return_value=Path("/nonexistent"),
    ), patch(
        "subprocess.run",
        side_effect=OSError("npm failed"),
    ):
        result = _ensure_mcp_filesystem_bin()
    assert result is None


def test_ensure_mcp_bin_npm_success_but_bin_missing(tmp_path):
    """npm returns 0 but binary not found → None."""
    mock_result = MagicMock()
    mock_result.returncode = 0

    def fake_which(name):
        if name == "npm":
            return "/usr/bin/npm"
        return None

    with patch(
        "backend.App.integrations.infrastructure.mcp.auto.auto.shutil.which",
        side_effect=fake_which,
    ), patch(
        "backend.App.integrations.infrastructure.mcp.auto.auto._local_bin_path",
        return_value=Path("/nonexistent/bin/mcp-server-filesystem"),
    ), patch(
        "subprocess.run",
        return_value=mock_result,
    ):
        result = _ensure_mcp_filesystem_bin()
    assert result is None
