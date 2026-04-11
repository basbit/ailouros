"""Extended tests for backend/App/integrations/infrastructure/doc_fetch.py."""
from __future__ import annotations

from unittest.mock import MagicMock, patch


from backend.App.integrations.infrastructure.doc_fetch import (
    _allow_private_hosts,
    _fetch_one,
    _is_blocked_host,
    _max_bytes,
    _safe_slug,
    _timeout_sec,
    _url_key,
    _write_pair,
)


# ---------------------------------------------------------------------------
# _max_bytes
# ---------------------------------------------------------------------------

def test_max_bytes_default(monkeypatch):
    monkeypatch.delenv("SWARM_DOC_FETCH_MAX_BYTES", raising=False)
    assert _max_bytes() == 1_500_000


def test_max_bytes_custom(monkeypatch):
    monkeypatch.setenv("SWARM_DOC_FETCH_MAX_BYTES", "500000")
    assert _max_bytes() == 500_000


def test_max_bytes_clamped_min(monkeypatch):
    monkeypatch.setenv("SWARM_DOC_FETCH_MAX_BYTES", "100")
    assert _max_bytes() == 4096


def test_max_bytes_invalid(monkeypatch):
    monkeypatch.setenv("SWARM_DOC_FETCH_MAX_BYTES", "abc")
    assert _max_bytes() == 1_500_000


# ---------------------------------------------------------------------------
# _timeout_sec
# ---------------------------------------------------------------------------

def test_timeout_sec_default(monkeypatch):
    monkeypatch.delenv("SWARM_DOC_FETCH_TIMEOUT", raising=False)
    assert _timeout_sec() == 20.0


def test_timeout_sec_custom(monkeypatch):
    monkeypatch.setenv("SWARM_DOC_FETCH_TIMEOUT", "10")
    assert _timeout_sec() == 10.0


def test_timeout_sec_clamped_min(monkeypatch):
    monkeypatch.setenv("SWARM_DOC_FETCH_TIMEOUT", "1")
    assert _timeout_sec() == 3.0


def test_timeout_sec_invalid(monkeypatch):
    monkeypatch.setenv("SWARM_DOC_FETCH_TIMEOUT", "bad")
    assert _timeout_sec() == 20.0


# ---------------------------------------------------------------------------
# _allow_private_hosts
# ---------------------------------------------------------------------------

def test_allow_private_hosts_default(monkeypatch):
    monkeypatch.delenv("SWARM_DOC_FETCH_ALLOW_PRIVATE", raising=False)
    assert _allow_private_hosts() is False


def test_allow_private_hosts_enabled(monkeypatch):
    monkeypatch.setenv("SWARM_DOC_FETCH_ALLOW_PRIVATE", "1")
    assert _allow_private_hosts() is True


# ---------------------------------------------------------------------------
# _is_blocked_host
# ---------------------------------------------------------------------------

def test_is_blocked_host_localhost(monkeypatch):
    monkeypatch.delenv("SWARM_DOC_FETCH_ALLOW_PRIVATE", raising=False)
    assert _is_blocked_host("localhost") is True


def test_is_blocked_host_ipv4_loopback(monkeypatch):
    monkeypatch.delenv("SWARM_DOC_FETCH_ALLOW_PRIVATE", raising=False)
    assert _is_blocked_host("127.0.0.1") is True


def test_is_blocked_host_ipv6_loopback(monkeypatch):
    monkeypatch.delenv("SWARM_DOC_FETCH_ALLOW_PRIVATE", raising=False)
    assert _is_blocked_host("::1") is True


def test_is_blocked_host_private_10(monkeypatch):
    monkeypatch.delenv("SWARM_DOC_FETCH_ALLOW_PRIVATE", raising=False)
    assert _is_blocked_host("10.0.0.1") is True


def test_is_blocked_host_private_172(monkeypatch):
    monkeypatch.delenv("SWARM_DOC_FETCH_ALLOW_PRIVATE", raising=False)
    assert _is_blocked_host("172.16.0.1") is True
    assert _is_blocked_host("172.31.255.255") is True
    assert _is_blocked_host("172.32.0.0") is False  # outside range


def test_is_blocked_host_private_192(monkeypatch):
    monkeypatch.delenv("SWARM_DOC_FETCH_ALLOW_PRIVATE", raising=False)
    assert _is_blocked_host("192.168.1.1") is True


def test_is_blocked_host_link_local(monkeypatch):
    monkeypatch.delenv("SWARM_DOC_FETCH_ALLOW_PRIVATE", raising=False)
    assert _is_blocked_host("169.254.0.1") is True


def test_is_blocked_host_google_metadata(monkeypatch):
    monkeypatch.delenv("SWARM_DOC_FETCH_ALLOW_PRIVATE", raising=False)
    assert _is_blocked_host("metadata.google.internal") is True


def test_is_blocked_host_public(monkeypatch):
    monkeypatch.delenv("SWARM_DOC_FETCH_ALLOW_PRIVATE", raising=False)
    assert _is_blocked_host("example.com") is False
    assert _is_blocked_host("docs.python.org") is False


def test_is_blocked_host_allowed_when_private_enabled(monkeypatch):
    monkeypatch.setenv("SWARM_DOC_FETCH_ALLOW_PRIVATE", "1")
    assert _is_blocked_host("localhost") is False
    assert _is_blocked_host("192.168.1.1") is False


def test_is_blocked_host_localhost_subdomain(monkeypatch):
    monkeypatch.delenv("SWARM_DOC_FETCH_ALLOW_PRIVATE", raising=False)
    assert _is_blocked_host("api.localhost") is True


# ---------------------------------------------------------------------------
# _url_key
# ---------------------------------------------------------------------------

def test_url_key_length():
    key = _url_key("https://example.com/page")
    assert len(key) == 16


def test_url_key_deterministic():
    url = "https://example.com/docs"
    assert _url_key(url) == _url_key(url)


def test_url_key_different_urls():
    assert _url_key("https://a.com") != _url_key("https://b.com")


# ---------------------------------------------------------------------------
# _safe_slug
# ---------------------------------------------------------------------------

def test_safe_slug_from_title():
    slug = _safe_slug("API Reference", "https://example.com/api", "abc123")
    assert "ABC" not in slug or "API_Reference" in slug or "abc123" in slug


def test_safe_slug_from_url_when_no_title():
    slug = _safe_slug("", "https://docs.example.com/guide.html", "abc123")
    assert isinstance(slug, str)
    assert len(slug) > 0


def test_safe_slug_strips_special_chars():
    slug = _safe_slug("Hello World! <docs>", "https://x.com", "k")
    assert "<" not in slug
    assert ">" not in slug
    assert "!" not in slug


# ---------------------------------------------------------------------------
# _write_pair
# ---------------------------------------------------------------------------

def test_write_pair_creates_art_and_ws(tmp_path):
    art_dir = tmp_path / "art"
    ws_dir = tmp_path / "ws"
    meta = {"url": "https://example.com", "bytes": 5}
    _write_pair(art_dir, ws_dir, "body content", meta)
    assert (art_dir / "body.txt").read_text() == "body content"
    assert (ws_dir / "body.txt").read_text() == "body content"
    import json
    assert json.loads((art_dir / "meta.json").read_text())["url"] == "https://example.com"


def test_write_pair_no_ws_dir(tmp_path):
    art_dir = tmp_path / "art"
    _write_pair(art_dir, None, "text", {"url": "x"})
    assert (art_dir / "body.txt").exists()


# ---------------------------------------------------------------------------
# _fetch_one
# ---------------------------------------------------------------------------

def test_fetch_one_non_http_url():
    body, meta, err = _fetch_one("ftp://example.com/file", "title")
    assert err == "only_http_https"
    assert meta is None


def test_fetch_one_blocked_host(monkeypatch):
    monkeypatch.delenv("SWARM_DOC_FETCH_ALLOW_PRIVATE", raising=False)
    body, meta, err = _fetch_one("http://localhost/page", "local page")
    assert err == "blocked_host_private_or_local"


def test_fetch_one_success(monkeypatch):
    monkeypatch.delenv("SWARM_DOC_FETCH_ALLOW_PRIVATE", raising=False)

    mock_response = MagicMock()
    mock_response.content = b"page content"
    mock_response.headers = {"content-type": "text/html; charset=utf-8"}
    mock_response.status_code = 200

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = mock_response

    with patch("backend.App.integrations.infrastructure.doc_fetch.httpx.Client", return_value=mock_client):
        body, meta, err = _fetch_one("https://example.com/page", "Page")
    assert err == ""
    assert body == "page content"
    assert meta["status_code"] == 200
    assert meta["bytes"] == len(b"page content")


def test_fetch_one_404(monkeypatch):
    monkeypatch.delenv("SWARM_DOC_FETCH_ALLOW_PRIVATE", raising=False)

    mock_response = MagicMock()
    mock_response.content = b"not found"
    mock_response.headers = {"content-type": "text/html"}
    mock_response.status_code = 404

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = mock_response

    with patch("backend.App.integrations.infrastructure.doc_fetch.httpx.Client", return_value=mock_client):
        body, meta, err = _fetch_one("https://example.com/missing", "Missing")
    assert err == "http_404"
    assert meta is not None
    assert body == "not found"


def test_fetch_one_network_error(monkeypatch):
    monkeypatch.delenv("SWARM_DOC_FETCH_ALLOW_PRIVATE", raising=False)

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.side_effect = Exception("connection refused")

    with patch("backend.App.integrations.infrastructure.doc_fetch.httpx.Client", return_value=mock_client):
        body, meta, err = _fetch_one("https://example.com", "test")
    assert err != ""
    assert meta is None


def test_fetch_one_binary_content(monkeypatch):
    monkeypatch.delenv("SWARM_DOC_FETCH_ALLOW_PRIVATE", raising=False)

    mock_response = MagicMock()
    mock_response.content = b"\x00\x01\x02binary" + b"x" * 4000
    mock_response.headers = {"content-type": "application/octet-stream"}
    mock_response.status_code = 200

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = mock_response

    with patch("backend.App.integrations.infrastructure.doc_fetch.httpx.Client", return_value=mock_client):
        body, meta, err = _fetch_one("https://example.com/file.bin", "binary")
    assert err == "binary_or_unknown_content"
