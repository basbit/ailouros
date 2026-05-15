from __future__ import annotations

import hashlib
import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.App.plugins.infrastructure.registry_client import (
    BlobIntegrityError,
    RegistryFetchError,
    download_blob,
    fetch_registry,
)


def _registry_payload() -> bytes:
    return json.dumps({
        "registry_id": "test",
        "registry_url": "https://example.com/registry.json",
        "updated_at": "2026-05-14",
        "plugins": [],
    }).encode()


def _mock_urlopen(data: bytes, status: int = 200):
    resp = MagicMock()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    resp.getcode.return_value = status
    resp.read.return_value = data
    return resp


def test_fetch_registry_success():
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(_registry_payload())):
        listing = fetch_registry("https://example.com/registry.json")
    assert listing.registry_id == "test"


def test_fetch_registry_http_error():
    err = urllib.error.HTTPError(
        "https://example.com/registry.json", 404, "Not Found", {}, None
    )
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(RegistryFetchError, match="404"):
            fetch_registry("https://example.com/registry.json")


def test_fetch_registry_url_error():
    err = urllib.error.URLError("Connection refused")
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(RegistryFetchError, match="cannot reach"):
            fetch_registry("https://example.com/registry.json")


def test_fetch_registry_invalid_json():
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(b"not json{")):
        with pytest.raises(RegistryFetchError, match="invalid JSON structure"):
            fetch_registry("https://example.com/registry.json")


def test_fetch_registry_non_200_status():
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(b"err", status=500)):
        with pytest.raises(RegistryFetchError, match="HTTP 500"):
            fetch_registry("https://example.com/registry.json")


def test_download_blob_success(tmp_path: Path):
    blob_data = b"tarball content"
    sha256 = hashlib.sha256(blob_data).hexdigest()
    dest = tmp_path / "plugin.tar.gz"

    with patch("urllib.request.urlopen", return_value=_mock_urlopen(blob_data)):
        download_blob("https://example.com/plugin.tar.gz", sha256, dest)

    assert dest.read_bytes() == blob_data


def test_download_blob_sha256_mismatch(tmp_path: Path):
    blob_data = b"tarball content"
    dest = tmp_path / "plugin.tar.gz"

    with patch("urllib.request.urlopen", return_value=_mock_urlopen(blob_data)):
        with pytest.raises(BlobIntegrityError, match="SHA-256 mismatch"):
            download_blob("https://example.com/plugin.tar.gz", "deadbeef", dest)


def test_download_blob_http_error(tmp_path: Path):
    dest = tmp_path / "plugin.tar.gz"
    err = urllib.error.HTTPError(
        "https://example.com/plugin.tar.gz", 503, "Service Unavailable", {}, None
    )
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(RegistryFetchError, match="503"):
            download_blob("https://example.com/plugin.tar.gz", "abc", dest)


def test_download_blob_url_error(tmp_path: Path):
    dest = tmp_path / "plugin.tar.gz"
    err = urllib.error.URLError("timeout")
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(RegistryFetchError, match="cannot reach"):
            download_blob("https://example.com/plugin.tar.gz", "abc", dest)


def test_download_blob_creates_parent_dir(tmp_path: Path):
    blob_data = b"data"
    sha256 = hashlib.sha256(blob_data).hexdigest()
    dest = tmp_path / "subdir" / "plugin.tar.gz"

    with patch("urllib.request.urlopen", return_value=_mock_urlopen(blob_data)):
        download_blob("https://example.com/x.tar.gz", sha256, dest)

    assert dest.exists()
