"""Tests for the multi-provider web search router (Tavily / Exa / ScrapingDog)."""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# select_provider
# ---------------------------------------------------------------------------

def test_no_keys_returns_none():
    from backend.App.integrations.infrastructure.mcp.web_search.web_search_router import select_provider
    provider, key = select_provider({"tavily": "", "exa": "", "scrapingdog": ""})
    assert provider is None
    assert key is None


def test_single_key_selected():
    from backend.App.integrations.infrastructure.mcp.web_search.web_search_router import select_provider
    with patch.dict(os.environ, {}, clear=False):
        provider, key = select_provider({"tavily": "tvly-abc", "exa": "", "scrapingdog": ""})
    assert provider == "tavily"
    assert key == "tvly-abc"


def test_rotation_picks_lowest_usage(tmp_path, monkeypatch):
    """Provider with lower usage is preferred."""
    from backend.App.integrations.infrastructure.mcp.web_search import web_search_router as wsr

    counts_file = tmp_path / "web_search_counts.json"
    month = wsr._month_key()
    counts_file.write_text(json.dumps({month: {"tavily": 500, "exa": 10}}))
    monkeypatch.setattr(wsr, "_COUNTS_FILE", counts_file)

    provider, _ = wsr.select_provider({"tavily": "tvly-key", "exa": "exa-key", "scrapingdog": ""})
    assert provider == "exa"  # lower usage


def test_paid_fallback_when_all_at_limit(tmp_path, monkeypatch):
    """Falls back to first provider (paid mode) when all hit 1000."""
    from backend.App.integrations.infrastructure.mcp.web_search import web_search_router as wsr

    counts_file = tmp_path / "web_search_counts.json"
    month = wsr._month_key()
    counts_file.write_text(json.dumps({month: {"tavily": 1000, "exa": 1000}}))
    monkeypatch.setattr(wsr, "_COUNTS_FILE", counts_file)

    provider, key = wsr.select_provider({"tavily": "tvly-key", "exa": "exa-key", "scrapingdog": ""})
    assert provider == "tavily"  # first in priority order
    assert key == "tvly-key"


# ---------------------------------------------------------------------------
# web_search_available
# ---------------------------------------------------------------------------

def test_available_with_key():
    from backend.App.integrations.infrastructure.mcp.web_search.web_search_router import web_search_available
    assert web_search_available({"tavily": "key", "exa": "", "scrapingdog": ""}) is True


def test_not_available_without_keys():
    from backend.App.integrations.infrastructure.mcp.web_search.web_search_router import web_search_available
    assert web_search_available({"tavily": "", "exa": "", "scrapingdog": ""}) is False


def test_available_from_env(monkeypatch):
    from backend.App.integrations.infrastructure.mcp.web_search import web_search_router as wsr
    monkeypatch.setenv("SWARM_TAVILY_API_KEY", "tvly-env-key")
    assert wsr.web_search_available() is True


# ---------------------------------------------------------------------------
# Monthly counter increment
# ---------------------------------------------------------------------------

def test_increment_creates_file(tmp_path, monkeypatch):
    from backend.App.integrations.infrastructure.mcp.web_search import web_search_router as wsr

    counts_file = tmp_path / "web_search_counts.json"
    monkeypatch.setattr(wsr, "_COUNTS_FILE", counts_file)

    wsr._increment("tavily")

    data = json.loads(counts_file.read_text())
    month = wsr._month_key()
    assert data[month]["tavily"] == 1


def test_increment_accumulates(tmp_path, monkeypatch):
    from backend.App.integrations.infrastructure.mcp.web_search import web_search_router as wsr

    counts_file = tmp_path / "web_search_counts.json"
    monkeypatch.setattr(wsr, "_COUNTS_FILE", counts_file)

    wsr._increment("exa")
    wsr._increment("exa")
    wsr._increment("exa")

    data = json.loads(counts_file.read_text())
    month = wsr._month_key()
    assert data[month]["exa"] == 3


# ---------------------------------------------------------------------------
# auto.py integration: web_search_router enabled, DDG not set
# ---------------------------------------------------------------------------

def test_auto_sets_web_search_enabled(monkeypatch, tmp_path):
    """When Tavily key is set, auto.py sets _WEB_SEARCH_ENABLED=1."""
    monkeypatch.setenv("SWARM_MCP_AUTO", "1")
    monkeypatch.setenv("SWARM_TAVILY_API_KEY", "tvly-key")
    monkeypatch.delenv("SWARM_EXA_API_KEY", raising=False)
    monkeypatch.delenv("SWARM_SCRAPINGDOG_API_KEY", raising=False)

    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    with patch(
        "backend.App.integrations.infrastructure.mcp.auto.auto._ensure_mcp_filesystem_bin",
        return_value=None,
    ), patch("shutil.which", return_value="/usr/bin/npx"):
        from backend.App.integrations.infrastructure.mcp.auto.auto import (
            apply_auto_mcp_to_agent_config,
        )
        apply_auto_mcp_to_agent_config({"swarm": {}}, workspace_root=str(workspace_dir))

    assert os.environ.get("_WEB_SEARCH_ENABLED") == "1"
    assert not os.environ.get("_DDG_SEARCH_ENABLED")


def test_auto_no_keys_no_ddg(monkeypatch, tmp_path):
    """With no search keys and no DDG package, neither flag is set."""
    monkeypatch.setenv("SWARM_MCP_AUTO", "1")
    monkeypatch.delenv("SWARM_TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("SWARM_EXA_API_KEY", raising=False)
    monkeypatch.delenv("SWARM_SCRAPINGDOG_API_KEY", raising=False)
    monkeypatch.delenv("_WEB_SEARCH_ENABLED", raising=False)
    monkeypatch.delenv("_DDG_SEARCH_ENABLED", raising=False)

    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    with patch(
        "backend.App.integrations.infrastructure.mcp.auto.auto._ensure_mcp_filesystem_bin",
        return_value=None,
    ), patch("shutil.which", return_value="/usr/bin/npx"), patch(
        "backend.App.integrations.infrastructure.mcp.web_search.ddg_search.ddg_search_available",
        return_value=False,
    ):
        from backend.App.integrations.infrastructure.mcp.auto.auto import (
            apply_auto_mcp_to_agent_config,
        )
        apply_auto_mcp_to_agent_config({"swarm": {}}, workspace_root=str(workspace_dir))

    assert not os.environ.get("_WEB_SEARCH_ENABLED")
    assert not os.environ.get("_DDG_SEARCH_ENABLED")
