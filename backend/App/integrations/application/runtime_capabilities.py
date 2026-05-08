from __future__ import annotations

import logging
import os
import shutil
from dataclasses import asdict, dataclass
from typing import Any

from backend.App.shared.infrastructure.app_config_load import load_app_config_json

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CapabilityProbe:
    name: str
    ready: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _bin_present(name: str) -> bool:
    return shutil.which(name) is not None


def _env_truthy(name: str) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _bool_setting(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _configured_media_providers() -> list[tuple[str, str]]:
    raw = load_app_config_json("runtime_capabilities.json").get("media_provider_api_keys")
    if not isinstance(raw, list):
        return []
    providers: list[tuple[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        env_key = str(item.get("env_key") or "").strip()
        if name and env_key:
            providers.append((name, env_key))
    return providers


def probe_capabilities() -> list[CapabilityProbe]:
    probes: list[CapabilityProbe] = []

    desktop_mode = _env_truthy("AILOUROS_DESKTOP")

    workspace_write_allowed = _env_truthy("SWARM_ALLOW_WORKSPACE_WRITE")
    if workspace_write_allowed:
        workspace_detail = "SWARM_ALLOW_WORKSPACE_WRITE=1"
    elif desktop_mode:
        workspace_detail = (
            "Desktop runtime did not set SWARM_ALLOW_WORKSPACE_WRITE=1; "
            "restart the app or report the issue."
        )
    else:
        workspace_detail = "set SWARM_ALLOW_WORKSPACE_WRITE=1 to enable file writes"
    probes.append(CapabilityProbe(
        name="workspace_write",
        ready=workspace_write_allowed,
        detail=workspace_detail,
    ))

    command_exec_allowed = _env_truthy("SWARM_ALLOW_COMMAND_EXEC")
    if command_exec_allowed:
        command_detail = "SWARM_ALLOW_COMMAND_EXEC=1"
    elif desktop_mode:
        command_detail = (
            "Desktop runtime did not set SWARM_ALLOW_COMMAND_EXEC=1; "
            "shell verification will fail."
        )
    else:
        command_detail = "set SWARM_ALLOW_COMMAND_EXEC=1 to allow shell verification"
    probes.append(CapabilityProbe(
        name="command_exec",
        ready=command_exec_allowed,
        detail=command_detail,
    ))

    sudo_prompt_allowed = _env_truthy("SWARM_ALLOW_SUDO_PROMPT")
    probes.append(CapabilityProbe(
        name="sudo_prompt",
        ready=sudo_prompt_allowed,
        detail="SWARM_ALLOW_SUDO_PROMPT=1" if sudo_prompt_allowed
        else "manual-execution fallback only",
    ))

    npx_present = _bin_present("npx")
    probes.append(CapabilityProbe(
        name="mcp_filesystem_via_npx",
        ready=npx_present,
        detail="npx found on PATH" if npx_present else "install Node/npx to auto-enable MCP",
    ))

    git_present = _bin_present("git")
    probes.append(CapabilityProbe(
        name="git",
        ready=git_present,
        detail="git found on PATH" if git_present else "git not on PATH",
    ))

    has_search_key = any(_env_truthy(name) for name in (
        "SWARM_TAVILY_API_KEY",
        "SWARM_EXA_API_KEY",
        "SWARM_SCRAPINGDOG_API_KEY",
    )) or any(
        (os.getenv(name) or "").strip()
        for name in (
            "SWARM_TAVILY_API_KEY",
            "SWARM_EXA_API_KEY",
            "SWARM_SCRAPINGDOG_API_KEY",
        )
    )
    probes.append(CapabilityProbe(
        name="web_search_key",
        ready=has_search_key,
        detail="at least one search API key configured" if has_search_key
        else "no SWARM_*_API_KEY for web search configured",
    ))

    playwright_present = _bin_present("playwright")
    probes.append(CapabilityProbe(
        name="visual_probe_playwright",
        ready=playwright_present,
        detail="playwright on PATH" if playwright_present else "playwright not installed",
    ))

    require_writes_block = _bool_setting("SWARM_REQUIRE_DEV_WRITES", default=True)
    probes.append(CapabilityProbe(
        name="require_dev_writes_block",
        ready=require_writes_block,
        detail="zero-write runs fail by default" if require_writes_block
        else "zero-write runs are tolerated (legacy mode)",
    ))

    require_trusted_gates = _bool_setting("SWARM_REQUIRE_TRUSTED_GATES_PASS", default=True)
    probes.append(CapabilityProbe(
        name="require_trusted_gates_block",
        ready=require_trusted_gates,
        detail="failed trusted gates fail the run" if require_trusted_gates
        else "failed trusted gates surface as warnings only (legacy mode)",
    ))

    for probe_name, env_key in _configured_media_providers():
        configured = bool((os.getenv(env_key) or "").strip())
        probes.append(CapabilityProbe(
            name=probe_name,
            ready=configured,
            detail=f"{env_key}=set" if configured else f"set {env_key} to enable",
        ))

    return probes


def summarize() -> dict[str, Any]:
    probes = probe_capabilities()
    ready_count = sum(1 for probe in probes if probe.ready)
    return {
        "probes": [probe.to_dict() for probe in probes],
        "ready": ready_count,
        "total": len(probes),
    }
