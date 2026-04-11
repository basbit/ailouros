from __future__ import annotations

from backend.App.orchestration.application.nodes.arch import arch_node


class _FakeArchitectAgent:
    def __init__(self, **_: object) -> None:
        self.used_model = "fake-model"
        self.used_provider = "fake-provider"
        self.role = "ARCHITECT"


def test_arch_node_returns_validated_repo_evidence(monkeypatch, tmp_path):
    target = tmp_path / "src" / "service.py"
    target.parent.mkdir(parents=True)
    target.write_text("from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")

    monkeypatch.setattr(
        "backend.App.orchestration.application.nodes.arch.ArchitectAgent",
        _FakeArchitectAgent,
    )
    monkeypatch.setattr(
        "backend.App.orchestration.application.nodes.arch._llm_planning_agent_run",
        lambda agent, prompt, state: (
            "Architecture summary\n\n```json\n"
            '{"repo_evidence":[{"path":"src/service.py","start_line":1,"end_line":2,'
            '"excerpt":"from fastapi import FastAPI\\napp = FastAPI()",'
            '"why":"The repo already contains FastAPI bootstrap code."}],'
            '"unverified_claims":[]}\n```',
            "fake-model",
            "fake-provider",
        ),
    )

    result = arch_node({"workspace_root": str(tmp_path), "agent_config": {}, "pm_output": ""})

    assert result["arch_model"] == "fake-model"
    assert result["arch_repo_evidence"][0]["path"] == "src/service.py"
    assert result["arch_repo_evidence"][0]["excerpt_sha256"]
    assert result["arch_unverified_claims"] == []
    assert result["arch_memory_artifact"]["verified_facts"]
    assert result["arch_memory_artifact"]["decisions"]


def test_arch_node_falls_back_to_empty_repo_evidence_when_artifact_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "backend.App.orchestration.application.nodes.arch.ArchitectAgent",
        _FakeArchitectAgent,
    )
    monkeypatch.setattr(
        "backend.App.orchestration.application.nodes.arch._llm_planning_agent_run",
        lambda agent, prompt, state: ("Architecture summary without artifact", "m", "p"),
    )

    result = arch_node({"workspace_root": str(tmp_path), "agent_config": {}, "pm_output": ""})

    assert result["arch_repo_evidence"] == []
    assert result["arch_unverified_claims"] == []
    assert result["arch_output"].startswith("[ARCH ERROR]")


def test_arch_node_retries_once_when_repo_evidence_artifact_missing(monkeypatch, tmp_path):
    target = tmp_path / "src" / "service.py"
    target.parent.mkdir(parents=True)
    target.write_text("from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")

    monkeypatch.setattr(
        "backend.App.orchestration.application.nodes.arch.ArchitectAgent",
        _FakeArchitectAgent,
    )
    calls = {"count": 0}

    def _fake_run(agent, prompt, state):
        calls["count"] += 1
        if calls["count"] == 1:
            return ("Architecture summary without artifact", "m", "p")
        return (
            "Architecture summary\n\n```json\n"
            '{"repo_evidence":[{"path":"src/service.py","start_line":1,"end_line":2,'
            '"excerpt":"from fastapi import FastAPI\\napp = FastAPI()",'
            '"why":"The repo already contains FastAPI bootstrap code."}],'
            '"unverified_claims":[]}\n```',
            "m",
            "p",
        )

    monkeypatch.setattr(
        "backend.App.orchestration.application.nodes.arch._llm_planning_agent_run",
        _fake_run,
    )

    result = arch_node({"workspace_root": str(tmp_path), "agent_config": {}, "pm_output": ""})

    assert calls["count"] == 2
    assert result["arch_repo_evidence"][0]["path"] == "src/service.py"


def test_arch_node_allows_unverified_claims_without_workspace(monkeypatch):
    monkeypatch.setattr(
        "backend.App.orchestration.application.nodes.arch.ArchitectAgent",
        _FakeArchitectAgent,
    )
    monkeypatch.setattr(
        "backend.App.orchestration.application.nodes.arch._llm_planning_agent_run",
        lambda agent, prompt, state: (
            "Architecture summary\n\n```json\n"
            '{"repo_evidence":[],"unverified_claims":["Current deployment target is unknown."]}\n```',
            "m",
            "p",
        ),
    )

    result = arch_node({"workspace_root": "", "agent_config": {}, "pm_output": ""})

    assert result["arch_repo_evidence"] == []
    assert result["arch_unverified_claims"] == ["Current deployment target is unknown."]
