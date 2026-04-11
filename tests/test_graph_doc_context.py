"""Контекст для generate_documentation: spec/arch + doc-цепочка."""

from backend.App.orchestration.application.pipeline_graph import (
    _code_analysis_is_weak,
    _documentation_product_context_block,
    _effective_spec_for_build,
    _spec_arch_context_for_docs,
    generate_documentation_node,
)
from backend.App.orchestration.application.nodes import documentation as documentation_nodes


def test_code_analysis_is_weak():
    assert _code_analysis_is_weak({}) is True
    assert _code_analysis_is_weak({"files": []}) is True
    assert _code_analysis_is_weak({"note": "workspace_root_empty", "files": []}) is True
    assert _code_analysis_is_weak({"files": [{"path": "a.py"}]}) is False


def test_spec_arch_context_for_docs():
    assert _spec_arch_context_for_docs({}) == ""
    s = _spec_arch_context_for_docs(
        {"spec_output": "SPEC", "arch_output": "ARCH"}  # type: ignore[arg-type]
    )
    assert "SPEC" in s and "ARCH" in s


def test_effective_spec_for_build_prefers_spec_merge():
    st = {  # type: ignore[var-annotated]
        "spec_output": "merged spec",
        "pm_output": "pm only",
        "task_id": "t1",
    }
    assert _effective_spec_for_build(st) == "merged spec"


def test_effective_spec_for_build_pm_dev_shortcut():
    st = {"spec_output": "", "pm_output": "tasks from pm", "task_id": "t2"}  # type: ignore[arg-type]
    out = _effective_spec_for_build(st)
    assert "[PM — plan and tasks]" in out
    assert "tasks from pm" in out


def test_effective_spec_for_build_orders_ba_arch_after_pm():
    st = {
        "spec_output": "",
        "pm_output": "P",
        "ba_output": "B",
        "arch_output": "A",
        "task_id": "t3",
    }  # type: ignore[arg-type]
    out = _effective_spec_for_build(st)
    assert out.index("P") < out.index("B") < out.index("A")


def test_generate_documentation_uses_architect_prompt_only_from_code_diagram(monkeypatch):
    """Раньше: code_diagram отсутствовал → подмешивался architect (Software Architect)."""
    captured: dict[str, str] = {}

    class FakeDiagram:
        used_model = "m"
        used_provider = "p"

        def __init__(self, **kwargs):
            captured["diagram_prompt_path"] = str(
                kwargs.get("system_prompt_path_override") or ""
            )

        def run(self, prompt: str) -> str:
            captured["diagram_prompt"] = prompt
            return "mermaid"

    class FakeDoc:
        used_model = "m2"
        used_provider = "p2"

        def __init__(self, **kwargs):
            pass

        def run(self, prompt: str) -> str:
            return "docs"

    monkeypatch.setattr("backend.App.orchestration.application.pipeline_graph.CodeDiagramAgent", FakeDiagram)
    monkeypatch.setattr("backend.App.orchestration.application.pipeline_graph.DocGenerateAgent", FakeDoc)
    monkeypatch.setattr("backend.App.orchestration.application.pipeline_graph._remote_api_client_kwargs", lambda _s: {})

    state = {
        "agent_config": {
            "architect": {"prompt_path": "engineering-software-architect.md"},
            # code_diagram намеренно нет — не должны брать prompt architect
        },
        "code_analysis": {"files": [{"path": "x"}], "schema": "swarm_code_analysis/v1"},
        "input": "task",
        "spec_output": "",
        "arch_output": "",
    }
    generate_documentation_node(state)  # type: ignore[arg-type]
    assert captured.get("diagram_prompt_path") != "engineering-software-architect.md"
    assert "[Input] Static analysis" in captured.get("diagram_prompt", "")


def test_generate_documentation_weak_analysis_injects_spec_arch(monkeypatch):
    """Пустой scan — всё равно подмешиваем spec/arch в оба промпта (CTX-03)."""
    captured: list[str] = []

    class FakeDiagram:
        used_model = "m"
        used_provider = "p"

        def __init__(self, **kwargs):
            pass

        def run(self, prompt: str) -> str:
            captured.append(prompt)
            return "m"

    class FakeDoc:
        used_model = "m2"
        used_provider = "p2"

        def __init__(self, **kwargs):
            pass

        def run(self, prompt: str) -> str:
            captured.append(prompt)
            return "d"

    monkeypatch.setattr("backend.App.orchestration.application.pipeline_graph.CodeDiagramAgent", FakeDiagram)
    monkeypatch.setattr("backend.App.orchestration.application.pipeline_graph.DocGenerateAgent", FakeDoc)
    monkeypatch.setattr("backend.App.orchestration.application.pipeline_graph._remote_api_client_kwargs", lambda _s: {})

    state = {
        "agent_config": {},
        "code_analysis": {"files": [], "schema": "swarm_code_analysis/v1"},
        "input": "task",
        "spec_output": "Утверждённая спека здесь",
        "arch_output": "Стек и границы",
        "task_id": "t-weak-spec",
    }
    generate_documentation_node(state)  # type: ignore[arg-type]
    assert "Утверждённая спека" in captured[0]
    assert "Стек и границы" in captured[0]
    assert "[Product / specification context]" in captured[0]
    assert "[Input] Static analysis" in captured[0]
    assert len(captured) == 2
    assert "Утверждённая спека" in captured[1]
    assert "smaller excerpt than diagram pass" in captured[1]


def test_documentation_product_context_falls_back_to_pm_when_no_spec_arch():
    st = {
        "spec_output": "",
        "arch_output": "",
        "pm_output": "PM plan body",
        "task_id": "t-pm",
    }  # type: ignore[var-annotated]
    block = _documentation_product_context_block(st, log_node="test")  # type: ignore[arg-type]
    assert "PM plan body" in block
    assert "[PM — plan and tasks]" in block


def test_refactor_plan_prompt_includes_effective_spec(monkeypatch):
    captured: list[str] = []

    class FakeRefactor:
        used_model = "mr"
        used_provider = "pr"

        def __init__(self, **kwargs):
            pass

        def run(self, prompt: str) -> str:
            captured.append(prompt)
            return (
                "plan\n\n```json\n"
                '{"repo_evidence":[],"unverified_claims":["Repository facts unavailable in this prompt-only test."]}\n'
                "```"
            )

    monkeypatch.setattr(documentation_nodes, "RefactorPlanAgent", FakeRefactor)
    monkeypatch.setattr(
        documentation_nodes,
        "_remote_api_client_kwargs_for_role",
        lambda _state, _cfg: {},
    )

    state = {
        "agent_config": {},
        "code_analysis": {"files": [{"path": "a.py"}], "schema": "swarm_code_analysis/v1"},
        "spec_output": "MERGED_SPEC_UNIQUE",
        "problem_spotter_output": "some problems",
        "task_id": "t-refactor",
    }
    documentation_nodes.refactor_plan_node(state)  # type: ignore[arg-type]
    assert len(captured) == 1
    assert "MERGED_SPEC_UNIQUE" in captured[0]
    assert "Approved product context" in captured[0]
    assert "some problems" in captured[0]
