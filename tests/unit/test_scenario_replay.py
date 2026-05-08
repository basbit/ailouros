"""Тесты для replay runner и golden fixtures."""

import json
from pathlib import Path

import pytest

from backend.App.orchestration.application.scenarios.registry import (
    default_scenario_registry,
)
from backend.App.orchestration.application.scenarios.replay import (
    ReplayCase,
    default_golden_root,
    discover_cases,
    load_case,
    run_all,
    run_case,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    default_scenario_registry.cache_clear()
    yield
    default_scenario_registry.cache_clear()


_GOLDEN_ROOT = default_golden_root()
_GOLDEN_CASE_DIRS = sorted(
    [d for d in _GOLDEN_ROOT.iterdir() if d.is_dir() and not d.name.startswith("__")]
    if _GOLDEN_ROOT.is_dir() else []
)


@pytest.mark.parametrize(
    "case_dir",
    _GOLDEN_CASE_DIRS,
    ids=[d.name for d in _GOLDEN_CASE_DIRS],
)
def test_golden_case_passes(case_dir: Path):
    case = load_case(case_dir)
    result = run_case(case)
    assert result.passed, (
        f"Golden case {case.name} failed: {result.failures}\n"
        f"actual={json.dumps(result.actual, indent=2, default=str)}"
    )


def test_run_all_returns_results_for_all_cases():
    results = run_all(_GOLDEN_ROOT)
    assert len(results) == len(_GOLDEN_CASE_DIRS)
    assert all(r.passed for r in results)


def test_load_case_missing_input_raises(tmp_path: Path):
    case_dir = tmp_path / "broken"
    case_dir.mkdir()
    (case_dir / "expected.json").write_text("{}", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="input.json"):
        load_case(case_dir)


def test_load_case_missing_expected_raises(tmp_path: Path):
    case_dir = tmp_path / "broken"
    case_dir.mkdir()
    (case_dir / "input.json").write_text(
        json.dumps({"scenario_id": "x"}), encoding="utf-8",
    )
    with pytest.raises(FileNotFoundError, match="expected.json"):
        load_case(case_dir)


def test_load_case_requires_scenario_id(tmp_path: Path):
    case_dir = tmp_path / "broken"
    case_dir.mkdir()
    (case_dir / "input.json").write_text("{}", encoding="utf-8")
    (case_dir / "expected.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="scenario_id"):
        load_case(case_dir)


def test_run_case_unknown_scenario_returns_failure():
    case = ReplayCase(
        name="bogus",
        scenario_id="does_not_exist_xyz",
        overrides=type("X", (), {"pipeline_steps": None, "agent_config": None,
                                 "workspace_write": None, "skip_gates": None,
                                 "model_profile": None})(),
        expected={},
    )
    result = run_case(case)
    assert result.passed is False
    assert any("Unknown scenario" in failure for failure in result.failures)


def test_discover_cases_skips_underscore_dirs(tmp_path: Path):
    (tmp_path / "__skip").mkdir()
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "input.json").write_text(
        json.dumps({"scenario_id": "code_review"}), encoding="utf-8",
    )
    (tmp_path / "real" / "expected.json").write_text("{}", encoding="utf-8")
    cases = discover_cases(tmp_path)
    assert len(cases) == 1
    assert cases[0].name == "real"


def test_discover_cases_missing_dir_returns_empty(tmp_path: Path):
    cases = discover_cases(tmp_path / "does_not_exist")
    assert cases == []


def test_run_case_diffs_pipeline_steps(tmp_path: Path):
    case_dir = tmp_path / "diff_case"
    case_dir.mkdir()
    (case_dir / "input.json").write_text(
        json.dumps({"scenario_id": "code_review"}), encoding="utf-8",
    )
    (case_dir / "expected.json").write_text(
        json.dumps({"pipeline_steps": ["only_one_step"]}), encoding="utf-8",
    )
    case = load_case(case_dir)
    result = run_case(case)
    assert result.passed is False
    assert any("pipeline_steps" in failure for failure in result.failures)
