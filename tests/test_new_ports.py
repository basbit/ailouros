"""Tests for H-3 new domain ports in backend.App.domain.ports."""

from __future__ import annotations

import pytest

from backend.App.orchestration.domain.ports import ShellApprovalPolicy
from backend.App.integrations.domain.ports import (
    DocumentationFetchPort,
    ObservabilityPort,
    PromptTemplateRepositoryPort,
    RemoteModelRegistryPort,
    SkillRepositoryPort,
)


# ---------------------------------------------------------------------------
# ShellApprovalPolicy (plain class, no ABC — easiest to test directly)
# ---------------------------------------------------------------------------

class TestShellApprovalPolicy:
    def setup_method(self) -> None:
        self.policy = ShellApprovalPolicy()

    def test_empty_allowlist_denies_everything(self) -> None:
        assert self.policy.is_allowed("git status", []) is False

    def test_exact_match_allowed(self) -> None:
        assert self.policy.is_allowed("git status", ["git status"]) is True

    def test_prefix_match_allowed(self) -> None:
        assert self.policy.is_allowed("git log --oneline", ["git "]) is True

    def test_non_matching_prefix_denied(self) -> None:
        assert self.policy.is_allowed("rm -rf /", ["git ", "make "]) is False

    def test_unrelated_prefix_not_matched(self) -> None:
        # "make" should NOT match "git status"
        assert self.policy.is_allowed("git status", ["make"]) is False

    def test_multiple_allowlist_entries(self) -> None:
        allowlist = ["make ", "git ", "npm "]
        assert self.policy.is_allowed("npm install", allowlist) is True
        assert self.policy.is_allowed("curl evil.com", allowlist) is False

    def test_leading_whitespace_stripped(self) -> None:
        assert self.policy.is_allowed("  git status", ["git "]) is True

    def test_max_timeout_returns_positive_int(self) -> None:
        timeout = self.policy.max_timeout_sec()
        assert isinstance(timeout, int)
        assert timeout > 0

    def test_max_timeout_is_300(self) -> None:
        assert self.policy.max_timeout_sec() == 300


# ---------------------------------------------------------------------------
# ABCs are instantiation-guarded (can't instantiate directly)
# ---------------------------------------------------------------------------

def test_documentation_fetch_port_is_abstract() -> None:
    with pytest.raises(TypeError):
        DocumentationFetchPort()  # type: ignore[abstract]


def test_remote_model_registry_port_is_abstract() -> None:
    with pytest.raises(TypeError):
        RemoteModelRegistryPort()  # type: ignore[abstract]


def test_observability_port_is_abstract() -> None:
    with pytest.raises(TypeError):
        ObservabilityPort()  # type: ignore[abstract]


def test_prompt_template_repository_port_is_abstract() -> None:
    with pytest.raises(TypeError):
        PromptTemplateRepositoryPort()  # type: ignore[abstract]


def test_skill_repository_port_is_abstract() -> None:
    with pytest.raises(TypeError):
        SkillRepositoryPort()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# Concrete implementations satisfy each port's contract
# ---------------------------------------------------------------------------

class _FakeDocFetch(DocumentationFetchPort):
    def fetch(self, url: str, *, max_chars: int = 50_000) -> str:
        return f"content from {url}"[:max_chars]


class _FakeModelRegistry(RemoteModelRegistryPort):
    def list_models(self, provider: str) -> list[str]:
        return [f"{provider}/model-a", f"{provider}/model-b"]


class _FakeObservability(ObservabilityPort):
    def __init__(self) -> None:
        self.metrics: list[tuple] = []
        self.traces: list[tuple] = []

    def record_metric(self, name: str, value: float, tags=None) -> None:
        self.metrics.append((name, value, tags))

    def trace_step(self, step_id: str, data: dict) -> None:
        self.traces.append((step_id, data))

    from contextlib import contextmanager

    @contextmanager
    def step_span_ctx(self, step_id: str, state: dict):
        yield


class _FakePromptRepo(PromptTemplateRepositoryPort):
    def get_template(self, name: str) -> str:
        templates = {"pm_system": "You are a PM. Goal: {goal}"}
        if name not in templates:
            raise KeyError(name)
        return templates[name]


class _FakeSkillRepo(SkillRepositoryPort):
    def get_skill(self, skill_id: str) -> str:
        if skill_id == "unknown":
            raise KeyError(skill_id)
        return f"# Skill: {skill_id}"


def test_fake_doc_fetch_satisfies_port() -> None:
    port = _FakeDocFetch()
    result = port.fetch("https://example.com/docs")
    assert "example.com" in result


def test_fake_doc_fetch_max_chars_truncates() -> None:
    port = _FakeDocFetch()
    result = port.fetch("https://example.com/docs", max_chars=10)
    assert len(result) <= 10


def test_fake_model_registry_returns_list() -> None:
    port = _FakeModelRegistry()
    models = port.list_models("ollama")
    assert isinstance(models, list)
    assert all("ollama" in m for m in models)


def test_fake_observability_records_metric() -> None:
    port = _FakeObservability()
    port.record_metric("latency", 42.5, {"agent": "pm"})
    assert port.metrics == [("latency", 42.5, {"agent": "pm"})]


def test_fake_observability_traces_step() -> None:
    port = _FakeObservability()
    port.trace_step("step-1", {"status": "ok"})
    assert port.traces[0] == ("step-1", {"status": "ok"})


def test_fake_prompt_repo_returns_template() -> None:
    port = _FakePromptRepo()
    tpl = port.get_template("pm_system")
    assert "{goal}" in tpl


def test_fake_prompt_repo_raises_key_error_for_unknown() -> None:
    port = _FakePromptRepo()
    with pytest.raises(KeyError):
        port.get_template("nonexistent_template")


def test_fake_skill_repo_returns_skill() -> None:
    port = _FakeSkillRepo()
    skill = port.get_skill("code_review")
    assert "code_review" in skill


def test_fake_skill_repo_raises_key_error_for_unknown() -> None:
    port = _FakeSkillRepo()
    with pytest.raises(KeyError):
        port.get_skill("unknown")
