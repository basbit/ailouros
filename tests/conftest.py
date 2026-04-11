"""Глобальные настройки тестов."""

import os

import pytest

from backend.App.integrations.infrastructure.observability.observability_adapter import (
    NullObservabilityAdapter,
)

# Unit tests without mandatory Redis (TaskStore → in-memory). CI/local without docker redis.
os.environ.setdefault("REDIS_REQUIRED", "0")

# Don't auto-start MCP filesystem in unit tests.
os.environ.setdefault("SWARM_MCP_AUTO", "0")

# Default model for tests — agents require SWARM_MODEL to be set.
os.environ.setdefault("SWARM_MODEL", "test-model")


@pytest.fixture
def null_observability():
    return NullObservabilityAdapter()
