"""Tests for orchestrator.mcp_manager."""
from __future__ import annotations

from unittest.mock import patch

from backend.App.integrations.infrastructure.mcp.manager import MCPManager, MCPServerHandle


class TestMCPManagerStartAllNoConfig:
    def test_start_all_no_config(self, tmp_path):
        """No mcp_config.json → no errors, handles is empty."""
        manager = MCPManager(str(tmp_path))
        manager.start_all()  # must not raise
        assert manager.get_status() == {}


class TestMCPManagerCommandNotFound:
    def test_start_all_command_not_found(self, tmp_path):
        """When command is not on PATH, server status is 'failed'."""
        (tmp_path / ".swarm").mkdir()
        import json
        config = {
            "servers": [
                {
                    "name": "test-srv",
                    "command": "nonexistent-binary-xyz",
                    "args": [],
                    "enabled": True,
                }
            ]
        }
        (tmp_path / ".swarm" / "mcp_config.json").write_text(json.dumps(config))

        manager = MCPManager(str(tmp_path))
        with patch("shutil.which", return_value=None):
            manager._start_one(config["servers"][0])

        status = manager.get_status()
        assert status["test-srv"]["status"] == "failed"
        assert status["test-srv"]["error"] is not None


class TestMCPManagerGetStatusEmpty:
    def test_get_status_empty(self, tmp_path):
        """No servers started → get_status returns empty dict."""
        manager = MCPManager(str(tmp_path))
        assert manager.get_status() == {}


class TestMCPManagerStopAllNoProcesses:
    def test_stop_all_no_processes(self, tmp_path):
        """Calling stop_all with no running processes must not crash."""
        manager = MCPManager(str(tmp_path))
        manager.stop_all()  # must not raise

    def test_stop_all_with_failed_handle(self, tmp_path):
        """stop_all skips handles with status != running."""
        manager = MCPManager(str(tmp_path))
        manager._handles["dead"] = MCPServerHandle(
            name="dead", command="npx", args=[], status="failed"
        )
        manager.stop_all()  # must not raise
        assert manager._handles["dead"].status == "failed"
