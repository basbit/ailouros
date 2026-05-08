from __future__ import annotations

import copy
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from backend.App.integrations.infrastructure.mcp.stdio.session import coerce_mcp_config_dict
from backend.App.shared.domain.validators import is_truthy_env

logger = logging.getLogger(__name__)

_MCP_LOCAL_DIR = Path(os.environ.get("SWARM_MCP_LOCAL_DIR", "")) if os.environ.get("SWARM_MCP_LOCAL_DIR") else Path.home() / ".swarm" / "mcp"
_MCP_PKG = os.environ.get("SWARM_MCP_FS_PACKAGE", "@modelcontextprotocol/server-filesystem")
_MCP_BIN_NAME = os.environ.get("SWARM_MCP_FS_BIN", "mcp-server-filesystem")


def _load_mcp_config_file(path: str) -> Optional[dict[str, Any]]:
    p = Path(path).expanduser()
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _local_bin_path() -> Path:
    return _MCP_LOCAL_DIR / "node_modules" / ".bin" / _MCP_BIN_NAME


def _ensure_mcp_filesystem_bin() -> Optional[str]:
    global_bin = shutil.which(_MCP_BIN_NAME)
    if global_bin:
        return global_bin

    local_bin = _local_bin_path()
    if local_bin.is_file():
        return str(local_bin)

    npm = shutil.which("npm")
    if not npm:
        logger.warning("MCP auto: npm not found, cannot install %s", _MCP_PKG)
        return None

    _MCP_LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(
        "MCP auto: first-time install of %s to %s (may take a few seconds)…",
        _MCP_PKG,
        _MCP_LOCAL_DIR,
    )
    try:
        result = subprocess.run(
            [
                npm,
                "install",
                "--prefix",
                str(_MCP_LOCAL_DIR),
                _MCP_PKG,
                "--prefer-offline",
                "--no-audit",
                "--no-fund",
                "--loglevel=warn",
                "--no-progress",
            ],
            capture_output=True,
            timeout=300,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.error("MCP auto: npm install timed out after 300 s")
        return None
    except OSError as exc:
        logger.error("MCP auto: npm install failed: %s", exc)
        return None

    if result.returncode != 0:
        stderr_text = result.stderr.decode("utf-8", errors="replace")[:600]
        logger.error(
            "MCP auto: npm install returned rc=%d: %s", result.returncode, stderr_text
        )
        return None

    if local_bin.is_file():
        logger.info("MCP auto: installed %s → %s", _MCP_PKG, local_bin)
        return str(local_bin)

    logger.warning(
        "MCP auto: npm install succeeded but binary not found at %s", local_bin
    )
    return None


def apply_auto_mcp_to_agent_config(
    agent_config: dict[str, Any],
    *,
    workspace_root: str,
) -> dict[str, Any]:
    ac = copy.deepcopy(agent_config)
    status_summary: dict[str, Any] = {
        "filesystem": "skipped",
        "web_search": "off",
        "fetch_page": "off",
        "git": "off",
        "context7": "off",
        "github": "off",
        "notes": [],
    }
    existing = ac.get("mcp")
    if isinstance(existing, dict):
        ac["mcp"] = coerce_mcp_config_dict(dict(existing))
        if ac["mcp"].get("servers"):
            status_summary["filesystem"] = "user_provided"
            status_summary["notes"].append("user-supplied mcp.servers — auto-discovery bypassed")
            ac["mcp"]["status_summary"] = status_summary
            return ac

    cfg_path = (os.getenv("SWARM_MCP_CONFIG") or "").strip()
    if cfg_path:
        loaded = _load_mcp_config_file(cfg_path)
        if loaded:
            loaded = coerce_mcp_config_dict(loaded)
            if loaded.get("servers"):
                ac["mcp"] = loaded
                return ac

    xdg = Path.home() / ".config" / "ailouros" / "mcp.json"
    if xdg.is_file():
        loaded = _load_mcp_config_file(str(xdg))
        if loaded:
            loaded = coerce_mcp_config_dict(loaded)
            if loaded.get("servers"):
                ac["mcp"] = loaded
                return ac

    swarm = ac.get("swarm")
    if not isinstance(swarm, dict):
        swarm = {}
        ac["swarm"] = swarm

    if "mcp_auto" in swarm:
        auto = bool(swarm.get("mcp_auto"))
    else:
        auto = is_truthy_env("SWARM_MCP_AUTO", default=True)
    if not auto:
        return ac

    wr = (workspace_root or "").strip()
    if not wr:
        return ac

    wr_abs = str(Path(wr).expanduser().resolve())
    if not Path(wr_abs).is_dir():
        return ac

    servers: list[dict[str, Any]] = []

    mcp_bin = _ensure_mcp_filesystem_bin()

    if mcp_bin:
        servers.append({
            "name": "workspace",
            "command": mcp_bin,
            "args": [wr_abs],
        })
        status_summary["filesystem"] = "enabled"
    else:
        npx = shutil.which("npx")
        if npx:
            logger.warning(
                "MCP auto: falling back to npx (slow). "
                "Run `npm install -g %s` for faster startup.",
                _MCP_PKG,
            )
            servers.append({
                "name": "workspace",
                "command": npx,
                "args": ["-y", _MCP_PKG, wr_abs],
                "env": {
                    "CI": "1",
                    "NO_UPDATE_NOTIFIER": "1",
                    "npm_config_update_notifier": "false",
                    "npm_config_progress": "false",
                },
            })
            status_summary["filesystem"] = "enabled_via_npx"
            status_summary["notes"].append(
                f"slow startup — install globally: npm install -g {_MCP_PKG}"
            )
        else:
            logger.warning(
                "MCP auto: filesystem server skipped (npm/npx not found). "
                "Other MCP tools may still be available."
            )
            status_summary["filesystem"] = "disabled_no_npm"
            status_summary["notes"].append("filesystem server unavailable: install Node.js (npm/npx)")

    from backend.App.integrations.infrastructure.mcp.web_search.web_search_router import (
        web_search_available,
        get_active_provider_info,
    )
    _search_config_keys = {
        "tavily": str(swarm.get("tavily_api_key") or "").strip(),
        "exa": str(swarm.get("exa_api_key") or "").strip(),
        "scrapingdog": str(swarm.get("scrapingdog_api_key") or "").strip(),
    }
    if web_search_available(_search_config_keys):
        os.environ["_WEB_SEARCH_ENABLED"] = "1"
        info = get_active_provider_info()
        logger.info(
            "MCP auto: web_search_provider=router configured=%s usage=%s",
            info.get("configured_providers"),
            info.get("usage"),
        )
        os.environ.pop("_DDG_SEARCH_ENABLED", None)
        configured_providers = info.get("configured_providers") or []
        status_summary["web_search"] = (
            f"router({', '.join(configured_providers)})"
            if configured_providers
            else "router"
        )
    else:
        from backend.App.integrations.infrastructure.mcp.web_search.ddg_search import (
            ddg_search_available,
        )
        if ddg_search_available():
            os.environ["_DDG_SEARCH_ENABLED"] = "1"
            logger.info(
                "MCP auto: web_search_provider=duckduckgo "
                "(no provider keys set, DDG package available)"
            )
            status_summary["web_search"] = "duckduckgo"
            status_summary["notes"].append(
                "using duckduckgo (no provider keys set) — set tavily_api_key/exa_api_key/scrapingdog_api_key for better results"
            )
        else:
            os.environ.pop("_WEB_SEARCH_ENABLED", None)
            os.environ.pop("_DDG_SEARCH_ENABLED", None)
            logger.warning(
                "MCP auto: web_search_provider=none — no provider keys set "
                "(SWARM_TAVILY_API_KEY / SWARM_EXA_API_KEY / SWARM_SCRAPINGDOG_API_KEY) "
                "and duckduckgo-search not installed."
            )
            status_summary["web_search"] = "none"
            status_summary["notes"].append(
                "web_search disabled: no provider keys (tavily/exa/scrapingdog) and duckduckgo-search not installed"
            )

    _fetch_page_flag = swarm.get("fetch_page")
    if _fetch_page_flag is None:
        _fetch_page_flag = is_truthy_env("SWARM_FETCH_PAGE", default=True)
    if _fetch_page_flag:
        from backend.App.integrations.infrastructure.mcp.web_search.fetch_page import (
            fetch_page_available,
        )
        if fetch_page_available():
            os.environ["_FETCH_PAGE_ENABLED"] = "1"
            logger.info("MCP auto: fetch_page=enabled (httpx available)")
            status_summary["fetch_page"] = "enabled"
        else:
            os.environ.pop("_FETCH_PAGE_ENABLED", None)
            logger.warning("MCP auto: fetch_page=disabled (httpx not installed)")
            status_summary["fetch_page"] = "disabled_no_httpx"
            status_summary["notes"].append("fetch_page disabled: install httpx (pip install httpx)")
    else:
        os.environ.pop("_FETCH_PAGE_ENABLED", None)
        status_summary["fetch_page"] = "disabled_by_config"

    _git_flag = swarm.get("git_mcp")
    if _git_flag is None:
        _git_flag = is_truthy_env("SWARM_GIT_MCP", default=True)
    if _git_flag:
        from backend.App.integrations.infrastructure.mcp.git.git_mcp import (
            workspace_has_git,
            git_mcp_available,
            git_mcp_config,
        )
        if workspace_has_git(wr):
            if git_mcp_available():
                servers.append(git_mcp_config(wr_abs))
                logger.info("MCP auto: git=enabled (workspace has .git)")
                status_summary["git"] = "enabled"
            else:
                logger.error(
                    "MCP auto: git=UNAVAILABLE — 'uvx' not found. "
                    "Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh"
                )
                status_summary["git"] = "unavailable_no_uvx"
                status_summary["notes"].append(
                    "git MCP unavailable: install uv (curl -LsSf https://astral.sh/uv/install.sh | sh)"
                )
        else:
            logger.debug("MCP auto: git=skipped (no .git in workspace)")
            status_summary["git"] = "skipped_no_git_dir"

    _ctx7_flag = swarm.get("context7")
    if _ctx7_flag is None:
        _ctx7_flag = is_truthy_env("SWARM_CONTEXT7", default=False)
    if _ctx7_flag:
        from backend.App.integrations.infrastructure.mcp.docs.context7_mcp import (
            context7_mcp_config,
        )
        servers.append(context7_mcp_config())
        logger.info("MCP auto: context7=enabled")
        status_summary["context7"] = "enabled"

    _gh_flag = swarm.get("github_mcp")
    if _gh_flag is None:
        _gh_flag = is_truthy_env("SWARM_GITHUB_MCP", default=False)
    if _gh_flag:
        from backend.App.integrations.infrastructure.mcp.github.github_mcp import (
            github_token,
            github_mcp_config,
        )
        _gh_token = github_token()
        if _gh_token:
            servers.append(github_mcp_config(_gh_token))
            logger.info("MCP auto: github=enabled (GITHUB_TOKEN set)")
            status_summary["github"] = "enabled"
        else:
            logger.warning(
                "MCP auto: github=requested but no GITHUB_TOKEN or "
                "GITHUB_PERSONAL_ACCESS_TOKEN found"
            )
            status_summary["github"] = "no_token"
            status_summary["notes"].append(
                "github MCP requested but no GITHUB_TOKEN/GITHUB_PERSONAL_ACCESS_TOKEN found"
            )

    ac["mcp"] = {"servers": servers, "auto": True, "status_summary": status_summary}
    return ac


def format_mcp_auto_status_line(status_summary: dict[str, Any]) -> str:
    if not isinstance(status_summary, dict):
        return ""
    fragments: list[str] = []
    for key in ("filesystem", "web_search", "fetch_page", "git", "context7", "github"):
        value = status_summary.get(key)
        if value:
            fragments.append(f"{key}={value}")
    notes_list = status_summary.get("notes") or []
    notes_suffix = ""
    if notes_list:
        notes_suffix = " | notes: " + "; ".join(str(note) for note in notes_list)
    return "MCP auto: " + ", ".join(fragments) + notes_suffix
