from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

from backend.App.testing.domain.ports import (
    BrowserProbePort,
    ProjectLauncherPort,
    VisualArtifactStorePort,
    VisualEvidenceManifest,
    VisualLaunchHandlePort,
    VisualLaunchResult,
    VisualPageEvidence,
    VisualProbeConfig,
    VisualScreenshotArtifact,
    VisualViewport,
)
from backend.App.testing.infrastructure._visual_probe_node_plan import (
    StartPlan as _StartPlan,
    VisualProbeUnavailable,
    node_start_plan as _node_start_plan,
    normalise_pages as _normalise_pages,
    render_start_command as _render_start_command,
)


_DEFAULT_VIEWPORTS: tuple[VisualViewport, ...] = (
    VisualViewport(name="desktop", width=1440, height=1000),
    VisualViewport(name="mobile", width=390, height=844),
)


class LocalVisualArtifactStore(VisualArtifactStorePort):
    def __init__(self, base_dir: str) -> None:
        self._base_directory = base_dir

    def resolve_visual_artifacts_dir(self, task_id: str, run_id: str) -> str:
        path = Path(self._base_directory) / "visual" / task_id / run_id
        path.mkdir(parents=True, exist_ok=True)
        return str(path)


class _LocalLaunchHandle(VisualLaunchHandlePort):
    def __init__(
        self,
        result: VisualLaunchResult,
        process: Optional[subprocess.Popen[str]] = None,
        stdout_file: Any = None,
        stderr_file: Any = None,
    ) -> None:
        self._result = result
        self._process = process
        self._stdout_file = stdout_file
        self._stderr_file = stderr_file

    @property
    def result(self) -> VisualLaunchResult:
        return self._result

    def stop(self) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            try:
                if os.name == "posix":
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                else:
                    process.terminate()
                process.wait(timeout=8)
            except Exception:
                try:
                    if os.name == "posix":
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    else:
                        process.kill()
                except Exception:
                    pass
        for file_obj in (self._stdout_file, self._stderr_file):
            if file_obj is not None:
                try:
                    file_obj.close()
                except OSError:
                    pass


class LocalProjectLauncher(ProjectLauncherPort):
    def launch(self, config: VisualProbeConfig) -> VisualLaunchHandlePort:
        workspace_path = Path(config.workspace_root or "").expanduser().resolve()
        if not workspace_path.is_dir():
            raise VisualProbeUnavailable(f"workspace_root does not exist: {workspace_path}")

        if config.base_url.strip():
            base_url = config.base_url.strip().rstrip("/")
            self._wait_until_ready(
                self._readiness_url(base_url, config.ready_path),
                config.startup_timeout_sec,
            )
            return _LocalLaunchHandle(
                VisualLaunchResult(base_url=base_url, started_process=False),
            )

        plan = self._build_start_plan(config, workspace_path)
        artifacts_path = Path(config.artifacts_dir)
        artifacts_path.mkdir(parents=True, exist_ok=True)
        stdout_path = artifacts_path / "server.stdout.log"
        stderr_path = artifacts_path / "server.stderr.log"
        stdout_file = stdout_path.open("w", encoding="utf-8")
        stderr_file = stderr_path.open("w", encoding="utf-8")
        try:
            process = subprocess.Popen(
                plan.arguments,
                cwd=str(plan.working_directory),
                stdout=stdout_file,
                stderr=stderr_file,
                stdin=subprocess.DEVNULL,
                text=True,
                env=plan.environment,
                start_new_session=os.name == "posix",
            )
        except OSError:
            stdout_file.close()
            stderr_file.close()
            raise

        handle = _LocalLaunchHandle(
            VisualLaunchResult(
                base_url=plan.base_url,
                start_command=plan.command_text,
                started_process=True,
                stdout_log_path=str(stdout_path),
                stderr_log_path=str(stderr_path),
            ),
            process=process,
            stdout_file=stdout_file,
            stderr_file=stderr_file,
        )
        try:
            self._wait_until_ready(
                self._readiness_url(plan.base_url, config.ready_path),
                config.startup_timeout_sec,
            )
        except Exception:
            handle.stop()
            raise
        return handle

    def _build_start_plan(
        self,
        config: VisualProbeConfig,
        workspace_path: Path,
    ) -> _StartPlan:
        working_directory = _safe_working_directory(
            workspace_path,
            config.start_directory,
        )
        port = config.port if config.port > 0 else _find_free_port()
        base_url = f"http://127.0.0.1:{port}"
        environment = _launcher_environment(port)

        command = config.start_command.strip()
        if command:
            rendered = _render_start_command(command, port=port, base_url=base_url)
            return _StartPlan(
                arguments=shlex.split(rendered),
                working_directory=working_directory,
                base_url=base_url,
                command_text=rendered,
                environment=environment,
            )

        package_json = working_directory / "package.json"
        if package_json.is_file():
            return _node_start_plan(
                package_json,
                working_directory,
                port,
                base_url,
                environment,
            )

        if (working_directory / "index.html").is_file():
            arguments = [
                sys.executable,
                "-m",
                "http.server",
                str(port),
                "--bind",
                "127.0.0.1",
            ]
            return _StartPlan(
                arguments=arguments,
                working_directory=working_directory,
                base_url=base_url,
                command_text=shlex.join(arguments),
                environment=environment,
            )

        raise VisualProbeUnavailable(
            "No visual start configuration found. Provide "
            "agent_config.swarm.visual_probe.start_command or base_url."
        )

    @staticmethod
    def _readiness_url(base_url: str, ready_path: str) -> str:
        ready_path = ready_path.strip() or "/"
        if ready_path.startswith("http://") or ready_path.startswith("https://"):
            return ready_path
        return urllib.parse.urljoin(base_url.rstrip("/") + "/", ready_path.lstrip("/"))

    @staticmethod
    def _wait_until_ready(url: str, timeout_sec: int) -> None:
        deadline = time.monotonic() + max(1, timeout_sec)
        last_error = ""
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=2) as response:
                    if int(response.status) < 500:
                        return
                    last_error = f"HTTP {response.status}"
            except (OSError, urllib.error.URLError) as error:
                last_error = str(error)
            time.sleep(0.5)
        raise TimeoutError(f"Visual probe readiness timed out for {url}: {last_error}")


class PlaywrightVisualProbe(BrowserProbePort):
    def __init__(self, working_dir: Optional[str] = None) -> None:
        self._working_dir = working_dir

    def probe(self, config: VisualProbeConfig) -> VisualEvidenceManifest:
        artifacts_path = Path(config.artifacts_dir)
        screenshots_path = artifacts_path / "screenshots"
        evidence_path = artifacts_path / "evidence"
        har_path = artifacts_path / "har"
        trace_path = artifacts_path / "trace"
        test_results_path = artifacts_path / "test-results"
        for path in (screenshots_path, evidence_path, har_path, trace_path, test_results_path):
            path.mkdir(parents=True, exist_ok=True)

        spec_path = artifacts_path / "visual-probe.spec.cjs"
        config_path = artifacts_path / "visual-playwright.config.cjs"
        spec_path.write_text(_PLAYWRIGHT_SPEC, encoding="utf-8")
        config_path.write_text(_PLAYWRIGHT_CONFIG, encoding="utf-8")

        pages = _normalise_pages(config.pages, config.max_pages)
        viewports = config.viewports or list(_DEFAULT_VIEWPORTS)
        environment = os.environ.copy()
        environment.update(
            {
                "SWARM_VISUAL_BASE_URL": config.base_url,
                "SWARM_VISUAL_PAGES": json.dumps(pages),
                "SWARM_VISUAL_VIEWPORTS": json.dumps(
                    [viewport.__dict__ for viewport in viewports],
                ),
                "SWARM_VISUAL_SCREENSHOT_DIR": str(screenshots_path),
                "SWARM_VISUAL_EVIDENCE_DIR": str(evidence_path),
                "SWARM_VISUAL_HAR_DIR": str(har_path),
                "SWARM_VISUAL_TRACE_DIR": str(trace_path),
                "SWARM_VISUAL_CAPTURE_HAR": "1" if config.capture_har else "0",
                "SWARM_VISUAL_CAPTURE_TRACE": "1" if config.capture_trace else "0",
                "SWARM_VISUAL_JUNIT_PATH": str(artifacts_path / "junit.xml"),
                "SWARM_VISUAL_TEST_OUTPUT_DIR": str(test_results_path),
                "SWARM_VISUAL_PAGE_TIMEOUT_MS": str(config.page_timeout_ms),
            }
        )
        environment["NODE_PATH"] = _node_path_for(
            config.workspace_root,
            environment.get("NODE_PATH", ""),
        )
        environment["PATH"] = _path_with_working_node(environment.get("PATH", ""))

        playwright_binary = _resolve_playwright_binary(config.workspace_root)
        npx_binary = shutil.which("npx", path=environment.get("PATH", ""))
        if not playwright_binary and npx_binary is None:
            raise VisualProbeUnavailable(
                "Visual probe requires a Playwright binary in node_modules or 'npx' on PATH"
            )
        command = (
            [playwright_binary, "test", str(spec_path), "--config", str(config_path)]
            if playwright_binary
            else [
                npx_binary or "npx",
                "playwright",
                "test",
                str(spec_path),
                "--config",
                str(config_path),
            ]
        )
        try:
            completed_process = subprocess.run(
                command,
                cwd=self._working_dir or config.workspace_root or None,
                capture_output=True,
                text=True,
                timeout=config.global_timeout_sec,
                env=environment,
            )
            exit_code = completed_process.returncode
            stdout = completed_process.stdout or ""
            stderr = completed_process.stderr or ""
        except subprocess.TimeoutExpired as error:
            exit_code = 124
            stdout = _coerce_subprocess_text(error.stdout)
            stderr = "Timed out after " + str(config.global_timeout_sec) + "s\n"
            stderr += _coerce_subprocess_text(error.stderr)

        (artifacts_path / "playwright.stdout.log").write_text(stdout, encoding="utf-8")
        (artifacts_path / "playwright.stderr.log").write_text(stderr, encoding="utf-8")

        page_evidence = _read_page_evidence(evidence_path)
        errors = _manifest_errors(page_evidence)
        if exit_code != 0:
            errors.append(f"playwright exited with code {exit_code}: {stderr[-1000:]}")

        status = "passed" if not errors else "failed"
        har_paths = [page.har_path for page in page_evidence if page.har_path]
        trace_paths = [page.trace_path for page in page_evidence if page.trace_path]
        return VisualEvidenceManifest(
            status=status,
            task_id=config.task_id,
            base_url=config.base_url,
            start_command=config.start_command,
            artifacts_dir=str(artifacts_path),
            pages=page_evidence,
            errors=errors,
            summary=_build_summary(status, page_evidence, errors),
            artifacts_url=_artifact_url(str(artifacts_path)),
            manifest_url=_artifact_url(str(artifacts_path / "manifest.json")),
            har_paths=har_paths,
            har_urls=[_artifact_url(path) for path in har_paths],
            trace_paths=trace_paths,
            trace_urls=[_artifact_url(path) for path in trace_paths],
        )


def _safe_working_directory(workspace_path: Path, start_directory: str) -> Path:
    if not start_directory.strip():
        return workspace_path
    candidate = (workspace_path / start_directory).resolve()
    try:
        candidate.relative_to(workspace_path)
    except ValueError as error:
        raise VisualProbeUnavailable("visual_probe.start_directory escapes workspace") from error
    if not candidate.is_dir():
        raise VisualProbeUnavailable(f"visual_probe.start_directory does not exist: {candidate}")
    return candidate


def _launcher_environment(port: int) -> dict[str, str]:
    allowed = {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TMPDIR",
        "TEMP",
        "TMP",
        "NODE_ENV",
        "npm_config_cache",
    }
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    environment.update({"HOST": "127.0.0.1", "PORT": str(port), "BROWSER": "none", "CI": "1"})
    return environment


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _node_path_for(workspace_root: str, existing: str) -> str:
    node_module_paths = []
    if workspace_root:
        workspace_path = Path(workspace_root)
        for candidate in (
            workspace_path / "node_modules",
            workspace_path / "frontend" / "node_modules",
        ):
            if candidate.is_dir():
                node_module_paths.append(str(candidate))
    try:
        from backend.App.paths import APP_ROOT

        bundled = APP_ROOT / "frontend" / "node_modules"
        if bundled.is_dir():
            node_module_paths.append(str(bundled))
    except Exception:
        pass
    if existing:
        node_module_paths.append(existing)
    return os.pathsep.join(node_module_paths)


def _path_with_working_node(existing: str) -> str:
    node_bin = _resolve_working_node_bin(existing)
    if not node_bin:
        return existing
    parts = [part for part in existing.split(os.pathsep) if part]
    filtered = [part for part in parts if Path(part) != node_bin]
    return os.pathsep.join([str(node_bin), *filtered])


def _resolve_working_node_bin(existing_path: str) -> Path | None:
    executable = "node.exe" if os.name == "nt" else "node"
    candidates: list[Path] = []
    explicit = os.getenv("SWARM_NODE_BIN", "").strip() or os.getenv("NVM_BIN", "").strip()
    if explicit:
        candidates.append(Path(explicit))
    try:
        nvm_root = Path(os.getenv("NVM_DIR", str(Path.home() / ".nvm")))
        versions_root = nvm_root / "versions" / "node"
        if versions_root.is_dir():
            candidates.extend(
                sorted(
                    (item / "bin" for item in versions_root.iterdir() if item.is_dir()),
                    reverse=True,
                )
            )
    except OSError:
        pass
    candidates.extend(Path(part) for part in existing_path.split(os.pathsep) if part)

    seen: set[str] = set()
    for directory in candidates:
        try:
            resolved_dir = directory.expanduser().resolve()
        except OSError:
            continue
        key = str(resolved_dir)
        if key in seen:
            continue
        seen.add(key)
        if _node_executable_works(resolved_dir / executable):
            return resolved_dir
    return None


def _node_executable_works(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        completed = subprocess.run(
            [str(path), "--version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _resolve_playwright_binary(workspace_root: str) -> str:
    executable = "playwright.cmd" if os.name == "nt" else "playwright"
    candidates: list[Path] = []
    if workspace_root:
        root = Path(workspace_root)
        candidates.extend(
            [
                root / "node_modules" / ".bin" / executable,
                root / "frontend" / "node_modules" / ".bin" / executable,
            ]
        )
    try:
        from backend.App.paths import APP_ROOT

        candidates.append(APP_ROOT / "frontend" / "node_modules" / ".bin" / executable)
    except Exception:
        pass
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return ""


def _coerce_subprocess_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _artifact_url(path: str) -> str:
    if not path:
        return ""
    try:
        from backend.App.paths import artifacts_root

        root = artifacts_root()
        relative_path = Path(path).resolve().relative_to(root)
    except Exception:
        return ""
    quoted_path = "/".join(urllib.parse.quote(part) for part in relative_path.parts)
    return f"/artifacts/{quoted_path}"


def _read_page_evidence(evidence_path: Path) -> list[VisualPageEvidence]:
    pages: list[VisualPageEvidence] = []
    for path in sorted(evidence_path.glob("*.json")):
        try:
            evidence_data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        screenshot_data = evidence_data.get("screenshot")
        screenshot = None
        if isinstance(screenshot_data, dict):
            screenshot_path = str(screenshot_data.get("path") or "")
            screenshot = VisualScreenshotArtifact(
                viewport=str(screenshot_data.get("viewport") or ""),
                path=screenshot_path,
                width=int(screenshot_data.get("width") or 0),
                height=int(screenshot_data.get("height") or 0),
                url=str(screenshot_data.get("url") or _artifact_url(screenshot_path)),
            )
        har_path = str(evidence_data.get("har_path") or "")
        trace_path = str(evidence_data.get("trace_path") or "")
        pages.append(
            VisualPageEvidence(
                url=str(evidence_data.get("url") or ""),
                page_path=str(evidence_data.get("page_path") or ""),
                viewport=str(evidence_data.get("viewport") or ""),
                title=str(evidence_data.get("title") or ""),
                response_status=evidence_data.get("response_status"),
                screenshot=screenshot,
                console_errors=list(evidence_data.get("console_errors") or []),
                page_errors=list(evidence_data.get("page_errors") or []),
                network_failures=list(evidence_data.get("network_failures") or []),
                checks=dict(evidence_data.get("checks") or {}),
                har_path=har_path,
                har_url=str(evidence_data.get("har_url") or _artifact_url(har_path)),
                trace_path=trace_path,
                trace_url=str(evidence_data.get("trace_url") or _artifact_url(trace_path)),
            )
        )
    return pages


def _manifest_errors(pages: list[VisualPageEvidence]) -> list[str]:
    errors: list[str] = []
    if not pages:
        return ["no page evidence was produced"]
    for page in pages:
        prefix = f"{page.viewport} {page.page_path}"
        if page.response_status is not None and int(page.response_status) >= 400:
            errors.append(f"{prefix}: HTTP {page.response_status}")
        if page.console_errors:
            errors.append(f"{prefix}: console errors ({len(page.console_errors)})")
        if page.page_errors:
            errors.append(f"{prefix}: page errors ({len(page.page_errors)})")
        if page.network_failures:
            errors.append(f"{prefix}: network failures ({len(page.network_failures)})")
        if page.checks.get("blank_page"):
            errors.append(f"{prefix}: blank page detected")
        if page.checks.get("horizontal_scroll"):
            errors.append(f"{prefix}: horizontal scroll detected")
    return errors


def _build_summary(
    status: str,
    pages: list[VisualPageEvidence],
    errors: list[str],
) -> str:
    screenshot_count = sum(1 for page in pages if page.screenshot is not None)
    har_count = sum(1 for page in pages if page.har_path)
    trace_count = sum(1 for page in pages if page.trace_path)
    lines = [
        f"Visual probe status: {status}",
        f"Pages/viewport captures: {len(pages)}",
        f"Screenshots: {screenshot_count}",
    ]
    if har_count:
        lines.append(f"HAR files: {har_count}")
    if trace_count:
        lines.append(f"Trace files: {trace_count}")
    if errors:
        lines.append("Findings:")
        lines.extend(f"- {error}" for error in errors[:10])
    else:
        lines.append("No browser runtime findings detected by automated checks.")
    return "\n".join(lines)


_PLAYWRIGHT_CONFIG = r"""
module.exports = {
  timeout: Number(process.env.SWARM_VISUAL_PAGE_TIMEOUT_MS || 30000) + 10000,
  reporter: [
    ['list'],
    ['junit', { outputFile: process.env.SWARM_VISUAL_JUNIT_PATH }],
  ],
  outputDir: process.env.SWARM_VISUAL_TEST_OUTPUT_DIR,
  use: {
    baseURL: process.env.SWARM_VISUAL_BASE_URL,
    ignoreHTTPSErrors: true,
    trace: 'off',
    screenshot: 'off',
  },
  workers: 1,
  projects: [{ name: 'chromium' }],
};
"""


_PLAYWRIGHT_SPEC = r"""
const { test } = require('@playwright/test');
const fileSystem = require('fs');
const pathModule = require('path');

const pages = JSON.parse(process.env.SWARM_VISUAL_PAGES || '["/"]');
const viewports = JSON.parse(process.env.SWARM_VISUAL_VIEWPORTS || '[]');
const screenshotDirectory = process.env.SWARM_VISUAL_SCREENSHOT_DIR;
const evidenceDirectory = process.env.SWARM_VISUAL_EVIDENCE_DIR;
const harDirectory = process.env.SWARM_VISUAL_HAR_DIR;
const traceDirectory = process.env.SWARM_VISUAL_TRACE_DIR;
const captureHar = process.env.SWARM_VISUAL_CAPTURE_HAR === '1';
const captureTrace = process.env.SWARM_VISUAL_CAPTURE_TRACE === '1';
const pageTimeout = Number(process.env.SWARM_VISUAL_PAGE_TIMEOUT_MS || 30000);

function safeName(value) {
  return String(value || 'page')
    .replace(/^https?:\/\//, '')
    .replace(/[^a-zA-Z0-9_-]+/g, '-')
    .replace(/^-|-$/g, '') || 'page';
}

async function collectChecks(page) {
  return await page.evaluate(() => {
    const documentElement = document.documentElement;
    const body = document.body;
    const textLength = (body && body.innerText ? body.innerText.trim().length : 0);
    const mediaCount = document.querySelectorAll('img, svg, canvas, video').length;
    return {
      text_length: textLength,
      media_count: mediaCount,
      blank_page: textLength < 10 && mediaCount === 0,
      horizontal_scroll: documentElement.scrollWidth > window.innerWidth + 2,
      viewport_width: window.innerWidth,
      viewport_height: window.innerHeight,
      document_width: documentElement.scrollWidth,
      document_height: documentElement.scrollHeight,
      main_content_detected: Boolean(
        document.querySelector('main, [role="main"], #root, #app, body > *'),
      ),
    };
  });
}

for (const pagePath of pages) {
  for (const viewport of viewports) {
    test(`${viewport.name} ${pagePath}`, async ({ browser }) => {
      const artifactName = `${safeName(pagePath)}-${safeName(viewport.name)}`;
      const harPath = captureHar ? pathModule.join(harDirectory, `${artifactName}.har`) : '';
      const tracePath = captureTrace ? pathModule.join(traceDirectory, `${artifactName}.zip`) : '';
      const contextOptions = {
        viewport: { width: viewport.width, height: viewport.height },
        ignoreHTTPSErrors: true,
      };
      if (captureHar) {
        contextOptions.recordHar = { path: harPath, mode: 'minimal' };
      }
      const context = await browser.newContext(contextOptions);
      if (captureTrace) {
        await context.tracing.start({
          screenshots: true,
          snapshots: true,
          sources: true,
        });
      }
      const page = await context.newPage();
      const consoleErrors = [];
      const pageErrors = [];
      const networkFailures = [];
      page.on('console', (message) => {
        if (message.type() === 'error') consoleErrors.push(message.text());
      });
      page.on('pageerror', (error) => {
        pageErrors.push(error && error.message ? error.message : String(error));
      });
      page.on('requestfailed', (request) => {
        networkFailures.push(
          `${request.method()} ${request.url()} ${request.failure()?.errorText || ''}`.trim(),
        );
      });

      let response = null;
      try {
        response = await page.goto(pagePath, {
          waitUntil: 'networkidle',
          timeout: pageTimeout,
        });
        await page.waitForTimeout(300);

        const title = await page.title();
        const screenshotPath = pathModule.join(screenshotDirectory, `${artifactName}.png`);
        await page.screenshot({ path: screenshotPath, fullPage: true });
        const checks = await collectChecks(page);
        const currentUrl = page.url();
        const evidence = {
          url: currentUrl,
          page_path: pagePath,
          viewport: viewport.name,
          title,
          response_status: response ? response.status() : null,
          screenshot: {
            viewport: viewport.name,
            path: screenshotPath,
            width: viewport.width,
            height: viewport.height,
          },
          console_errors: consoleErrors,
          page_errors: pageErrors,
          network_failures: networkFailures,
          checks,
          har_path: harPath,
          trace_path: tracePath,
        };
        const evidencePath = pathModule.join(evidenceDirectory, `${artifactName}.json`);
        await fileSystem.promises.writeFile(
          evidencePath,
          JSON.stringify(evidence, null, 2),
          'utf-8',
        );
      } finally {
        if (captureTrace) {
          await context.tracing.stop({ path: tracePath });
        }
        await context.close();
      }
    });
  }
}
"""
