from __future__ import annotations

from backend.App.orchestration.application.nodes.devops import devops_node


class _FakeDevopsAgent:
    def __init__(self, **_: object) -> None:
        self.used_model = "fake-model"
        self.used_provider = "fake-provider"
        self.role = "DEVOPS"


def test_devops_node_returns_validated_repo_evidence(monkeypatch, tmp_path):
    target = tmp_path / "package.json"
    target.write_text('{"scripts":{"test":"vitest"}}\n', encoding="utf-8")

    monkeypatch.setattr(
        "backend.App.orchestration.application.nodes.devops.DevopsAgent",
        _FakeDevopsAgent,
    )
    monkeypatch.setattr(
        "backend.App.orchestration.application.nodes.devops._llm_planning_agent_run",
        lambda agent, prompt, state: (
            "<swarm_shell>\nnpm test\n</swarm_shell>\n\n"
            "1. Run tests.\n\n"
            "```json\n"
            '{"repo_evidence":[{"path":"package.json","start_line":1,"end_line":1,'
            '"excerpt":"{\\"scripts\\":{\\"test\\":\\"vitest\\"}}",'
            '"why":"The repository defines the vitest test script."}],'
            '"unverified_claims":[]}\n```',
            "fake-model",
            "fake-provider",
        ),
    )

    result = devops_node(
        {
            "workspace_root": str(tmp_path),
            "agent_config": {},
            "spec_output": "Approved spec",
            "task_id": "t-1",
        }
    )

    assert result["devops_model"] == "fake-model"
    assert result["devops_repo_evidence"][0]["path"] == "package.json"
    assert result["devops_repo_evidence"][0]["excerpt_sha256"]
    assert result["devops_unverified_claims"] == []


def test_devops_node_falls_back_to_empty_repo_evidence_when_artifact_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "backend.App.orchestration.application.nodes.devops.DevopsAgent",
        _FakeDevopsAgent,
    )
    monkeypatch.setattr(
        "backend.App.orchestration.application.nodes.devops._llm_planning_agent_run",
        lambda agent, prompt, state: ("<swarm_shell>\nmake test\n</swarm_shell>", "m", "p"),
    )

    result = devops_node(
        {
            "workspace_root": str(tmp_path),
            "agent_config": {},
            "spec_output": "Approved spec",
            "task_id": "t-2",
        }
    )

    assert result["devops_repo_evidence"] == []
    assert result["devops_unverified_claims"] == []


def test_devops_node_allows_unverified_claims_without_workspace(monkeypatch):
    monkeypatch.setattr(
        "backend.App.orchestration.application.nodes.devops.DevopsAgent",
        _FakeDevopsAgent,
    )
    monkeypatch.setattr(
        "backend.App.orchestration.application.nodes.devops._llm_planning_agent_run",
        lambda agent, prompt, state: (
            "<swarm_shell>\nmake test\n</swarm_shell>\n\n"
            "```json\n"
            '{"repo_evidence":[],"unverified_claims":["Current CI runner is unknown without repository access."]}\n```',
            "m",
            "p",
        ),
    )

    result = devops_node(
        {
            "workspace_root": "",
            "agent_config": {},
            "spec_output": "Approved spec",
            "task_id": "t-3",
        }
    )

    assert result["devops_repo_evidence"] == []
    assert result["devops_unverified_claims"] == ["Current CI runner is unknown without repository access."]
