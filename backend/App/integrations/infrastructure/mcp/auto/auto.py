"""Автоконфиг MCP (stdio) при наличии workspace и без явного agent_config.mcp."""

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

logger = logging.getLogger(__name__)

# Каталог для локальной установки пакета (однократно, без сетевых запросов при старте).
_MCP_LOCAL_DIR = Path(os.environ.get("SWARM_MCP_LOCAL_DIR", "")) if os.environ.get("SWARM_MCP_LOCAL_DIR") else Path.home() / ".swarm" / "mcp"
_MCP_PKG = os.environ.get("SWARM_MCP_FS_PACKAGE", "@modelcontextprotocol/server-filesystem")
_MCP_BIN_NAME = os.environ.get("SWARM_MCP_FS_BIN", "mcp-server-filesystem")


def _truthy_env(key: str, default: str = "0") -> bool:
    return (os.getenv(key, default) or "").strip().lower() in ("1", "true", "yes", "on")


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
    """Возвращает путь к исполняемому файлу mcp-server-filesystem.

    Приоритет:
      1. Глобальный бинарь (npm install -g) — fastest, no network.
      2. Локальная установка в ~/.swarm/mcp/ — fast after first install.
      3. Однократная npm install --prefix ~/.swarm/mcp/ (если ни 1, ни 2 нет).
      4. None — вызывающий код вернётся к npx (slow, network required).

    Установка блокирует поток, но выполняется только один раз.  Последующие
    вызовы находят бинарь сразу (< 1 мс).  Сетевой запрос npm выполняется
    только при первой установке; впоследствии — ``--prefer-offline``.
    """
    # 1. Global binary
    global_bin = shutil.which(_MCP_BIN_NAME)
    if global_bin:
        return global_bin

    # 2. Local install (cached from previous run)
    local_bin = _local_bin_path()
    if local_bin.is_file():
        return str(local_bin)

    # 3. One-time npm install to local dir
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
            timeout=300,  # 5 min — достаточно даже при медленном интернете
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
    """
    Копия agent_config с подмешанным MCP.

    Порядок:
    1) ``SWARM_MCP_CONFIG`` / ``~/.config/ailouros/mcp.json`` — если есть ``servers``.
    2) Авто filesystem: если в ``swarm`` есть ключ ``mcp_auto`` — используется его bool;
       иначе ``SWARM_MCP_AUTO`` (по умолчанию 1). При ``true`` и заданном ``workspace_root``
       добавляется сервер ``@modelcontextprotocol/server-filesystem``.

       Бинарь запускается напрямую (без npx) если пакет установлен через ``npm install
       --prefix ~/.swarm/mcp``. При первом запуске установка выполняется автоматически.
       Это устраняет сетевую задержку npx при каждом старте пайплайна.

    Явный ``agent_config["mcp"]["servers"]`` не перезаписывается.
    """
    ac = copy.deepcopy(agent_config)
    existing = ac.get("mcp")
    if isinstance(existing, dict):
        ac["mcp"] = coerce_mcp_config_dict(dict(existing))
        if ac["mcp"].get("servers"):
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
        auto = _truthy_env("SWARM_MCP_AUTO", "1")
    if not auto:
        return ac

    wr = (workspace_root or "").strip()
    if not wr:
        return ac

    wr_abs = str(Path(wr).expanduser().resolve())
    if not Path(wr_abs).is_dir():
        return ac

    servers: list[dict[str, Any]] = []

    # Попытка найти/установить бинарь без npx-overhead.
    mcp_bin = _ensure_mcp_filesystem_bin()

    if mcp_bin:
        # Прямой запуск: node не делает сетевых запросов → старт < 1 с.
        servers.append({
            "name": "workspace",
            "command": mcp_bin,
            "args": [wr_abs],
        })
    else:
        # Fallback: npx — медленнее (сетевая проверка версии), но работает.
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
        else:
            logger.warning(
                "MCP auto: filesystem server skipped (npm/npx not found). "
                "Other MCP tools may still be available."
            )

    brave_api_key = (os.getenv("SWARM_BRAVE_SEARCH_API_KEY") or "").strip()
    if not brave_api_key:
        brave_api_key = str(swarm.get("brave_search_api_key") or "").strip()
    if brave_api_key:
        from backend.App.integrations.infrastructure.mcp.web_search.brave_search_mcp import (
            brave_search_mcp_config,
        )
        servers.append(brave_search_mcp_config(brave_api_key))
        logger.info("MCP auto: web_search_provider=brave (SWARM_BRAVE_SEARCH_API_KEY is set)")
        os.environ.pop("_DDG_SEARCH_ENABLED", None)
    else:
        from backend.App.integrations.infrastructure.mcp.web_search.ddg_search import (
            ddg_search_available,
        )
        if ddg_search_available():
            os.environ["_DDG_SEARCH_ENABLED"] = "1"
            logger.info(
                "MCP auto: web_search_provider=duckduckgo (no API key, DDG package available)"
            )
        else:
            os.environ.pop("_DDG_SEARCH_ENABLED", None)
            logger.warning(
                "MCP auto: web_search_provider=none — no Brave key set and "
                "duckduckgo-search package not installed. "
                "Install 'duckduckgo-search' or set SWARM_BRAVE_SEARCH_API_KEY."
            )

    # ── fetch_page builtin tool ──────────────────────────────────────────
    _fetch_page_flag = swarm.get("fetch_page")
    if _fetch_page_flag is None:
        _fetch_page_flag = _truthy_env("SWARM_FETCH_PAGE", "1")
    if _fetch_page_flag:
        from backend.App.integrations.infrastructure.mcp.web_search.fetch_page import (
            fetch_page_available,
        )
        if fetch_page_available():
            os.environ["_FETCH_PAGE_ENABLED"] = "1"
            logger.info("MCP auto: fetch_page=enabled (httpx available)")
        else:
            os.environ.pop("_FETCH_PAGE_ENABLED", None)
            logger.warning("MCP auto: fetch_page=disabled (httpx not installed)")
    else:
        os.environ.pop("_FETCH_PAGE_ENABLED", None)

    # ── git MCP server (read-only) ────────────────────────────────────────
    _git_flag = swarm.get("git_mcp")
    if _git_flag is None:
        _git_flag = _truthy_env("SWARM_GIT_MCP", "1")
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
            else:
                logger.error(
                    "MCP auto: git=UNAVAILABLE — 'uvx' not found. "
                    "Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh"
                )
        else:
            logger.debug("MCP auto: git=skipped (no .git in workspace)")

    # ── Context7 docs/RAG ─────────────────────────────────────────────────
    _ctx7_flag = swarm.get("context7")
    if _ctx7_flag is None:
        _ctx7_flag = _truthy_env("SWARM_CONTEXT7", "0")
    if _ctx7_flag:
        from backend.App.integrations.infrastructure.mcp.docs.context7_mcp import (
            context7_mcp_config,
        )
        servers.append(context7_mcp_config())
        logger.info("MCP auto: context7=enabled")

    # ── GitHub MCP server ─────────────────────────────────────────────────
    _gh_flag = swarm.get("github_mcp")
    if _gh_flag is None:
        _gh_flag = _truthy_env("SWARM_GITHUB_MCP", "0")
    if _gh_flag:
        from backend.App.integrations.infrastructure.mcp.github.github_mcp import (
            github_token,
            github_mcp_config,
        )
        _gh_token = github_token()
        if _gh_token:
            servers.append(github_mcp_config(_gh_token))
            logger.info("MCP auto: github=enabled (GITHUB_TOKEN set)")
        else:
            logger.warning(
                "MCP auto: github=requested but no GITHUB_TOKEN or "
                "GITHUB_PERSONAL_ACCESS_TOKEN found"
            )

    ac["mcp"] = {"servers": servers, "auto": True}
    return ac
