from __future__ import annotations

from backend.App.orchestration.application.nodes import documentation as documentation_nodes


class _FakeProblemSpotterAgent:
    def __init__(self, **_: object) -> None:
        self.role = "PROBLEM_SPOTTER"


class _FakeRefactorPlanAgent:
    def __init__(self, **_: object) -> None:
        self.role = "REFACTOR_PLAN"


def test_problem_spotter_node_returns_validated_repo_evidence(monkeypatch, tmp_path):
    target = tmp_path / "src" / "service.py"
    target.parent.mkdir(parents=True)
    target.write_text("def slow_path():\n    return legacy_call()\n", encoding="utf-8")

    monkeypatch.setattr(documentation_nodes, "ProblemSpotterAgent", _FakeProblemSpotterAgent)
    monkeypatch.setattr(
        documentation_nodes,
        "_remote_api_client_kwargs_for_role",
        lambda _state, _cfg: {},
    )
    monkeypatch.setattr(
        documentation_nodes,
        "_llm_agent_run_with_optional_mcp",
        lambda agent, prompt, state, readonly_tools=True: (
            "Found issue\n\n```json\n"
            '{"repo_evidence":[{"path":"src/service.py","start_line":1,"end_line":2,'
            '"excerpt":"def slow_path():\\n    return legacy_call()",'
            '"why":"This function shows a direct legacy dependency in production flow."}],'
            '"unverified_claims":[]}\n```',
            "m",
            "p",
        ),
    )

    result = documentation_nodes.problem_spotter_node(
        {
            "workspace_root": str(tmp_path),
            "agent_config": {},
            "code_analysis": {"files": [{"path": "src/service.py"}], "schema": "swarm_code_analysis/v1"},
            "task_id": "t-problem",
        }
    )

    assert result["problem_spotter_repo_evidence"][0]["path"] == "src/service.py"
    assert result["problem_spotter_repo_evidence"][0]["excerpt_sha256"]
    assert result["problem_spotter_unverified_claims"] == []


def test_refactor_plan_node_returns_validated_repo_evidence(monkeypatch, tmp_path):
    target = tmp_path / "src" / "service.py"
    target.parent.mkdir(parents=True)
    target.write_text("def slow_path():\n    return legacy_call()\n", encoding="utf-8")

    monkeypatch.setattr(documentation_nodes, "RefactorPlanAgent", _FakeRefactorPlanAgent)
    monkeypatch.setattr(
        documentation_nodes,
        "_remote_api_client_kwargs_for_role",
        lambda _state, _cfg: {},
    )
    monkeypatch.setattr(
        documentation_nodes,
        "_llm_agent_run_with_optional_mcp",
        lambda agent, prompt, state, readonly_tools=True: (
            "Refactor plan\n\n```json\n"
            '{"repo_evidence":[{"path":"src/service.py","start_line":1,"end_line":2,'
            '"excerpt":"def slow_path():\\n    return legacy_call()",'
            '"why":"This function should be isolated behind an adapter."}],'
            '"unverified_claims":[]}\n```',
            "m",
            "p",
        ),
    )

    result = documentation_nodes.refactor_plan_node(
        {
            "workspace_root": str(tmp_path),
            "agent_config": {},
            "code_analysis": {"files": [{"path": "src/service.py"}], "schema": "swarm_code_analysis/v1"},
            "problem_spotter_output": "problem list",
            "problem_spotter_repo_evidence": [
                {
                    "path": "src/service.py",
                    "start_line": 1,
                    "end_line": 2,
                    "excerpt": "def slow_path():\n    return legacy_call()",
                    "excerpt_sha256": "hash",
                    "why": "This function shows a direct legacy dependency in production flow.",
                }
            ],
            "problem_spotter_unverified_claims": [],
            "spec_output": "MERGED_SPEC",
            "task_id": "t-refactor-evidence",
        }
    )

    assert result["refactor_plan_repo_evidence"][0]["path"] == "src/service.py"
    assert result["refactor_plan_repo_evidence"][0]["excerpt_sha256"]
    assert result["refactor_plan_unverified_claims"] == []
