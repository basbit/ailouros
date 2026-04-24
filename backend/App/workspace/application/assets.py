from __future__ import annotations

import hashlib
import json
import mimetypes
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from backend.App.shared.infrastructure.app_config_load import load_app_config_json
from backend.App.workspace.infrastructure.patch_parser import safe_relative_path


def collect_asset_requests(pipeline_snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    requests_by_path: dict[str, dict[str, Any]] = {}

    def collect(container: Any) -> None:
        if not isinstance(container, dict):
            return
        for request in container.get("asset_requests") or []:
            if isinstance(request, dict) and request.get("path"):
                requests_by_path[str(request["path"])] = dict(request)
        for path in container.get("binary_assets_requested") or []:
            if path:
                requests_by_path.setdefault(
                    str(path),
                    {
                        "path": str(path),
                        "source": "upload",
                        "url": "",
                        "prompt": "",
                        "license": "",
                        "provenance": "binary_text_tag_rejected",
                    },
                )

    collect(pipeline_snapshot.get("workspace_writes"))
    for item in pipeline_snapshot.get("workspace_writes_incremental") or []:
        collect(item)
    return [requests_by_path[path] for path in sorted(requests_by_path)]


def build_asset_manifest_entries(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for request in requests:
        source = str(request.get("source") or "upload").strip().lower()
        status = {
            "download": "pending_download",
            "generate": "pending_generation",
            "upload": "pending_upload",
        }.get(source, "blocked_unknown_source")
        entries.append({
            "path": str(request.get("path") or ""),
            "source": source,
            "status": status,
            "url": str(request.get("url") or ""),
            "prompt": str(request.get("prompt") or ""),
            "license": str(request.get("license") or ""),
            "provenance": str(request.get("provenance") or ""),
        })
    return entries


def resolve_download_assets(workspace_root: Path, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    policy = load_app_config_json("asset_pipeline_policy.json")
    max_download_bytes = int(policy["max_download_bytes"])
    timeout_seconds = int(policy["download_timeout_seconds"])
    allowed_prefixes = tuple(str(prefix) for prefix in policy["allowed_content_type_prefixes"])
    resolved_entries: list[dict[str, Any]] = []
    for entry in entries:
        item = dict(entry)
        if item.get("source") != "download":
            resolved_entries.append(item)
            continue
        url = str(item.get("url") or "").strip()
        license_name = str(item.get("license") or "").strip()
        if not url or not license_name:
            item["status"] = "blocked_missing_download_provenance"
            resolved_entries.append(item)
            continue
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "agent-swarm-asset-pipeline"})
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip()
                content_length = int(response.headers.get("Content-Length") or "0")
                if content_length > max_download_bytes:
                    item["status"] = "blocked_download_too_large"
                    resolved_entries.append(item)
                    continue
                body = response.read(max_download_bytes + 1)
            if len(body) > max_download_bytes:
                item["status"] = "blocked_download_too_large"
                resolved_entries.append(item)
                continue
            guessed_type = content_type or mimetypes.guess_type(item["path"])[0] or "application/octet-stream"
            if not guessed_type.startswith(allowed_prefixes):
                item["status"] = "blocked_content_type"
                item["content_type"] = guessed_type
                resolved_entries.append(item)
                continue
            destination = safe_relative_path(workspace_root, str(item["path"]))
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(body)
            item["status"] = "ready"
            item["content_type"] = guessed_type
            item["sha256"] = hashlib.sha256(body).hexdigest()
            item["bytes"] = len(body)
        except (OSError, urllib.error.URLError, ValueError) as error:
            item["status"] = "blocked_download_failed"
            item["error"] = str(error)
        resolved_entries.append(item)
    return resolved_entries


def build_asset_manifest(pipeline_snapshot: dict[str, Any], workspace_root: Path | None = None) -> dict[str, Any]:
    existing = pipeline_snapshot.get("asset_manifest")
    if isinstance(existing, dict) and existing.get("requested_assets"):
        return existing
    requests = collect_asset_requests(pipeline_snapshot)
    entries = build_asset_manifest_entries(requests)
    if workspace_root is not None:
        entries = resolve_download_assets(workspace_root, entries)
    blocked = [entry for entry in entries if entry.get("status") != "ready"]
    return {
        "schema": "swarm_asset_manifest/v1",
        "status": "ready" if entries and not blocked else ("blocked" if entries else "not_required"),
        "requested_assets": entries,
    }


def write_workspace_asset_manifest(workspace_root: Path, manifest: dict[str, Any]) -> Path | None:
    if not manifest.get("requested_assets"):
        return None
    policy = load_app_config_json("asset_pipeline_policy.json")
    relative_path = str(policy["manifest_relative_path"])
    manifest_path = safe_relative_path(workspace_root, relative_path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path
