from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

SWARM_MCP_AUTOSTART = os.getenv("SWARM_MCP_AUTOSTART", "1")


@dataclass
class MCPServerHandle:
    name: str
    command: str
    args: list[str]
    process: Optional[subprocess.Popen] = field(default=None, repr=False)
    status: str = "stopped"
    error: Optional[str] = None
    pid: Optional[int] = None


class MCPManager:
    def __init__(self, workspace_root: str) -> None:
        self._root = workspace_root
        self._handles: dict[str, MCPServerHandle] = {}

    def _load_servers(self) -> list[dict]:
        try:
            from backend.App.integrations.infrastructure.mcp.auto.setup import load_mcp_config
            configuration = load_mcp_config(self._root)
            if not configuration:
                return []
            return [s for s in configuration.get("servers", []) if s.get("enabled", True)]
        except Exception as exc:
            logger.warning("mcp_manager: cannot load config: %s", exc)
            return []

    def start_all(self) -> None:
        if SWARM_MCP_AUTOSTART == "0":
            logger.info("mcp_manager: autostart disabled (SWARM_MCP_AUTOSTART=0)")
            return
        servers = self._load_servers()
        if not servers:
            logger.info(
                "mcp_manager: no MCP servers in .swarm/mcp_config.json — skipping autostart"
            )
            return
        for srv in servers:
            self._start_one(srv)

    def _start_one(self, srv: dict) -> None:
        name = srv["name"]
        command = srv.get("command", "npx")
        args = [a.replace("{workspace_root}", self._root) for a in srv.get("args", [])]
        if not shutil.which(command):
            logger.warning("mcp_manager: %r not on PATH, skipping %s", command, name)
            self._handles[name] = MCPServerHandle(
                name=name,
                command=command,
                args=args,
                status="failed",
                error=f"{command!r} not found",
            )
            return
        try:
            proc = subprocess.Popen(
                [command] + args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self._handles[name] = MCPServerHandle(
                name=name,
                command=command,
                args=args,
                process=proc,
                status="running",
                pid=proc.pid,
            )
            logger.info("mcp_manager: started %s pid=%d", name, proc.pid)
        except OSError as exc:
            self._handles[name] = MCPServerHandle(
                name=name, command=command, args=args, status="failed", error=str(exc)
            )
            logger.error("mcp_manager: failed to start %s: %s", name, exc)

    def stop_all(self) -> None:
        for name, handle in self._handles.items():
            if handle.process and handle.status == "running":
                try:
                    handle.process.terminate()
                    handle.process.wait(timeout=5)
                    handle.status = "stopped"
                    logger.info("mcp_manager: stopped %s", name)
                except Exception as exc:
                    logger.warning("mcp_manager: error stopping %s: %s", name, exc)
                    try:
                        handle.process.kill()
                    except Exception as kill_error:
                        logger.warning("mcp_manager: kill failed for %s: %s", name, kill_error)

    def get_status(self) -> dict[str, dict]:
        result = {}
        for name, h in self._handles.items():
            if h.process and h.status == "running":
                if h.process.poll() is not None:
                    h.status = "failed"
                    h.error = f"exited with code {h.process.returncode}"
            result[name] = {"status": h.status, "pid": h.pid, "error": h.error}
        return result
