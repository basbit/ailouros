"""Генерация pipeline_steps по описанию задачи (AutoSwarmBuilder-lite)."""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from backend.App.orchestration.infrastructure.agents.base_agent import BaseAgent, resolve_agent_model


def _valid_step_ids() -> list[str]:
    from backend.App.orchestration.application.pipeline_graph import DEFAULT_PIPELINE_STEP_IDS

    return list(DEFAULT_PIPELINE_STEP_IDS)


def _remote_kw(ac: dict[str, Any], role_key: str) -> dict[str, Any]:
    from backend.App.orchestration.application.pipeline_graph import _remote_api_client_kwargs_for_role

    role_cfg = ac.get(role_key) if isinstance(ac.get(role_key), dict) else {}
    return _remote_api_client_kwargs_for_role({"agent_config": ac}, role_cfg)


def _planner_remote_kw(
    ac: dict[str, Any],
    planner_cfg: dict[str, Any],
) -> dict[str, Any]:
    from backend.App.orchestration.application.pipeline_graph import _remote_api_client_kwargs

    environment = str(planner_cfg.get("environment") or "").strip().lower()
    if environment in {"cloud", "anthropic"}:
        return _remote_api_client_kwargs({"agent_config": ac})
    if environment:
        return {}
    return _remote_kw(ac, "pm")


def plan_pipeline_steps(
    goal: str,
    *,
    agent_config: Optional[dict[str, Any]] = None,
    constraints: str = "",
) -> dict[str, Any]:
    """Вызов LLM: вернуть JSON с pipeline_steps и rationale."""
    ac = agent_config or {}
    _pm_raw = ac.get("pm")
    pm: dict[str, Any] = _pm_raw if isinstance(_pm_raw, dict) else {}
    _planner_raw = ac.get("swarm_planner")
    planner_cfg: dict[str, Any] = _planner_raw if isinstance(_planner_raw, dict) else {}
    env = str(planner_cfg.get("environment") or pm.get("environment") or "ollama")
    model = str(
        planner_cfg.get("model")
        or pm.get("model")
        or resolve_agent_model("PM")
    ).strip()

    ids = _valid_step_ids()
    allowed = ", ".join(ids[:60])
    if len(ids) > 60:
        allowed += ", …"

    sys = (
        "You are the AIlourOS pipeline planner. Return ONLY one JSON object without markdown:\n"
        '{"pipeline_steps":["pm",...],"rationale":"brief explanation"}\n'
        f"Allowed step ids (strictly from this list): {allowed}.\n"
        "Keep logical order: after a worker role usually review_, then human_.\n"
        "Custom roles: only crole_<slug>, if user explicitly requests and slug is known."
        "\n\nOptionally, if you have strong reasons, you may include a 'recommended_models' object "
        "mapping role names to capability requirements: "
        "e.g. {\"pm\": \"needs_tool_calling\", \"dev\": \"needs_code\"}. "
        "This is OPTIONAL — omit if uncertain."
    )
    user = f"Goal:\n{goal.strip()}\n"
    if constraints.strip():
        user += f"\nConstraints:\n{constraints.strip()}\n"

    rkw = _planner_remote_kw(ac, planner_cfg)
    agent = BaseAgent(
        role="PM",
        system_prompt=sys,
        model=model,
        environment=env,
        **rkw,
    )
    raw = agent.run(user).strip()
    m = re.search(r"\{[\s\S]*\}", raw)
    blob = m.group(0) if m else raw
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        return {
            "pipeline_steps": [],
            "rationale": "",
            "planner_error": "json_parse_error",
            "planner_error_detail": str(exc)[:500],
            "raw": raw[:4000],
        }
    steps = data.get("pipeline_steps")
    if not isinstance(steps, list):
        return {
            "pipeline_steps": [],
            "rationale": str(data.get("rationale") or "")[:4000],
            "planner_error": "pipeline_steps_not_a_list",
            "raw": raw[:4000],
        }
    out_steps = [str(s).strip() for s in steps if str(s).strip()]
    result: dict[str, Any] = {
        "pipeline_steps": out_steps,
        "rationale": str(data.get("rationale") or "")[:4000],
        "planner_model": agent.used_model,
        "planner_provider": agent.used_provider,
    }
    recommended = data.get("recommended_models")
    if isinstance(recommended, dict):
        result["recommended_models"] = recommended
    return result
