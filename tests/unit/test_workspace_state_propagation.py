from __future__ import annotations

from backend.App.orchestration.application.pipeline.pipeline_state import (
    pipeline_workspace_parts_from_meta,
)
from backend.App.orchestration.application.pipeline.workspace_helpers import (
    resolved_workspace_root,
)


def test_workspace_parts_populates_both_root_keys():
    parts = pipeline_workspace_parts_from_meta(
        {
            "workspace_root": "/Users/baster/projects/my/game_1",
            "workspace_root_resolved": "/Users/baster/projects/my/game_1",
        }
    )
    assert parts["workspace_root"] == "/Users/baster/projects/my/game_1"
    assert parts["workspace_root_resolved"] == "/Users/baster/projects/my/game_1"


def test_workspace_parts_defaults_root_to_resolved():
    parts = pipeline_workspace_parts_from_meta(
        {"workspace_root_resolved": "/abs/path"}
    )
    assert parts["workspace_root"] == "/abs/path"
    assert parts["workspace_root_resolved"] == "/abs/path"


def test_workspace_parts_defaults_resolved_to_raw():
    parts = pipeline_workspace_parts_from_meta(
        {"workspace_root": "/Users/me/code"}
    )
    assert parts["workspace_root"] == "/Users/me/code"
    assert parts["workspace_root_resolved"] == "/Users/me/code"


def test_workspace_parts_empty_when_neither_provided():
    parts = pipeline_workspace_parts_from_meta({})
    assert parts["workspace_root"] == ""
    assert parts["workspace_root_resolved"] == ""


def test_resolved_workspace_root_prefers_resolved():
    state = {
        "workspace_root": "/raw",
        "workspace_root_resolved": "/resolved",
    }
    assert resolved_workspace_root(state) == "/resolved"


def test_resolved_workspace_root_falls_back_to_raw():
    state = {"workspace_root": "/raw"}
    assert resolved_workspace_root(state) == "/raw"


def test_resolved_workspace_root_empty_when_missing():
    assert resolved_workspace_root({}) == ""
