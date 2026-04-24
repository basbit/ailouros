"""RunE2ESuite use case — runs an E2E test suite and returns the result."""
from __future__ import annotations

import logging
import uuid

from backend.App.testing.domain.ports import (
    E2EArtifactStorePort,
    E2ERunnerPort,
    E2ERunResult,
    E2ESuiteConfig,
)

logger = logging.getLogger(__name__)


class RunE2ESuite:
    """Use case: run an E2E test suite for a given task and return the result.

    Args:
        runner: Port that executes the actual test suite process.
        artifact_store: Port that resolves the artifacts directory path.
    """

    def __init__(self, runner: E2ERunnerPort, artifact_store: E2EArtifactStorePort) -> None:
        self._runner = runner
        self._artifact_store = artifact_store

    def execute(
        self,
        task_id: str,
        suite_path: str,
        base_url: str,
        global_timeout_sec: int = 300,
    ) -> E2ERunResult:
        """Run the E2E suite and return the result.

        Args:
            task_id: Task identifier used to namespace artifacts.
            suite_path: Path or tag filter passed to the test runner.
            base_url: URL of the application under test.
            global_timeout_sec: Maximum allowed wall-clock seconds for the run.

        Returns:
            An :class:`E2ERunResult` describing exit code, artifact paths,
            and captured output.
        """
        run_id = str(uuid.uuid4())[:8]
        artifacts_dir = self._artifact_store.resolve_artifacts_dir(task_id, run_id)
        config = E2ESuiteConfig(
            suite_path=suite_path,
            base_url=base_url,
            artifacts_dir=artifacts_dir,
            global_timeout_sec=global_timeout_sec,
            task_id=task_id,
        )
        logger.info(
            "RunE2ESuite: task=%s run=%s suite=%s base_url=%s",
            task_id,
            run_id,
            suite_path,
            base_url,
        )
        result = self._runner.run(config)
        logger.info(
            "RunE2ESuite: exit_code=%d artifacts=%s",
            result.exit_code,
            result.artifacts_dir,
        )
        return result
