from __future__ import annotations

from backend.App.orchestration.application.context.source_research import (
    ensure_source_research,
)


def test_source_research_manifest_not_required() -> None:
    state = {"user_task": "Summarize the local project.", "agent_config": {}}

    ensure_source_research(state, caller_step="pm")  # type: ignore[arg-type]

    assert state["source_research_output"] == "SOURCE_RESEARCH_NOT_REQUIRED"
    assert state["research_manifest"]["status"] == "not_required"
    assert state["research_manifest"]["required"] is False
