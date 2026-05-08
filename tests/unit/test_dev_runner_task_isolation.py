from backend.App.orchestration.application.nodes.dev_runner import (
    tasks_share_expected_paths,
)


def test_tasks_share_expected_paths_detects_conflict() -> None:
    tasks = [
        {"id": "a", "expected_paths": ["Assets/Scripts/GameFlowManager.cs"]},
        {"id": "b", "expected_paths": ["Assets/Scripts/GameFlowManager.cs"]},
    ]

    assert tasks_share_expected_paths(tasks) is True


def test_tasks_share_expected_paths_allows_disjoint_paths() -> None:
    tasks = [
        {"id": "a", "expected_paths": ["Assets/Scripts/GameFlowManager.cs"]},
        {"id": "b", "expected_paths": ["Assets/Scripts/ResourceGainManager.cs"]},
    ]

    assert tasks_share_expected_paths(tasks) is False
