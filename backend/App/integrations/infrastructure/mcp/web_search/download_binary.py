from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_ALLOWED_MIME_PREFIXES: frozenset[str] = frozenset({
    "image/",
    "audio/",
    "video/",
    "font/",
    "application/octet-stream",
    "application/zip",
    "application/x-tar",
    "application/x-7z-compressed",
    "application/json",
    "application/xml",
    "text/plain",
})


def download_binary_available() -> bool:
    try:
        import httpx as _httpx_check
        del _httpx_check
        return True
    except ImportError:
        return False


def _download_timeout_seconds() -> int:
    try:
        return int(os.getenv("SWARM_DOWNLOAD_BINARY_TIMEOUT", "30"))
    except ValueError:
        return 30


def _download_max_bytes() -> int:
    try:
        return int(os.getenv("SWARM_DOWNLOAD_BINARY_MAX_BYTES", str(20 * 1024 * 1024)))
    except ValueError:
        return 20 * 1024 * 1024


def _is_path_under_root(target: Path, root: Path) -> bool:
    try:
        target_resolved = target.resolve()
        root_resolved = root.resolve()
    except (OSError, ValueError):
        return False
    return str(target_resolved).startswith(str(root_resolved))


def _validate_download_inputs(
    *,
    url: str,
    workspace_root: str,
    relative_target_path: str,
) -> tuple[Optional[Path], Optional[str], str, str]:
    if not download_binary_available():
        return (None, "httpx_not_installed", "", "")
    workspace_root_clean = (workspace_root or "").strip()
    if not workspace_root_clean:
        return (None, "workspace_root_missing", "", "")
    workspace_root_path = Path(workspace_root_clean).expanduser()
    if not workspace_root_path.is_dir():
        return (None, "workspace_root_not_directory", "", "")
    relative_clean = (relative_target_path or "").strip().lstrip("/").lstrip("\\")
    if not relative_clean:
        return (None, "relative_target_path_missing", "", "")
    target_path = (workspace_root_path / relative_clean).expanduser()
    if not _is_path_under_root(target_path, workspace_root_path):
        return (None, "path_traversal_blocked", "", "")
    url_clean = (url or "").strip()
    if not url_clean:
        return (None, "url_missing", "", "")
    if not url_clean.startswith(("http://", "https://")):
        return (None, "non_http_scheme", "", "")
    return (target_path, None, url_clean, relative_clean)


def _check_mime_acceptable(
    content_type_header: str,
    expected_mime_prefix: str,
) -> Optional[str]:
    if expected_mime_prefix:
        if not content_type_header.startswith(expected_mime_prefix.strip().lower()):
            return f"mime_mismatch: expected={expected_mime_prefix} got={content_type_header}"
        return None
    if content_type_header:
        allowed = any(
            content_type_header.startswith(prefix)
            for prefix in _ALLOWED_MIME_PREFIXES
        )
        if not allowed:
            return f"mime_not_in_allowlist: {content_type_header}"
    return None


def _fetch_url_bytes(
    url_clean: str,
    timeout_seconds: int,
) -> tuple[Optional[bytes], str, str]:
    import httpx

    try:
        with httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=True,
            max_redirects=5,
        ) as client:
            response = client.get(
                url_clean,
                headers={
                    "User-Agent": "AIlourOS-AssetFetcher/1.0",
                    "Accept": "*/*",
                },
            )
            response.raise_for_status()
            content_type_header = response.headers.get("content-type", "").split(";")[0].strip().lower()
            return (response.content, content_type_header, "")
    except httpx.TimeoutException:
        return (None, "", f"timeout_after_{timeout_seconds}s")
    except httpx.HTTPStatusError as http_error:
        return (None, "", f"http_{http_error.response.status_code}")
    except Exception as fetch_error:
        return (None, "", f"fetch_failed: {fetch_error}")


def _persist_to_target_path(target_path: Path, content_bytes: bytes) -> Optional[str]:
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(content_bytes)
        return None
    except OSError as write_error:
        return f"write_failed: {write_error}"


def download_to_workspace(
    *,
    url: str,
    workspace_root: str,
    relative_target_path: str,
    expected_mime_prefix: str = "",
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "url": url,
        "target_path": relative_target_path,
        "status": "skipped",
        "bytes_written": 0,
        "content_type": "",
        "error": "",
    }
    target_path, validation_error, url_clean, _relative_clean = _validate_download_inputs(
        url=url,
        workspace_root=workspace_root,
        relative_target_path=relative_target_path,
    )
    if validation_error:
        result["error"] = validation_error
        return result
    if target_path is None:
        result["error"] = "target_path_missing"
        return result

    timeout_seconds = _download_timeout_seconds()
    max_bytes = _download_max_bytes()

    content_bytes, content_type_header, fetch_error = _fetch_url_bytes(url_clean, timeout_seconds)
    result["content_type"] = content_type_header
    if fetch_error:
        result["error"] = fetch_error
        return result
    if content_bytes is None:
        result["error"] = "fetch_returned_no_bytes"
        return result

    mime_error = _check_mime_acceptable(content_type_header, expected_mime_prefix)
    if mime_error:
        result["error"] = mime_error
        return result

    if len(content_bytes) > max_bytes:
        result["error"] = f"size_exceeded: {len(content_bytes)} > {max_bytes}"
        return result

    write_error = _persist_to_target_path(target_path, content_bytes)
    if write_error:
        result["error"] = write_error
        return result

    result["status"] = "downloaded"
    result["bytes_written"] = len(content_bytes)
    return result
