"""Auto-setup MCP servers based on project stack analysis.

Rules (INV-6): only proposes configuration — nothing is applied without explicit user Apply.
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Runtime tool resolution
# ---------------------------------------------------------------------------

def _resolve_uvx() -> str:
    """Return the full path to the ``uvx`` binary.

    Search order:
    1. System PATH (covers global installs).
    2. Current Python prefix / bin (covers pip-installed ``uv`` in the venv).
    3. Common macOS/Linux user-install locations.
    4. Falls back to the bare ``"uvx"`` string with a warning so the error
       surfaces at MCP spawn time rather than at import time.
    """
    found = shutil.which("uvx")
    if found:
        return found

    # pip install uv puts uvx next to the current python interpreter
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


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class MCPServerSpec:
    """Specification for a single MCP server."""

    name: str
    package: str
    transport: str
    command: str
    args: list[str]
    scope_dirs: list[str] = field(default_factory=list)
    reason: str = ""
    enabled: bool = True


# ---------------------------------------------------------------------------
# Default server specs by stack
# ---------------------------------------------------------------------------

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


def _make_brave_search_spec(api_key: str) -> MCPServerSpec:
    return MCPServerSpec(
        name="brave-search",
        package="@modelcontextprotocol/server-brave-search",
        transport="stdio",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-brave-search"],
        scope_dirs=[],
        reason=(
            "Recommended when tasks require internet/web search — "
            "web and news search via Brave Search API"
        ),
        enabled=bool(api_key),
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


# Base specs every project gets (always included regardless of stack)
_BASE_SPECS = ("filesystem", "git", "fetch")

# Extra specs per stack
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def recommend_mcp_servers(
    workspace_root: str,
    detected_stack: list[str],
    *,
    brave_api_key: str = "",
) -> list[MCPServerSpec]:
    """Return recommended MCP server specs for the given workspace and stack.

    Always includes filesystem + git + fetch as a minimum baseline.
    Brave Search is added when ``brave_api_key`` is provided (or the
    ``SWARM_BRAVE_SEARCH_API_KEY`` / ``BRAVE_API_KEY`` env vars are set).
    """
    import os

    root = workspace_root or ""

    # Resolve brave API key from caller or environment
    resolved_brave_key = (
        brave_api_key
        or os.getenv("SWARM_BRAVE_SEARCH_API_KEY", "")
        or os.getenv("BRAVE_API_KEY", "")
    )

    specs: dict[str, MCPServerSpec] = {
        "filesystem": _make_filesystem_spec(root),
        "git": _make_git_spec(root),
        "fetch": _make_fetch_spec(),
    }

    if resolved_brave_key:
        specs["brave-search"] = _make_brave_search_spec(resolved_brave_key)

    # Add stack-specific extras
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
    """Recursively substitute {workspace_root} placeholder in strings."""
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
    """Build the .swarm/mcp_config.json dict from a list of specs.

    Substitutes {workspace_root} placeholder in all string fields.
    """
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
    """Write config to {workspace_root}/.swarm/mcp_config.json.

    Only called after explicit user Apply (INV-6).

    Raises:
        ValueError: if workspace_root is empty or does not exist.
        PermissionError: if .swarm/ cannot be created.
    """
    if not workspace_root or not workspace_root.strip():
        raise ValueError("workspace_root must be provided to save mcp_config")
    root = Path(workspace_root).resolve()
    if not root.exists():
        raise ValueError(f"workspace_root does not exist: {root}")

    target = (root / ".swarm" / "mcp_config.json").resolve()
    # Protect against path traversal
    if not str(target).startswith(str(root)):
        raise ValueError("path traversal detected")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("mcp.auto.setup.save_mcp_config: wrote %s", target)
    return target


def _migrate_mcp_config(config: dict[str, Any], target: Path) -> tuple[dict[str, Any], bool]:
    """Apply in-place migrations to a loaded MCP config dict.

    Returns (possibly-modified-config, was_changed).

    Migrations applied:
    - git server: npx @modelcontextprotocol/server-git → uvx mcp-server-git
      (the npm package does not exist; uvx is the correct runtime)
    - fetch server: add if missing (always useful, no API key required)
    """
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

        # Migration: fix broken git MCP (npm package does not exist)
        if (
            name == "git"
            and cmd == "npx"
            and any("@modelcontextprotocol/server-git" in str(a) for a in args)
        ):
            # Rebuild with correct uvx-based spec
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

        # Migration: fix broken fetch MCP (@modelcontextprotocol/server-fetch does not exist on npm)
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

        # Migration: upgrade bare "uvx" command to full resolved path so subprocess
        # can find the binary even when PATH is restricted at spawn time.
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

    # Add fetch server if absent
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
    """Load .swarm/mcp_config.json if it exists, otherwise return None.

    Applies automatic migrations (e.g. broken git npm package → uvx) and
    rewrites the file so the fix is permanent.
    """
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
