from __future__ import annotations

from pathlib import Path

from backend.App.spec.domain.provenance import (
    ProvenanceError,
    ProvenanceSidecar,
    parse_sidecar,
    serialise_sidecar,
)

_SIDECAR_SUFFIX = ".codegen_meta.json"


class SidecarStoreError(Exception):
    pass


class SidecarNotFoundError(SidecarStoreError):
    pass


def _sidecar_path_for(generated_file: Path, workspace_root: Path) -> Path:
    resolved = generated_file.expanduser().resolve()
    ws_resolved = workspace_root.expanduser().resolve()
    try:
        resolved.relative_to(ws_resolved)
    except ValueError as exc:
        raise SidecarStoreError(
            f"generated_file {generated_file!r} resolves outside workspace_root {workspace_root!r}"
        ) from exc
    return resolved.parent / (resolved.name + _SIDECAR_SUFFIX)


def write_sidecar(
    sidecar: ProvenanceSidecar,
    generated_file: Path,
    workspace_root: Path,
) -> Path:
    target = _sidecar_path_for(generated_file, workspace_root)
    try:
        target.write_text(serialise_sidecar(sidecar), encoding="utf-8")
    except OSError as exc:
        raise SidecarStoreError(
            f"failed to write sidecar for {generated_file}: {exc}"
        ) from exc
    return target


def read_sidecar(
    generated_file: Path,
    workspace_root: Path,
) -> ProvenanceSidecar:
    target = _sidecar_path_for(generated_file, workspace_root)
    if not target.is_file():
        raise SidecarNotFoundError(
            f"sidecar not found for {generated_file}: expected {target}"
        )
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise SidecarStoreError(
            f"failed to read sidecar {target}: {exc}"
        ) from exc
    try:
        return parse_sidecar(raw)
    except ProvenanceError as exc:
        raise SidecarStoreError(
            f"sidecar {target} is malformed: {exc}"
        ) from exc


def sidecar_exists(generated_file: Path, workspace_root: Path) -> bool:
    try:
        return _sidecar_path_for(generated_file, workspace_root).is_file()
    except SidecarStoreError:
        return False


__all__ = [
    "SidecarNotFoundError",
    "SidecarStoreError",
    "read_sidecar",
    "sidecar_exists",
    "write_sidecar",
]
