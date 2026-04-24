from __future__ import annotations

import json

from backend.App.orchestration.application.use_cases.post_run_intelligence import persist_post_run_intelligence
from backend.App.orchestration.domain.agent_identity import build_agent_profiles
from backend.App.orchestration.domain.automation_agents import build_automation_agent_report


def test_build_agent_profiles_from_verdicts_and_human_edits() -> None:
    profiles = build_agent_profiles({
        "dev_review_output": "VERDICT: NEEDS_WORK\nMissing launch evidence.",
        "dev_human_output": "Approved after manual fix.",
    })

    assert profiles["dev"]["blocking_reviews"] == 1
    assert profiles["dev"]["human_edits"] == 1
    assert profiles["dev"]["scratchpad"]["verdicts"] == ["NEEDS_WORK"]


def test_build_automation_agent_report_uses_run_evidence() -> None:
    report = build_automation_agent_report({
        "workspace_truth": {"changed_files": ["app.py"]},
        "run_manifest": {"status": "executed"},
        "verification_gates": [{"gate_name": "build_gate", "passed": False}],
        "asset_manifest": {"requested_assets": [{"path": "logo.png"}]},
    })

    findings = {item["agent"]: item for item in report["findings"]}
    assert findings["pipeline_recommender"]["status"] == "ready"
    assert findings["swarm_optimizer"]["status"] == "needs_attention"
    assert findings["safe_task_executor"]["status"] == "blocked"


def test_persist_post_run_intelligence_writes_artifacts(tmp_path) -> None:
    task_dir = tmp_path / "artifacts" / "task-1"
    workspace_root = tmp_path / "workspace"
    task_dir.mkdir(parents=True)
    workspace_root.mkdir()

    result = persist_post_run_intelligence(
        task_dir,
        workspace_root,
        {"qa_review_output": "VERDICT: OK"},
    )

    assert result["agent_identity"]["profiles"]["qa"]["successful_reviews"] == 1
    assert json.loads((task_dir / "automation_agents.json").read_text(encoding="utf-8"))["schema"]
    assert (workspace_root / ".swarm" / "agent_identity.json").is_file()
