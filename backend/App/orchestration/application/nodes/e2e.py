"""E2E pipeline node — runs the Playwright E2E suite as a pipeline step.

Reads configuration from environment variables:
- ``E2E_BASE_URL`` (required): URL of the application under test.
  If not set, the step is skipped gracefully.
- ``E2E_SUITE`` (optional, default ``e2e/``): path or tag filter for the suite.
- ``E2E_GLOBAL_TIMEOUT_SEC`` (optional, default 300): wall-clock timeout in seconds.

Never raises — all errors are captured and stored in state.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from backend.App.orchestration.application.pipeline_state import PipelineState

logger = logging.getLogger(__name__)


def e2e_node(state: PipelineState) -> dict[str, Any]:
    """Pipeline node: run the E2E test suite.

    Args:
        state: Current pipeline state.

    Returns:
        Dict with ``e2e_output`` and optionally ``e2e_artifacts_dir`` and
        ``e2e_status`` keys.
    """
    base_url = os.getenv("E2E_BASE_URL", "").strip()
    if not base_url:
        return {
            "e2e_output": "E2E_BASE_URL not set — skipping e2e step",
            "e2e_status": "skipped",
        }

    suite_path = os.getenv("E2E_SUITE", "e2e/").strip() or "e2e/"

    timeout_raw = os.getenv("E2E_GLOBAL_TIMEOUT_SEC", "").strip()
    try:
        global_timeout_sec = int(timeout_raw) if timeout_raw else 300
    except ValueError:
        logger.warning("e2e_node: invalid E2E_GLOBAL_TIMEOUT_SEC=%r — using 300", timeout_raw)
        global_timeout_sec = 300

    task_id: str = state.get("task_id") or "unknown"

    try:
        from backend.App.testing.infrastructure.playwright_runner import (
            LocalArtifactStore,
            PlaywrightRunner,
        )
        from backend.App.testing.application.use_cases.run_e2e_suite import RunE2ESuite

        from backend.App.paths import artifacts_root as _anchored_artifacts_root
        artifacts_root = str(_anchored_artifacts_root())
        runner = PlaywrightRunner()
        artifact_store = LocalArtifactStore(base_dir=artifacts_root)
        use_case = RunE2ESuite(runner=runner, artifact_store=artifact_store)

        result = use_case.execute(
            task_id=task_id,
            suite_path=suite_path,
            base_url=base_url,
            global_timeout_sec=global_timeout_sec,
        )
    except Exception as exc:
        logger.warning("e2e_node: unexpected error: %s", exc, exc_info=True)
        return {
            "e2e_output": f"E2E step error: {exc}",
            "e2e_status": "error",
        }

    if result.exit_code == 0:
        return {
            "e2e_output": "E2E passed",
            "e2e_artifacts_dir": result.artifacts_dir,
        }

    return {
        "e2e_output": (
            f"E2E failed (exit {result.exit_code}): {result.stderr[:500]}"
        ),
        "e2e_artifacts_dir": result.artifacts_dir,
    }
