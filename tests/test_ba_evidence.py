from __future__ import annotations

from backend.App.orchestration.application.nodes.ba import ba_node


class _FakeBAAgent:
    def __init__(self, **_: object) -> None:
        self.used_model = "fake-model"
        self.used_provider = "fake-provider"
        self.role = "BA"


def test_ba_node_returns_validated_repo_evidence(monkeypatch, tmp_path):
    target = tmp_path / "backend" / "routes.py"
    target.parent.mkdir(parents=True)
    target.write_text("@app.get('/orders')\n", encoding="utf-8")

    monkeypatch.setattr(
        "backend.App.orchestration.application.nodes.ba.BAAgent",
        _FakeBAAgent,
    )
    monkeypatch.setattr(
        "backend.App.orchestration.application.nodes.ba._llm_planning_agent_run",
        lambda agent, prompt, state: (
            "BA requirements\n\n```json\n"
            '{"repo_evidence":[{"path":"backend/routes.py","start_line":1,"end_line":1,'
            '"excerpt":"@app.get(\'/orders\')",'
            '"why":"The existing product already exposes an orders endpoint."}],'
            '"unverified_claims":[]}\n```',
            "fake-model",
            "fake-provider",
        ),
    )

    result = ba_node({"workspace_root": str(tmp_path), "agent_config": {}, "pm_output": ""})

    assert result["ba_model"] == "fake-model"
    assert result["ba_repo_evidence"][0]["path"] == "backend/routes.py"
    assert result["ba_repo_evidence"][0]["excerpt_sha256"]
    assert result["ba_unverified_claims"] == []
    assert result["ba_memory_artifact"]["verified_facts"]
    assert result["ba_memory_artifact"]["decisions"]


def test_ba_node_falls_back_to_empty_repo_evidence_when_artifact_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "backend.App.orchestration.application.nodes.ba.BAAgent",
        _FakeBAAgent,
    )
    monkeypatch.setattr(
        "backend.App.orchestration.application.nodes.ba._llm_planning_agent_run",
        lambda agent, prompt, state: ("BA requirements without artifact", "m", "p"),
    )

    result = ba_node({"workspace_root": str(tmp_path), "agent_config": {}, "pm_output": ""})

    assert result["ba_repo_evidence"] == []
    assert result["ba_unverified_claims"] == []


def test_ba_node_allows_unverified_claims_without_workspace(monkeypatch):
    monkeypatch.setattr(
        "backend.App.orchestration.application.nodes.ba.BAAgent",
        _FakeBAAgent,
    )
    monkeypatch.setattr(
        "backend.App.orchestration.application.nodes.ba._llm_planning_agent_run",
        lambda agent, prompt, state: (
            "BA requirements\n\n```json\n"
            '{"repo_evidence":[],"unverified_claims":["Current fulfillment workflow is unknown without repository access."]}\n```',
            "m",
            "p",
        ),
    )

    result = ba_node({"workspace_root": "", "agent_config": {}, "pm_output": ""})

    assert result["ba_repo_evidence"] == []
    assert result["ba_unverified_claims"] == [
        "Current fulfillment workflow is unknown without repository access."
    ]


def test_ba_node_suppresses_unverified_claims_when_workspace_available(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "backend.App.orchestration.application.nodes.ba.BAAgent",
        _FakeBAAgent,
    )
    monkeypatch.setattr(
        "backend.App.orchestration.application.nodes.ba._llm_planning_agent_run",
        lambda agent, prompt, state: (
            "BA requirements\n\n```json\n"
            '{"repo_evidence":[],"unverified_claims":["This claim should have been proven from the repo."]}\n```',
            "m",
            "p",
        ),
    )

    result = ba_node({"workspace_root": str(tmp_path), "agent_config": {}, "pm_output": ""})

    assert result["ba_repo_evidence"] == []
    assert result["ba_unverified_claims"] == []
