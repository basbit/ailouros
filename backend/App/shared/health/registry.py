from __future__ import annotations

from backend.App.shared.health.probe import HealthProbe
from backend.App.shared.health.probes.embedding_probe import EmbeddingProbe
from backend.App.shared.health.probes.llm_probe import LlmProbe
from backend.App.shared.health.probes.plugin_registry_probe import PluginRegistryProbe
from backend.App.shared.health.probes.qdrant_probe import QdrantProbe
from backend.App.shared.health.probes.redis_probe import RedisProbe
from backend.App.shared.health.probes.spec_engine_probe import SpecEngineProbe


def default_probes() -> tuple[HealthProbe, ...]:
    return (
        EmbeddingProbe(),
        QdrantProbe(),
        RedisProbe(),
        LlmProbe(),
        SpecEngineProbe(),
        PluginRegistryProbe(),
    )


__all__ = ["default_probes"]
