from __future__ import annotations

import json
import logging
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.App.integrations.infrastructure.mcp.git.git_mcp import workspace_has_git

logger = logging.getLogger(__name__)


def _resolve_uvx() -> str:
    found = shutil.which("uvx")
    if found:
        return found

    candidates = [
        Path(sys.prefix) / "bin" / "uvx",
        Path(sys.executable).parent / "uvx",
        Path.home() / ".local" / "bin" / "uvx",
        Path("/usr/local/bin/uvx"),
        Path("/opt/homebrew/bin/uvx"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)

    logger.warning(
        "mcp.auto.setup: uvx not found on PATH or common locations — "
        "run `pip install uv` to enable git/fetch MCP servers; falling back to bare 'uvx'"
    )
    return "uvx"


def _unavailable_reason(base_reason: str, tool: str) -> str:
    return (
        f"{base_reason} Currently disabled because `uvx` is not installed; "
        f"install `uv` to enable {tool} MCP."
    )


@dataclass
class MCPServerSpec:
    name: str
    package: str
    transport: str
    command: str
    args: list[str]
    scope_dirs: list[str] = field(default_factory=list)
    reason: str = ""
    enabled: bool = True


def _make_filesystem_spec(workspace_root: str) -> MCPServerSpec:
    return MCPServerSpec(
        name="filesystem",
        package="@modelcontextprotocol/server-filesystem",
        transport="stdio",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", "{workspace_root}"],
        scope_dirs=["{workspace_root}"],
        reason=(
            "File read/write access for agents — reduces context by fetching "
            "files on demand instead of inlining full workspace snapshot"
        ),
    )


def _make_git_spec(workspace_root: str) -> MCPServerSpec:
    uvx = _resolve_uvx()
    available = uvx != "uvx"
    reason = (
        "Git operations: diff, log, blame — no need to pass full git "
        "history in context"
    )
    return MCPServerSpec(
        name="git",
        package="mcp-server-git",
        transport="stdio",
        command=uvx,
        args=["mcp-server-git", "--repository", "{workspace_root}"],
        scope_dirs=["{workspace_root}"],
        reason=reason if available else _unavailable_reason(reason, "git"),
        enabled=available,
    )


def _make_fetch_spec() -> MCPServerSpec:
    uvx = _resolve_uvx()
    available = uvx != "uvx"
    reason = (
        "Fetch web pages and URLs — agents can retrieve documentation, "
        "API references, and external resources on demand"
    )
    return MCPServerSpec(
        name="fetch",
        package="mcp-server-fetch",
        transport="stdio",
        command=uvx,
        args=["mcp-server-fetch"],
        scope_dirs=[],
        reason=reason if available else _unavailable_reason(reason, "fetch"),
        enabled=available,
    )


def _make_web_search_spec(has_any_key: bool) -> MCPServerSpec:
    return MCPServerSpec(
        name="web_search",
        package="",
        transport="builtin",
        command="",
        args=[],
        scope_dirs=[],
        reason=(
            "Recommended when tasks require internet/web search — "
            "automatically rotates between Tavily, Exa, and ScrapingDog "
            "to stay within the 1000 req/month free tier of each provider"
        ),
        enabled=has_any_key,
    )


def _make_everything_spec() -> MCPServerSpec:
    return MCPServerSpec(
        name="everything",
        package="@modelcontextprotocol/server-everything",
        transport="stdio",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-everything"],
        scope_dirs=[],
        reason="Broad MCP capability set for Node.js projects",
    )


_BASE_SPECS = ("filesystem", "git", "fetch")

STACK_MCP_DEFAULTS: dict[str, list[str]] = {
    "nodejs": ["everything"],
    "python": [],
    "rust": [],
    "go": [],
    "java": [],
    "ruby": [],
    "php": [],
    "dotnet": [],
    "elixir": [],
}


def recommend_mcp_servers(
    workspace_root: str,
    detected_stack: list[str],
    *,
    brave_api_key: str = "",
) -> list[MCPServerSpec]:
    import os

    root = workspace_root or ""

    has_search_key = bool(
        os.getenv("SWARM_TAVILY_API_KEY", "")
        or os.getenv("SWARM_EXA_API_KEY", "")
        or os.getenv("SWARM_SCRAPINGDOG_API_KEY", "")
    )

    specs: dict[str, MCPServerSpec] = {
        "filesystem": _make_filesystem_spec(root),
        "fetch": _make_fetch_spec(),
    }

    if workspace_has_git(root):
        specs["git"] = _make_git_spec(root)

    if has_search_key:
        specs["web_search"] = _make_web_search_spec(has_search_key)

    for stack in detected_stack:
        extras = STACK_MCP_DEFAULTS.get(stack, [])
        for extra in extras:
            if extra not in specs:
                if extra == "everything":
                    specs["everything"] = _make_everything_spec()

    result = list(specs.values())
    logger.info(
        "mcp.auto.setup.recommend_mcp_servers: workspace=%s stack=%s recommended=%s",
        root,
        detected_stack,
        [s.name for s in result],
    )
    return result


def _substitute_workspace_root(value: Any, workspace_root: str) -> Any:
    if isinstance(value, str):
        return value.replace("{workspace_root}", workspace_root)
    if isinstance(value, list):
        return [_substitute_workspace_root(v, workspace_root) for v in value]
    if isinstance(value, dict):
        return {k: _substitute_workspace_root(v, workspace_root) for k, v in value.items()}
    return value


def build_mcp_config(
    specs: list[MCPServerSpec],
    workspace_root: str,
    *,
    generated_by: str = "ai_preconfigure",
    base_model: str = "",
) -> dict[str, Any]:
    servers: list[dict[str, Any]] = []
    for spec in specs:
        server: dict[str, Any] = {
            "name": spec.name,
            "transport": spec.transport,
            "command": _substitute_workspace_root(spec.command, workspace_root),
            "args": _substitute_workspace_root(spec.args, workspace_root),
            "scope": _substitute_workspace_root(spec.scope_dirs, workspace_root),
            "enabled": spec.enabled,
            "reason": spec.reason,
        }
        servers.append(server)

    config: dict[str, Any] = {
        "version": "1",
        "servers": servers,
        "workspace_root": workspace_root,
        "generated_by": generated_by,
    }
    if base_model:
        config["base_model"] = base_model
    return config


def save_mcp_config(workspace_root: str, config: dict[str, Any]) -> Path:
    if not workspace_root or not workspace_root.strip():
        raise ValueError("workspace_root must be provided to save mcp_config")
    root = Path(workspace_root).resolve()
    if not root.exists():
        raise ValueError(f"workspace_root does not exist: {root}")

    target = (root / ".swarm" / "mcp_config.json").resolve()
    if not str(target).startswith(str(root)):
        raise ValueError("path traversal detected")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("mcp.auto.setup.save_mcp_config: wrote %s", target)
    return target


def _migrate_mcp_config(config: dict[str, Any], target: Path) -> tuple[dict[str, Any], bool]:
    servers: list[dict[str, Any]] = config.get("servers", [])
    if not isinstance(servers, list):
        return config, False

    changed = False
    resolved_uvx = _resolve_uvx()
    uvx_available = resolved_uvx != "uvx"
    has_fetch = any(s.get("name") == "fetch" for s in servers)

    migrated: list[dict[str, Any]] = []
    for srv in servers:
        if not isinstance(srv, dict):
            migrated.append(srv)
            continue

        name = srv.get("name", "")
        cmd = srv.get("command", "")
        args: list = srv.get("args") or []

        if (
            name == "git"
            and cmd == "npx"
            and any("@modelcontextprotocol/server-git" in str(a) for a in args)
        ):
            workspace_root_arg = next(
                (str(a) for a in args if str(a).startswith("/")), "{workspace_root}"
            )
            fixed = dict(srv)
            fixed["package"] = "mcp-server-git"
            fixed["command"] = resolved_uvx
            fixed["args"] = ["mcp-server-git", "--repository", workspace_root_arg]
            if not uvx_available:
                fixed["enabled"] = False
                fixed["reason"] = _unavailable_reason(
                    str(fixed.get("reason") or "Git MCP server."), "git"
                )
            migrated.append(fixed)
            changed = True
            logger.info(
                "mcp.auto.setup.migrate: fixed git server: npx→uvx mcp-server-git (path=%s)",
                workspace_root_arg,
            )
            continue

        if (
            name == "fetch"
            and cmd == "npx"
            and any("@modelcontextprotocol/server-fetch" in str(a) for a in args)
        ):
            fixed = dict(srv)
            fixed["package"] = "mcp-server-fetch"
            fixed["command"] = resolved_uvx
            fixed["args"] = ["mcp-server-fetch"]
            if not uvx_available:
                fixed["enabled"] = False
                fixed["reason"] = _unavailable_reason(
                    str(fixed.get("reason") or "Fetch MCP server."), "fetch"
                )
            migrated.append(fixed)
            changed = True
            logger.info("mcp.auto.setup.migrate: fixed fetch server: npx→uvx mcp-server-fetch")
            continue

        if cmd in ("uvx",) and resolved_uvx != "uvx" and name in ("git", "fetch"):
            fixed = dict(srv)
            fixed["command"] = resolved_uvx
            migrated.append(fixed)
            changed = True
            logger.info(
                "mcp.auto.setup.migrate: resolved bare 'uvx' → %s for server=%s",
                resolved_uvx,
                name,
            )
            continue

        if name in ("git", "fetch") and not uvx_available and srv.get("enabled", True):
            fixed = dict(srv)
            fixed["enabled"] = False
            fixed["reason"] = _unavailable_reason(str(fixed.get("reason") or ""), name)
            migrated.append(fixed)
            changed = True
            logger.info(
                "mcp.auto.setup.migrate: disabled %s server because uvx is unavailable",
                name,
            )
            continue

        migrated.append(srv)

    if not has_fetch:
        migrated.append({
            "name": "fetch",
            "package": "mcp-server-fetch",
            "transport": "stdio",
            "command": resolved_uvx,
            "args": ["mcp-server-fetch"],
            "scope": [],
            "enabled": uvx_available,
            "reason": (
                "Fetch web pages and URLs — agents can retrieve documentation, "
                "API references, and external resources on demand"
            ) if uvx_available else _unavailable_reason(
                "Fetch web pages and URLs — agents can retrieve documentation, "
                "API references, and external resources on demand",
                "fetch",
            ),
        })
        changed = True
        logger.info("mcp.auto.setup.migrate: added fetch server to existing config")

    if changed:
        config = dict(config)
        config["servers"] = migrated
        try:
            target.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info("mcp.auto.setup.migrate: rewrote %s with migrations applied", target)
        except OSError as exc:
            logger.warning("mcp.auto.setup.migrate: could not rewrite %s: %s", target, exc)

    return config, changed


def load_mcp_config(workspace_root: str) -> dict[str, Any] | None:
    if not workspace_root or not workspace_root.strip():
        return None
    target = Path(workspace_root).resolve() / ".swarm" / "mcp_config.json"
    if not target.exists():
        return None
    try:
        config = json.loads(target.read_text(encoding="utf-8"))
        config, _ = _migrate_mcp_config(config, target)
        return config
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("mcp.auto.setup.load_mcp_config: failed to read %s: %s", target, exc)
        return None
