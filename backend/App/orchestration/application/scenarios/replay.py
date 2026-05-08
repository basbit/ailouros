from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.App.orchestration.application.scenarios.preview import (
    PreviewOverrides,
    build_scenario_preview,
)
from backend.App.orchestration.application.scenarios.registry import (
    default_scenario_registry,
)
from backend.App.orchestration.domain.scenarios.errors import ScenarioNotFound


@dataclass(frozen=True)
class ReplayCase:
    name: str
    scenario_id: str
    overrides: PreviewOverrides
    expected: dict[str, Any]


@dataclass
class ReplayResult:
    case_name: str
    scenario_id: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    actual: dict[str, Any] = field(default_factory=dict)


def load_case(case_dir: Path) -> ReplayCase:
    input_path = case_dir / "input.json"
    expected_path = case_dir / "expected.json"
    if not input_path.is_file():
        raise FileNotFoundError(f"Missing input.json in {case_dir}")
    if not expected_path.is_file():
        raise FileNotFoundError(f"Missing expected.json in {case_dir}")
    raw_input = json.loads(input_path.read_text(encoding="utf-8"))
    raw_expected = json.loads(expected_path.read_text(encoding="utf-8"))
    if not isinstance(raw_input, dict):
        raise ValueError(f"input.json must be a JSON object: {input_path}")
    if not isinstance(raw_expected, dict):
        raise ValueError(f"expected.json must be a JSON object: {expected_path}")
    scenario_id = raw_input.get("scenario_id")
    if not isinstance(scenario_id, str) or not scenario_id.strip():
        raise ValueError(f"input.json must include 'scenario_id': {input_path}")
    overrides = PreviewOverrides(
        pipeline_steps=raw_input.get("pipeline_steps"),
        agent_config=raw_input.get("agent_config"),
        workspace_write=raw_input.get("workspace_write"),
        skip_gates=raw_input.get("skip_gates"),
        model_profile=raw_input.get("model_profile"),
    )
    return ReplayCase(
        name=case_dir.name,
        scenario_id=scenario_id.strip(),
        overrides=overrides,
        expected=raw_expected,
    )


def discover_cases(root: Path) -> list[ReplayCase]:
    if not root.is_dir():
        return []
    cases: list[ReplayCase] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith("__"):
            continue
        cases.append(load_case(entry))
    return cases


def _compare_list(
    actual: Any,
    expected: Any,
    field: str,
    failures: list[str],
) -> None:
    if not isinstance(actual, list):
        failures.append(f"{field}: actual is not a list")
        return
    if not isinstance(expected, list):
        failures.append(f"{field}: expected is not a list")
        return
    if list(actual) != list(expected):
        failures.append(f"{field}: actual={actual} expected={expected}")


def _compare_subset(
    actual: Any,
    expected_subset: Any,
    field: str,
    failures: list[str],
) -> None:
    if not isinstance(actual, list):
        failures.append(f"{field}: actual is not a list")
        return
    if not isinstance(expected_subset, list):
        failures.append(f"{field}: expected_subset is not a list")
        return
    actual_set = set(actual)
    missing = [item for item in expected_subset if item not in actual_set]
    if missing:
        failures.append(f"{field}_contains: missing {missing} in {actual}")


def _compare_count(
    actual: Any,
    expected: Any,
    field: str,
    failures: list[str],
) -> None:
    if not isinstance(actual, list):
        failures.append(f"{field}_count: actual is not a list")
        return
    if not isinstance(expected, int):
        failures.append(f"{field}_count: expected is not an int")
        return
    if len(actual) != expected:
        failures.append(
            f"{field}_count: actual={len(actual)} expected={expected}"
        )


def run_case(case: ReplayCase) -> ReplayResult:
    try:
        scenario = default_scenario_registry().get(case.scenario_id)
    except ScenarioNotFound as exc:
        return ReplayResult(
            case_name=case.name,
            scenario_id=case.scenario_id,
            passed=False,
            failures=[f"Unknown scenario: {exc}"],
        )

    actual = build_scenario_preview(scenario, case.overrides)
    failures: list[str] = []

    if "pipeline_steps" in case.expected:
        _compare_list(
            actual.get("pipeline_steps"),
            case.expected["pipeline_steps"],
            "pipeline_steps",
            failures,
        )
    if "pipeline_steps_contains" in case.expected:
        _compare_subset(
            actual.get("pipeline_steps"),
            case.expected["pipeline_steps_contains"],
            "pipeline_steps",
            failures,
        )
    if "default_gates" in case.expected:
        _compare_list(
            actual.get("default_gates"),
            case.expected["default_gates"],
            "default_gates",
            failures,
        )
    if "expected_artifacts" in case.expected:
        _compare_list(
            actual.get("expected_artifacts"),
            case.expected["expected_artifacts"],
            "expected_artifacts",
            failures,
        )
    if "required_tools" in case.expected:
        _compare_list(
            actual.get("required_tools"),
            case.expected["required_tools"],
            "required_tools",
            failures,
        )
    if "skipped_gates" in case.expected:
        _compare_list(
            actual.get("skipped_gates"),
            case.expected["skipped_gates"],
            "skipped_gates",
            failures,
        )
    if "warnings_count" in case.expected:
        _compare_count(
            actual.get("warnings"),
            case.expected["warnings_count"],
            "warnings",
            failures,
        )
    if "warnings_contains" in case.expected:
        _compare_subset(
            actual.get("warnings"),
            case.expected["warnings_contains"],
            "warnings",
            failures,
        )
    if "workspace_write" in case.expected:
        if actual.get("workspace_write") != case.expected["workspace_write"]:
            failures.append(
                f"workspace_write: actual={actual.get('workspace_write')} "
                f"expected={case.expected['workspace_write']}"
            )
    if "model_profile_applied" in case.expected:
        if actual.get("model_profile_applied") != case.expected["model_profile_applied"]:
            failures.append(
                "model_profile_applied: "
                f"actual={actual.get('model_profile_applied')} "
                f"expected={case.expected['model_profile_applied']}"
            )

    return ReplayResult(
        case_name=case.name,
        scenario_id=case.scenario_id,
        passed=len(failures) == 0,
        failures=failures,
        actual=actual,
    )


def run_all(root: Path) -> list[ReplayResult]:
    return [run_case(case) for case in discover_cases(root)]


def default_golden_root() -> Path:
    return Path(__file__).resolve().parents[5] / "tests" / "golden" / "scenarios"
