"""pipeline.hooks — модуль хуков по dotted path."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from backend.App.orchestration.application.routing.pipeline_graph import _hook_wrap
from backend.App.orchestration.application.pipeline.pipeline_hooks import clear_hooks_cache_for_tests


def _write_hook_mod(root: Path) -> None:
    pkg = root / "hookpkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "swarm_hooks.py").write_text(
        """
def before_pipeline_step(step_id, state):
    if step_id == "pm":
        return {"_hook_seen": "before:" + step_id}
    return {}

def after_pipeline_step(step_id, state, step_delta):
    state["_hook_after"] = step_id
""",
        encoding="utf-8",
    )


def test_hook_before_merges_into_step(monkeypatch, tmp_path: Path):
    _write_hook_mod(tmp_path)
    sys.path.insert(0, str(tmp_path))
    clear_hooks_cache_for_tests()
    monkeypatch.setenv("SWARM_PIPELINE_HOOKS_MODULE", "hookpkg.swarm_hooks")

    def fake_pm(_state: dict[str, Any]) -> dict[str, Any]:
        return {"pm_output": "ok", "pm_model": "m", "pm_provider": "local"}

    wrapped = _hook_wrap("pm", fake_pm)
    state: dict[str, Any] = {
        "input": "hi",
        "agent_config": {},
        "workspace_root": "",
        "workspace_apply_writes": False,
        "task_id": "",
        "code_analysis": {},
        "doc_fetch_manifest": [],
    }
    try:
        out = wrapped(state)  # type: ignore[arg-type]
    finally:
        sys.path.remove(str(tmp_path))
        clear_hooks_cache_for_tests()
        monkeypatch.delenv("SWARM_PIPELINE_HOOKS_MODULE", raising=False)

    assert out.get("_hook_seen") == "before:pm"
    assert out.get("pm_output") == "ok"
