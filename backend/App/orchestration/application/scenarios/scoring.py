from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

_DEFAULT_WEIGHTS: dict[str, float] = {
    "artifacts": 0.4,
    "quality_checks": 0.5,
    "warnings": 0.1,
}


@dataclass(frozen=True)
class ScenarioScore:
    artifact_score: float
    quality_check_score: float
    warnings_score: float
    overall_score: float
    breakdown: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 1.0
    if numerator <= 0:
        return 0.0
    return min(1.0, numerator / denominator)


def _normalize_weights(weights: dict[str, Any]) -> dict[str, float]:
    if not weights:
        return dict(_DEFAULT_WEIGHTS)
    cleaned: dict[str, float] = {}
    for key in ("artifacts", "quality_checks", "warnings"):
        value = weights.get(key)
        if isinstance(value, (int, float)) and value >= 0:
            cleaned[key] = float(value)
    if not cleaned:
        return dict(_DEFAULT_WEIGHTS)
    total = sum(cleaned.values())
    if total <= 0:
        return dict(_DEFAULT_WEIGHTS)
    for key in cleaned:
        cleaned[key] = cleaned[key] / total
    for key, value in _DEFAULT_WEIGHTS.items():
        cleaned.setdefault(key, value)
    final_total = sum(cleaned[key] for key in ("artifacts", "quality_checks", "warnings"))
    if final_total <= 0:
        return dict(_DEFAULT_WEIGHTS)
    for key in ("artifacts", "quality_checks", "warnings"):
        cleaned[key] = cleaned[key] / final_total
    return cleaned


def score_scenario_run(
    snapshot: dict[str, Any],
    weights: dict[str, Any] | None = None,
) -> ScenarioScore:
    artifact_summary = snapshot.get("scenario_artifact_summary") or {}
    quality_summary = snapshot.get("scenario_quality_check_summary") or {}
    warnings = snapshot.get("scenario_warnings") or []

    artifact_present = int(artifact_summary.get("present") or 0)
    artifact_total = int(artifact_summary.get("total") or 0)
    artifact_score = _safe_ratio(artifact_present, artifact_total)

    quality_passed = int(quality_summary.get("passed") or 0)
    quality_total = int(quality_summary.get("total") or 0)
    quality_score = _safe_ratio(quality_passed, quality_total)

    warning_count = len(warnings) if isinstance(warnings, list) else 0
    if warning_count == 0:
        warnings_score = 1.0
    else:
        warnings_score = max(0.0, 1.0 - 0.2 * warning_count)

    normalized = _normalize_weights(weights or {})
    overall_score = (
        artifact_score * normalized["artifacts"]
        + quality_score * normalized["quality_checks"]
        + warnings_score * normalized["warnings"]
    )
    overall_score = max(0.0, min(1.0, overall_score))

    breakdown = {
        "weights": normalized,
        "artifacts": {
            "present": artifact_present,
            "total": artifact_total,
            "score": artifact_score,
        },
        "quality_checks": {
            "passed": quality_passed,
            "total": quality_total,
            "score": quality_score,
            "blocking_failed": list(quality_summary.get("blocking_failed") or []),
        },
        "warnings": {"count": warning_count, "score": warnings_score},
    }
    return ScenarioScore(
        artifact_score=artifact_score,
        quality_check_score=quality_score,
        warnings_score=warnings_score,
        overall_score=overall_score,
        breakdown=breakdown,
    )
