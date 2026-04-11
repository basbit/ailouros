"""prometheus_export: observe + /metrics payload."""

from __future__ import annotations

import pytest


@pytest.fixture
def prom_off(monkeypatch):
    monkeypatch.setenv("SWARM_PROMETHEUS", "0")


def test_prometheus_disabled_no_body(prom_off):
    from backend.App.integrations.infrastructure.observability.prometheus import prometheus_metrics_response

    assert prometheus_metrics_response() is None


def test_observe_then_metrics_contains_step(monkeypatch):
    pytest.importorskip("prometheus_client")
    monkeypatch.delenv("SWARM_PROMETHEUS", raising=False)
    from backend.App.integrations.infrastructure.observability.prometheus import observe_pipeline_step, prometheus_metrics_response

    observe_pipeline_step("pm", 12.5, {})
    observe_pipeline_step("pm", 50.0, {})
    resp = prometheus_metrics_response()
    assert resp is not None
    body = resp.body.decode("utf-8")
    assert "swarm_pipeline_step_duration_seconds" in body
    assert "swarm_pipeline_step_completed_total" in body
    assert 'step_id="pm"' in body
