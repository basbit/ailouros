from backend.App.integrations.infrastructure.model_discovery import (
    DiscoveredModel,
    _cached,
    discovery_metrics_snapshot,
    reset_discovery_metrics_for_tests,
)


def test_model_discovery_metrics_track_network_and_cache_hits():
    reset_discovery_metrics_for_tests()

    calls = {"count": 0}

    def fetcher():
        calls["count"] += 1
        return [DiscoveredModel(model_id="phi-4", provider="lm_studio")]

    first = _cached("lm_studio", fetcher)
    second = _cached("lm_studio", fetcher)
    metrics = discovery_metrics_snapshot()

    assert len(first) == 1
    assert len(second) == 1
    assert calls["count"] == 1
    assert metrics["lm_studio"]["network_calls"] == 1
    assert metrics["lm_studio"]["cache_hits"] >= 1
    assert metrics["lm_studio"]["models_returned"] >= 2
