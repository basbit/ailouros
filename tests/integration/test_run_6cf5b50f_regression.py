from __future__ import annotations

import pytest

from backend.App.orchestration.application.enforcement.pipeline_enforcement import (
    enforce_non_empty_step_output,
)
from backend.App.orchestration.application.streaming.stream_finalise import build_asset_manifest
from backend.App.orchestration.domain.quality_gate_policy import extract_verdict
from backend.App.workspace.infrastructure.code_analysis.scan import _entities_csharp


TARGET_RUN_IDENTIFIER = "6cf5b50f-5f3d-43f2-b8ee-1e58d2eaeb0d"


def test_run_6cf5b50f_regression_guards() -> None:
    review_devops_output = (
        "VERDICT: NEEDS_WORK\n"
        '<defect_report>{"defects":[{"id":"D1","severity":"P1","fixed":false}]}</defect_report>\n'
        "After retry, shell evidence was added.\n"
        "VERDICT: OK\n"
    )
    asset_manifest = build_asset_manifest({
        "workspace_writes_incremental": [
            {"binary_assets_requested": ["Assets/Art/JournalSkin.png"]}
        ]
    })
    csharp_entities = _entities_csharp(
        "namespace Garden.Gameplay\n"
        "{\n"
        "public class JournalController : MonoBehaviour\n"
        "{\n"
        "[SerializeField] private Sprite journalSkin;\n"
        "public void OpenJournal() {}\n"
        "}\n"
        "}\n",
        "Assets/Scripts/JournalController.cs",
    )

    assert TARGET_RUN_IDENTIFIER
    assert extract_verdict(review_devops_output) == "OK"
    assert asset_manifest["status"] == "blocked"
    assert asset_manifest["requested_assets"][0]["path"] == "Assets/Art/JournalSkin.png"
    assert ("class", "JournalController") in {
        (entity["kind"], entity["name"]) for entity in csharp_entities
    }
    assert ("field", "journalSkin") in {
        (entity["kind"], entity["name"]) for entity in csharp_entities
    }
    with pytest.raises(RuntimeError, match="ux_researcher_output is empty"):
        enforce_non_empty_step_output({"ux_researcher_output": ""}, "ux_researcher")
