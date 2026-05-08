from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from backend.App.paths import APP_ROOT, artifacts_root
from backend.App.testing.application.use_cases.run_visual_probe import RunVisualProbe
from backend.App.testing.domain.ports import VisualProbeConfig
from backend.App.testing.infrastructure.visual_probe import (
    LocalProjectLauncher,
    LocalVisualArtifactStore,
    PlaywrightVisualProbe,
)


def _playwright_runtime_available() -> bool:
    bin_name = "playwright.cmd" if os.name == "nt" else "playwright"
    candidate_node_modules = (
        APP_ROOT / "frontend" / "node_modules" / ".bin" / bin_name,
        APP_ROOT / "node_modules" / ".bin" / bin_name,
    )
    if not any(candidate.is_file() for candidate in candidate_node_modules):
        return False
    chromium_cache = Path.home() / ".cache" / "ms-playwright"
    if not chromium_cache.is_dir():
        return False
    return any(chromium_cache.glob("chromium-*"))


@pytest.mark.skipif(
    not _playwright_runtime_available(),
    reason="Playwright npm package or chromium browser is not installed locally",
)
def test_visual_probe_golden_static_page_with_har_and_trace(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "index.html").write_text(
        """
        <!doctype html>
        <html>
          <head><title>Visual golden</title></head>
          <body>
            <main style="max-width:720px;margin:24px auto;font-family:sans-serif">
              <h1>Visual golden page</h1>
              <p>Runtime evidence should include screenshot, HAR, and trace.</p>
            </main>
          </body>
        </html>
        """,
        encoding="utf-8",
    )

    task_id = "visual-golden-test"
    root = artifacts_root()
    use_case = RunVisualProbe(
        launcher=LocalProjectLauncher(),
        browser_probe=PlaywrightVisualProbe(working_dir=str(workspace)),
        artifact_store=LocalVisualArtifactStore(str(root)),
    )

    try:
        try:
            manifest = use_case.execute(
                VisualProbeConfig(
                    workspace_root=str(workspace),
                    task_id=task_id,
                    artifacts_dir="",
                    pages=["/"],
                    startup_timeout_sec=10,
                    page_timeout_ms=10_000,
                    global_timeout_sec=60,
                    capture_har=True,
                    capture_trace=True,
                )
            )
        except Exception as error:
            if "Executable doesn't exist" in str(error) or "browserType.launch" in str(error):
                pytest.skip(f"Playwright browser is not installed: {error}")
            raise

        if manifest.status == "failed" and any(
            "Executable doesn't exist" in error
            or "browserType.launch" in error
            or "requires a Playwright binary" in error
            for error in manifest.errors
        ):
            pytest.skip(f"Playwright browser is not installed: {manifest.errors[0]}")

        assert manifest.status == "passed"
        assert manifest.pages
        first = manifest.pages[0]
        assert first.screenshot is not None
        assert Path(first.screenshot.path).is_file()
        assert first.screenshot.url
        assert first.har_path and Path(first.har_path).is_file()
        assert first.har_url
        assert first.trace_path and Path(first.trace_path).is_file()
        assert first.trace_url
        assert Path(manifest.artifacts_dir, "manifest.json").is_file()
    finally:
        shutil.rmtree(root / "visual" / task_id, ignore_errors=True)
