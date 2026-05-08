from langgraph_pipeline import run_pipeline


def test_pipeline_happy_path(monkeypatch):
    monkeypatch.setenv("SWARM_DEVOPS_REQUIRE_REPO_PATH", "0")
    monkeypatch.setattr("langgraph_pipeline.PMAgent.run", lambda self, _: "pm tasks")
    monkeypatch.setattr("langgraph_pipeline.ReviewerAgent.run", lambda self, _: "VERDICT: OK")
    monkeypatch.setattr("langgraph_pipeline.StackReviewerAgent.run", lambda self, _: "VERDICT: OK")
    monkeypatch.setattr("langgraph_pipeline.HumanAgent.run", lambda self, _: "human ok")
    monkeypatch.setattr(
        "langgraph_pipeline.BAAgent.run",
        lambda self, _: (
            ("ba spec " * 30)
            + "\n```json\n"
            + '{"repo_evidence":[],"unverified_claims":["Repository facts unavailable in this test."]}\n'
            + "```"
        ),
    )
    monkeypatch.setattr(
        "langgraph_pipeline.ArchitectAgent.run",
        lambda self, _: (
            ("arch spec " * 30)
            + "\n```json\n"
            + '{"repo_evidence":[],"unverified_claims":["Repository facts unavailable in this test."]}\n'
            + "```"
        ),
    )

    monkeypatch.setattr("backend.App.orchestration.application.routing.pipeline_graph.CodeDiagramAgent.run", lambda self, _: "mermaid ok")
    monkeypatch.setattr("backend.App.orchestration.application.routing.pipeline_graph.DocGenerateAgent.run", lambda self, _: "docs ok")
    monkeypatch.setattr(
        "backend.App.orchestration.application.routing.pipeline_graph.ProblemSpotterAgent.run",
        lambda self, _: (
            "problems ok\n```json\n"
            '{"repo_evidence":[],"unverified_claims":["Repository facts unavailable in this test."]}\n'
            "```"
        ),
    )
    monkeypatch.setattr(
        "backend.App.orchestration.application.routing.pipeline_graph.RefactorPlanAgent.run",
        lambda self, _: (
            "refactor plan ok\n```json\n"
            '{"repo_evidence":[],"unverified_claims":["Repository facts unavailable in this test."]}\n'
            "```"
        ),
    )
    monkeypatch.setattr(
        "langgraph_pipeline.DevopsAgent.run",
        lambda self, _: (
            "devops bootstrap\n```json\n"
            '{"repo_evidence":[],"unverified_claims":["Repository facts unavailable in this test."]}\n'
            "```"
        ),
    )
    monkeypatch.setattr(
        "langgraph_pipeline.DevLeadAgent.run",
        lambda self, _: (
            '```json\n'
            '{"tasks":[{"id":"1","title":"x","development_scope":"d","testing_scope":"q"}],'
            '"deliverables":{"must_exist_files":["src/app.py"],"spec_symbols":["AppService"],'
            '"verification_commands":[{"command":"build_gate","expected":"build gate passes"}],"assumptions":[],'
            '"production_paths":["src"],"placeholder_allow_list":[]}}\n'
            '```'
        ),
    )
    monkeypatch.setattr("langgraph_pipeline.DevAgent.run", lambda self, _: "dev code")
    monkeypatch.setattr("langgraph_pipeline.QAAgent.run", lambda self, _: "qa report")

    result = run_pipeline("Сделать сайт-визитку")
    assert result["pm_output"] == "pm tasks"
    assert result["pm_review_output"].startswith("VERDICT: OK")
    assert result["pm_human_output"] == "human ok"
    assert result["ba_output"].startswith("ba spec")
    assert result["arch_output"].startswith("arch spec")
    assert result["stack_review_output"].startswith("VERDICT: OK")
    assert "BA specification section:" in result["spec_output"]
    assert "ba spec" in result["spec_output"]
    assert "dev code" in result["dev_output"]
    assert result.get("dev_task_outputs") == ["dev code"]
    assert "qa report" in result["qa_output"]
    assert result.get("qa_task_outputs") == ["qa report"]
    assert result["qa_human_output"] == "human ok"
