"""Тесты для ScenarioRegistry."""

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from backend.App.orchestration.application.scenarios.registry import ScenarioRegistry
from backend.App.orchestration.domain.scenarios.errors import (
    ScenarioNotFound,
    ScenarioRegistryError,
)
from backend.App.orchestration.infrastructure.scenario_loader import ScenarioFileLoader


_KNOWN = frozenset(["clarify_input", "pm", "review_pm", "ba", "review_ba", "architect", "dev", "qa",
                    "analyze_code", "problem_spotter", "refactor_plan", "human_code_review",
                    "review_arch", "spec_merge", "review_spec", "devops", "review_devops",
                    "dev_lead", "review_dev_lead", "review_dev", "review_qa", "human_qa"])


def _make_loader(tmp_path: Path, scenarios: list[dict[str, Any]]) -> ScenarioFileLoader:
    for sc in scenarios:
        (tmp_path / f"{sc['id']}.json").write_text(json.dumps(sc), encoding="utf-8")
    return ScenarioFileLoader(tmp_path)


def _valid(sid: str = "s1") -> dict[str, Any]:
    return {
        "id": sid,
        "title": f"Scenario {sid}",
        "category": "development",
        "description": "desc",
        "pipeline_steps": ["clarify_input", "pm"],
        "default_gates": [],
        "expected_artifacts": [],
        "required_tools": [],
        "workspace_write_default": False,
        "recommended_models": {},
        "agent_config_defaults": {},
        "tags": [],
    }


def test_valid_and_invalid_mixed(tmp_path, caplog):
    invalid = {
        "id": "bad",
        "title": "",
        "category": "development",
        "description": "desc",
        "pipeline_steps": ["clarify_input"],
        "default_gates": [],
        "expected_artifacts": [],
        "required_tools": [],
        "workspace_write_default": False,
        "recommended_models": {},
        "agent_config_defaults": {},
        "tags": [],
    }
    loader = _make_loader(tmp_path, [_valid("good"), invalid])
    registry = ScenarioRegistry(loader=loader, known_step_ids_factory=lambda: _KNOWN)
    with caplog.at_level(logging.WARNING):
        scenarios = registry.list_all()
    assert len(scenarios) == 1
    assert scenarios[0].id == "good"


def test_get_raises_scenario_not_found(tmp_path):
    loader = _make_loader(tmp_path, [_valid("s1")])
    registry = ScenarioRegistry(loader=loader, known_step_ids_factory=lambda: _KNOWN)
    with pytest.raises(ScenarioNotFound):
        registry.get("missing")


def test_duplicate_ids_raise(tmp_path):
    loader = _make_loader(tmp_path, [_valid("s1")])
    (tmp_path / "s1_copy.json").write_text(json.dumps(_valid("s1")), encoding="utf-8")
    registry = ScenarioRegistry(loader=loader, known_step_ids_factory=lambda: _KNOWN)
    with pytest.raises(ScenarioRegistryError):
        registry.list_all()


_BUNDLED_SCENARIOS_DIR = Path(__file__).resolve().parents[2] / "config" / "scenarios"


@pytest.mark.parametrize("json_file", list(_BUNDLED_SCENARIOS_DIR.glob("*.json")))
def test_bundled_scenario_parses(json_file: Path):
    loader = ScenarioFileLoader(_BUNDLED_SCENARIOS_DIR)
    registry = ScenarioRegistry(loader=loader)
    registry.reload()
    scenarios = registry.list_all()
    ids = {s.id for s in scenarios}
    data = json.loads(json_file.read_text(encoding="utf-8"))
    assert data["id"] in ids
