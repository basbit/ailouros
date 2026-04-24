from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

_MAX_DEV_QA_SUBTASKS = int(os.environ.get("SWARM_MAX_DEV_QA_SUBTASKS", "20"))
_REQUIRED_DELIVERABLE_KEYS = {
    "must_exist_files",
    "spec_symbols",
    "verification_commands",
    "assumptions",
    "production_paths",
    "placeholder_allow_list",
}


def _normalize_string_list(raw: Any) -> list[str]:
    values: list[str] = []
    for item in raw or []:
        text = str(item or "").strip()
        if text and text not in values:
            values.append(text)
    return values


def _normalize_task_dependencies(raw: Any) -> list[str]:
    return _normalize_string_list(raw)


def _normalize_verification_commands(raw: Any) -> list[dict[str, str]]:
    commands: list[dict[str, str]] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        command = str(item.get("command") or "").strip()
        expected = str(item.get("expected") or "").strip()
        if not command or not expected:
            continue
        commands.append({"command": command, "expected": expected})
    return commands


def _normalize_placeholder_allow_list(raw: Any) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        pattern = str(item.get("pattern") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if not pattern or not reason:
            continue
        key = (path, pattern, reason)
        if key in seen:
            continue
        seen.add(key)
        entries.append({"path": path, "pattern": pattern, "reason": reason})
    return entries


def _normalize_deliverables(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    return {
        "must_exist_files": _normalize_string_list(data.get("must_exist_files")),
        "spec_symbols": _normalize_string_list(data.get("spec_symbols")),
        "verification_commands": _normalize_verification_commands(data.get("verification_commands")),
        "assumptions": _normalize_string_list(data.get("assumptions")),
        "production_paths": _normalize_string_list(data.get("production_paths")),
        "placeholder_allow_list": _normalize_placeholder_allow_list(data.get("placeholder_allow_list")),
    }


def _extract_json_by_brackets(text: str) -> list[str]:
    results: list[str] = []
    for opener, closer in (('{', '}'), ('[', ']')):
        i = 0
        while i < len(text):
            start = text.find(opener, i)
            if start == -1:
                break
            depth = 0
            in_str = False
            escape = False
            for j in range(start, len(text)):
                ch = text[j]
                if escape:
                    escape = False
                    continue
                if ch == '\\' and in_str:
                    escape = True
                    continue
                if ch == '"':
                    in_str = not in_str
                    continue
                if in_str:
                    continue
                if ch == opener:
                    depth += 1
                elif ch == closer:
                    depth -= 1
                    if depth == 0:
                        results.append(text[start:j + 1])
                        i = j + 1
                        break
            else:
                break  # unbalanced — stop scanning for this opener
    results.sort(key=len, reverse=True)
    return results


def parse_dev_lead_plan(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return {"tasks": [], "deliverables": _normalize_deliverables(None), "has_deliverables": False}

    blocks = re.findall(
        r"```(?:json)?\s*([\[{][\s\S]*[\]}])\s*```",
        text,
        flags=re.IGNORECASE,
    )
    candidates: list[str] = blocks + [text] + _extract_json_by_brackets(text)
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        has_deliverables = isinstance(data, dict) and isinstance(data.get("deliverables"), dict)
        if isinstance(data, list):
            data = {"tasks": data, "deliverables": {}}
        if not isinstance(data, dict):
            continue
        deliverables_raw = data.get("deliverables")
        deliverables_keys = set(deliverables_raw.keys()) if isinstance(deliverables_raw, dict) else set()
        return {
            "tasks": _parse_tasks_from_data(data.get("tasks")),
            "deliverables": _normalize_deliverables(deliverables_raw),
            "has_deliverables": has_deliverables,
            "has_complete_deliverables": has_deliverables and _REQUIRED_DELIVERABLE_KEYS.issubset(deliverables_keys),
        }
    return {
        "tasks": [],
        "deliverables": _normalize_deliverables(None),
        "has_deliverables": False,
        "has_complete_deliverables": False,
    }


def _parse_tasks_from_data(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, list):
        return []
    parsed_tasks: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        subtask_id = str(item.get("id") or item.get("task_id") or len(parsed_tasks) + 1)
        title = str(item.get("title") or item.get("name") or f"Task {subtask_id}")
        dev_scope = (
            item.get("development_scope")
            or item.get("development")
            or item.get("dev_scope")
            or item.get("scope")
            or ""
        )
        qa_scope = (
            item.get("testing_scope")
            or item.get("testing")
            or item.get("qa_scope")
            or item.get("acceptance")
            or ""
        )
        if not isinstance(dev_scope, str):
            dev_scope = str(dev_scope)
        if not isinstance(qa_scope, str):
            qa_scope = str(qa_scope)
        parsed_tasks.append(
            {
                "id": subtask_id,
                "title": title,
                "development_scope": dev_scope.strip(),
                "testing_scope": qa_scope.strip(),
                "expected_paths": _normalize_string_list(item.get("expected_paths")),
                "dependencies": _normalize_task_dependencies(item.get("dependencies")),
            }
        )
    return parsed_tasks


def parse_dev_qa_task_plan(raw: str) -> list[dict[str, Any]]:
    return parse_dev_lead_plan(raw)["tasks"]


def read_dev_qa_task_count_target(
    agent_config: Optional[dict[str, Any]],
) -> Optional[int]:
    ac = agent_config or {}
    swarm = ac.get("swarm")
    swarm_d: dict[str, Any] = swarm if isinstance(swarm, dict) else {}

    def _coerce_cap(raw: Any) -> Optional[int]:
        if raw is None:
            return None
        try:
            parsed_int = int(raw)
        except (TypeError, ValueError):
            return None
        if parsed_int < 1:
            return None
        return min(parsed_int, _MAX_DEV_QA_SUBTASKS)

    legacy_count = _coerce_cap(swarm_d.get("dev_qa_task_count"))
    if legacy_count is not None:
        return legacy_count

    dev_count = _coerce_cap(swarm_d.get("dev_task_count"))
    qa_count = _coerce_cap(swarm_d.get("qa_task_count"))
    if dev_count is not None or qa_count is not None:
        parts = [x for x in (dev_count, qa_count) if x is not None]
        return max(parts) if parts else None

    env = os.getenv("SWARM_DEV_QA_TASK_COUNT", "").strip()
    if env.isdigit():
        return _coerce_cap(int(env))
    return None


def normalize_dev_qa_tasks_to_count(
    tasks: list[dict[str, Any]], target: int,
) -> list[dict[str, Any]]:
    if target < 1:
        return list(tasks)
    task_list = list(tasks)
    if len(task_list) >= target:
        return task_list[:target]
    original_count = len(task_list)
    for k in range(original_count, target):
        idx = k + 1
        task_list.append(
            {
                "id": str(idx),
                "title": f"Subtask {idx}/{target}",
                "development_scope": (
                    "Implement the remaining scope according to the **full specification**, consistently "
                    "with the already planned subtasks (supplement to previous plan items)."
                ),
                "testing_scope": (
                    f"Verify subtask {idx}: scenarios and acceptance criteria from the specification "
                    "for this work fragment."
                ),
            }
        )
    return task_list


def _dev_spec_max_chars() -> int:
    raw = os.getenv("SWARM_DEV_SPEC_MAX_CHARS", "").strip()
    if raw.isdigit():
        v = int(raw)
        if v > 0:
            return v
    return 80_000


def _dev_devops_max_chars() -> int:
    raw = os.getenv("SWARM_DEV_DEVOPS_MAX_CHARS", "").strip()
    if raw.isdigit():
        v = int(raw)
        if v > 0:
            return v
    return 20_000
