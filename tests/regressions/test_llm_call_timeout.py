"""Regression: LLM call timeout defaults.

Bug: task aec02899-768e-431c-82a8-2db2ea705184 hung because the local
LLM timeout env var was unset and defaulted to "no timeout". A reviewer
call on qwen3.5-9b sat at 99.9% processing indefinitely and the SSE
stream went silent.

Expected:
  * Local LLM base URL → `_local_llm_request_timeout_sec()` returns a
    finite default (review-rules §2: fail fast by default).
  * `SWARM_LLM_CALL_TIMEOUT_SEC` env overrides the default.
  * `SWARM_LOCAL_LLM_TIMEOUT_SEC` takes precedence when both are set.
  * Explicit opt-out via `0` / `none` / `off` returns None.
  * Non-local URL (https://api.openai.com/...) returns None (we don't
    want to cap remote cloud calls from this helper — cloud has its own
    retry infra).
"""
from __future__ import annotations

import pytest

from backend.App.integrations.infrastructure.llm.router import (
    _local_llm_request_timeout_sec,
)


_LOCAL_URL = "http://localhost:1234/v1"
_CLOUD_URL = "https://api.anthropic.com/v1"


def test_local_url_has_default_timeout_when_env_unset(monkeypatch):
    monkeypatch.delenv("SWARM_LOCAL_LLM_TIMEOUT_SEC", raising=False)
    monkeypatch.delenv("SWARM_LLM_CALL_TIMEOUT_SEC", raising=False)
    timeout = _local_llm_request_timeout_sec(_LOCAL_URL)
    assert timeout is not None, "default timeout must be finite (review-rules §2)"
    assert 0 < timeout <= 3600, f"default out of safe range: {timeout}"


def test_cloud_url_returns_none(monkeypatch):
    monkeypatch.setenv("SWARM_LLM_CALL_TIMEOUT_SEC", "120")
    assert _local_llm_request_timeout_sec(_CLOUD_URL) is None


def test_local_specific_env_wins_over_global(monkeypatch):
    monkeypatch.setenv("SWARM_LOCAL_LLM_TIMEOUT_SEC", "42")
    monkeypatch.setenv("SWARM_LLM_CALL_TIMEOUT_SEC", "999")
    assert _local_llm_request_timeout_sec(_LOCAL_URL) == pytest.approx(42.0)


def test_global_env_used_when_local_unset(monkeypatch):
    monkeypatch.delenv("SWARM_LOCAL_LLM_TIMEOUT_SEC", raising=False)
    monkeypatch.setenv("SWARM_LLM_CALL_TIMEOUT_SEC", "77")
    assert _local_llm_request_timeout_sec(_LOCAL_URL) == pytest.approx(77.0)


@pytest.mark.parametrize("optout", ["0", "none", "off", "disabled", "NONE", "Off"])
def test_explicit_optout_returns_none(monkeypatch, optout):
    monkeypatch.setenv("SWARM_LLM_CALL_TIMEOUT_SEC", optout)
    monkeypatch.delenv("SWARM_LOCAL_LLM_TIMEOUT_SEC", raising=False)
    assert _local_llm_request_timeout_sec(_LOCAL_URL) is None


def test_invalid_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("SWARM_LLM_CALL_TIMEOUT_SEC", "not-a-number")
    monkeypatch.delenv("SWARM_LOCAL_LLM_TIMEOUT_SEC", raising=False)
    # Invalid value → skipped, default kicks in
    timeout = _local_llm_request_timeout_sec(_LOCAL_URL)
    assert timeout is not None and timeout > 0


def test_negative_value_ignored(monkeypatch):
    """Negative/zero values in env are not valid positive timeouts — default applies."""
    monkeypatch.setenv("SWARM_LLM_CALL_TIMEOUT_SEC", "-5")
    monkeypatch.delenv("SWARM_LOCAL_LLM_TIMEOUT_SEC", raising=False)
    timeout = _local_llm_request_timeout_sec(_LOCAL_URL)
    # Negative is not a valid timeout → default kicks in (not opt-out)
    assert timeout is not None and timeout > 0
