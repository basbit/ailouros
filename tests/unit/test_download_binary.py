from unittest.mock import MagicMock, patch

from backend.App.integrations.infrastructure.mcp.web_search.download_binary import (
    download_binary_available,
    download_to_workspace,
)


def test_download_binary_available():
    assert download_binary_available() in (True, False)


def test_workspace_root_missing_returns_error(tmp_path):
    result = download_to_workspace(
        url="https://example.com/file.png",
        workspace_root="",
        relative_target_path="Assets/x.png",
    )
    assert result["status"] == "skipped"
    assert result["error"] == "workspace_root_missing"


def test_workspace_root_not_directory_returns_error(tmp_path):
    nonexistent = tmp_path / "does_not_exist"
    result = download_to_workspace(
        url="https://example.com/file.png",
        workspace_root=str(nonexistent),
        relative_target_path="Assets/x.png",
    )
    assert result["status"] == "skipped"
    assert result["error"] == "workspace_root_not_directory"


def test_relative_path_traversal_blocked(tmp_path):
    result = download_to_workspace(
        url="https://example.com/file.png",
        workspace_root=str(tmp_path),
        relative_target_path="../escape.png",
    )
    assert result["status"] == "skipped"
    assert result["error"] == "path_traversal_blocked"


def test_non_http_url_blocked(tmp_path):
    result = download_to_workspace(
        url="file:///etc/passwd",
        workspace_root=str(tmp_path),
        relative_target_path="Assets/x.png",
    )
    assert result["status"] == "skipped"
    assert result["error"] == "non_http_scheme"


def test_empty_url_blocked(tmp_path):
    result = download_to_workspace(
        url="",
        workspace_root=str(tmp_path),
        relative_target_path="Assets/x.png",
    )
    assert result["status"] == "skipped"


def test_download_writes_file_with_acceptable_mime(tmp_path):
    fake_response = MagicMock()
    fake_response.headers = {"content-type": "image/png"}
    fake_response.content = b"\x89PNG\r\n\x1a\nfake"
    fake_response.raise_for_status = MagicMock()

    fake_client = MagicMock()
    fake_client.__enter__ = lambda self: self
    fake_client.__exit__ = lambda *a: None
    fake_client.get = MagicMock(return_value=fake_response)

    with patch(
        "backend.App.integrations.infrastructure.mcp.web_search.download_binary.download_binary_available",
        return_value=True,
    ), patch("httpx.Client", return_value=fake_client):
        result = download_to_workspace(
            url="https://example.com/sprite.png",
            workspace_root=str(tmp_path),
            relative_target_path="Assets/Images/sprite.png",
            expected_mime_prefix="image/",
        )

    assert result["status"] == "downloaded"
    assert result["bytes_written"] == len(b"\x89PNG\r\n\x1a\nfake")
    written = (tmp_path / "Assets/Images/sprite.png").read_bytes()
    assert written == b"\x89PNG\r\n\x1a\nfake"


def test_download_rejects_mime_mismatch(tmp_path):
    fake_response = MagicMock()
    fake_response.headers = {"content-type": "text/html"}
    fake_response.content = b"<html>blocked</html>"
    fake_response.raise_for_status = MagicMock()

    fake_client = MagicMock()
    fake_client.__enter__ = lambda self: self
    fake_client.__exit__ = lambda *a: None
    fake_client.get = MagicMock(return_value=fake_response)

    with patch(
        "backend.App.integrations.infrastructure.mcp.web_search.download_binary.download_binary_available",
        return_value=True,
    ), patch("httpx.Client", return_value=fake_client):
        result = download_to_workspace(
            url="https://example.com/sprite.png",
            workspace_root=str(tmp_path),
            relative_target_path="Assets/Images/sprite.png",
            expected_mime_prefix="image/",
        )

    assert result["status"] == "skipped"
    assert "mime_mismatch" in result["error"]
    assert not (tmp_path / "Assets/Images/sprite.png").exists()


def test_download_size_limit_enforced(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DOWNLOAD_BINARY_MAX_BYTES", "10")
    fake_response = MagicMock()
    fake_response.headers = {"content-type": "image/png"}
    fake_response.content = b"x" * 100
    fake_response.raise_for_status = MagicMock()

    fake_client = MagicMock()
    fake_client.__enter__ = lambda self: self
    fake_client.__exit__ = lambda *a: None
    fake_client.get = MagicMock(return_value=fake_response)

    with patch(
        "backend.App.integrations.infrastructure.mcp.web_search.download_binary.download_binary_available",
        return_value=True,
    ), patch("httpx.Client", return_value=fake_client):
        result = download_to_workspace(
            url="https://example.com/big.png",
            workspace_root=str(tmp_path),
            relative_target_path="Assets/Images/big.png",
            expected_mime_prefix="image/",
        )

    assert result["status"] == "skipped"
    assert "size_exceeded" in result["error"]
