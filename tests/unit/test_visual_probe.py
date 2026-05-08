from __future__ import annotations

import json
import shutil
import urllib.request
from pathlib import Path

from backend.App.orchestration.application.nodes.qa import _visual_evidence_prompt_block
from backend.App.orchestration.application.nodes.visual_probe import visual_probe_node
from backend.App.orchestration.application.nodes.visual_probe import _screenshot_image_parts
from backend.App.orchestration.application.pipeline.step_output_extractor import (
    StepOutputExtractor,
)
from backend.App.testing.application.use_cases.run_visual_probe import RunVisualProbe
from backend.App.testing.domain.ports import (
    BrowserProbePort,
    ProjectLauncherPort,
    VisualEvidenceManifest,
    VisualLaunchHandlePort,
    VisualLaunchResult,
    VisualPageEvidence,
    VisualProbeConfig,
    VisualScreenshotArtifact,
)
from backend.App.testing.infrastructure.visual_probe import (
    LocalProjectLauncher,
    LocalVisualArtifactStore,
    PlaywrightVisualProbe,
    _render_start_command,
)


class _FakeHandle(VisualLaunchHandlePort):
    def __init__(self) -> None:
        self.stopped = False
        self._result = VisualLaunchResult(
            base_url="http://127.0.0.1:9999",
            start_command="npm run dev",
            started_process=True,
        )

    @property
    def result(self) -> VisualLaunchResult:
        return self._result

    def stop(self) -> None:
        self.stopped = True


class _FakeLauncher(ProjectLauncherPort):
    def __init__(self, handle: _FakeHandle) -> None:
        self.handle = handle

    def launch(self, config: VisualProbeConfig) -> VisualLaunchHandlePort:
        assert config.artifacts_dir
        return self.handle


class _FakeBrowserProbe(BrowserProbePort):
    def probe(self, config: VisualProbeConfig) -> VisualEvidenceManifest:
        screenshot = VisualScreenshotArtifact(
            viewport="desktop",
            path=str(Path(config.artifacts_dir) / "screenshots" / "home.png"),
            width=1440,
            height=1000,
            url="/artifacts/visual/task-1/run/screenshots/home.png",
        )
        return VisualEvidenceManifest(
            status="passed",
            task_id=config.task_id,
            base_url=config.base_url,
            start_command=config.start_command,
            artifacts_dir=config.artifacts_dir,
            pages=[
                VisualPageEvidence(
                    url=config.base_url + "/",
                    page_path="/",
                    viewport="desktop",
                    screenshot=screenshot,
                    checks={"blank_page": False},
                )
            ],
            summary="Visual probe status: passed",
        )


def test_run_visual_probe_writes_manifest_and_stops_launcher(tmp_path: Path):
    handle = _FakeHandle()
    use_case = RunVisualProbe(
        launcher=_FakeLauncher(handle),
        browser_probe=_FakeBrowserProbe(),
        artifact_store=LocalVisualArtifactStore(str(tmp_path)),
    )

    manifest = use_case.execute(
        VisualProbeConfig(
            workspace_root=str(tmp_path),
            task_id="task-1",
            artifacts_dir="",
        )
    )

    assert handle.stopped is True
    assert manifest.status == "passed"
    manifest_path = Path(manifest.artifacts_dir) / "manifest.json"
    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert saved["schema"] == "visual_evidence/v1"
    assert saved["pages"][0]["screenshot"]["path"].endswith("home.png")


def test_local_project_launcher_serves_static_index(tmp_path: Path):
    (tmp_path / "index.html").write_text("<main>Hello visual probe</main>", encoding="utf-8")
    artifacts_dir = tmp_path / "artifacts"
    config = VisualProbeConfig(
        workspace_root=str(tmp_path),
        task_id="static",
        artifacts_dir=str(artifacts_dir),
        startup_timeout_sec=5,
    )

    handle = LocalProjectLauncher().launch(config)
    try:
        assert handle.result.started_process is True
        with urllib.request.urlopen(handle.result.base_url, timeout=2) as response:
            body = response.read().decode("utf-8")
        assert "Hello visual probe" in body
        assert (artifacts_dir / "server.stdout.log").exists()
    finally:
        handle.stop()


def test_start_command_renderer_only_replaces_known_placeholders():
    rendered = _render_start_command(
        "npm run dev -- --port {port} --define '{\"feature\":true}' --url {base_url}",
        port=4321,
        base_url="http://127.0.0.1:4321",
    )

    assert "--port 4321" in rendered
    assert '{"feature":true}' in rendered
    assert "http://127.0.0.1:4321" in rendered


def test_playwright_probe_init_does_not_require_npx(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    PlaywrightVisualProbe(working_dir="")


def test_visual_probe_node_skips_without_workspace_root():
    result = visual_probe_node({"agent_config": {}})

    assert result["visual_probe_status"] == "skipped"
    assert "workspace_root is not configured" in result["visual_probe_output"]


def test_visual_evidence_prompt_block_includes_screenshot_paths():
    block = _visual_evidence_prompt_block(
        {
            "visual_probe_manifest": {
                "status": "failed",
                "pages": [
                    {
                        "page_path": "/",
                        "viewport": "mobile",
                        "screenshot": {"path": "/tmp/home-mobile.png"},
                    }
                ],
            }
        }
    )

    assert "[Visual runtime evidence]" in block
    assert "/tmp/home-mobile.png" in block


def test_step_output_extractor_supports_visual_steps():
    extractor = StepOutputExtractor()

    event = extractor.emit_completed(
        "visual_probe",
        {"visual_probe_output": "Visual probe status: passed"},
    )

    assert event["agent"] == "visual_probe"
    assert event["message"] == "Visual probe status: passed"


def test_screenshot_image_parts_embed_readable_png(tmp_path: Path):
    png_path = tmp_path / "shot.png"
    png_path.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
        b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\n"
        b"IDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n"
        b"-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    parts = _screenshot_image_parts(
        {
            "pages": [
                {
                    "page_path": "/",
                    "viewport": "desktop",
                    "screenshot": {"path": str(png_path), "viewport": "desktop"},
                }
            ]
        },
        max_images=1,
        max_bytes=10_000,
    )

    assert parts[0]["type"] == "text"
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
