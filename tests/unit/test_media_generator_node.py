from __future__ import annotations

from pathlib import Path

from backend.App.orchestration.application.nodes.media_generator import media_generator_node
from backend.App.orchestration.domain.media.contracts import MediaArtifact, MediaRequest


class FakeMediaProvider:
    name = "fake"

    def __init__(self, *, cost: float = 0.25, license: str = "cc0") -> None:
        self.cost = cost
        self.license = license

    def supports(self, kind: str) -> bool:
        return kind == "image"

    def estimate_cost(self, request: MediaRequest) -> float:
        return self.cost

    def generate(self, request: MediaRequest) -> MediaArtifact:
        return MediaArtifact(
            relative_path=request.target_path,
            kind=request.kind,
            bytes_size=123,
            provider=self.name,
            license=self.license,
            cost_usd=self.cost,
        )


def test_media_generator_uses_swarm_media_budget_keys(tmp_path: Path) -> None:
    result = media_generator_node({
        "workspace_root": str(tmp_path),
        "media_requests": [
            {
                "kind": "image",
                "prompt": "Product hero image",
                "target_path": "assets/hero.png",
            }
        ],
        "agent_config": {
            "swarm": {
                "media": {
                    "budget": {
                        "max_cost_usd_per_task": 1.0,
                        "max_attempts_per_asset": 1,
                    },
                    "license_policy": "permissive_only",
                }
            }
        },
        "_media_provider_registry": [FakeMediaProvider()],
    })

    assert result["media_generator_output"].startswith("[ok] image")
    assert result["media_budget_used"] == 0.25
    assert result["media_budget"]["max_cost_usd"] == 1.0
    assert result["media_budget"]["max_attempts"] == 1
    assert result["media_artifacts"][0]["relative_path"] == "assets/hero.png"


def test_media_generator_still_reads_legacy_top_level_media_budget(tmp_path: Path) -> None:
    result = media_generator_node({
        "workspace_root": str(tmp_path),
        "media_requests": [
            {
                "kind": "image",
                "prompt": "Product hero image",
                "target_path": "assets/hero.png",
            }
        ],
        "agent_config": {
            "media": {
                "max_cost_usd": 0.1,
                "max_attempts": 1,
                "license_policy": "permissive_only",
            }
        },
        "_media_provider_registry": [FakeMediaProvider(cost=0.25)],
    })

    assert "would exceed cap" in result["media_generator_output"]
    assert result["media_artifacts"] == []
