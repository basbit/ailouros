from __future__ import annotations

from backend.App.orchestration.application.streaming.stream_finalise import (
    build_asset_manifest as _build_asset_manifest,
    build_run_manifest as _build_run_manifest,
    build_workspace_truth as _build_workspace_truth,
)


def test_build_run_manifest_from_incremental_shell_runs() -> None:
    manifest = _build_run_manifest({
        "workspace_writes_incremental": [
            {
                "step": "devops",
                "shell_runs": [{"cmd": "npm run build", "returncode": 0}],
            }
        ]
    })

    assert manifest["status"] == "executed"
    assert manifest["commands_executed"] == 1


def test_build_workspace_truth_merges_incremental_and_final_writes() -> None:
    truth = _build_workspace_truth({
        "workspace_writes": {"written": ["a.py"]},
        "workspace_writes_incremental": [{"patched": ["b.py"]}],
        "filesystem_truth": {"diff": {"changed_files": ["c.py"]}},
    })

    assert truth["changed_files"] == ["a.py", "b.py", "c.py"]


def test_build_asset_manifest_from_binary_requests() -> None:
    manifest = _build_asset_manifest({
        "workspace_writes_incremental": [
            {"binary_assets_requested": ["Assets/Art/JournalSkin.png"]}
        ]
    })

    assert manifest["status"] == "blocked"
    assert manifest["requested_assets"][0]["path"] == "Assets/Art/JournalSkin.png"


def test_build_asset_manifest_from_asset_request() -> None:
    manifest = _build_asset_manifest({
        "workspace_writes": {
            "asset_requests": [
                {
                    "path": "Assets/Art/JournalSkin.png",
                    "source": "generate",
                    "prompt": "painted journal cover",
                    "license": "",
                    "provenance": "user_prompt",
                }
            ]
        }
    })

    assert manifest["status"] == "blocked"
    assert manifest["requested_assets"][0]["source"] == "generate"
    assert manifest["requested_assets"][0]["status"] == "pending_generation"
