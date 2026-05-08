from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()


_ALLOWED_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
    ".pdf", ".md", ".txt", ".csv", ".json",
    ".mp3", ".wav", ".ogg",
    ".mp4", ".webm",
    ".zip",
})

_MAX_BYTES = 25 * 1024 * 1024


def _is_safe_descendant(target: Path, root: Path) -> bool:
    try:
        target.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


@router.post("/v1/assets/upload")
async def upload_asset(
    workspace_root: str = Form(""),
    target_subdir: str = Form("assets"),
    upload: UploadFile = File(...),
    rename_to: Optional[str] = Form(None),
) -> JSONResponse:
    workspace_clean = (workspace_root or "").strip()
    if not workspace_clean:
        raise HTTPException(status_code=400, detail="workspace_root is required")
    workspace_path = Path(workspace_clean).expanduser()
    if not workspace_path.is_dir():
        raise HTTPException(status_code=400, detail="workspace_root is not a directory")

    subdir_clean = (target_subdir or "assets").strip().lstrip("/").lstrip("\\")
    if ".." in subdir_clean.split("/"):
        raise HTTPException(status_code=400, detail="target_subdir must not contain '..'")
    target_dir = workspace_path / subdir_clean
    if not _is_safe_descendant(target_dir, workspace_path):
        raise HTTPException(status_code=400, detail="target_subdir escapes workspace_root")
    target_dir.mkdir(parents=True, exist_ok=True)

    file_name = (rename_to or upload.filename or "asset").strip()
    file_name = file_name.split("/")[-1].split("\\")[-1]
    if not file_name:
        raise HTTPException(status_code=400, detail="empty filename")
    suffix = Path(file_name).suffix.lower()
    if not suffix:
        raise HTTPException(status_code=415, detail="file extension is required")
    if suffix not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"file extension {suffix!r} is not allowed",
        )
    target_path = target_dir / file_name
    if not _is_safe_descendant(target_path, workspace_path):
        raise HTTPException(status_code=400, detail="resolved path escapes workspace_root")

    body = await upload.read()
    if len(body) > _MAX_BYTES:
        raise HTTPException(status_code=413, detail="file exceeds size cap")
    try:
        target_path.write_bytes(body)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"could not write file: {exc}") from exc

    return JSONResponse({
        "ok": True,
        "relative_path": str(target_path.relative_to(workspace_path)),
        "size_bytes": len(body),
    })
