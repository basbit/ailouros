from __future__ import annotations

from dataclasses import dataclass
from typing import Any


VALID_INPUT_KEYS = frozenset({
    "prompt",
    "workspace_root",
    "project_context_file",
    "workspace_write",
})


@dataclass(frozen=True)
class InputSpec:
    key: str
    label: str
    hint: str = ""
    required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "hint": self.hint,
            "required": self.required,
        }
