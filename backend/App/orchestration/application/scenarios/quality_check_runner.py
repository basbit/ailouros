from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Optional

from backend.App.orchestration.application.scenarios.artifact_check import ArtifactStatus
from backend.App.orchestration.domain.scenarios.quality_checks import (
    QualityCheckResult,
    QualityCheckSpec,
)

logger = logging.getLogger(__name__)


def _safe_join(base: Path, rel: str) -> Optional[Path]:
    try:
        candidate = (base / rel).resolve(strict=False)
        base_resolved = base.resolve(strict=False)
    except (OSError, ValueError):
        return None
    try:
        candidate.relative_to(base_resolved)
    except ValueError:
        return None
    return candidate


def _check_artifact_count(
    spec: QualityCheckSpec,
    artifact_status: list[ArtifactStatus],
) -> QualityCheckResult:
    minimum = int(spec.config.get("min", 0) or 0)
    present = sum(1 for entry in artifact_status if entry.present)
    passed = present >= minimum
    msg = (
        f"{present} of {len(artifact_status)} expected artifacts present "
        f"(min={minimum})"
    )
    return QualityCheckResult(
        id=spec.id,
        type=spec.type,
        passed=passed,
        severity=spec.severity,
        blocking=spec.blocking,
        message=msg,
        detail={"present": present, "total": len(artifact_status), "min": minimum},
    )


def _check_artifact_min_size(
    spec: QualityCheckSpec,
    task_dir: Path,
) -> QualityCheckResult:
    rel = str(spec.config.get("path") or "").strip()
    minimum = int(spec.config.get("min_bytes", 0) or 0)
    if not rel:
        return QualityCheckResult(
            id=spec.id, type=spec.type, passed=False,
            severity=spec.severity, blocking=spec.blocking,
            message="config.path is required",
        )
    full = _safe_join(task_dir, rel)
    if full is None or not full.is_file():
        return QualityCheckResult(
            id=spec.id, type=spec.type, passed=False,
            severity=spec.severity, blocking=spec.blocking,
            message=f"Artifact {rel!r} not present",
            detail={"path": rel, "min_bytes": minimum},
        )
    try:
        size = full.stat().st_size
    except OSError as exc:
        return QualityCheckResult(
            id=spec.id, type=spec.type, passed=False,
            severity=spec.severity, blocking=spec.blocking,
            message=f"Could not read {rel!r}: {exc}",
        )
    passed = size >= minimum
    return QualityCheckResult(
        id=spec.id, type=spec.type, passed=passed,
        severity=spec.severity, blocking=spec.blocking,
        message=f"Artifact {rel!r} size={size} bytes (min={minimum})",
        detail={"path": rel, "size": size, "min_bytes": minimum},
    )


def _check_agent_output_min_chars(
    spec: QualityCheckSpec,
    task_dir: Path,
) -> QualityCheckResult:
    agent = str(spec.config.get("agent") or "").strip()
    minimum = int(spec.config.get("min_chars", 0) or 0)
    if not agent:
        return QualityCheckResult(
            id=spec.id, type=spec.type, passed=False,
            severity=spec.severity, blocking=spec.blocking,
            message="config.agent is required",
        )
    rel = f"agents/{agent}.txt"
    full = _safe_join(task_dir, rel)
    if full is None or not full.is_file():
        return QualityCheckResult(
            id=spec.id, type=spec.type, passed=False,
            severity=spec.severity, blocking=spec.blocking,
            message=f"Agent output {rel!r} not present",
            detail={"agent": agent, "min_chars": minimum},
        )
    try:
        text = full.read_text(encoding="utf-8")
    except OSError as exc:
        return QualityCheckResult(
            id=spec.id, type=spec.type, passed=False,
            severity=spec.severity, blocking=spec.blocking,
            message=f"Could not read {rel!r}: {exc}",
        )
    chars = len(text.strip())
    passed = chars >= minimum
    return QualityCheckResult(
        id=spec.id, type=spec.type, passed=passed,
        severity=spec.severity, blocking=spec.blocking,
        message=f"Agent {agent!r} produced {chars} chars (min={minimum})",
        detail={"agent": agent, "chars": chars, "min_chars": minimum},
    )


def _check_agent_output_contains(
    spec: QualityCheckSpec,
    task_dir: Path,
) -> QualityCheckResult:
    agent = str(spec.config.get("agent") or "").strip()
    needle = str(spec.config.get("substring") or "")
    case_sensitive = bool(spec.config.get("case_sensitive", False))
    if not agent or not needle:
        return QualityCheckResult(
            id=spec.id, type=spec.type, passed=False,
            severity=spec.severity, blocking=spec.blocking,
            message="config.agent and config.substring are required",
        )
    rel = f"agents/{agent}.txt"
    full = _safe_join(task_dir, rel)
    if full is None or not full.is_file():
        return QualityCheckResult(
            id=spec.id, type=spec.type, passed=False,
            severity=spec.severity, blocking=spec.blocking,
            message=f"Agent output {rel!r} not present",
        )
    try:
        text = full.read_text(encoding="utf-8")
    except OSError as exc:
        return QualityCheckResult(
            id=spec.id, type=spec.type, passed=False,
            severity=spec.severity, blocking=spec.blocking,
            message=f"Could not read {rel!r}: {exc}",
        )
    haystack = text if case_sensitive else text.lower()
    target = needle if case_sensitive else needle.lower()
    passed = target in haystack
    return QualityCheckResult(
        id=spec.id, type=spec.type, passed=passed,
        severity=spec.severity, blocking=spec.blocking,
        message=(
            f"Agent {agent!r} {'contains' if passed else 'does not contain'} "
            f"required substring"
        ),
        detail={"agent": agent, "substring": needle, "case_sensitive": case_sensitive},
    )


def _check_pipeline_step_count(
    spec: QualityCheckSpec,
    pipeline_steps: list[str],
) -> QualityCheckResult:
    minimum = int(spec.config.get("min", 0) or 0)
    count = len(pipeline_steps)
    passed = count >= minimum
    return QualityCheckResult(
        id=spec.id, type=spec.type, passed=passed,
        severity=spec.severity, blocking=spec.blocking,
        message=f"Pipeline has {count} steps (min={minimum})",
        detail={"count": count, "min": minimum},
    )


def _check_every_artifact_min_size(
    spec: QualityCheckSpec,
    task_dir: Path,
    artifact_status: list[ArtifactStatus],
) -> QualityCheckResult:
    minimum = int(spec.config.get("min_bytes", 0) or 0)
    too_small: list[str] = []
    checked = 0
    for entry in artifact_status:
        if not entry.present:
            continue
        checked += 1
        full = _safe_join(task_dir, entry.path)
        if full is None or not full.is_file():
            too_small.append(entry.path)
            continue
        try:
            size = full.stat().st_size
        except OSError:
            too_small.append(entry.path)
            continue
        if size < minimum:
            too_small.append(entry.path)
    passed = len(too_small) == 0
    return QualityCheckResult(
        id=spec.id, type=spec.type, passed=passed,
        severity=spec.severity, blocking=spec.blocking,
        message=(
            f"All {checked} present artifacts >= {minimum} bytes"
            if passed
            else f"{len(too_small)} present artifact(s) below {minimum} bytes"
        ),
        detail={"min_bytes": minimum, "below_threshold": too_small} if too_small else None,
    )


def _check_agent_output_forbidden(
    spec: QualityCheckSpec,
    task_dir: Path,
) -> QualityCheckResult:
    agent = str(spec.config.get("agent") or "").strip()
    raw_substrings = spec.config.get("substrings") or []
    case_sensitive = bool(spec.config.get("case_sensitive", False))
    if not agent or not isinstance(raw_substrings, list) or not raw_substrings:
        return QualityCheckResult(
            id=spec.id, type=spec.type, passed=False,
            severity=spec.severity, blocking=spec.blocking,
            message="config.agent and config.substrings (list) are required",
        )
    forbidden = [str(item) for item in raw_substrings if isinstance(item, str) and item]
    rel = f"agents/{agent}.txt"
    full = _safe_join(task_dir, rel)
    if full is None or not full.is_file():
        return QualityCheckResult(
            id=spec.id, type=spec.type, passed=False,
            severity=spec.severity, blocking=spec.blocking,
            message=f"Agent output {rel!r} not present",
        )
    try:
        text = full.read_text(encoding="utf-8")
    except OSError as exc:
        return QualityCheckResult(
            id=spec.id, type=spec.type, passed=False,
            severity=spec.severity, blocking=spec.blocking,
            message=f"Could not read {rel!r}: {exc}",
        )
    haystack = text if case_sensitive else text.lower()
    found: list[str] = []
    for needle in forbidden:
        target = needle if case_sensitive else needle.lower()
        if target in haystack:
            found.append(needle)
    passed = len(found) == 0
    return QualityCheckResult(
        id=spec.id, type=spec.type, passed=passed,
        severity=spec.severity, blocking=spec.blocking,
        message=(
            f"Agent {agent!r} output is clean of {len(forbidden)} forbidden marker(s)"
            if passed
            else f"Agent {agent!r} output contains forbidden marker(s): {found}"
        ),
        detail={"forbidden": forbidden, "found": found} if found else None,
    )


def _read_agent_text(spec: QualityCheckSpec, task_dir: Path) -> tuple[str | None, str | None]:
    agent = str(spec.config.get("agent") or "").strip()
    if not agent:
        return None, "config.agent is required"
    rel = f"agents/{agent}.txt"
    full = _safe_join(task_dir, rel)
    if full is None or not full.is_file():
        return None, f"Agent output {rel!r} not present"
    try:
        return full.read_text(encoding="utf-8"), None
    except OSError as exc:
        return None, f"Could not read {rel!r}: {exc}"


def _check_min_source_count(
    spec: QualityCheckSpec,
    task_dir: Path,
) -> QualityCheckResult:
    from backend.App.orchestration.application.scenarios.typed_writers import parse_sources

    minimum = int(spec.config.get("min", 0) or 0)
    text, error = _read_agent_text(spec, task_dir)
    if text is None:
        return QualityCheckResult(
            id=spec.id, type=spec.type, passed=False,
            severity=spec.severity, blocking=spec.blocking,
            message=error or "agent output missing",
        )
    sources = parse_sources(text)
    kept = sum(1 for source in sources if source.kept)
    passed = kept >= minimum
    return QualityCheckResult(
        id=spec.id, type=spec.type, passed=passed,
        severity=spec.severity, blocking=spec.blocking,
        message=f"{kept} kept source(s) (min={minimum})",
        detail={"kept": kept, "total": len(sources), "min": minimum},
    )


def _check_claims_have_sources(
    spec: QualityCheckSpec,
    task_dir: Path,
) -> QualityCheckResult:
    from backend.App.orchestration.application.scenarios.typed_writers import (
        parse_unverified_claims,
    )

    text, error = _read_agent_text(spec, task_dir)
    if text is None:
        return QualityCheckResult(
            id=spec.id, type=spec.type, passed=False,
            severity=spec.severity, blocking=spec.blocking,
            message=error or "agent output missing",
        )
    unverified = parse_unverified_claims(text)
    passed = len(unverified) == 0
    return QualityCheckResult(
        id=spec.id, type=spec.type, passed=passed,
        severity=spec.severity, blocking=spec.blocking,
        message=("All claims have sources" if passed
                 else f"{len(unverified)} unverified claim(s) recorded"),
        detail={"unverified": unverified} if unverified else None,
    )


def _check_no_unverified_claims(
    spec: QualityCheckSpec,
    task_dir: Path,
) -> QualityCheckResult:
    return _check_claims_have_sources(spec, task_dir)


def _check_calculations_have_formula(
    spec: QualityCheckSpec,
    task_dir: Path,
) -> QualityCheckResult:
    from backend.App.orchestration.application.scenarios.typed_writers import (
        parse_calculations,
    )

    text, error = _read_agent_text(spec, task_dir)
    if text is None:
        return QualityCheckResult(
            id=spec.id, type=spec.type, passed=False,
            severity=spec.severity, blocking=spec.blocking,
            message=error or "agent output missing",
        )
    calculations = parse_calculations(text)
    missing = [calc.get("name") or "<unnamed>" for calc in calculations if not calc.get("formula")]
    passed = len(calculations) > 0 and not missing
    return QualityCheckResult(
        id=spec.id, type=spec.type, passed=passed,
        severity=spec.severity, blocking=spec.blocking,
        message=(
            f"{len(calculations)} calculation(s) with formulas"
            if passed
            else f"{len(missing)} calculation(s) missing formula"
        ),
        detail={"missing_formula_for": missing} if missing else None,
    )


def _check_charts_minimum(
    spec: QualityCheckSpec,
    task_dir: Path,
) -> QualityCheckResult:
    from backend.App.orchestration.application.scenarios.typed_writers import (
        parse_chart_manifest,
    )

    minimum = int(spec.config.get("min", 1) or 1)
    text, error = _read_agent_text(spec, task_dir)
    if text is None:
        return QualityCheckResult(
            id=spec.id, type=spec.type, passed=False,
            severity=spec.severity, blocking=spec.blocking,
            message=error or "agent output missing",
        )
    charts = parse_chart_manifest(text)
    passed = len(charts) >= minimum
    return QualityCheckResult(
        id=spec.id, type=spec.type, passed=passed,
        severity=spec.severity, blocking=spec.blocking,
        message=f"{len(charts)} chart(s) declared (min={minimum})",
        detail={"count": len(charts), "min": minimum},
    )


def _check_no_warnings(
    spec: QualityCheckSpec,
    warnings: list[str],
) -> QualityCheckResult:
    passed = len(warnings) == 0
    return QualityCheckResult(
        id=spec.id, type=spec.type, passed=passed,
        severity=spec.severity, blocking=spec.blocking,
        message=("No scenario warnings recorded" if passed else f"{len(warnings)} warnings recorded"),
        detail={"warnings": list(warnings)} if warnings else None,
    )


def run_quality_checks(
    specs: Iterable[QualityCheckSpec],
    task_dir: Path,
    artifact_status: list[ArtifactStatus],
    warnings: list[str],
    pipeline_steps: list[str] | None = None,
) -> list[QualityCheckResult]:
    steps = list(pipeline_steps or [])
    results: list[QualityCheckResult] = []
    for spec in specs:
        if spec.type == "artifact_count":
            results.append(_check_artifact_count(spec, artifact_status))
        elif spec.type == "artifact_min_size":
            results.append(_check_artifact_min_size(spec, task_dir))
        elif spec.type == "agent_output_min_chars":
            results.append(_check_agent_output_min_chars(spec, task_dir))
        elif spec.type == "agent_output_contains":
            results.append(_check_agent_output_contains(spec, task_dir))
        elif spec.type == "no_warnings":
            results.append(_check_no_warnings(spec, warnings))
        elif spec.type == "pipeline_step_count":
            results.append(_check_pipeline_step_count(spec, steps))
        elif spec.type == "every_artifact_min_size":
            results.append(
                _check_every_artifact_min_size(spec, task_dir, artifact_status)
            )
        elif spec.type == "agent_output_forbidden":
            results.append(_check_agent_output_forbidden(spec, task_dir))
        elif spec.type == "min_source_count":
            results.append(_check_min_source_count(spec, task_dir))
        elif spec.type == "claims_have_sources":
            results.append(_check_claims_have_sources(spec, task_dir))
        elif spec.type == "no_unverified_claims":
            results.append(_check_no_unverified_claims(spec, task_dir))
        elif spec.type == "calculations_have_formula":
            results.append(_check_calculations_have_formula(spec, task_dir))
        elif spec.type == "charts_minimum":
            results.append(_check_charts_minimum(spec, task_dir))
        else:
            logger.warning("Unknown quality check type %r in spec %r", spec.type, spec.id)
            results.append(
                QualityCheckResult(
                    id=spec.id, type=spec.type, passed=False,
                    severity=spec.severity, blocking=spec.blocking,
                    message=f"Unknown check type: {spec.type!r}",
                )
            )
    return results


def summarize_quality_results(results: list[QualityCheckResult]) -> dict[str, Any]:
    passed = sum(1 for result in results if result.passed)
    failed = len(results) - passed
    blocking_failed = [
        result.id for result in results
        if not result.passed and result.blocking
    ]
    return {
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "blocking_failed": blocking_failed,
    }
