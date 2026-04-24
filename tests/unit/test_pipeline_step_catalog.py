from __future__ import annotations

import json
from pathlib import Path

from backend.App.integrations.infrastructure.pipeline_presets import resolve_preset
from backend.App.orchestration.application.routing.step_registry import PIPELINE_STEP_REGISTRY
from backend.App.shared.domain.pipeline_step_catalog import (
    langgraph_node_name_set,
    load_pipeline_step_catalog,
    validate_pipeline_step_catalog,
)

_EXPECTED_DEFAULT_TOPOLOGY_LANGGRAPH_NODES: frozenset[str] = frozenset(
    {
        "PM",
        "REVIEW_PM",
        "HUMAN_PM",
        "BA",
        "REVIEW_BA",
        "HUMAN_BA",
        "ARCH",
        "REVIEW_STACK",
        "REVIEW_ARCH",
        "HUMAN_ARCH",
        "SPEC_MERGE",
        "REVIEW_SPEC",
        "HUMAN_SPEC",
        "ANALYZE_CODE",
        "GENERATE_DOCUMENTATION",
        "PROBLEM_SPOTTER",
        "REFACTOR_PLAN",
        "HUMAN_CODE_REVIEW",
        "DEVOPS",
        "REVIEW_DEVOPS",
        "HUMAN_DEVOPS",
        "DEV_LEAD",
        "REVIEW_DEV_LEAD",
        "HUMAN_DEV_LEAD",
        "DEV",
        "VERIFICATION_LAYER",
        "REVIEW_DEV",
        "DEV_RETRY_GATE",
        "HUMAN_DEV",
        "QA",
        "REVIEW_QA",
        "QA_RETRY_GATE",
        "HUMAN_QA",
        "FINALIZE_PIPELINE",
    }
)


def test_pipeline_step_catalog_matches_registry():
    validate_pipeline_step_catalog(frozenset(PIPELINE_STEP_REGISTRY.keys()))


def test_catalog_langgraph_nodes_match_default_linear_ring_graph():
    assert langgraph_node_name_set() == _EXPECTED_DEFAULT_TOPOLOGY_LANGGRAPH_NODES


def test_catalog_langgraph_maps_to_distinct_step_ids():
    m = load_pipeline_step_catalog().get("langgraph_node_to_step") or {}
    values = list(m.values())
    assert len(values) == len(set(values)), "duplicate pipeline step id in langgraph_node_to_step"


def test_shipped_pipeline_presets_only_use_registered_steps():
    repo_root = Path(__file__).resolve().parents[2]
    raw = json.loads((repo_root / "config" / "pipeline_presets.json").read_text(encoding="utf-8"))
    presets = raw.get("presets") or {}
    assert isinstance(presets, dict)
    for preset_name in presets:
        resolve_preset(preset_name)
