from backend.App.orchestration.application.nodes._shared import (
    _web_research_guidance_block,
)


def test_returns_empty_when_no_search_enabled(monkeypatch):
    monkeypatch.delenv("_WEB_SEARCH_ENABLED", raising=False)
    monkeypatch.delenv("_DDG_SEARCH_ENABLED", raising=False)
    monkeypatch.delenv("_FETCH_PAGE_ENABLED", raising=False)
    state = {"user_task": "find game theme on the internet"}
    assert _web_research_guidance_block(state, role="pm") == ""


def test_returns_empty_when_neither_role_nor_user_wants_research(monkeypatch):
    monkeypatch.setenv("_WEB_SEARCH_ENABLED", "1")
    state = {"user_task": "fix bug in payment service"}
    assert _web_research_guidance_block(state, role="qa") == ""


def test_includes_web_search_for_pm_role(monkeypatch):
    monkeypatch.setenv("_WEB_SEARCH_ENABLED", "1")
    monkeypatch.delenv("_FETCH_PAGE_ENABLED", raising=False)
    state = {"user_task": "build something"}
    block = _web_research_guidance_block(state, role="pm")
    assert "web_search" in block
    assert "Web research tools available" in block


def test_includes_fetch_page_when_enabled(monkeypatch):
    monkeypatch.setenv("_WEB_SEARCH_ENABLED", "1")
    monkeypatch.setenv("_FETCH_PAGE_ENABLED", "1")
    state = {"user_task": "research game themes online"}
    block = _web_research_guidance_block(state, role="architect")
    assert "fetch_page" in block
    assert "web_search" in block


def test_user_keyword_triggers_block_for_any_role(monkeypatch):
    monkeypatch.setenv("_WEB_SEARCH_ENABLED", "1")
    state = {"user_task": "найди в интернете темы для игры"}
    block = _web_research_guidance_block(state, role="dev")
    assert block != ""
    assert "web_search" in block


def test_block_mentions_creative_commons_for_assets(monkeypatch):
    monkeypatch.setenv("_WEB_SEARCH_ENABLED", "1")
    state = {"user_task": "research game theme"}
    block = _web_research_guidance_block(state, role="image_generator")
    assert "Creative Commons" in block or "license" in block.lower()


def test_block_includes_pii_avoidance(monkeypatch):
    monkeypatch.setenv("_WEB_SEARCH_ENABLED", "1")
    state = {"user_task": "research"}
    block = _web_research_guidance_block(state, role="pm")
    assert "personally-identifiable" in block.lower() or "PII" in block
