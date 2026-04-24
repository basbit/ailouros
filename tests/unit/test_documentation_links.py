"""Tests for backend/App/integrations/infrastructure/documentation_links.py."""
from __future__ import annotations

from backend.App.integrations.infrastructure.documentation_links import (
    _format_fetched_manifest_block,
    format_documentation_links_block,
    iter_documentation_sources,
)


# ---------------------------------------------------------------------------
# iter_documentation_sources
# ---------------------------------------------------------------------------

def test_iter_sources_empty():
    result = iter_documentation_sources({})
    assert result == []


def test_iter_sources_string_list():
    sw = {"documentation_sources": ["https://docs.example.com", "https://api.example.com"]}
    result = iter_documentation_sources(sw)
    assert len(result) == 2
    urls = [r[0] for r in result]
    assert "https://docs.example.com" in urls


def test_iter_sources_dict_items():
    sw = {
        "documentation_sources": [
            {"url": "https://docs.example.com", "title": "Main Docs", "note": "Important"},
        ]
    }
    result = iter_documentation_sources(sw)
    assert len(result) == 1
    assert result[0][0] == "https://docs.example.com"
    assert result[0][1] == "Main Docs"
    assert result[0][2] == "Important"


def test_iter_sources_filters_non_http():
    sw = {"documentation_sources": ["ftp://invalid.com", "https://valid.com"]}
    result = iter_documentation_sources(sw)
    assert len(result) == 1
    assert result[0][0] == "https://valid.com"


def test_iter_sources_deduplicates():
    sw = {
        "documentation_sources": [
            "https://docs.example.com",
            "https://docs.example.com",  # duplicate
        ]
    }
    result = iter_documentation_sources(sw)
    assert len(result) == 1


def test_iter_sources_documentation_urls_key():
    sw = {"documentation_urls": ["https://api.example.com/v2"]}
    result = iter_documentation_sources(sw)
    assert len(result) == 1
    assert result[0][0] == "https://api.example.com/v2"


def test_iter_sources_doc_links_key():
    sw = {"doc_links": ["https://changelog.example.com"]}
    result = iter_documentation_sources(sw)
    assert len(result) == 1


def test_iter_sources_documentation_urls_dict():
    sw = {
        "documentation_urls": [
            {"url": "https://example.com", "title": "Example", "note": "See also"},
        ]
    }
    result = iter_documentation_sources(sw)
    assert len(result) == 1
    assert result[0][1] == "Example"


def test_iter_sources_filters_empty_url():
    sw = {"documentation_sources": ["", None, "https://valid.com"]}
    result = iter_documentation_sources(sw)
    urls = [r[0] for r in result]
    assert "" not in urls
    assert "https://valid.com" in urls


def test_iter_sources_dict_with_href():
    sw = {
        "documentation_sources": [
            {"href": "https://example.com/api", "title": "API"}
        ]
    }
    result = iter_documentation_sources(sw)
    assert len(result) == 1
    assert result[0][0] == "https://example.com/api"


def test_iter_sources_dict_with_description():
    sw = {
        "documentation_sources": [
            {"url": "https://example.com", "description": "Main docs"}
        ]
    }
    result = iter_documentation_sources(sw)
    assert result[0][2] == "Main docs"


# ---------------------------------------------------------------------------
# _format_fetched_manifest_block
# ---------------------------------------------------------------------------

def test_format_fetched_manifest_block_basic():
    manifest = [
        {"url": "https://docs.example.com", "title": "Docs", "ok": True}
    ]
    result = _format_fetched_manifest_block(manifest)
    assert "https://docs.example.com" in result
    assert "Docs" in result
    assert "SWARM_DOC_FETCH" in result


def test_format_fetched_manifest_with_workspace_path():
    manifest = [
        {
            "url": "https://docs.example.com",
            "title": "Docs",
            "workspace_rel_path": "docs/external/docs.txt",
            "ok": True,
        }
    ]
    result = _format_fetched_manifest_block(manifest)
    assert "docs/external/docs.txt" in result
    assert "MCP" in result


def test_format_fetched_manifest_with_artifact_dir():
    manifest = [
        {
            "url": "https://docs.example.com",
            "artifact_dir": "/tmp/artifacts/docs",
            "ok": True,
        }
    ]
    result = _format_fetched_manifest_block(manifest)
    assert "/tmp/artifacts/docs" in result


def test_format_fetched_manifest_with_error():
    manifest = [
        {
            "url": "https://docs.example.com",
            "ok": False,
            "error": "timeout",
        }
    ]
    result = _format_fetched_manifest_block(manifest)
    assert "timeout" in result


def test_format_fetched_manifest_no_title():
    manifest = [{"url": "https://docs.example.com", "ok": True}]
    result = _format_fetched_manifest_block(manifest)
    assert "https://docs.example.com" in result


# ---------------------------------------------------------------------------
# format_documentation_links_block
# ---------------------------------------------------------------------------

def test_format_documentation_links_block_empty():
    result = format_documentation_links_block({})
    assert result == ""


def test_format_documentation_links_block_with_sources():
    sw = {"documentation_sources": ["https://docs.example.com"]}
    result = format_documentation_links_block(sw)
    assert "https://docs.example.com" in result
    assert "документация" in result.lower() or "URL" in result


def test_format_documentation_links_block_with_title():
    sw = {
        "documentation_sources": [
            {"url": "https://docs.example.com", "title": "Main Docs"}
        ]
    }
    result = format_documentation_links_block(sw)
    assert "Main Docs" in result
    assert "https://docs.example.com" in result


def test_format_documentation_links_block_with_note():
    sw = {
        "documentation_sources": [
            {"url": "https://docs.example.com", "note": "Check changelog"}
        ]
    }
    result = format_documentation_links_block(sw)
    assert "Check changelog" in result


def test_format_documentation_links_block_with_fetched_manifest():
    sw = {"documentation_sources": ["https://docs.example.com"]}
    manifest = [{"url": "https://docs.example.com", "title": "Fetched", "ok": True}]
    result = format_documentation_links_block(sw, fetched_manifest=manifest)
    assert "Fetched" in result
    assert "SWARM_DOC_FETCH" in result


def test_format_documentation_links_block_manifest_only():
    manifest = [{"url": "https://docs.example.com", "ok": True}]
    result = format_documentation_links_block({}, fetched_manifest=manifest)
    assert "https://docs.example.com" in result


def test_format_documentation_links_block_returns_string():
    sw = {"documentation_sources": ["https://docs.example.com"]}
    result = format_documentation_links_block(sw)
    assert isinstance(result, str)
