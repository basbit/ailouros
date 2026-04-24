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
        return frozenset(str(x).strip().lower() for x in raw if str(x).strip())
    return frozenset(default)


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
def required_non_empty_output_steps() -> frozenset[str]:
    policy = load_enforcement_policy()
    raw = policy.get("required_non_empty_output_steps")
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(str(value).strip() for value in raw if str(value).strip())


def min_review_content_chars() -> int:
    policy = load_enforcement_policy()
    default = int(policy.get("min_review_content_chars", 120))
    raw = os.getenv("SWARM_MIN_REVIEW_CONTENT_CHARS", "").strip()
    return int(raw) if raw else default


def max_planning_review_retries() -> int:
    policy = load_enforcement_policy()
    default_retries = int(policy.get("max_planning_review_retries_default", 2))
    for env_var in ("SWARM_MAX_PLANNING_RETRIES", "SWARM_MAX_STEP_RETRIES"):
        raw = os.getenv(env_var, "").strip()
        if not raw:
            continue
        try:
            value = int(raw)
        except ValueError:
            _logger.warning("%s=%r is not an int, ignoring", env_var, raw)
            continue
        return max(0, value)
    return default_retries


def is_empty_review(review_output: str | None) -> bool:
    if not review_output:
        return True
    return len(review_output.strip()) < min_review_content_chars()


def swarm_file_min_lines() -> int:
    policy = load_enforcement_policy()
    default = int(policy.get("swarm_file_tag_min_lines_default", 20))
    raw = os.getenv("SWARM_FILE_TAG_MIN_LINES", "").strip()
    return int(raw) if raw else default


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
