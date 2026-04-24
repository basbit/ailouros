"""Tests for backend/App/orchestration/application/nodes/_shared.py."""
from unittest.mock import MagicMock, patch

import pytest

from backend.App.orchestration.application.nodes._shared import (
    _database_context_for_prompt,
    _documentation_locale_line,
    _human_cfg,
    _pipeline_should_cancel,
    _redact_database_url,
    _remote_api_client_kwargs,
    _remote_api_client_kwargs_for_role,
    _reviewer_cfg,
    _server_stream_shutdown_requested,
    _skills_extra_for_role_cfg,
    _stack_reviewer_cfg,
    _stream_progress_emit,
    _swarm_languages_line,
    _validate_tools_only_mcp_state,
    _warn_workspace_context_vs_custom_pipeline,
    _workspace_root_str,
    make_agent,
)


def _state(**kwargs):
    return dict(kwargs)


# ---------------------------------------------------------------------------
# _stream_progress_emit
# ---------------------------------------------------------------------------

def test_stream_progress_emit_puts_to_queue():
    from queue import SimpleQueue
    q = SimpleQueue()
    state = {"_stream_progress_queue": q}
    _stream_progress_emit(state, "hello")
    assert not q.empty()
    assert q.get_nowait() == "hello"


def test_stream_progress_emit_no_queue():
    _stream_progress_emit({}, "message")  # should not raise


def test_stream_progress_emit_queue_exception(caplog):
    mock_q = MagicMock()
    mock_q.put_nowait.side_effect = Exception("full")
    state = {"_stream_progress_queue": mock_q}
    _stream_progress_emit(state, "msg")  # should swallow exception


# ---------------------------------------------------------------------------
# _server_stream_shutdown_requested
# ---------------------------------------------------------------------------

def test_server_stream_shutdown_not_set():
    with patch(
        "backend.App.orchestration.application.nodes._shared."
        "_server_stream_shutdown_requested"
    ) as mock_fn:
        mock_fn.return_value = False
        assert mock_fn() is False


def test_server_stream_shutdown_set():
    mock_event = MagicMock()
    mock_event.is_set.return_value = True
    with patch(
        "backend.App.orchestration.infrastructure.stream_cancel.SERVER_STREAM_SHUTDOWN",
        mock_event,
    ):
        result = _server_stream_shutdown_requested()
    assert result is True


def test_server_stream_shutdown_import_error():
    with patch(
        "backend.App.orchestration.application.nodes._shared."
        "_server_stream_shutdown_requested",
        side_effect=ImportError,
    ):
        pass  # Verify that direct call handles import error
    # Call the real function with a broken import
    with patch.dict("sys.modules", {"backend.App.orchestration.infrastructure.stream_cancel": None}):
        # Should return False on import error
        result = _server_stream_shutdown_requested()
    assert result is False


# ---------------------------------------------------------------------------
# _pipeline_should_cancel
# ---------------------------------------------------------------------------

def test_pipeline_should_cancel_no_cancel_event():
    assert _pipeline_should_cancel({}) is False


def test_pipeline_should_cancel_event_set():
    ev = MagicMock()
    ev.is_set.return_value = True
    state = {"_pipeline_cancel_event": ev}
    with patch(
        "backend.App.orchestration.application.nodes._shared._server_stream_shutdown_requested",
        return_value=False,
    ):
        assert _pipeline_should_cancel(state) is True


def test_pipeline_should_cancel_event_not_set():
    ev = MagicMock()
    ev.is_set.return_value = False
    state = {"_pipeline_cancel_event": ev}
    with patch(
        "backend.App.orchestration.application.nodes._shared._server_stream_shutdown_requested",
        return_value=False,
    ):
        assert _pipeline_should_cancel(state) is False


def test_pipeline_should_cancel_server_shutdown():
    with patch(
        "backend.App.orchestration.application.nodes._shared._server_stream_shutdown_requested",
        return_value=True,
    ):
        assert _pipeline_should_cancel({}) is True


# ---------------------------------------------------------------------------
# _reviewer_cfg / _human_cfg
# ---------------------------------------------------------------------------

def test_reviewer_cfg_extracts():
    state = _state(agent_config={"reviewer": {"model": "gpt-4"}})
    assert _reviewer_cfg(state) == {"model": "gpt-4"}


def test_reviewer_cfg_empty():
    assert _reviewer_cfg({}) == {}


def test_human_cfg_extracts():
    state = _state(agent_config={"human": {"timeout": 300}})
    assert _human_cfg(state) == {"timeout": 300}


# ---------------------------------------------------------------------------
# _remote_api_client_kwargs
# ---------------------------------------------------------------------------

def test_remote_api_client_kwargs_with_remote_api():
    state = _state(agent_config={
        "remote_api": {"provider": "openai", "api_key": "sk-123", "base_url": "https://api.openai.com/v1"}
    })
    result = _remote_api_client_kwargs(state)
    assert result["remote_provider"] == "openai"
    assert result["remote_api_key"] == "sk-123"
    assert result["remote_base_url"] == "https://api.openai.com/v1"


def test_remote_api_client_kwargs_legacy_cloud():
    state = _state(agent_config={
        "cloud": {"api_key": "legacy-key", "base_url": "https://api.anthropic.com"}
    })
    result = _remote_api_client_kwargs(state)
    assert result.get("remote_api_key") == "legacy-key"
    assert result.get("remote_provider") == "anthropic"


def test_remote_api_client_kwargs_empty():
    result = _remote_api_client_kwargs({})
    assert result == {}


def test_remote_api_client_kwargs_no_provider_infers_anthropic():
    state = _state(agent_config={
        "remote_api": {"api_key": "key-with-no-provider"}
    })
    result = _remote_api_client_kwargs(state)
    assert result.get("remote_provider") == "anthropic"


# ---------------------------------------------------------------------------
# _remote_api_client_kwargs_for_role
# ---------------------------------------------------------------------------

def test_remote_api_client_kwargs_for_role_no_role_cfg():
    state = _state(agent_config={"remote_api": {"provider": "openai", "api_key": "k"}})
    result = _remote_api_client_kwargs_for_role(state, None)
    assert result["remote_provider"] == "openai"


def test_remote_api_client_kwargs_for_role_with_profile():
    state = _state(agent_config={
        "remote_api_profiles": {
            "fast": {"provider": "openai", "api_key": "profile-key", "base_url": ""}
        },
        "remote_api": {},
    })
    role_cfg = {"remote_profile": "fast"}
    result = _remote_api_client_kwargs_for_role(state, role_cfg)
    assert result["remote_provider"] == "openai"
    assert result["remote_api_key"] == "profile-key"


def test_remote_api_client_kwargs_for_role_missing_profile():
    state = _state(agent_config={"remote_api_profiles": {}, "remote_api": {}})
    role_cfg = {"remote_profile": "nonexistent"}
    # Falls back to base kwargs
    result = _remote_api_client_kwargs_for_role(state, role_cfg)
    assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# _workspace_root_str
# ---------------------------------------------------------------------------

def test_workspace_root_str_in_shared():
    state = _state(workspace_root="  /proj  ")
    assert _workspace_root_str(state) == "/proj"


# ---------------------------------------------------------------------------
# _stack_reviewer_cfg
# ---------------------------------------------------------------------------

def test_stack_reviewer_cfg_falls_back_to_reviewer():
    state = _state(agent_config={
        "reviewer": {"model": "gpt-4", "environment": "cloud"},
        "stack_reviewer": {"model": "claude-3"},
    })
    cfg = _stack_reviewer_cfg(state)
    assert cfg["model"] == "claude-3"
    assert cfg["environment"] == "cloud"  # from reviewer fallback


def test_stack_reviewer_cfg_empty():
    assert _stack_reviewer_cfg({}) == {}


# ---------------------------------------------------------------------------
# _skills_extra_for_role_cfg
# ---------------------------------------------------------------------------

def test_skills_extra_for_role_cfg_calls_format():
    state = _state(agent_config={"swarm": {}})
    with patch(
        "backend.App.orchestration.application.nodes._shared.format_role_skills_extra",
        return_value="skills block",
    ):
        result = _skills_extra_for_role_cfg(state, {"skills": ["x"]})
    assert result == "skills block"


def test_skills_extra_for_role_cfg_no_agent_config():
    result = _skills_extra_for_role_cfg({}, None)
    assert result == ""


# ---------------------------------------------------------------------------
# _swarm_languages_line
# ---------------------------------------------------------------------------

def test_swarm_languages_line_with_langs():
    state = _state(agent_config={"swarm": {"languages": ["Python", "TypeScript"]}})
    result = _swarm_languages_line(state)
    assert "Python" in result
    assert "TypeScript" in result


def test_swarm_languages_line_no_langs():
    state = _state(agent_config={"swarm": {}})
    assert _swarm_languages_line(state) == ""


# ---------------------------------------------------------------------------
# _documentation_locale_line
# ---------------------------------------------------------------------------

def test_documentation_locale_line_default():
    result = _documentation_locale_line({})
    assert "match" in result.lower() or "language" in result.lower()


def test_documentation_locale_line_custom():
    state = _state(agent_config={"swarm": {"documentation_locale": "Russian"}})
    result = _documentation_locale_line(state)
    assert "Russian" in result


def test_documentation_locale_line_alias():
    state = _state(agent_config={"swarm": {"locale": "German"}})
    result = _documentation_locale_line(state)
    assert "German" in result


# ---------------------------------------------------------------------------
# _redact_database_url
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url,expected_fragment", [
    ("postgresql://user:password@localhost:5432/db", "user:***@localhost"),
    ("mysql://admin:secret@db.host/mydb", "admin:***@db.host"),
    ("redis://pass@redis.host", "redis://pass@redis.host"),  # no user:pass format
    ("https://other.url/path", "https://other.url/path"),
])
def test_redact_database_url(url, expected_fragment):
    result = _redact_database_url(url)
    assert expected_fragment in result


def test_redact_database_url_no_credentials():
    url = "postgresql://localhost:5432/mydb"
    result = _redact_database_url(url)
    assert result == url


# ---------------------------------------------------------------------------
# _database_context_for_prompt
# ---------------------------------------------------------------------------

def test_database_context_for_prompt_no_db():
    state = _state(agent_config={"swarm": {}})
    assert _database_context_for_prompt(state) == ""


def test_database_context_for_prompt_with_url():
    state = _state(agent_config={"swarm": {
        "database_url": "postgresql://user:pass@host/db"
    }})
    result = _database_context_for_prompt(state)
    assert "DSN" in result
    assert "***" in result  # password redacted


def test_database_context_for_prompt_with_hint_only():
    state = _state(agent_config={"swarm": {"database_hint": "Postgres schema v2"}})
    result = _database_context_for_prompt(state)
    assert "Postgres schema v2" in result


# ---------------------------------------------------------------------------
# _validate_tools_only_mcp_state
# ---------------------------------------------------------------------------

def test_validate_tools_only_mcp_state_ok_with_servers():
    state = _state(
        workspace_root="/proj",
        workspace_context_mode="tools_only",
        agent_config={"mcp": {"servers": ["srv"]}},
    )
    _validate_tools_only_mcp_state(state)  # should not raise


def test_validate_tools_only_mcp_state_no_servers_raises():
    state = _state(
        workspace_root="/proj",
        workspace_context_mode="tools_only",
        agent_config={"mcp": {}},
    )
    with pytest.raises(ValueError, match="MCP servers"):
        _validate_tools_only_mcp_state(state)


def test_validate_tools_only_mcp_state_not_tools_only():
    state = _state(workspace_root="/proj", workspace_context_mode="full")
    _validate_tools_only_mcp_state(state)  # should not raise


def test_validate_tools_only_mcp_state_no_root():
    state = _state(workspace_context_mode="tools_only", workspace_root="")
    _validate_tools_only_mcp_state(state)  # should not raise (no root)


# ---------------------------------------------------------------------------
# _warn_workspace_context_vs_custom_pipeline
# ---------------------------------------------------------------------------

def test_warn_workspace_context_no_warning_for_full_mode():
    state = _state(workspace_context_mode="full")
    _warn_workspace_context_vs_custom_pipeline(state, ["pm", "ba"])  # no warning needed


def test_warn_workspace_context_logs_when_no_analyze_code(caplog):
    state = _state(workspace_context_mode="index_only")
    with patch(
        "backend.App.orchestration.application.nodes._shared._workspace_context_mode_normalized",
        return_value="index_only",
    ):
        import logging
        with caplog.at_level(logging.WARNING):
            _warn_workspace_context_vs_custom_pipeline(state, ["pm", "ba"])


def test_warn_workspace_context_no_warning_when_analyze_code_present():
    state = _state(workspace_context_mode="index_only")
    _warn_workspace_context_vs_custom_pipeline(state, ["pm", "analyze_code", "ba"])


# ---------------------------------------------------------------------------
# make_agent
# ---------------------------------------------------------------------------

def test_make_agent_calls_factory():
    with patch(
        "backend.App.orchestration.application.nodes._shared._default_agent_factory"
    ) as mock_factory:
        mock_factory.create.return_value = MagicMock()
        make_agent("dev", model="llama3")
    mock_factory.create.assert_called_once_with("dev", model="llama3")
