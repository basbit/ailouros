"""Загрузка URL из swarm.documentation_sources при SWARM_DOC_FETCH=1.

Кэш: artifacts/<task_id|_no_task>/doc_fetch/<key>/body.txt + meta.json
Зеркало для MCP filesystem: <workspace>/.swarm/doc_fetch/<key>/ (те же файлы).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from backend.App.integrations.infrastructure.documentation_links import iter_documentation_sources

logger = logging.getLogger(__name__)

__all__ = ["doc_fetch_enabled", "run_doc_fetch_if_enabled"]


def doc_fetch_enabled() -> bool:
    v = (os.getenv("SWARM_DOC_FETCH", "0") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _max_bytes() -> int:
    try:
        return max(4096, min(20_000_000, int(os.getenv("SWARM_DOC_FETCH_MAX_BYTES", "1500000"))))
    except ValueError:
        return 1_500_000


def _timeout_sec() -> float:
    try:
        return max(3.0, min(120.0, float(os.getenv("SWARM_DOC_FETCH_TIMEOUT", "20"))))
    except ValueError:
        return 20.0


def _allow_private_hosts() -> bool:
    return (os.getenv("SWARM_DOC_FETCH_ALLOW_PRIVATE", "0") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _is_blocked_host(hostname: str) -> bool:
    if _allow_private_hosts():
        return False
    h = hostname.lower().strip("[]")
    if h in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return True
    if h.endswith(".localhost"):
        return True
    if h == "metadata.google.internal":
        return True
    parts = h.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        a, b = int(parts[0]), int(parts[1])
        if a == 10 or a == 127:
            return True
        if a == 172 and 16 <= b <= 31:
            return True
        if a == 192 and b == 168:
            return True
        if a == 169 and b == 254:
            return True
    return False


def _url_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _safe_slug(title: str, url: str, key: str) -> str:
    base = (title or "").strip() or urlparse(url).path.split("/")[-1] or "page"
    base = re.sub(r"[^\w.\-]+", "_", base, flags=re.UNICODE)[:48].strip("._") or "page"
    return f"{key}_{base}"


def _write_pair(
    art_dir: Path,
    ws_dir: Optional[Path],
    body: str,
    meta: dict[str, Any],
) -> None:
    art_dir.mkdir(parents=True, exist_ok=True)
    (art_dir / "body.txt").write_text(body, encoding="utf-8", errors="replace")
    (art_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if ws_dir is not None:
        ws_dir.mkdir(parents=True, exist_ok=True)
        (ws_dir / "body.txt").write_text(body, encoding="utf-8", errors="replace")
        (ws_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _fetch_one(url: str, title: str) -> tuple[str, Optional[dict[str, Any]], str]:
    """Returns (body_text, meta dict for json, error_message)."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return "", None, "only_http_https"
        host = parsed.hostname
        if not host:
            return "", None, "no_host"
        if _is_blocked_host(host):
            return "", None, "blocked_host_private_or_local"
    except Exception as exc:
        return "", None, f"parse_error:{exc}"

    max_b = _max_bytes()
    timeout = _timeout_sec()
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            r = client.get(
                url,
                headers={"User-Agent": "AIlourOS-doc-fetch/1.0"},
            )
            raw = r.content[:max_b]
            ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
            if b"\x00" in raw[:4000] and "text" not in ctype and "json" not in ctype:
                return "", None, "binary_or_unknown_content"
            text = raw.decode("utf-8", errors="replace")
            meta = {
                "url": url,
                "title": title or "",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "bytes": len(raw),
                "content_type": ctype or None,
                "status_code": r.status_code,
                "truncated": len(r.content) > max_b,
                "error": None,
            }
            if r.status_code >= 400:
                return text, meta, f"http_{r.status_code}"
            return text, meta, ""
    except Exception as exc:
        return "", None, str(exc)[:500]


def run_doc_fetch_if_enabled(
    agent_config: dict[str, Any],
    *,
    workspace_root: str = "",
    task_id: str = "",
) -> list[dict[str, Any]]:
    """Загружает доки в artifacts и (если есть) в workspace/.swarm/doc_fetch/."""
    if not doc_fetch_enabled():
        return []

    sw = agent_config.get("swarm")
    if not isinstance(sw, dict):
        return []

    rows = iter_documentation_sources(sw)
    if not rows:
        return []

    from backend.App.paths import artifacts_root as _anchored_artifacts_root
    art_root = _anchored_artifacts_root()
    tid = (task_id or "").strip() or "_no_task"
    base_art = art_root / tid / "doc_fetch"

    ws_root: Optional[Path] = None
    wr = (workspace_root or "").strip()
    if wr:
        try:
            ws = Path(wr).expanduser().resolve()
            if ws.is_dir():
                ws_root = ws / ".swarm" / "doc_fetch"
        except OSError:
            ws_root = None

    manifest: list[dict[str, Any]] = []

    for url, title, note in rows:
        key = _url_key(url)
        slug = _safe_slug(title, url, key)
        art_dir = base_art / key
        ws_dir = (ws_root / key) if ws_root is not None else None

        body, meta, err = _fetch_one(url, title)
        if meta is None:
            meta_out = {
                "url": url,
                "title": title or "",
                "note": note or "",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "bytes": 0,
                "error": err,
            }
            try:
                art_dir.mkdir(parents=True, exist_ok=True)
                (art_dir / "meta.json").write_text(
                    json.dumps(meta_out, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError as ose:
                logger.warning("doc_fetch meta write failed %s: %s", art_dir, ose)
            rel_ws = f".swarm/doc_fetch/{key}/body.txt" if ws_root is not None else ""
            manifest.append(
                {
                    "url": url,
                    "title": title,
                    "note": note,
                    "ok": False,
                    "error": err,
                    "artifact_dir": str(art_dir),
                    "workspace_rel_path": rel_ws,
                    "slug": slug,
                }
            )
            continue

        meta["note"] = note or ""
        if err:
            meta["error"] = err
        try:
            _write_pair(art_dir, ws_dir, body, meta)
        except OSError as ose:
            logger.warning("doc_fetch write failed %s: %s", art_dir, ose)
            manifest.append(
                {
                    "url": url,
                    "title": title,
                    "note": note,
                    "ok": False,
                    "error": str(ose),
                    "artifact_dir": str(art_dir),
                    "workspace_rel_path": (
                        f".swarm/doc_fetch/{key}/body.txt" if ws_root is not None else ""
                    ),
                    "slug": slug,
                }
            )
            continue

        rel_ws = f".swarm/doc_fetch/{key}/body.txt" if ws_root is not None else ""
        manifest.append(
            {
                "url": url,
                "title": title,
                "note": note,
                "ok": err == "",
                "error": err or None,
                "artifact_dir": str(art_dir),
                "workspace_rel_path": rel_ws,
                "slug": slug,
                "bytes": meta.get("bytes"),
                "status_code": meta.get("status_code"),
            }
        )

    return manifest
