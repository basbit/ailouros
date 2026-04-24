from __future__ import annotations

import os

import pytest

_SMOKE_ENABLED = os.environ.get("SWARM_SMOKE", "").strip() == "1"


def pytest_collection_modifyitems(config, items):
    if _SMOKE_ENABLED:
        return
    skip = pytest.mark.skip(
        reason="Smoke tests require SWARM_SMOKE=1 (use `make smoke`)."
    )
    smoke_marker = os.sep + "smoke" + os.sep
    for item in items:
        path = str(getattr(item, "fspath", ""))
        if smoke_marker in path or path.endswith(os.sep + "smoke"):
            item.add_marker(skip)


@pytest.fixture
def real_embedding_provider(monkeypatch):
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
