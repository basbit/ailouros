from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from backend.App.testing.domain.ports import (
    E2EArtifactStorePort,
    E2ERunnerPort,
    E2ERunResult,
    E2ESuiteConfig,
)


class PlaywrightRunner(E2ERunnerPort):

    def __init__(self, working_dir: Optional[str] = None) -> None:
        if shutil.which("npx") is None:
            raise RuntimeError(
                "PlaywrightRunner requires 'npx' on PATH. "
                "Install Node.js (https://nodejs.org) and ensure 'npx' is accessible."
            )
        self._working_dir = working_dir or os.getcwd()

    def run(self, config: E2ESuiteConfig) -> E2ERunResult:
        artifacts_path = Path(config.artifacts_dir)
        artifacts_path.mkdir(parents=True, exist_ok=True)

        junit_xml_path = str(artifacts_path / "junit.xml")

        env = os.environ.copy()
        env["PLAYWRIGHT_JUNIT_OUTPUT_NAME"] = junit_xml_path
        env["BASE_URL"] = config.base_url

        cmd = [
            "npx",
            "playwright",
            "test",
            config.suite_path,
            "--reporter=junit,html",
        ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=config.global_timeout_sec,
                env=env,
                cwd=self._working_dir,
            )
            exit_code = proc.returncode
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            stdout = (exc.stdout or b"").decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = (exc.stderr or b"").decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            exit_code = 124
            stderr = f"Timed out after {config.global_timeout_sec}s\n" + stderr

        resolved_junit: Optional[str] = junit_xml_path if Path(junit_xml_path).exists() else None

        return E2ERunResult(
            exit_code=exit_code,
            junit_xml_path=resolved_junit,
            artifacts_dir=config.artifacts_dir,
            stdout=stdout,
            stderr=stderr,
        )


class LocalArtifactStore(E2EArtifactStorePort):

    def __init__(self, base_dir: str) -> None:
        self._base_dir = base_dir

    def resolve_artifacts_dir(self, task_id: str, run_id: str) -> str:
        path = Path(self._base_dir) / "e2e" / task_id / run_id
        path.mkdir(parents=True, exist_ok=True)
        return str(path)
