from pathlib import Path

import pytest

from backend.App.orchestration.application.enforcement.workspace_preflight import (
    enforce_workspace_preflight,
)


def test_workspace_preflight_blocks_existing_source_corruption(tmp_path: Path) -> None:
    source_dir = tmp_path / "Assets" / "Scripts"
    source_dir.mkdir(parents=True)
    (source_dir / "ResourceGainManager.cs").write_text(
        "public class ResourceGainManager {}\n=======\n",
        encoding="utf-8",
    )
    state = {
        "workspace_root": str(tmp_path),
        "workspace_apply_writes": True,
    }

    with pytest.raises(RuntimeError) as info:
        enforce_workspace_preflight(state, "dev")

    assert "preflight_blocker" in str(info.value)
    assert state["workspace_preflight"]["passed"] is False
    assert "preflight_source_corruption" in state["_failed_trusted_gates"]


def test_workspace_preflight_passes_clean_write_step(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")
    state = {
        "workspace_root": str(tmp_path),
        "workspace_apply_writes": True,
    }

    event = enforce_workspace_preflight(state, "dev")

    assert event is not None
    assert event["status"] == "completed"
    assert state["workspace_preflight"]["passed"] is True


def test_workspace_preflight_ignores_non_write_steps(tmp_path: Path) -> None:
    (tmp_path / "bad.cs").write_text("class Bad {}\n=======\n", encoding="utf-8")
    state = {
        "workspace_root": str(tmp_path),
        "workspace_apply_writes": True,
    }

    assert enforce_workspace_preflight(state, "pm") is None
    assert "workspace_preflight" not in state
