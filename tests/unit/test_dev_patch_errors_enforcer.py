from unittest.mock import MagicMock

from backend.App.orchestration.application.enforcement.dev_patch_errors_enforcer import (
    enforce_dev_patch_errors,
    _build_file_context_blocks,
    _extract_failed_file_paths,
    _force_swarm_file_threshold,
    _format_patch_errors_for_reprompt,
    _max_patch_retries,
    _read_current_file_content,
    _update_per_file_failure_counts,
)


def _consume(generator):
    events = list(generator)
    return events


def test_enforce_dev_patch_errors_no_errors_returns_empty(monkeypatch):
    monkeypatch.setenv("SWARM_MAX_DEV_PATCH_RETRIES", "2")
    state = {"_dev_patch_errors_for_retry": []}
    events = _consume(enforce_dev_patch_errors(
        state,
        resolve_step=MagicMock(),
        base_agent_config={},
        run_step_with_stream_progress=MagicMock(return_value=iter([])),
        emit_completed=MagicMock(return_value={}),
    ))
    assert events == []


def test_enforce_dev_patch_errors_reprompts_dev(monkeypatch):
    monkeypatch.setenv("SWARM_MAX_DEV_PATCH_RETRIES", "2")
    state = {
        "_dev_patch_errors_for_retry": ["patch 'a.cs': SEARCH not found"],
    }
    dev_func = MagicMock()
    resolve_step = MagicMock(return_value=("dev", dev_func))
    run_step_with_stream_progress = MagicMock(
        return_value=iter([{"agent": "dev", "status": "progress", "message": "rerun"}])
    )
    emit_completed = MagicMock(return_value={"agent": "dev", "status": "completed"})

    events = _consume(enforce_dev_patch_errors(
        state,
        resolve_step=resolve_step,
        base_agent_config={},
        run_step_with_stream_progress=run_step_with_stream_progress,
        emit_completed=emit_completed,
    ))

    assert any(event.get("agent") == "orchestrator" for event in events)
    assert any(event.get("agent") == "dev" and event.get("status") == "completed"
               for event in events)
    assert state["_dev_patch_retry_count"] == 1
    assert "_dev_patch_errors_for_retry" not in state
    assert resolve_step.called


def test_enforce_dev_patch_errors_respects_max_retries(monkeypatch):
    monkeypatch.setenv("SWARM_MAX_DEV_PATCH_RETRIES", "2")
    state = {
        "_dev_patch_errors_for_retry": ["error"],
        "_dev_patch_retry_count": 2,
    }
    resolve_step = MagicMock()
    events = _consume(enforce_dev_patch_errors(
        state,
        resolve_step=resolve_step,
        base_agent_config={},
        run_step_with_stream_progress=MagicMock(return_value=iter([])),
        emit_completed=MagicMock(return_value={}),
    ))

    assert events == []
    assert "_dev_patch_errors_for_retry" not in state
    assert not resolve_step.called


def test_format_patch_errors_for_reprompt():
    formatted = _format_patch_errors_for_reprompt([
        "patch 'a.cs': hunk 1 failed",
        "patch 'b.cs': no separator",
    ])
    assert "patch 'a.cs': hunk 1 failed" in formatted
    assert "patch 'b.cs': no separator" in formatted
    assert formatted.count("  -") == 2


def test_max_patch_retries_env_override(monkeypatch):
    monkeypatch.setenv("SWARM_MAX_DEV_PATCH_RETRIES", "5")
    assert _max_patch_retries() == 5


def test_max_patch_retries_fallback(monkeypatch):
    monkeypatch.delenv("SWARM_MAX_DEV_PATCH_RETRIES", raising=False)
    monkeypatch.setenv("SWARM_MAX_STEP_RETRIES", "3")
    assert _max_patch_retries() == 3


def test_max_patch_retries_default(monkeypatch):
    monkeypatch.delenv("SWARM_MAX_DEV_PATCH_RETRIES", raising=False)
    monkeypatch.delenv("SWARM_MAX_STEP_RETRIES", raising=False)
    assert _max_patch_retries() == 2


def test_extract_failed_file_paths_single_error():
    errors = ["patch 'Managers/GameFlowManager.cs': hunk 1: SEARCH must occur exactly 1 time, found 0"]
    paths = _extract_failed_file_paths(errors)
    assert paths == ["Managers/GameFlowManager.cs"]


def test_extract_failed_file_paths_dedupes():
    errors = [
        "patch 'A.cs': hunk 1 failed",
        "patch 'A.cs': hunk 2 failed",
        "patch 'B.cs': SEARCH not found",
    ]
    paths = _extract_failed_file_paths(errors)
    assert paths == ["A.cs", "B.cs"]


def test_extract_failed_file_paths_handles_no_path():
    errors = ["some generic error", "another one"]
    paths = _extract_failed_file_paths(errors)
    assert paths == []


def test_read_current_file_content_reads_existing_file(tmp_path):
    test_file = tmp_path / "foo.txt"
    test_file.write_text("hello world", encoding="utf-8")
    content, was_read = _read_current_file_content(str(tmp_path), "foo.txt", 1000)
    assert was_read
    assert content == "hello world"


def test_read_current_file_content_truncates_long_file(tmp_path):
    test_file = tmp_path / "big.txt"
    test_file.write_text("x" * 5000, encoding="utf-8")
    content, was_read = _read_current_file_content(str(tmp_path), "big.txt", 100)
    assert was_read
    assert "[file truncated at 100 chars]" in content
    assert content.startswith("x" * 100)


def test_read_current_file_content_rejects_traversal(tmp_path):
    sibling_dir = tmp_path.parent
    secret = sibling_dir / "secret.txt"
    secret.write_text("topsecret", encoding="utf-8")
    try:
        content, was_read = _read_current_file_content(
            str(tmp_path), "../secret.txt", 1000,
        )
        assert not was_read
        assert content == ""
    finally:
        if secret.exists():
            secret.unlink()


def test_read_current_file_content_missing_file_returns_empty(tmp_path):
    content, was_read = _read_current_file_content(str(tmp_path), "does_not_exist.cs", 1000)
    assert not was_read
    assert content == ""


def test_update_per_file_failure_counts_accumulates():
    state_dict: dict = {}
    counts = _update_per_file_failure_counts(state_dict, ["A.cs", "B.cs"])
    assert counts["A.cs"] == 1
    assert counts["B.cs"] == 1
    counts2 = _update_per_file_failure_counts(state_dict, ["A.cs"])
    assert counts2["A.cs"] == 2
    assert counts2["B.cs"] == 1


def test_build_file_context_blocks_forces_swarm_file_after_threshold(tmp_path):
    test_file = tmp_path / "Foo.cs"
    test_file.write_text("public class Foo {}", encoding="utf-8")
    counts = {"Foo.cs": 2}
    block, force_paths = _build_file_context_blocks(
        str(tmp_path), ["Foo.cs"], counts,
        force_swarm_file_threshold=2,
        max_chars_per_file=1000,
    )
    assert "Foo.cs" in block
    assert "REQUIRED STRATEGY" in block
    assert "<swarm_file path='Foo.cs'>" in block
    assert force_paths == ["Foo.cs"]


def test_build_file_context_blocks_below_threshold_uses_patch_strategy(tmp_path):
    test_file = tmp_path / "Bar.cs"
    test_file.write_text("public class Bar {}", encoding="utf-8")
    counts = {"Bar.cs": 1}
    block, force_paths = _build_file_context_blocks(
        str(tmp_path), ["Bar.cs"], counts,
        force_swarm_file_threshold=2,
        max_chars_per_file=1000,
    )
    assert "Bar.cs" in block
    assert "small, unique SEARCH anchors" in block
    assert force_paths == []


def test_build_file_context_blocks_handles_missing_file(tmp_path):
    counts = {"NewFile.cs": 1}
    block, force_paths = _build_file_context_blocks(
        str(tmp_path), ["NewFile.cs"], counts,
        force_swarm_file_threshold=2,
        max_chars_per_file=1000,
    )
    assert "does not exist on disk yet" in block
    assert force_paths == []


def test_force_swarm_file_threshold_default(monkeypatch):
    monkeypatch.delenv("SWARM_FORCE_SWARM_FILE_AFTER_N_FAILS", raising=False)
    assert _force_swarm_file_threshold() == 0


def test_force_swarm_file_threshold_env_override(monkeypatch):
    monkeypatch.setenv("SWARM_FORCE_SWARM_FILE_AFTER_N_FAILS", "3")
    assert _force_swarm_file_threshold() == 3


def test_build_file_context_blocks_does_not_force_when_threshold_disabled(tmp_path):
    test_file = tmp_path / "NoRewrite.cs"
    test_file.write_text("public class NoRewrite {}", encoding="utf-8")
    counts = {"NoRewrite.cs": 99}
    block, force_paths = _build_file_context_blocks(
        str(tmp_path), ["NoRewrite.cs"], counts,
        force_swarm_file_threshold=0,
        max_chars_per_file=1000,
    )
    assert "Do not rewrite the entire existing file" in block
    assert "MANDATORY" not in block
    assert force_paths == []


def test_enforce_dev_patch_errors_includes_file_content_in_reprompt(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_MAX_DEV_PATCH_RETRIES", "2")
    test_file = tmp_path / "X.cs"
    test_file.write_text("class X {}", encoding="utf-8")

    captured_reprompts: list[str] = []

    def capture_reprompt(step_id, dev_func, state):
        captured_reprompts.append(state.get("_swarm_file_reprompt") or "")
        yield {"agent": "dev", "status": "progress"}

    state = {
        "_dev_patch_errors_for_retry": [
            "patch 'X.cs': hunk 1: SEARCH must occur exactly 1 time, found 0"
        ],
        "workspace_root": str(tmp_path),
    }
    list(enforce_dev_patch_errors(
        state,
        resolve_step=MagicMock(return_value=("dev", MagicMock())),
        base_agent_config={},
        run_step_with_stream_progress=capture_reprompt,
        emit_completed=MagicMock(return_value={"agent": "dev", "status": "completed"}),
    ))
    assert captured_reprompts
    reprompt = captured_reprompts[0]
    assert "X.cs" in reprompt
    assert "class X {}" in reprompt
    assert "Current actual file content" in reprompt


def test_enforce_dev_patch_errors_forces_swarm_file_on_repeat_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_MAX_DEV_PATCH_RETRIES", "5")
    monkeypatch.setenv("SWARM_FORCE_SWARM_FILE_AFTER_N_FAILS", "2")
    test_file = tmp_path / "Y.cs"
    test_file.write_text("class Y {}", encoding="utf-8")

    captured_reprompts: list[str] = []

    def capture_reprompt(step_id, dev_func, state):
        captured_reprompts.append(state.get("_swarm_file_reprompt") or "")
        yield {"agent": "dev", "status": "progress"}

    state = {
        "_dev_patch_errors_for_retry": ["patch 'Y.cs': hunk 1 failed"],
        "workspace_root": str(tmp_path),
        "_dev_patch_per_file_failures": {"Y.cs": 1},
    }
    list(enforce_dev_patch_errors(
        state,
        resolve_step=MagicMock(return_value=("dev", MagicMock())),
        base_agent_config={},
        run_step_with_stream_progress=capture_reprompt,
        emit_completed=MagicMock(return_value={"agent": "dev", "status": "completed"}),
    ))
    assert captured_reprompts
    reprompt = captured_reprompts[0]
    assert "MANDATORY: full file rewrite" in reprompt
    assert "Y.cs" in reprompt
    assert state["_dev_patch_per_file_failures"]["Y.cs"] == 2
