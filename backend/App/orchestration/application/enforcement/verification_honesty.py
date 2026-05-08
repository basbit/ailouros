from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class VerificationVerdict:
    not_run: tuple[str, ...]
    skipped_by_policy: tuple[str, ...]
    failed: tuple[str, ...]
    passed: tuple[str, ...]
    has_runnable: bool
    blocking: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["not_run"] = list(self.not_run)
        payload["skipped_by_policy"] = list(self.skipped_by_policy)
        payload["failed"] = list(self.failed)
        payload["passed"] = list(self.passed)
        return payload


def classify_verification(snapshot: dict[str, Any]) -> VerificationVerdict:
    contract = snapshot.get("verification_contract") or {}
    expected = contract.get("expected_trusted_commands") or []
    expected_names = [
        str(entry.get("command") or "").strip()
        for entry in expected
        if isinstance(entry, dict) and entry.get("command")
    ]
    gates_run_raw = contract.get("gates_run") or []
    gates_run = {str(name).strip() for name in gates_run_raw if name}

    failed_trusted_raw = snapshot.get("_failed_trusted_gates") or []
    failed_trusted = {str(name).strip() for name in failed_trusted_raw if name}

    passed: list[str] = []
    failed: list[str] = []
    not_run: list[str] = []
    skipped_by_policy: list[str] = []

    runnable_executed = bool(snapshot.get("dev_runnable_check_executed"))
    runnable_skipped = bool(snapshot.get("dev_runnable_skipped_by_policy"))

    for command in expected_names:
        if command in failed_trusted:
            failed.append(command)
        elif command in gates_run:
            passed.append(command)
        elif runnable_skipped:
            skipped_by_policy.append(command)
        else:
            not_run.append(command)

    blocking = bool(failed) or (
        bool(expected_names) and not runnable_executed and not runnable_skipped
    )
    return VerificationVerdict(
        not_run=tuple(not_run),
        skipped_by_policy=tuple(skipped_by_policy),
        failed=tuple(failed),
        passed=tuple(passed),
        has_runnable=runnable_executed,
        blocking=blocking,
    )
