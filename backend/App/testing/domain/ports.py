from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class E2ESuiteConfig:
    suite_path: str
    base_url: str
    artifacts_dir: str
    global_timeout_sec: int
    task_id: str


@dataclass
class E2ERunResult:
    exit_code: int
    junit_xml_path: Optional[str]
    artifacts_dir: str
    stdout: str
    stderr: str


class E2ERunnerPort(ABC):
    @abstractmethod
    def run(self, config: E2ESuiteConfig) -> E2ERunResult: ...


class E2EArtifactStorePort(ABC):
    @abstractmethod
    def resolve_artifacts_dir(self, task_id: str, run_id: str) -> str: ...


@dataclass
class VisualViewport:
    name: str
    width: int
    height: int


@dataclass
class VisualProbeConfig:
    workspace_root: str
    task_id: str
    artifacts_dir: str
    base_url: str = ""
    start_command: str = ""
    start_directory: str = ""
    ready_path: str = "/"
    pages: list[str] = field(default_factory=lambda: ["/"])
    viewports: list[VisualViewport] = field(default_factory=list)
    port: int = 0
    startup_timeout_sec: int = 60
    page_timeout_ms: int = 30_000
    global_timeout_sec: int = 180
    max_pages: int = 5
    capture_har: bool = False
    capture_trace: bool = False


@dataclass
class VisualLaunchResult:
    base_url: str
    start_command: str = ""
    started_process: bool = False
    stdout_log_path: str = ""
    stderr_log_path: str = ""


@dataclass
class VisualScreenshotArtifact:
    viewport: str
    path: str
    width: int
    height: int
    url: str = ""


@dataclass
class VisualPageEvidence:
    url: str
    page_path: str
    viewport: str
    title: str = ""
    response_status: Optional[int] = None
    screenshot: Optional[VisualScreenshotArtifact] = None
    console_errors: list[str] = field(default_factory=list)
    page_errors: list[str] = field(default_factory=list)
    network_failures: list[str] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)
    har_path: str = ""
    har_url: str = ""
    trace_path: str = ""
    trace_url: str = ""


@dataclass
class VisualEvidenceManifest:
    schema: str = "visual_evidence/v1"
    status: str = "skipped"
    task_id: str = ""
    base_url: str = ""
    start_command: str = ""
    artifacts_dir: str = ""
    pages: list[VisualPageEvidence] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    summary: str = ""
    stdout_log_path: str = ""
    stderr_log_path: str = ""
    artifacts_url: str = ""
    manifest_url: str = ""
    har_paths: list[str] = field(default_factory=list)
    har_urls: list[str] = field(default_factory=list)
    trace_paths: list[str] = field(default_factory=list)
    trace_urls: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class VisualArtifactStorePort(ABC):
    @abstractmethod
    def resolve_visual_artifacts_dir(self, task_id: str, run_id: str) -> str: ...


class VisualLaunchHandlePort(ABC):
    @property
    @abstractmethod
    def result(self) -> VisualLaunchResult: ...

    @abstractmethod
    def stop(self) -> None: ...


class ProjectLauncherPort(ABC):
    @abstractmethod
    def launch(self, config: VisualProbeConfig) -> VisualLaunchHandlePort: ...


class BrowserProbePort(ABC):
    @abstractmethod
    def probe(self, config: VisualProbeConfig) -> VisualEvidenceManifest: ...
