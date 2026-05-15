from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Mapping, Optional

_KNOWN_ROLES: frozenset[str] = frozenset(
    {
        "pm",
        "ba",
        "architect",
        "dev_lead",
        "dev",
        "qa",
        "review_dev",
        "review_pm",
        "review_ba",
        "human_qa",
        "spec_drafter",
        "codegen_agent",
        "code_verifier",
    }
)


@dataclass(frozen=True)
class RoleBudget:
    prompt_tokens_max: Optional[int] = None
    reasoning_tokens_max: Optional[int] = None
    completion_tokens_max: Optional[int] = None
    total_tokens_ceiling: Optional[int] = None


_FIELD_NAMES: frozenset[str] = frozenset(f.name for f in fields(RoleBudget))


class BudgetExceededError(RuntimeError):
    def __init__(self, channel: str, used: int, cap: int, *, role: str = "") -> None:
        self.channel = channel
        self.used = used
        self.cap = cap
        self.role = role
        suffix = f" role={role!r}" if role else ""
        super().__init__(
            f"RoleBudget exceeded on channel={channel!r}: used={used} > cap={cap}{suffix}"
        )


def _validate_int_field(role: str, field_name: str, raw: Any) -> Optional[int]:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(
            f"role_budgets[{role!r}].{field_name}: expected int, got {type(raw).__name__}={raw!r}"
        )
    if raw < 0:
        raise ValueError(
            f"role_budgets[{role!r}].{field_name}: must be non-negative, got {raw}"
        )
    return raw


def _build_role_budget(role: str, raw: Mapping[str, Any]) -> RoleBudget:
    unknown = set(raw.keys()) - _FIELD_NAMES
    if unknown:
        raise ValueError(
            f"role_budgets[{role!r}]: unknown field(s) {sorted(unknown)!r}; "
            f"allowed={sorted(_FIELD_NAMES)!r}"
        )
    values: dict[str, Optional[int]] = {}
    for field_name in _FIELD_NAMES:
        values[field_name] = _validate_int_field(role, field_name, raw.get(field_name))
    return RoleBudget(**values)


def parse_role_budgets(raw_json: Any) -> dict[str, RoleBudget]:
    if not isinstance(raw_json, Mapping):
        raise ValueError(
            f"role_budgets: root must be a JSON object, got {type(raw_json).__name__}"
        )
    parsed: dict[str, RoleBudget] = {}
    for role, fields_dict in raw_json.items():
        if not isinstance(role, str) or not role:
            raise ValueError(f"role_budgets: invalid role key {role!r}")
        if not isinstance(fields_dict, Mapping):
            raise ValueError(
                f"role_budgets[{role!r}]: expected JSON object, got {type(fields_dict).__name__}"
            )
        parsed[role] = _build_role_budget(role, fields_dict)
    missing = _KNOWN_ROLES - parsed.keys()
    if missing:
        raise ValueError(
            f"role_budgets: missing required role(s) {sorted(missing)!r}; "
            f"known={sorted(_KNOWN_ROLES)!r}"
        )
    return parsed


__all__ = [
    "RoleBudget",
    "BudgetExceededError",
    "parse_role_budgets",
]
