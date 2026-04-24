from __future__ import annotations

import json

from backend.App.shared.application.settings_resolver import get_setting_bool, reset_state_for_tests
from backend.App.workspace.infrastructure.project_settings import save_project_settings


def test_project_settings_save_invalidates_settings_cache(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    save_project_settings(workspace_root, {"dream": {"enabled": False}})
    assert get_setting_bool("dream.enabled", workspace_root=workspace_root, default=True) is False

    settings_path = workspace_root / ".swarm" / "settings.json"
    settings_path.write_text(json.dumps({"dream": {"enabled": True}}), encoding="utf-8")
    save_project_settings(workspace_root, {"dream": {"enabled": True}})

    assert get_setting_bool("dream.enabled", workspace_root=workspace_root, default=False) is True
    reset_state_for_tests()
