from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState
from backend.App.orchestration.domain.media.contracts import (
    MediaArtifact,
    MediaBudget,
    MediaRequest,
)
from backend.App.orchestration.domain.media.errors import (
    MediaPolicyViolation,
    MediaProviderUnavailable,
)

logger = logging.getLogger(__name__)

_LICENSE_POLICIES = frozenset({"permissive_only", "any", "off"})


def _media_config(state: PipelineState) -> dict[str, Any]:
    agent_config = state.get("agent_config") or {}
    if not isinstance(agent_config, dict):
        return {}
    root_media = agent_config.get("media")
    swarm = agent_config.get("swarm")
    swarm_media = swarm.get("media") if isinstance(swarm, dict) else None
    if isinstance(swarm_media, dict):
        media_config = dict(swarm_media)
        if isinstance(root_media, dict):
            media_config = {**root_media, **media_config}
        return media_config
    return dict(root_media) if isinstance(root_media, dict) else {}


def _first_number(*values: Any, default: float = 0.0) -> float:
    for value in values:
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return default


def _first_int(*values: Any, default: int = 1) -> int:
    for value in values:
        if value in (None, ""):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return default


def _resolve_budget(state: PipelineState) -> MediaBudget:
    media_config = _media_config(state)
    budget_config_raw = media_config.get("budget")
    budget_config: dict[str, Any] = (
        budget_config_raw if isinstance(budget_config_raw, dict) else {}
    )
    max_cost_usd = _first_number(
        media_config.get("max_cost_usd"),
        budget_config.get("max_cost_usd"),
        budget_config.get("max_cost_usd_per_task"),
        default=0.0,
    )
    max_attempts = _first_int(
        media_config.get("max_attempts"),
        budget_config.get("max_attempts"),
        budget_config.get("max_attempts_per_asset"),
        default=1,
    )
    license_policy = str(
        media_config.get("license_policy")
        or budget_config.get("license_policy")
        or "permissive_only"
    ).strip().lower()
    if license_policy not in _LICENSE_POLICIES:
        license_policy = "permissive_only"
    return MediaBudget(
        max_cost_usd=max_cost_usd,
        max_attempts=max(1, max_attempts),
        license_policy=license_policy,
    )


def _parse_requests(state: PipelineState) -> list[MediaRequest]:
    raw = state.get("media_requests") or []
    if not isinstance(raw, list):
        return []
    requests: list[MediaRequest] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind") or "").strip().lower()
        prompt = str(entry.get("prompt") or "").strip()
        target = str(entry.get("target_path") or "").strip().lstrip("/")
        if not kind or not prompt or not target:
            continue
        requests.append(MediaRequest(
            kind=kind,
            prompt=prompt,
            target_path=target,
            width=entry.get("width") if isinstance(entry.get("width"), int) else None,
            height=entry.get("height") if isinstance(entry.get("height"), int) else None,
            duration_seconds=(
                float(entry["duration_seconds"])
                if isinstance(entry.get("duration_seconds"), (int, float))
                else None
            ),
            voice=str(entry["voice"]) if isinstance(entry.get("voice"), str) else None,
            extra=entry.get("extra") if isinstance(entry.get("extra"), dict) else None,
        ))
    return requests


def _select_provider(state: PipelineState, kind: str):
    registry = state.get("_media_provider_registry")
    if not isinstance(registry, list):
        return None
    for provider in registry:
        try:
            if provider.supports(kind):
                return provider
        except AttributeError:
            continue
    return None


def media_generator_node(state: PipelineState) -> dict[str, Any]:
    requests = _parse_requests(state)
    if not requests:
        return {
            "media_generator_output": "[no media_requests in state]",
            "media_artifacts": [],
        }
    budget = _resolve_budget(state)
    workspace_root = str(state.get("workspace_root") or "").strip()
    if not workspace_root:
        return {
            "media_generator_output": "[media_generator skipped: no workspace_root]",
            "media_artifacts": [],
        }
    workspace_path = Path(workspace_root).expanduser().resolve()
    artifacts: list[dict[str, Any]] = []
    log_lines: list[str] = []
    spent_cost = 0.0
    attempts = 0

    for request in requests:
        if attempts >= budget.max_attempts:
            log_lines.append("[budget] max_attempts reached — skipping remaining requests")
            break
        attempts += 1
        provider = _select_provider(state, request.kind)
        if provider is None:
            log_lines.append(
                f"[provider] no provider supports kind={request.kind!r} "
                f"target={request.target_path}"
            )
            continue
        try:
            estimated = float(provider.estimate_cost(request))
        except Exception as exc:
            log_lines.append(
                f"[provider:{provider.name}] estimate_cost failed: {exc}"
            )
            continue
        if budget.max_cost_usd > 0 and (spent_cost + estimated) > budget.max_cost_usd:
            log_lines.append(
                f"[budget] would exceed cap (spent={spent_cost:.2f} + "
                f"{estimated:.2f} > {budget.max_cost_usd:.2f}) — skipping {request.target_path}"
            )
            continue
        try:
            artifact: MediaArtifact = provider.generate(request)
        except MediaProviderUnavailable as exc:
            log_lines.append(f"[provider:{provider.name}] unavailable: {exc}")
            continue
        except MediaPolicyViolation as exc:
            log_lines.append(f"[policy] {exc}")
            continue
        except Exception as exc:
            log_lines.append(f"[provider:{provider.name}] error: {exc}")
            continue
        if budget.license_policy == "permissive_only" and artifact.license.lower() not in {
            "cc0", "cc-by", "mit", "apache-2.0", "permissive",
        }:
            log_lines.append(
                f"[policy] rejected non-permissive license={artifact.license} for "
                f"{request.target_path}"
            )
            continue
        spent_cost += estimated
        artifact_target = workspace_path / "var" / "artifacts" / "media" / artifact.relative_path
        log_lines.append(
            f"[ok] {request.kind} -> {artifact.relative_path} "
            f"({artifact.bytes_size} bytes, license={artifact.license}, "
            f"cost~${estimated:.2f})"
        )
        artifacts.append({**artifact.to_dict(), "estimated_cost_usd": estimated, "absolute_hint": str(artifact_target)})

    return {
        "media_generator_output": "\n".join(log_lines) or "[no media generated]",
        "media_artifacts": artifacts,
        "media_budget_used": spent_cost,
        "media_budget": budget.to_dict(),
    }
