
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def resolve_model(role: str, role_default_model: str) -> str:
    role_key = role.upper()

    route_specific = os.getenv(f"SWARM_ROUTE_{role_key}")
    route = route_specific.lower() if route_specific else ""
    _route_planning_roles = {
        "PM",
        "BA",
        "ARCH",
        "REVIEWER",
        "STACK_REVIEWER",
        "DEV_LEAD",
        "DOC_GEN",
        "PROBLEM_SPOTTER",
        "REFACTOR_PLAN",
        "CODE_DIAGRAM",
    }
    _model_planning_roles = _route_planning_roles - {"PM"}
    if not route:
        if role_key in _route_planning_roles:
            route = os.getenv("SWARM_ROUTE_PLANNING", "").lower()
        elif role_key in {"DEV", "QA", "DEVOPS"}:
            route = os.getenv("SWARM_ROUTE_BUILD", "").lower()
    if not route:
        route = os.getenv("SWARM_ROUTE_DEFAULT", "local").lower()

    if route == "cloud":
        specific_cloud = os.getenv(f"SWARM_MODEL_CLOUD_{role_key}")
        if specific_cloud:
            return specific_cloud.strip()

        cloud_ba_arch = os.getenv("SWARM_MODEL_CLOUD_BA_ARCH", "").strip()
        if (
            role_key in {
                "BA",
                "ARCH",
                "REVIEWER",
                "STACK_REVIEWER",
                "REFACTOR_PLAN",
                "CODE_DIAGRAM",
                "DOC_GEN",
            }
            and cloud_ba_arch
        ):
            return cloud_ba_arch

        if role_key in _model_planning_roles:
            cloud_planning = os.getenv("SWARM_MODEL_CLOUD_PLANNING", "").strip()
            if cloud_planning:
                return cloud_planning
        if role_key in {"DEV", "QA", "DEVOPS"}:
            cloud_build = os.getenv("SWARM_MODEL_CLOUD_BUILD", "").strip()
            if cloud_build:
                return cloud_build

        from backend.App.integrations.infrastructure.llm.config import SWARM_MODEL_CLOUD_DEFAULT
        return os.getenv("SWARM_MODEL_CLOUD", SWARM_MODEL_CLOUD_DEFAULT).strip() or SWARM_MODEL_CLOUD_DEFAULT

    specific = os.getenv(f"SWARM_MODEL_{role_key}")
    if specific:
        return specific.strip()

    ba_arch = os.getenv("SWARM_MODEL_BA_ARCH", "").strip()
    if role_key in {
        "BA",
        "ARCH",
        "REVIEWER",
        "STACK_REVIEWER",
        "REFACTOR_PLAN",
        "CODE_DIAGRAM",
        "DOC_GEN",
    } and ba_arch:
        return ba_arch

    if role_key in _model_planning_roles:
        planning = os.getenv("SWARM_MODEL_PLANNING", "").strip()
        if planning:
            return planning

    if role_key in {"DEV", "QA", "DEVOPS"}:
        build = os.getenv("SWARM_MODEL_BUILD", "").strip()
        if build:
            return build

    return os.getenv("SWARM_MODEL", role_default_model).strip() or role_default_model


def resolve_base_url(role: str) -> Optional[str]:
    role_key = role.upper()
    specific = os.getenv(f"SWARM_BASE_URL_{role_key}", "").strip()
    if specific:
        return specific
    generic = os.getenv("SWARM_BASE_URL", "").strip()
    return generic if generic else None
