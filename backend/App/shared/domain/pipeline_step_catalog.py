from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, FrozenSet

from config.runtime import resolve_app_config_path

_CATALOG_FILENAME = "pipeline_step_catalog.json"

LANGGRAPH_SYNTHETIC_STEP_IDS: frozenset[str] = frozenset(
    {
        "verification_layer",
        "dev_retry_gate",
        "finalize_pipeline",
        "qa_retry_gate",
    }
)

WIKI_ONLY_STEP_IDS: frozenset[str] = frozenset(
    {
        "documentation",
        "design",
        "marketing",
    }
)


@lru_cache(maxsize=1)
def load_pipeline_step_catalog() -> dict[str, Any]:
    path = resolve_app_config_path(_CATALOG_FILENAME, env_var="SWARM_PIPELINE_STEP_CATALOG_PATH")
    return json.loads(path.read_text(encoding="utf-8"))


def wiki_loader_config() -> dict[str, Any]:
    raw = load_pipeline_step_catalog().get("wiki")
    if not isinstance(raw, dict):
        raise ValueError("pipeline_step_catalog.json: missing or invalid 'wiki' object")
    return raw


def langgraph_node_name_set() -> frozenset[str]:
    raw = load_pipeline_step_catalog().get("langgraph_node_to_step") or {}
    if not isinstance(raw, dict):
        return frozenset()
    return frozenset(str(k) for k in raw)


def langgraph_node_to_step_id_map() -> dict[str, str]:
    raw = load_pipeline_step_catalog().get("langgraph_node_to_step") or {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def validate_pipeline_step_catalog(registry_step_ids: FrozenSet[str]) -> None:
    allowed = set(registry_step_ids) | LANGGRAPH_SYNTHETIC_STEP_IDS
    wiki_steps = allowed | WIKI_ONLY_STEP_IDS
    data = load_pipeline_step_catalog()

    lg = data.get("langgraph_node_to_step") or {}
    if not isinstance(lg, dict):
        raise AssertionError("pipeline_step_catalog.langgraph_node_to_step must be an object")
    for _node, sid in lg.items():
        s = str(sid)
        if s not in allowed:
            raise AssertionError(
                f"pipeline_step_catalog.langgraph_node_to_step: unknown step id {s!r}"
            )

    for block_name in ("planning_steps_output_keys", "research_signal_steps"):
        block = data.get(block_name) or {}
        if not isinstance(block, dict):
            raise AssertionError(f"pipeline_step_catalog.{block_name} must be an object")
        for sid in block:
            if str(sid) not in allowed:
                raise AssertionError(
                    f"pipeline_step_catalog.{block_name}: unknown step id {sid!r}"
                )

    rps = data.get("research_plan_step_ids") or []
    if not isinstance(rps, list):
        raise AssertionError("pipeline_step_catalog.research_plan_step_ids must be an array")
    for sid in rps:
        if str(sid) not in allowed:
            raise AssertionError(
                f"pipeline_step_catalog.research_plan_step_ids: unknown step id {sid!r}"
            )

    smo = data.get("step_min_output") or {}
    if not isinstance(smo, dict):
        raise AssertionError("pipeline_step_catalog.step_min_output must be an object")
    for sid in smo:
        if str(sid) not in allowed:
            raise AssertionError(
                f"pipeline_step_catalog.step_min_output: unknown step id {sid!r}"
            )

    sck = data.get("step_cleanup_keys") or {}
    if not isinstance(sck, dict):
        raise AssertionError("pipeline_step_catalog.step_cleanup_keys must be an object")
    for sid in sck:
        if str(sid) not in allowed:
            raise AssertionError(
                f"pipeline_step_catalog.step_cleanup_keys: unknown step id {sid!r}"
            )

    cr = data.get("critical_review_step_to_output_key") or {}
    if not isinstance(cr, dict):
        raise AssertionError(
            "pipeline_step_catalog.critical_review_step_to_output_key must be an object"
        )
    for review_step in cr:
        if str(review_step) not in allowed:
            raise AssertionError(
                "pipeline_step_catalog.critical_review_step_to_output_key: "
                f"unknown review step {review_step!r}"
            )

    for block_name in ("planning_review_resume_step", "planning_review_target_step"):
        block = data.get(block_name) or {}
        if not isinstance(block, dict):
            raise AssertionError(f"pipeline_step_catalog.{block_name} must be an object")
        for review_step, target in block.items():
            if str(review_step) not in allowed:
                raise AssertionError(
                    f"pipeline_step_catalog.{block_name}: unknown review step {review_step!r}"
                )
            if str(target) not in allowed:
                raise AssertionError(
                    f"pipeline_step_catalog.{block_name}: unknown target step {target!r}"
                )

    deps = data.get("step_dependencies") or {}
    if not isinstance(deps, dict):
        raise AssertionError("pipeline_step_catalog.step_dependencies must be an object")
    for step_id, prerequisites in deps.items():
        if str(step_id) not in allowed:
            raise AssertionError(
                f"pipeline_step_catalog.step_dependencies: unknown step id {step_id!r}"
            )
        if not isinstance(prerequisites, list):
            raise AssertionError(
                f"pipeline_step_catalog.step_dependencies[{step_id!r}] must be an array"
            )
        for prerequisite in prerequisites:
            if str(prerequisite) not in allowed:
                raise AssertionError(
                    f"pipeline_step_catalog.step_dependencies[{step_id!r}]: "
                    f"unknown prerequisite step id {prerequisite!r}"
                )

    wiki = data.get("wiki") or {}
    if not isinstance(wiki, dict):
        raise AssertionError("pipeline_step_catalog.wiki must be an object")
    hints = wiki.get("step_hints") or {}
    if isinstance(hints, dict):
        for sid in hints:
            if str(sid) not in wiki_steps:
                raise AssertionError(
                    f"pipeline_step_catalog.wiki.step_hints: unknown step id {sid!r}"
                )
    qps = wiki.get("query_sources_per_step") or {}
    if isinstance(qps, dict):
        for sid in qps:
            if str(sid) not in wiki_steps:
                raise AssertionError(
                    "pipeline_step_catalog.wiki.query_sources_per_step: "
                    f"unknown step id {sid!r}"
                )
