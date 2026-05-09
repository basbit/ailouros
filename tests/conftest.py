"""Глобальные настройки тестов."""

import os

import pytest

from backend.App.integrations.infrastructure.observability.observability_adapter import (
    NullObservabilityAdapter,
)
from backend.App.orchestration.infrastructure.stream_cancel import clear_stream_shutdown

# Unit tests without mandatory Redis (TaskStore → in-memory). CI/local without docker redis.
os.environ.setdefault("REDIS_REQUIRED", "0")

# Don't auto-start MCP filesystem in unit tests.
os.environ.setdefault("SWARM_MCP_AUTO", "0")

# Default model for tests — agents require SWARM_MODEL to be set.
os.environ.setdefault("SWARM_MODEL", "test-model")

os.environ.setdefault("AILOUROS_LLM_PROVIDER_PROFILE_OVERRIDE", "1")


@pytest.fixture(autouse=True)
def _reset_stream_shutdown():
    """Clear SERVER_STREAM_SHUTDOWN between tests.

    TestClient triggers the FastAPI lifespan teardown on exit, which calls
    mark_stream_shutdown_start(). Without this reset the flag stays set for
    every subsequent test, causing _pipeline_should_cancel() to return True
    and fail tests that run a pipeline.
    """
    clear_stream_shutdown()
    yield
    clear_stream_shutdown()


@pytest.fixture
def null_observability():
    return NullObservabilityAdapter()
