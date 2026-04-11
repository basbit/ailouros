"""Ссылки на внешнюю документацию (swarm) — без LangGraph."""

from backend.App.integrations.infrastructure.documentation_links import (
    format_documentation_links_block,
    iter_documentation_sources,
)


def test_iter_documentation_sources_empty():
    assert iter_documentation_sources({}) == []


def test_iter_documentation_sources_skips_non_http():
    rows = iter_documentation_sources(
        {
            "documentation_sources": [
                {"url": "https://a.example/x", "title": "A"},
                {"url": "file:///etc/passwd"},
                "https://b.example/",
            ]
        }
    )
    assert len(rows) == 2
    assert rows[0][0] == "https://a.example/x"
    assert rows[0][1] == "A"
    assert rows[1][0] == "https://b.example/"


def test_iter_documentation_urls_alias():
    rows = iter_documentation_sources(
        {"documentation_urls": ["https://u.example/", "https://u.example/"]}
    )
    assert len(rows) == 1


def test_format_documentation_links_block():
    text = format_documentation_links_block(
        {
            "documentation_sources": [
                {"url": "https://docs.python.org/3/", "title": "Python 3"},
            ]
        }
    )
    assert "[External documentation" in text
    assert "Python 3" in text
    assert "https://docs.python.org/3/" in text
