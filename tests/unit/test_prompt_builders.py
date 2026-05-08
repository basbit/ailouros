"""Tests for backend/App/orchestration/application/nodes/_prompt_builders.py."""


from backend.App.orchestration.application.nodes._prompt_builders import (
    _code_analysis_is_weak,
    _compact_code_analysis_for_prompt,
    _doc_chain_spec_max_chars,
    _doc_generate_second_pass_analysis_max_chars,
    _doc_spec_max_each_chars,
    _effective_spec_for_build,
    _review_int_env,
    _should_compact_for_reviewer,
    _should_use_mcp_for_workspace,
    _spec_arch_context_for_docs,
    _spec_for_build_mcp_safe,
    _swarm_block,
    _workspace_context_mode_normalized,
    build_compact_build_phase_user_context,
    build_phase_pipeline_user_context,
    embedded_pipeline_input_for_review,
    embedded_review_artifact,
    pipeline_user_task,
    planning_mcp_tool_instruction,
    planning_pipeline_user_context,
    should_use_compact_build_pipeline_input,
)
from backend.App.shared.infrastructure.app_config_load import load_app_config_json


def _state(**kwargs):
    return dict(kwargs)


def _compact_workspace_template(key: str) -> str:
    section = load_app_config_json("prompt_fragments.json")["compact_build_workspace"]
    return str(section[key]).format(workspace_root="/proj").strip()


# ---------------------------------------------------------------------------
# pipeline_user_task
# ---------------------------------------------------------------------------

def test_pipeline_user_task_explicit_user_task():
    state = _state(user_task="do something")
    assert pipeline_user_task(state) == "do something"


def test_pipeline_user_task_from_input_assembled():
    marker = "\n\n---\n\n# User task\n\n"
    state = _state(input="preamble" + marker + "  actual task  ")
    assert pipeline_user_task(state) == "actual task"


def test_pipeline_user_task_from_plain_input():
    state = _state(input="plain prompt")
    assert pipeline_user_task(state) == "plain prompt"


def test_pipeline_user_task_empty_state():
    state = _state()
    assert pipeline_user_task(state) == ""


def test_pipeline_user_task_empty_user_task_falls_back_to_input():
    state = _state(user_task="   ", input="fallback")
    assert pipeline_user_task(state) == "fallback"


# ---------------------------------------------------------------------------
# _workspace_context_mode_normalized
# ---------------------------------------------------------------------------

def test_workspace_context_mode_normalized_default():
    assert _workspace_context_mode_normalized({}) == "full"


def test_workspace_context_mode_normalized_explicit():
    assert _workspace_context_mode_normalized({"workspace_context_mode": "retrieve"}) == "retrieve"


def test_workspace_context_mode_normalized_strips_whitespace():
    assert _workspace_context_mode_normalized({"workspace_context_mode": "  index_only  "}) == "index_only"


# ---------------------------------------------------------------------------
# _swarm_block
# ---------------------------------------------------------------------------

def test_swarm_block_returns_swarm_dict():
    state = _state(agent_config={"swarm": {"key": "val"}})
    assert _swarm_block(state) == {"key": "val"}


def test_swarm_block_returns_empty_when_not_dict():
    state = _state(agent_config={"swarm": "bad"})
    assert _swarm_block(state) == {}


def test_swarm_block_returns_empty_when_no_agent_config():
    assert _swarm_block({}) == {}


# ---------------------------------------------------------------------------
# _code_analysis_is_weak
# ---------------------------------------------------------------------------

def test_code_analysis_is_weak_empty_dict():
    assert _code_analysis_is_weak({}) is True


def test_code_analysis_is_weak_workspace_root_empty_note():
    assert _code_analysis_is_weak({"note": "workspace_root_empty"}) is True


def test_code_analysis_is_weak_empty_files_list():
    assert _code_analysis_is_weak({"files": []}) is True


def test_code_analysis_is_weak_with_files():
    assert _code_analysis_is_weak({"files": [{"path": "a.py"}]}) is False


def test_code_analysis_is_weak_none():
    assert _code_analysis_is_weak(None) is True


# ---------------------------------------------------------------------------
# _compact_code_analysis_for_prompt
# ---------------------------------------------------------------------------

def test_compact_code_analysis_empty():
    assert _compact_code_analysis_for_prompt({}) == "{}"


def test_compact_code_analysis_truncates_files():
    payload = {"files": [{"path": f"f{i}.py"} for i in range(200)]}
    result = _compact_code_analysis_for_prompt(payload)
    import json
    parsed = json.loads(result)
    assert len(parsed["files"]) == 120


def test_compact_code_analysis_truncates_long_json(monkeypatch):
    # Build a payload whose JSON > max_chars
    payload = {"files": [{"path": "a.py"}], "extra": "x" * 20000}
    result = _compact_code_analysis_for_prompt(payload, max_chars=100)
    assert result.endswith("\n…[truncated]")
    assert len(result) == 100 + len("\n…[truncated]")


# ---------------------------------------------------------------------------
# _review_int_env
# ---------------------------------------------------------------------------

def test_review_int_env_default(monkeypatch):
    monkeypatch.delenv("SOME_ENV_VAR", raising=False)
    assert _review_int_env("SOME_ENV_VAR", 42) == 42


def test_review_int_env_reads_positive(monkeypatch):
    monkeypatch.setenv("SOME_ENV_VAR", "99")
    assert _review_int_env("SOME_ENV_VAR", 42) == 99


def test_review_int_env_rejects_zero(monkeypatch):
    monkeypatch.setenv("SOME_ENV_VAR", "0")
    assert _review_int_env("SOME_ENV_VAR", 42) == 42


def test_review_int_env_rejects_non_digit(monkeypatch):
    monkeypatch.setenv("SOME_ENV_VAR", "abc")
    assert _review_int_env("SOME_ENV_VAR", 42) == 42


# ---------------------------------------------------------------------------
# _should_use_mcp_for_workspace
# ---------------------------------------------------------------------------

def test_should_use_mcp_no_workspace_root():
    state = _state(workspace_root="", workspace_context_mode="retrieve",
                   agent_config={"mcp": {"servers": ["srv"]}})
    assert _should_use_mcp_for_workspace(state) is False


def test_should_use_mcp_wrong_mode():
    state = _state(workspace_root="/tmp", workspace_context_mode="full",
                   agent_config={"mcp": {"servers": ["srv"]}})
    assert _should_use_mcp_for_workspace(state) is False


def test_should_use_mcp_skip_mcp_tools():
    state = _state(
        workspace_root="/tmp",
        workspace_context_mode="retrieve",
        agent_config={"swarm": {"skip_mcp_tools": True}, "mcp": {"servers": ["srv"]}},
    )
    assert _should_use_mcp_for_workspace(state) is False


def test_should_use_mcp_retrieve_with_servers():
    state = _state(
        workspace_root="/tmp",
        workspace_context_mode="retrieve",
        agent_config={"mcp": {"servers": ["srv"]}},
    )
    assert _should_use_mcp_for_workspace(state) is True


def test_should_use_mcp_no_servers():
    state = _state(
        workspace_root="/tmp",
        workspace_context_mode="retrieve",
        agent_config={"mcp": {}},
    )
    assert _should_use_mcp_for_workspace(state) is False


# ---------------------------------------------------------------------------
# planning_mcp_tool_instruction
# ---------------------------------------------------------------------------

def test_planning_mcp_tool_instruction_no_workspace():
    state = _state(workspace_root="", workspace_context_mode="retrieve")
    assert planning_mcp_tool_instruction(state) == ""


def test_planning_mcp_tool_instruction_wrong_mode():
    state = _state(workspace_root="/tmp", workspace_context_mode="full")
    assert planning_mcp_tool_instruction(state) == ""


def test_planning_mcp_tool_instruction_retrieve_with_mcp():
    state = _state(
        workspace_root="/tmp",
        workspace_context_mode="retrieve",
        agent_config={"mcp": {"servers": ["srv"]}},
    )
    result = planning_mcp_tool_instruction(state)
    assert "MCP filesystem" in result


def test_planning_mcp_tool_instruction_retrieve_mcp_fallback():
    state = _state(
        workspace_root="/tmp",
        workspace_context_mode="retrieve",
        workspace_context_mcp_fallback=True,
        agent_config={"mcp": {}},
    )
    result = planning_mcp_tool_instruction(state)
    assert "not configured" in result


def test_planning_mcp_tool_instruction_retrieve_no_mcp_no_fallback():
    state = _state(
        workspace_root="/tmp",
        workspace_context_mode="retrieve",
        agent_config={"mcp": {}},
    )
    result = planning_mcp_tool_instruction(state)
    assert result == ""


# ---------------------------------------------------------------------------
# _effective_spec_for_build
# ---------------------------------------------------------------------------

def test_effective_spec_for_build_returns_spec_output():
    state = _state(spec_output="the spec")
    assert _effective_spec_for_build(state) == "the spec"


def test_effective_spec_for_build_merges_pm_ba_arch():
    state = _state(pm_output="PM", ba_output="BA", arch_output="ARCH")
    result = _effective_spec_for_build(state)
    assert "PM" in result
    assert "BA" in result
    assert "ARCH" in result


def test_effective_spec_for_build_empty_all():
    state = _state()
    assert _effective_spec_for_build(state) == ""


def test_effective_spec_for_build_only_ba():
    state = _state(ba_output="BA only")
    result = _effective_spec_for_build(state)
    assert "BA only" in result


# ---------------------------------------------------------------------------
# _spec_arch_context_for_docs
# ---------------------------------------------------------------------------

def test_spec_arch_context_for_docs_both():
    state = _state(spec_output="SPEC", arch_output="ARCH")
    result = _spec_arch_context_for_docs(state)
    assert "SPEC" in result
    assert "ARCH" in result


def test_spec_arch_context_for_docs_empty():
    state = _state()
    assert _spec_arch_context_for_docs(state) == ""


def test_spec_arch_context_for_docs_truncates():
    state = _state(spec_output="x" * 20000)
    result = _spec_arch_context_for_docs(state, max_each=100)
    assert len(result) < 300


# ---------------------------------------------------------------------------
# _spec_for_build_mcp_safe
# ---------------------------------------------------------------------------

def test_spec_for_build_mcp_safe_no_mcp():
    state = _state(spec_output="full spec", workspace_root="", workspace_context_mode="full")
    result = _spec_for_build_mcp_safe(state)
    assert result == "full spec"


def test_spec_for_build_mcp_safe_truncates_when_mcp(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_SPEC_MAX_CHARS", "20")
    state = _state(
        spec_output="x" * 200,
        workspace_root="/tmp",
        workspace_context_mode="retrieve",
        agent_config={"mcp": {"servers": ["srv"]}},
    )
    result = _spec_for_build_mcp_safe(state)
    assert "truncated" in result
    assert len(result) <= 20 + 100


# ---------------------------------------------------------------------------
# should_use_compact_build_pipeline_input
# ---------------------------------------------------------------------------

def test_should_use_compact_tools_only_with_root():
    state = _state(workspace_root="/tmp", workspace_context_mode="tools_only")
    assert should_use_compact_build_pipeline_input(state) is True


def test_should_use_compact_retrieve_with_root():
    state = _state(workspace_root="/tmp", workspace_context_mode="retrieve")
    assert should_use_compact_build_pipeline_input(state) is True


def test_should_use_compact_full_mode():
    state = _state(workspace_root="/tmp", workspace_context_mode="full")
    assert should_use_compact_build_pipeline_input(state) is False


def test_should_use_compact_post_analysis_weak_code_analysis():
    state = _state(workspace_context_mode="post_analysis_compact", code_analysis={})
    assert should_use_compact_build_pipeline_input(state) is False


def test_should_use_compact_post_analysis_with_files():
    state = _state(
        workspace_context_mode="post_analysis_compact",
        code_analysis={"files": [{"path": "a.py"}]},
    )
    assert should_use_compact_build_pipeline_input(state) is True


def test_should_use_compact_fix_cycle_dev_even_in_full_mode():
    state = _state(
        workspace_root="/tmp",
        workspace_context_mode="full",
        _current_step_id="dev",
        pipeline_phase="FIX",
        open_defects=[{"id": "d1"}],
    )
    assert should_use_compact_build_pipeline_input(state) is True


# ---------------------------------------------------------------------------
# build_compact_build_phase_user_context
# ---------------------------------------------------------------------------

def test_build_compact_build_phase_basic():
    state = _state(
        user_task="implement X",
        project_manifest="manifest text",
        workspace_root="/proj",
        workspace_context_mode="retrieve",
        agent_config={"mcp": {"servers": []}},
        code_analysis={"files": [{"path": "a.py"}]},
    )
    result = build_compact_build_phase_user_context(state)
    assert "implement X" in result
    assert "manifest text" in result


def test_build_compact_build_phase_omits_mcp_wording_when_no_mcp_servers():
    state = _state(
        user_task="implement X",
        workspace_root="/proj",
        workspace_context_mode="retrieve",
        agent_config={"mcp": {"servers": []}},
        code_analysis={"files": [{"path": "a.py"}]},
    )
    result = build_compact_build_phase_user_context(state)
    assert _compact_workspace_template("mcp_available") not in result
    assert _compact_workspace_template("mcp_unavailable") in result


def test_build_compact_build_phase_omits_mcp_wording_after_tool_call_failure():
    state = _state(
        user_task="implement X",
        workspace_root="/proj",
        workspace_context_mode="retrieve",
        agent_config={"mcp": {"servers": [{"name": "workspace"}]}},
        mcp_tool_call_suspected_failure=True,
        code_analysis={"files": [{"path": "a.py"}]},
    )
    result = build_compact_build_phase_user_context(state)
    assert _compact_workspace_template("mcp_available") not in result
    assert _compact_workspace_template("mcp_unavailable") in result


def test_build_compact_build_phase_keeps_mcp_wording_when_mcp_active():
    state = _state(
        user_task="implement X",
        workspace_root="/proj",
        workspace_context_mode="retrieve",
        agent_config={"mcp": {"servers": [{"name": "workspace"}]}},
        code_analysis={"files": [{"path": "a.py"}]},
    )
    result = build_compact_build_phase_user_context(state)
    assert _compact_workspace_template("mcp_available") in result


def test_build_compact_build_phase_no_manifest():
    state = _state(user_task="task", workspace_root="/proj", workspace_context_mode="full")
    result = build_compact_build_phase_user_context(state)
    assert "task" in result


def test_build_compact_build_phase_prioritizes_relevant_paths():
    state = _state(
        user_task="fix service",
        workspace_root="/proj",
        workspace_context_mode="retrieve",
        _current_step_id="dev",
        code_analysis={
            "files": [
                {"path": "src/service.py"},
                {"path": "docs/readme.md"},
            ]
        },
        production_paths=["src"],
    )
    result = build_compact_build_phase_user_context(state)
    assert result.index("src/service.py") < result.index("docs/readme.md")


def test_build_compact_build_phase_includes_fix_cycle_summary():
    state = _state(
        user_task="fix bug",
        workspace_root="/proj",
        workspace_context_mode="full",
        _current_step_id="dev",
        pipeline_phase="FIX",
        open_defects=[{"id": "d1", "file_paths": ["src/service.py"]}],
        clustered_open_defects=[{"cluster_key": "logic", "count": 2, "file_paths": ["src/service.py"]}],
        verification_gates=[{"gate_name": "stub_gate", "passed": False}],
        step_retries={"dev": 1},
        step_feedback={"review_dev": ["Needs real fix, not placeholder"]},
        code_analysis={"files": [{"path": "src/service.py"}]},
    )
    result = build_compact_build_phase_user_context(state)
    assert "Fix cycle context reset" in result
    assert "Open defect clusters" in result
    assert "Failed trusted checks: stub_gate" in result
    assert "Needs real fix" in result


# ---------------------------------------------------------------------------
# build_phase_pipeline_user_context
# ---------------------------------------------------------------------------

def test_build_phase_pipeline_user_context_compact():
    state = _state(
        workspace_root="/tmp",
        workspace_context_mode="retrieve",
        agent_config={"mcp": {"servers": []}},
        user_task="do X",
        code_analysis={"files": [{"path": "x.py"}]},
    )
    result = build_phase_pipeline_user_context(state)
    assert "do X" in result


def test_build_phase_pipeline_user_context_full():
    state = _state(input="full input", workspace_context_mode="full")
    assert build_phase_pipeline_user_context(state) == "full input"


# ---------------------------------------------------------------------------
# planning_pipeline_user_context
# ---------------------------------------------------------------------------

def test_planning_pipeline_user_context_returns_input():
    state = _state(input="user message")
    assert planning_pipeline_user_context(state) == "user message"


def test_planning_pipeline_user_context_includes_source_research():
    state = _state(
        input="user message",
        source_research_output='{"summary":"Found event directories."}',
    )
    result = planning_pipeline_user_context(state)
    assert "[External source research brief]" in result
    assert "Found event directories" in result
    assert result.endswith("user message")


def test_planning_pipeline_user_context_empty():
    state = _state()
    assert planning_pipeline_user_context(state) == ""


# ---------------------------------------------------------------------------
# _should_compact_for_reviewer
# ---------------------------------------------------------------------------

def test_should_compact_for_reviewer_true():
    state = _state(workspace_root="/tmp", workspace_context_mode="retrieve",
                   agent_config={"mcp": {"servers": []}},
                   code_analysis={"files": [{"path": "x.py"}]})
    assert _should_compact_for_reviewer("review_dev", state) is True


def test_should_compact_for_reviewer_non_review_node():
    state = _state(workspace_root="/tmp", workspace_context_mode="retrieve",
                   agent_config={"mcp": {"servers": []}},
                   code_analysis={"files": [{"path": "x.py"}]})
    assert _should_compact_for_reviewer("pm", state) is False


def test_should_compact_for_reviewer_no_compact_mode():
    state = _state(workspace_context_mode="full")
    assert _should_compact_for_reviewer("review_dev", state) is False


# ---------------------------------------------------------------------------
# embedded_pipeline_input_for_review
# ---------------------------------------------------------------------------

def test_embedded_pipeline_input_for_review_returns_task_when_mcp():
    state = _state(
        workspace_root="/tmp",
        workspace_context_mode="retrieve",
        agent_config={"mcp": {"servers": ["srv"]}},
        user_task="task X",
        input="full input",
    )
    result = embedded_pipeline_input_for_review(state, log_node="review_dev")
    assert result == "task X"


def test_embedded_pipeline_input_for_review_truncates(monkeypatch):
    monkeypatch.setenv("SWARM_REVIEW_PIPELINE_INPUT_MAX_CHARS", "10")
    state = _state(input="x" * 100, workspace_context_mode="full")
    result = embedded_pipeline_input_for_review(state, log_node="review_dev")
    assert "truncated" in result
    assert result.startswith("x" * 10)


def test_embedded_pipeline_input_for_review_short_passthrough():
    state = _state(input="short", workspace_context_mode="full")
    result = embedded_pipeline_input_for_review(state, log_node="review_dev")
    assert result == "short"


# ---------------------------------------------------------------------------
# embedded_review_artifact
# ---------------------------------------------------------------------------

def test_embedded_review_artifact_short_passthrough():
    state = _state(workspace_context_mode="full")
    result = embedded_review_artifact(
        state, "hello", log_node="n", part_name="p",
        env_name="SOME_VAR", default_max=1000,
    )
    assert result == "hello"


def test_embedded_review_artifact_truncates(monkeypatch):
    monkeypatch.setenv("TEST_MAX_CHARS", "5")
    state = _state(workspace_context_mode="full")
    result = embedded_review_artifact(
        state, "x" * 100, log_node="n", part_name="p",
        env_name="TEST_MAX_CHARS", default_max=1000,
    )
    assert "truncated" in result
    assert result.startswith("xxxxx")


def test_embedded_review_artifact_mcp_uses_mcp_max():
    state = _state(
        workspace_root="/tmp",
        workspace_context_mode="retrieve",
        agent_config={"mcp": {"servers": ["srv"]}},
    )
    # mcp_max=10; text is 50 chars → should truncate
    result = embedded_review_artifact(
        state, "y" * 50, log_node="n", part_name="p",
        env_name="NO_SUCH_ENV_VAR_12345", default_max=5000, mcp_max=10,
    )
    assert "truncated" in result


# ---------------------------------------------------------------------------
# _doc_spec_max_each_chars / _doc_chain_spec_max_chars / _doc_generate_second_pass_analysis_max_chars
# ---------------------------------------------------------------------------

def test_doc_spec_max_each_chars_default(monkeypatch):
    monkeypatch.delenv("SWARM_DOC_SPEC_MAX_CHARS", raising=False)
    assert _doc_spec_max_each_chars() == 12000


def test_doc_chain_spec_max_chars_default(monkeypatch):
    monkeypatch.delenv("SWARM_DOC_CHAIN_SPEC_MAX_CHARS", raising=False)
    assert _doc_chain_spec_max_chars() == 24000


def test_doc_generate_second_pass_analysis_max_chars_default(monkeypatch):
    monkeypatch.delenv("SWARM_DOCUMENTATION_DOC_PASS_MAX_ANALYSIS_CHARS", raising=False)
    assert _doc_generate_second_pass_analysis_max_chars() == 9000
