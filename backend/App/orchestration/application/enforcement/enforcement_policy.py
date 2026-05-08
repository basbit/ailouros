from __future__ import annotations

import functools
import json
import logging
import os
import re
from typing import Any

from backend.App.shared.domain.pipeline_step_catalog import load_pipeline_step_catalog
from config.runtime import resolve_app_config_path

_logger = logging.getLogger(__name__)

_ENFORCEMENT_POLICY_PATH = resolve_app_config_path("pipeline_enforcement_policy.json")


@functools.lru_cache(maxsize=1)
def load_enforcement_policy() -> dict[str, Any]:
    try:
        return json.loads(_ENFORCEMENT_POLICY_PATH.read_text(encoding="utf-8"))
    except Exception as load_error:
        _logger.warning("pipeline_enforcement_policy.json load failed, using defaults: %s", load_error)
        return {}


@functools.lru_cache(maxsize=1)
def swarm_env_strings_that_mean_enabled() -> frozenset[str]:
    policy = load_enforcement_policy()
    raw = policy.get("swarm_env_strings_that_mean_enabled")
    default = ("1", "true", "yes", "on")
    if isinstance(raw, list) and raw:
        return frozenset(str(value).strip().lower() for value in raw if str(value).strip())
    return frozenset(default)


def _policy_section(name: str) -> dict[str, Any]:
    value = load_enforcement_policy().get(name)
    return value if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


@functools.lru_cache(maxsize=1)
def workspace_preflight_policy() -> dict[str, Any]:
    return dict(_policy_section("workspace_preflight"))


@functools.lru_cache(maxsize=1)
def source_integrity_policy() -> dict[str, Any]:
    return dict(_policy_section("source_integrity"))


@functools.lru_cache(maxsize=1)
def dev_patch_errors_policy() -> dict[str, Any]:
    return dict(_policy_section("dev_patch_errors"))


@functools.lru_cache(maxsize=1)
def secret_detection_policy() -> dict[str, Any]:
    return dict(_policy_section("secret_detection"))


@functools.lru_cache(maxsize=1)
def dev_runner_policy() -> dict[str, Any]:
    return dict(_policy_section("dev_runner"))


def configured_string_list(section: dict[str, Any], key: str) -> list[str]:
    return _string_list(section.get(key))


@functools.lru_cache(maxsize=1)
def critical_review_step_to_output_key() -> dict[str, str]:
    raw = load_pipeline_step_catalog().get("critical_review_step_to_output_key") or {}
    return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}


@functools.lru_cache(maxsize=1)
def planning_review_resume_step() -> dict[str, str]:
    raw = load_pipeline_step_catalog().get("planning_review_resume_step") or {}
    return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}


@functools.lru_cache(maxsize=1)
def planning_review_target_step() -> dict[str, str]:
    raw = load_pipeline_step_catalog().get("planning_review_target_step") or {}
    return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}


@functools.lru_cache(maxsize=1)
def step_dependencies() -> dict[str, tuple[str, ...]]:
    raw = load_pipeline_step_catalog().get("step_dependencies") or {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(step_id): tuple(str(prerequisite) for prerequisite in prerequisites)
        for step_id, prerequisites in raw.items()
        if isinstance(prerequisites, list)
    }


@functools.lru_cache(maxsize=1)
def required_non_empty_output_steps() -> frozenset[str]:
    policy = load_enforcement_policy()
    raw = policy.get("required_non_empty_output_steps")
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(str(value).strip() for value in raw if str(value).strip())


def dev_verification_step_id() -> str:
    policy = load_enforcement_policy()
    return str(policy.get("dev_verification_step_id") or "").strip()


def devops_script_contract_step_ids() -> frozenset[str]:
    policy = load_enforcement_policy()
    raw = policy.get("devops_script_contract_step_ids")
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(str(value).strip() for value in raw if str(value).strip())


def pre_review_blocker_steps() -> dict[str, str]:
    policy = load_enforcement_policy()
    raw = policy.get("pre_review_blockers")
    if not isinstance(raw, dict):
        return {}
    return {
        "human_step_id": str(raw.get("human_step_id") or "").strip(),
        "resume_pipeline_step": str(raw.get("resume_pipeline_step") or "").strip(),
    }


def repair_contract_steps() -> dict[str, str]:
    policy = load_enforcement_policy()
    raw = policy.get("repair_contract")
    if not isinstance(raw, dict):
        return {}
    return {
        "human_step_id": str(raw.get("human_step_id") or "").strip(),
        "resume_pipeline_step": str(raw.get("resume_pipeline_step") or "").strip(),
    }


def min_review_content_chars() -> int:
    policy = load_enforcement_policy()
    default = int(policy.get("min_review_content_chars") or 0)
    environment_key = str(policy.get("min_review_content_chars_environment_key") or "").strip()
    environment_value = os.getenv(environment_key, "").strip() if environment_key else ""
    return int(environment_value) if environment_value else default


def max_planning_review_retries() -> int:
    policy = load_enforcement_policy()
    default_retries = int(policy.get("max_planning_review_retries_default") or 0)
    for environment_key in configured_string_list(policy, "max_planning_review_retries_environment_keys"):
        environment_value = os.getenv(environment_key, "").strip()
        if not environment_value:
            continue
        try:
            value = int(environment_value)
        except ValueError:
            _logger.warning("%s=%r is not an int, ignoring", environment_key, environment_value)
            continue
        return max(0, value)
    return default_retries


def is_empty_review(review_output: str | None) -> bool:
    if not review_output:
        return True
    return len(review_output.strip()) < min_review_content_chars()


def is_quality_gate_enabled(state: Any | None = None) -> bool:
    if isinstance(state, dict):
        agent_config = state.get("agent_config") or {}
        swarm = agent_config.get("swarm") if isinstance(agent_config, dict) else None
        if isinstance(swarm, dict) and "quality_gate_enabled" in swarm:
            value = swarm["quality_gate_enabled"]
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() in swarm_env_strings_that_mean_enabled()
    env_value = os.getenv("SWARM_AUTO_RETRY_ON_NEEDS_WORK", "").strip().lower()
    if env_value:
        return env_value in swarm_env_strings_that_mean_enabled()
    return True


def swarm_file_min_lines() -> int:
    policy = load_enforcement_policy()
    default = int(policy.get("swarm_file_tag_min_lines_default") or 0)
    environment_key = str(policy.get("swarm_file_tag_min_lines_environment_key") or "").strip()
    environment_value = os.getenv(environment_key, "").strip() if environment_key else ""
    return int(environment_value) if environment_value else default


@functools.lru_cache(maxsize=1)
def code_fence_pattern() -> re.Pattern[str]:
    policy = load_enforcement_policy()
    pattern = policy.get("code_fence_pattern", r"```(?P<lang>[a-zA-Z0-9_.+-]*)\n(?P<body>.*?)```")
    return re.compile(pattern, re.DOTALL)


@functools.lru_cache(maxsize=1)
def swarm_file_tag_pattern() -> re.Pattern[str]:
    policy = load_enforcement_policy()
    pattern = policy.get("swarm_file_tag_pattern", r"<swarm_file\s+path=")
    return re.compile(pattern, re.IGNORECASE)
