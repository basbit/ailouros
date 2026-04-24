from __future__ import annotations

import functools
import json
from typing import Any

from backend.App.shared.domain.pipeline_step_catalog import (
    langgraph_node_to_step_id_map,
    load_pipeline_step_catalog,
)
from config.runtime import resolve_app_config_path

_RUNNERS_POLICY_PATH = "pipeline_runners_policy.json"


@functools.lru_cache(maxsize=1)
def load_runners_policy() -> dict[str, Any]:
    path = resolve_app_config_path(_RUNNERS_POLICY_PATH)
    return json.loads(path.read_text(encoding="utf-8"))


def needs_work_warning_threshold() -> int:
    return int(load_runners_policy().get("needs_work_warning_threshold", 2))


def dev_lead_required_sections() -> tuple[str, ...]:
    return tuple(load_runners_policy().get("dev_lead_required_sections", []))


def implementation_keywords() -> frozenset[str]:
    return frozenset(load_runners_policy().get("implementation_keywords", []))


def research_plan_keywords() -> frozenset[str]:
    return frozenset(load_runners_policy().get("research_plan_keywords", []))


def research_plan_step_ids() -> list[str]:
    return list(load_pipeline_step_catalog().get("research_plan_step_ids", []))


def intent_prefixes() -> tuple[str, ...]:
    return tuple(load_runners_policy().get("intent_prefixes", []))


def planning_steps_output_keys() -> dict[str, str]:
    raw = load_pipeline_step_catalog().get("planning_steps_output_keys") or {}
    return dict(raw) if isinstance(raw, dict) else {}


def research_signal_steps() -> dict[str, str]:
    raw = load_pipeline_step_catalog().get("research_signal_steps") or {}
    return dict(raw) if isinstance(raw, dict) else {}


def step_cleanup_keys() -> dict[str, list[str]]:
    raw = load_pipeline_step_catalog().get("step_cleanup_keys") or {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): list(v) for k, v in raw.items() if isinstance(v, list)}


def step_min_output() -> dict[str, tuple[str, int]]:
    raw = load_pipeline_step_catalog().get("step_min_output") or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, tuple[str, int]] = {}
    for k, v in raw.items():
        if isinstance(v, list) and len(v) == 2:
            out[str(k)] = (str(v[0]), int(v[1]))
    return out


def node_to_step_map() -> dict[str, str]:
    return langgraph_node_to_step_id_map()


def analyze_code_max_files_default() -> int:
    return int(load_runners_policy().get("analyze_code_max_files_default", 300))


def analyze_code_min_output_chars() -> int:
    return int(load_runners_policy().get("analyze_code_min_output_chars", 20))


def devops_command_markers() -> list[str]:
    return list(load_runners_policy().get("devops_command_markers", ["```", "#!/", "$ "]))
