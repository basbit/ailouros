"""Shared fixtures for smoke tests.

The whole package is gated by ``SWARM_SMOKE=1`` so they never run during
``make ci``. Sub-suites can additionally require a specific provider.
"""

from __future__ import annotations

import os

import pytest

_SMOKE_ENABLED = os.environ.get("SWARM_SMOKE", "").strip() == "1"


def pytest_collection_modifyitems(config, items):  # type: ignore[override]
    if _SMOKE_ENABLED:
        return
    skip = pytest.mark.skip(
        reason="Smoke tests require SWARM_SMOKE=1 (use `make smoke`)."
    )
    for item in items:
        item.add_marker(skip)


@pytest.fixture
def real_embedding_provider(monkeypatch):
    """Boot an actual embedding provider (local sentence-transformers).

    Skips the test when sentence-transformers cannot be imported. We
    intentionally avoid the OpenAI fallback here so that smoke runs are
    reproducible: the local model is bundled / cached on disk after first
    use, while remote providers introduce flakiness.
    """
    pytest.importorskip(
        "sentence_transformers",
        reason="install sentence-transformers to run embedding smoke tests",
    )
    monkeypatch.setenv("SWARM_EMBEDDING_PROVIDER", "local")
    monkeypatch.setenv(
        "SWARM_EMBEDDING_MODEL",
        os.environ.get(
            "SWARM_SMOKE_EMBEDDING_MODEL",
            "sentence-transformers/all-MiniLM-L6-v2",
        ),
    )
    from backend.App.integrations.infrastructure import embedding_service

    embedding_service.reset_embedding_provider()
    provider = embedding_service.get_embedding_provider()
    yield provider
    embedding_service.reset_embedding_provider()
