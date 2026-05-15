from __future__ import annotations

from pathlib import Path

from backend.App.shared.health.probes.embedding_probe import EmbeddingProbe
from backend.App.shared.health.probes.llm_probe import LlmProbe
from backend.App.shared.health.probes.plugin_registry_probe import PluginRegistryProbe
from backend.App.shared.health.probes.qdrant_probe import QdrantProbe
from backend.App.shared.health.probes.redis_probe import RedisProbe, redact_url
from backend.App.shared.health.probes.spec_engine_probe import SpecEngineProbe


class _FakeEmbeddingProvider:
    name = "stub"

    def embed(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]


class _BrokenEmbeddingProvider:
    name = "broken"

    def embed(self, texts):
        raise RuntimeError("model not loaded")


class _EmptyEmbeddingProvider:
    name = "null"

    def embed(self, texts):
        return [[] for _ in texts]


def test_embedding_probe_ok() -> None:
    probe = EmbeddingProbe(
        provider_getter=lambda: _FakeEmbeddingProvider(),
        cache_size_getter=lambda: 256,
    )
    result = probe.probe()
    assert result.status == "ok"
    assert result.metadata["provider"] == "stub"
    assert result.metadata["dim"] == "3"
    assert result.metadata["cache_size"] == "256"


def test_embedding_probe_empty_vector_degraded() -> None:
    probe = EmbeddingProbe(
        provider_getter=lambda: _EmptyEmbeddingProvider(),
        cache_size_getter=lambda: 0,
    )
    result = probe.probe()
    assert result.status == "degraded"


def test_embedding_probe_error_on_exception() -> None:
    probe = EmbeddingProbe(
        provider_getter=lambda: _BrokenEmbeddingProvider(),
        cache_size_getter=lambda: 0,
    )
    result = probe.probe()
    assert result.status == "error"
    assert "model not loaded" in result.detail


class _FakeQdrant:
    def list_collections(self):
        return ["a", "b"]


class _FakeQdrantBroken:
    def list_collections(self):
        raise RuntimeError("no connection")


class _InMemoryStub:
    pass


_InMemoryStub.__name__ = "InMemoryVectorStore"


def test_qdrant_probe_ok() -> None:
    probe = QdrantProbe(store_getter=lambda: _FakeQdrant())
    result = probe.probe()
    assert result.status == "ok"
    assert result.metadata["collections"] == "2"


def test_qdrant_probe_in_memory_degraded() -> None:
    instance = _InMemoryStub()
    probe = QdrantProbe(store_getter=lambda: instance)
    result = probe.probe()
    assert result.status == "degraded"


def test_qdrant_probe_error_on_collections_failure() -> None:
    probe = QdrantProbe(store_getter=lambda: _FakeQdrantBroken())
    result = probe.probe()
    assert result.status == "error"
    assert "no connection" in result.detail


def test_qdrant_probe_error_on_init() -> None:
    def boom() -> object:
        raise ConnectionError("refused")

    probe = QdrantProbe(store_getter=boom)
    result = probe.probe()
    assert result.status == "error"


class _RedisStubOk:
    def ping(self) -> bool:
        return True

    def config_get(self, key: str) -> dict[str, str]:
        return {"appendonly": "yes"}


class _RedisStubNoAof:
    def ping(self) -> bool:
        return True

    def config_get(self, key: str) -> dict[str, str]:
        return {"appendonly": "no"}


class _RedisStubFail:
    def ping(self) -> bool:
        raise ConnectionError("refused")


def test_redact_url_no_credentials_passthrough() -> None:
    assert redact_url("redis://localhost:6379/0") == "redis://localhost:6379/0"


def test_redact_url_masks_password() -> None:
    redacted = redact_url("redis://user:secret@host:6379/0")
    assert "secret" not in redacted
    assert "***" in redacted


def test_redis_probe_ok() -> None:
    probe = RedisProbe(
        client_factory=lambda url, t: _RedisStubOk(),
        url_getter=lambda: "redis://localhost:6379/0",
    )
    result = probe.probe()
    assert result.status == "ok"
    assert result.metadata["aof"] == "on"


def test_redis_probe_degraded_when_aof_off() -> None:
    probe = RedisProbe(
        client_factory=lambda url, t: _RedisStubNoAof(),
        url_getter=lambda: "redis://localhost:6379/0",
    )
    result = probe.probe()
    assert result.status == "degraded"
    assert result.metadata["aof"] == "off"


def test_redis_probe_error_on_ping_failure() -> None:
    probe = RedisProbe(
        client_factory=lambda url, t: _RedisStubFail(),
        url_getter=lambda: "redis://localhost:6379/0",
    )
    result = probe.probe()
    assert result.status == "error"
    assert "refused" in result.detail


def test_redis_probe_redacts_url_in_metadata() -> None:
    probe = RedisProbe(
        client_factory=lambda url, t: _RedisStubOk(),
        url_getter=lambda: "redis://user:hunter2@host:6379/0",
    )
    result = probe.probe()
    assert "hunter2" not in result.metadata["url"]


def test_llm_probe_disabled_when_no_providers() -> None:
    probe = LlmProbe(detector=lambda: [])
    result = probe.probe()
    assert result.status == "disabled"


def test_llm_probe_ok_when_all_reach() -> None:
    probe = LlmProbe(
        detector=lambda: [
            {"name": "ollama", "endpoint": "http://x/models"},
            {"name": "openai", "endpoint": "http://y/models"},
        ],
        pinger=lambda url, t: (True, "HTTP 200"),
    )
    result = probe.probe()
    assert result.status == "ok"
    assert result.metadata["provider_ollama"] == "ok"


def test_llm_probe_degraded_when_some_fail() -> None:
    def pinger(url: str, t: float) -> tuple[bool, str]:
        return (("ollama" in url), "HTTP 200" if "ollama" in url else "down")

    probe = LlmProbe(
        detector=lambda: [
            {"name": "ollama", "endpoint": "http://ollama/models"},
            {"name": "openai", "endpoint": "http://openai/models"},
        ],
        pinger=pinger,
    )
    result = probe.probe()
    assert result.status == "degraded"


def test_llm_probe_error_when_all_fail() -> None:
    probe = LlmProbe(
        detector=lambda: [{"name": "ollama", "endpoint": "http://x/models"}],
        pinger=lambda url, t: (False, "down"),
    )
    result = probe.probe()
    assert result.status == "error"


def test_spec_engine_probe_disabled_when_no_env(monkeypatch) -> None:
    monkeypatch.delenv("SWARM_WORKSPACE_ROOT", raising=False)
    probe = SpecEngineProbe()
    result = probe.probe()
    assert result.status == "disabled"


def test_spec_engine_probe_ok_with_specs(tmp_path: Path) -> None:
    specs = tmp_path / ".swarm" / "specs"
    specs.mkdir(parents=True)
    (specs / "first.md").write_text("# spec", encoding="utf-8")
    (specs / "second.md").write_text("# spec", encoding="utf-8")
    probe = SpecEngineProbe(workspace_getter=lambda: str(tmp_path))
    result = probe.probe()
    assert result.status == "ok"
    assert result.metadata["spec_count"] == "2"


def test_spec_engine_probe_degraded_when_missing_specs_dir(tmp_path: Path) -> None:
    probe = SpecEngineProbe(workspace_getter=lambda: str(tmp_path))
    result = probe.probe()
    assert result.status == "degraded"


def test_spec_engine_probe_error_when_workspace_missing(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    probe = SpecEngineProbe(workspace_getter=lambda: str(missing))
    result = probe.probe()
    assert result.status == "error"


def test_plugin_registry_probe_ok_with_redaction() -> None:
    probe = PluginRegistryProbe(
        installed_getter=lambda: [object(), object()],
        registry_url_getter=lambda: [
            "https://user:pass@registry.example.com/plugins",
            "https://public.example.com/plugins",
        ],
    )
    result = probe.probe()
    assert result.status == "ok"
    assert result.metadata["installed_count"] == "2"
    assert "pass" not in result.metadata["registries"]


def test_plugin_registry_probe_error_on_install_failure() -> None:
    def boom() -> list[object]:
        raise OSError("disk gone")

    probe = PluginRegistryProbe(installed_getter=boom)
    result = probe.probe()
    assert result.status == "error"
    assert "disk gone" in result.detail
