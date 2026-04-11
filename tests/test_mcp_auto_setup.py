"""Tests for orchestrator.mcp_auto_setup (G-4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from unittest.mock import patch

from backend.App.integrations.infrastructure.mcp.auto.setup import (
    build_mcp_config,
    load_mcp_config,
    recommend_mcp_servers,
    save_mcp_config,
)


# ---------------------------------------------------------------------------
# recommend_mcp_servers
# ---------------------------------------------------------------------------

def test_recommend_python_project() -> None:
    specs = recommend_mcp_servers("/some/project", ["python"])
    names = [s.name for s in specs]
    assert "filesystem" in names
    assert "git" in names


def test_recommend_nodejs_project() -> None:
    specs = recommend_mcp_servers("/some/project", ["nodejs"])
    names = [s.name for s in specs]
    assert "filesystem" in names
    assert "git" in names
    assert "everything" in names


def test_recommend_rust_project() -> None:
    specs = recommend_mcp_servers("/some/project", ["rust"])
    names = [s.name for s in specs]
    assert "filesystem" in names
    assert "git" in names


def test_recommend_unknown_stack_has_defaults() -> None:
    specs = recommend_mcp_servers("/some/project", ["cobol"])
    names = [s.name for s in specs]
    assert "filesystem" in names
    assert "git" in names


def test_recommend_empty_stack_has_defaults() -> None:
    specs = recommend_mcp_servers("/some/project", [])
    names = [s.name for s in specs]
    assert "filesystem" in names
    assert "git" in names


def test_recommend_disables_git_and_fetch_without_uvx() -> None:
    with patch(
        "backend.App.integrations.infrastructure.mcp.auto.setup._resolve_uvx",
        return_value="uvx",
    ):
        specs = recommend_mcp_servers("/some/project", ["python"])

    spec_by_name = {s.name: s for s in specs}
    assert spec_by_name["git"].enabled is False
    assert spec_by_name["fetch"].enabled is False
    assert "uvx" in spec_by_name["git"].reason
    assert "uvx" in spec_by_name["fetch"].reason


def test_recommend_no_duplicate_servers() -> None:
    # nodejs gives everything + filesystem + git; no duplicates
    specs = recommend_mcp_servers("/proj", ["nodejs", "python"])
    names = [s.name for s in specs]
    assert len(names) == len(set(names))


# ---------------------------------------------------------------------------
# build_mcp_config
# ---------------------------------------------------------------------------

def test_build_mcp_config_substitutes_workspace_root() -> None:
    specs = recommend_mcp_servers("{workspace_root}", ["python"])
    config = build_mcp_config(specs, "/real/path")
    for server in config["servers"]:
        # No {workspace_root} placeholders should remain
        assert "{workspace_root}" not in json.dumps(server)
        # Real path should appear in args or scope for filesystem/git
        if server["name"] in ("filesystem", "git"):
            assert "/real/path" in json.dumps(server)


def test_build_mcp_config_structure() -> None:
    specs = recommend_mcp_servers("/proj", ["python"])
    config = build_mcp_config(specs, "/proj", generated_by="test", base_model="mymodel")
    assert config["version"] == "1"
    assert config["workspace_root"] == "/proj"
    assert config["generated_by"] == "test"
    assert config["base_model"] == "mymodel"
    assert isinstance(config["servers"], list)
    assert len(config["servers"]) >= 2


def test_build_mcp_config_server_fields() -> None:
    specs = recommend_mcp_servers("/proj", ["python"])
    config = build_mcp_config(specs, "/proj")
    for server in config["servers"]:
        assert "name" in server
        assert "transport" in server
        assert "command" in server
        assert "args" in server
        assert "enabled" in server
        assert "reason" in server


# ---------------------------------------------------------------------------
# save_mcp_config / load_mcp_config
# ---------------------------------------------------------------------------

def test_save_and_load_mcp_config(tmp_path: Path) -> None:
    specs = recommend_mcp_servers(str(tmp_path), ["python"])
    config = build_mcp_config(specs, str(tmp_path))
    saved_path = save_mcp_config(str(tmp_path), config)

    assert saved_path.exists()
    loaded = load_mcp_config(str(tmp_path))
    assert loaded is not None
    assert loaded["version"] == "1"
    assert loaded["workspace_root"] == str(tmp_path)


def test_save_mcp_config_creates_swarm_dir(tmp_path: Path) -> None:
    assert not (tmp_path / ".swarm").exists()
    config = build_mcp_config([], str(tmp_path))
    save_mcp_config(str(tmp_path), config)
    assert (tmp_path / ".swarm" / "mcp_config.json").exists()


def test_save_mcp_config_empty_root_raises() -> None:
    with pytest.raises(ValueError, match="workspace_root"):
        save_mcp_config("", {})


def test_save_mcp_config_nonexistent_root_raises() -> None:
    with pytest.raises(ValueError, match="does not exist"):
        save_mcp_config("/nonexistent/path/xyz", {})


def test_load_mcp_config_returns_none_when_missing(tmp_path: Path) -> None:
    result = load_mcp_config(str(tmp_path))
    assert result is None


def test_load_mcp_config_returns_none_for_empty_root() -> None:
    result = load_mcp_config("")
    assert result is None


def test_load_mcp_config_handles_corrupt_json(tmp_path: Path) -> None:
    swarm = tmp_path / ".swarm"
    swarm.mkdir()
    (swarm / "mcp_config.json").write_text("not-json!!!", encoding="utf-8")
    result = load_mcp_config(str(tmp_path))
    assert result is None


def test_load_mcp_config_disables_uvx_servers_when_uvx_missing(tmp_path: Path) -> None:
    swarm = tmp_path / ".swarm"
    swarm.mkdir()
    (swarm / "mcp_config.json").write_text(
        json.dumps(
            {
                "version": "1",
                "servers": [
                    {
                        "name": "git",
                        "transport": "stdio",
                        "command": "uvx",
                        "args": ["mcp-server-git", "--repository", str(tmp_path)],
                        "enabled": True,
                        "reason": "Git operations",
                    },
                    {
                        "name": "fetch",
                        "transport": "stdio",
                        "command": "uvx",
                        "args": ["mcp-server-fetch"],
                        "enabled": True,
                        "reason": "Fetch web pages",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    with patch(
        "backend.App.integrations.infrastructure.mcp.auto.setup._resolve_uvx",
        return_value="uvx",
    ):
        result = load_mcp_config(str(tmp_path))

    assert result is not None
    servers = {srv["name"]: srv for srv in result["servers"]}
    assert servers["git"]["enabled"] is False
    assert servers["fetch"]["enabled"] is False
