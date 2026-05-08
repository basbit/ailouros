from __future__ import annotations

import json
import logging
import urllib.parse
import uuid
from dataclasses import replace
from pathlib import Path

from backend.App.testing.domain.ports import (
    BrowserProbePort,
    ProjectLauncherPort,
    VisualArtifactStorePort,
    VisualEvidenceManifest,
    VisualProbeConfig,
)

logger = logging.getLogger(__name__)


class RunVisualProbe:
    def __init__(
        self,
        *,
        launcher: ProjectLauncherPort,
        browser_probe: BrowserProbePort,
        artifact_store: VisualArtifactStorePort,
    ) -> None:
        self._launcher = launcher
        self._browser_probe = browser_probe
        self._artifact_store = artifact_store

    def execute(self, config: VisualProbeConfig) -> VisualEvidenceManifest:
        run_id = str(uuid.uuid4())[:8]
        artifacts_dir = self._artifact_store.resolve_visual_artifacts_dir(
            config.task_id or "unknown",
            run_id,
        )
        configured = replace(config, artifacts_dir=artifacts_dir)
        handle = None
        try:
            handle = self._launcher.launch(configured)
            launch_result = handle.result
            effective_config = replace(
                configured,
                base_url=launch_result.base_url,
                start_command=launch_result.start_command,
            )
            manifest = self._browser_probe.probe(effective_config)
            manifest.base_url = launch_result.base_url
            manifest.start_command = launch_result.start_command
            manifest.stdout_log_path = launch_result.stdout_log_path
            manifest.stderr_log_path = launch_result.stderr_log_path
        except Exception as error:
            logger.warning("RunVisualProbe failed: %s", error, exc_info=True)
            manifest = VisualEvidenceManifest(
                status="failed",
                task_id=config.task_id,
                base_url=config.base_url,
                start_command=config.start_command,
                artifacts_dir=artifacts_dir,
                errors=[str(error)],
                summary=f"Visual probe failed before browser evidence was complete: {error}",
            )
        finally:
            if handle is not None:
                try:
                    handle.stop()
                except Exception as stop_error:
                    logger.debug(
                        "RunVisualProbe launcher cleanup failed: %s",
                        stop_error,
                    )

        manifest.task_id = manifest.task_id or config.task_id
        manifest.artifacts_dir = manifest.artifacts_dir or artifacts_dir
        manifest.artifacts_url = manifest.artifacts_url or _artifact_url(manifest.artifacts_dir)
        manifest.manifest_url = manifest.manifest_url or _artifact_url(
            str(Path(manifest.artifacts_dir) / "manifest.json")
        )
        self._write_manifest(manifest)
        return manifest

    @staticmethod
    def _write_manifest(manifest: VisualEvidenceManifest) -> None:
        if not manifest.artifacts_dir:
            return
        path = Path(manifest.artifacts_dir) / "manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _artifact_url(path: str) -> str:
    if not path:
        return ""
    try:
        from backend.App.paths import artifacts_root

        relative_path = Path(path).resolve().relative_to(artifacts_root())
    except Exception:
        return ""
    return "/artifacts/" + "/".join(
        urllib.parse.quote(part) for part in relative_path.parts
    )
