"""Domain ports for E2E test execution bounded context."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class E2ESuiteConfig:
    """Configuration for a single E2E suite run."""

    suite_path: str          # path to test suite or tag filter
    base_url: str            # URL of app under test
    artifacts_dir: str       # where to write trace/screenshots/junit
    global_timeout_sec: int  # max seconds for the whole run
    task_id: str             # for naming artifact subdirectory


@dataclass
class E2ERunResult:
    """Result of an E2E suite run."""

    exit_code: int
    junit_xml_path: Optional[str]
    artifacts_dir: str
    stdout: str
    stderr: str


class E2ERunnerPort(ABC):
    """Port for executing an E2E test suite."""

    @abstractmethod
    def run(self, config: E2ESuiteConfig) -> E2ERunResult: ...


class E2EArtifactStorePort(ABC):
    """Port for resolving artifact storage paths."""

    @abstractmethod
    def resolve_artifacts_dir(self, task_id: str, run_id: str) -> str: ...
