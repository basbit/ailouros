"""Tests for backend/App/orchestration/application/review_moa.py."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.App.orchestration.domain.defect import parse_defect_report
from backend.App.shared.domain.validators import is_truthy_value as _truthy
from backend.App.orchestration.application.agents.review_moa import (
    _moa_cfg,
    _panel_size,
    _reviewer_cfg,
    _with_semaphore,
    moa_enabled_for_step,
    run_reviewer_or_moa,
)


# ---------------------------------------------------------------------------
# _truthy
# ---------------------------------------------------------------------------

def test_truthy_true_bool():
    assert _truthy(True) is True


def test_truthy_string_true_variants():
    for val in ("1", "true", "yes", "on", "True", "YES", "ON"):
        assert _truthy(val) is True


def test_truthy_false_values():
    assert _truthy(False) is False
    assert _truthy("0") is False
    assert _truthy("false") is False
    assert _truthy("no") is False
    assert _truthy("off") is False
    assert _truthy("") is False


def test_truthy_none():
    assert _truthy(None) is False


def test_truthy_integer():
    assert _truthy(1) is True


# ---------------------------------------------------------------------------
# _reviewer_cfg
# ---------------------------------------------------------------------------

def test_reviewer_cfg_basic():
    state = {"agent_config": {"reviewer": {"model": "claude"}}}
    assert _reviewer_cfg(state) == {"model": "claude"}


def test_reviewer_cfg_missing_reviewer():
    state = {"agent_config": {"dev": {"model": "llama3"}}}
    assert _reviewer_cfg(state) == {}


def test_reviewer_cfg_no_agent_config():
    assert _reviewer_cfg({}) == {}


def test_reviewer_cfg_non_dict_reviewer():
    state = {"agent_config": {"reviewer": "not-a-dict"}}
    assert _reviewer_cfg(state) == {}


# ---------------------------------------------------------------------------
# _moa_cfg
# ---------------------------------------------------------------------------

def test_moa_cfg_present():
    state = {"agent_config": {"reviewer": {"moa": {"enabled": True, "panel_size": 3}}}}
    cfg = _moa_cfg(state)
    assert cfg["enabled"] is True
    assert cfg["panel_size"] == 3


def test_moa_cfg_absent():
    state = {"agent_config": {"reviewer": {"model": "claude"}}}
    assert _moa_cfg(state) == {}


def test_moa_cfg_no_state():
    assert _moa_cfg({}) == {}


# ---------------------------------------------------------------------------
# moa_enabled_for_step
# ---------------------------------------------------------------------------

def test_moa_enabled_for_step_disabled():
    state = {"agent_config": {"reviewer": {"moa": {"enabled": False}}}}
    assert moa_enabled_for_step(state, "review_dev") is False


def test_moa_enabled_for_step_enabled_all():
    state = {"agent_config": {"reviewer": {"moa": {"enabled": True}}}}
    assert moa_enabled_for_step(state, "review_dev") is True
    assert moa_enabled_for_step(state, "review_pm") is True


def test_moa_enabled_for_step_specific_steps():
    state = {
        "agent_config": {
            "reviewer": {
                "moa": {"enabled": True, "steps": ["review_dev", "review_ba"]}
            }
        }
    }
    assert moa_enabled_for_step(state, "review_dev") is True
    assert moa_enabled_for_step(state, "review_pm") is False


def test_moa_enabled_for_step_empty():
    assert moa_enabled_for_step({}, "review_dev") is False


# ---------------------------------------------------------------------------
# _panel_size
# ---------------------------------------------------------------------------

def test_panel_size_default():
    state = {"agent_config": {"reviewer": {"moa": {}}}}
    assert _panel_size(state) == 3


def test_panel_size_custom():
    state = {"agent_config": {"reviewer": {"moa": {"panel_size": 5}}}}
    assert _panel_size(state) == 5


def test_panel_size_clamped_min():
    state = {"agent_config": {"reviewer": {"moa": {"panel_size": 1}}}}
    assert _panel_size(state) == 2


def test_panel_size_clamped_max():
    state = {"agent_config": {"reviewer": {"moa": {"panel_size": 100}}}}
    assert _panel_size(state) == 8


def test_panel_size_invalid_value():
    state = {"agent_config": {"reviewer": {"moa": {"panel_size": "bad"}}}}
    assert _panel_size(state) == 3


def test_panel_size_count_key():
    state = {"agent_config": {"reviewer": {"moa": {"count": 4}}}}
    assert _panel_size(state) == 4


# ---------------------------------------------------------------------------
# _with_semaphore
# ---------------------------------------------------------------------------

def test_with_semaphore_calls_fn():
    called = []

    def fn(x, y):
        called.append((x, y))
        return x + y

    result = _with_semaphore(fn, 2, 3)
    assert result == 5
    assert called == [(2, 3)]


def test_with_semaphore_releases_on_exception():
    from backend.App.orchestration.application.agents.review_moa import _parallel_semaphore
    sem = _parallel_semaphore()
    initial_value = sem._value

    def failing_fn():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        _with_semaphore(failing_fn)

    assert sem._value == initial_value  # semaphore was released


# ---------------------------------------------------------------------------
# run_reviewer_or_moa — single reviewer path
# ---------------------------------------------------------------------------

def test_run_reviewer_or_moa_single():
    mock_agent = MagicMock()
    mock_agent.run.return_value = "VERDICT: OK"
    mock_agent.used_model = "claude"
    mock_agent.used_provider = "anthropic"

    state = {}  # MoA disabled
    result = run_reviewer_or_moa(
        state,
        pipeline_step="review_dev",
        prompt="review this",
        output_key="dev_review_output",
        model_key="dev_review_model",
        provider_key="dev_review_provider",
        agent_factory=lambda: mock_agent,
    )
    assert result["dev_review_output"].startswith("VERDICT: OK")
    assert result["dev_review_model"] == "claude"
    assert result["dev_review_provider"] == "anthropic"


def test_run_reviewer_or_moa_repairs_missing_json_defect_report():
    agents = []
    for _ in range(2):
        mock_agent = MagicMock()
        mock_agent.used_model = "claude"
        mock_agent.used_provider = "anthropic"
        agents.append(mock_agent)

    repaired_output = (
        "Summary of issues.\n\n"
        "<defect_report>"
        "{\"defects\":[{\"id\":\"D1\",\"title\":\"Missing implementation\",\"severity\":\"P1\","
        "\"file_paths\":[\"backend/src/App/Post/Infrastructure/Parser/EventorEventParser.php\"],"
        "\"expected\":\"Parser implementation exists\",\"actual\":\"Only planning text was generated\","
        "\"repro_steps\":[\"Run dev step\"],\"acceptance\":[\"Create the parser file\"],"
        "\"category\":\"missing_implementation\",\"fixed\":false}],"
        "\"test_scenarios\":[],\"edge_cases\":[],\"regression_checks\":[]}"
        "</defect_report>\n"
        "VERDICT: NEEDS_WORK"
    )

    with patch(
        "backend.App.orchestration.application.agents.review_moa.run_agent_with_boundary",
        side_effect=["Summary only.\n\nVERDICT: NEEDS_WORK", repaired_output],
    ):
        result = run_reviewer_or_moa(
            {},
            pipeline_step="review_dev",
            prompt="review this",
            output_key="dev_review_output",
            model_key="dev_review_model",
            provider_key="dev_review_provider",
            agent_factory=lambda: agents.pop(0),
            require_json_defect_report=True,
        )

    report = parse_defect_report(result["dev_review_output"])
    assert report.has_blockers is True
    assert report.defects[0].title == "Missing implementation"


def test_run_reviewer_or_moa_synthesizes_blocker_when_repair_still_invalid():
    agents = []
    for _ in range(2):
        mock_agent = MagicMock()
        mock_agent.used_model = "claude"
        mock_agent.used_provider = "anthropic"
        agents.append(mock_agent)

    with patch(
        "backend.App.orchestration.application.agents.review_moa.run_agent_with_boundary",
        side_effect=[
            "No structured block.\n\nVERDICT: NEEDS_WORK",
            "Still no machine-readable block.\n\nVERDICT: NEEDS_WORK",
        ],
    ):
        result = run_reviewer_or_moa(
            {},
            pipeline_step="review_dev",
            prompt="review this",
            output_key="dev_review_output",
            model_key="dev_review_model",
            provider_key="dev_review_provider",
            agent_factory=lambda: agents.pop(0),
            require_json_defect_report=True,
        )

    report = parse_defect_report(result["dev_review_output"])
    assert report.has_blockers is True
    assert report.defects[0].category == "review_contract"


def test_run_reviewer_or_moa_moa_enabled():
    call_count = [0]

    def make_agent():
        mock = MagicMock()
        call_count[0] += 1
        mock.run.return_value = "VERDICT: OK"
        mock.used_model = "claude"
        mock.used_provider = "anthropic"
        return mock

    state = {
        "agent_config": {
            "reviewer": {
                "moa": {"enabled": True, "panel_size": 2}
            }
        }
    }

    with patch(
        "backend.App.orchestration.application.agents.review_moa.swarm_max_parallel_tasks",
        return_value=4,
    ):
        result = run_reviewer_or_moa(
            state,
            pipeline_step="review_dev",
            prompt="review this",
            output_key="dev_review_output",
            model_key="dev_review_model",
            provider_key="dev_review_provider",
            agent_factory=make_agent,
        )

    # MoA: 2 panelists + 1 aggregator = 3 agents created
    assert call_count[0] == 3
    assert "[MoA x2]" in result["dev_review_output"]
    assert "Panel" in result["dev_review_output"]


def test_run_reviewer_or_moa_moa_with_aggregator_hint():
    def make_agent():
        mock = MagicMock()
        mock.run.return_value = "VERDICT: OK summary"
        mock.used_model = "m"
        mock.used_provider = "p"
        return mock

    state = {
        "agent_config": {
            "reviewer": {
                "moa": {
                    "enabled": True,
                    "panel_size": 2,
                    "aggregator_hint": "Be concise.",
                }
            }
        }
    }

    with patch(
        "backend.App.orchestration.application.agents.review_moa.swarm_max_parallel_tasks",
        return_value=4,
    ):
        result = run_reviewer_or_moa(
            state,
            pipeline_step="review_dev",
            prompt="review",
            output_key="out",
            model_key="m",
            provider_key="p",
            agent_factory=make_agent,
        )
    assert "out" in result


def test_run_reviewer_or_moa_step_not_in_moa_steps():
    mock_agent = MagicMock()
    mock_agent.run.return_value = "VERDICT: OK"
    mock_agent.used_model = "m"
    mock_agent.used_provider = "p"

    state = {
        "agent_config": {
            "reviewer": {
                "moa": {
                    "enabled": True,
                    "steps": ["review_ba"],
                }
            }
        }
    }
    result = run_reviewer_or_moa(
        state,
        pipeline_step="review_dev",  # not in moa steps
        prompt="p",
        output_key="out",
        model_key="m",
        provider_key="p",
        agent_factory=lambda: mock_agent,
    )
    # Falls back to single reviewer (reviewer may append evidence notes)
    assert result["out"].startswith("VERDICT: OK")
