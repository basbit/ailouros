"""Extended tests for backend/App/integrations/infrastructure/llm/cache.py."""
from unittest.mock import MagicMock, patch

import pytest

from backend.App.integrations.infrastructure.llm import cache as _cache_module
from backend.App.integrations.infrastructure.llm.cache import (
    _redis_socket_timeout_params,
    cache_enabled,
    cache_key,
    cache_ttl,
    get_cached,
    set_cached,
)


@pytest.fixture(autouse=True)
def _reset_cache_module_state():
    _cache_module._redis_unavailable = False
    _cache_module._cached_redis_client = None
    _cache_module._cached_redis_url = ""
    _cache_module._lru_cache.clear()
    _cache_module._lru_keys.clear()
    yield
    _cache_module._redis_unavailable = False
    _cache_module._cached_redis_client = None
    _cache_module._cached_redis_url = ""
    _cache_module._lru_cache.clear()
    _cache_module._lru_keys.clear()


# ---------------------------------------------------------------------------
# cache_ttl / cache_enabled
# ---------------------------------------------------------------------------

def test_cache_ttl_zero(monkeypatch):
    monkeypatch.setenv("SWARM_LLM_CACHE_TTL", "0")
    assert cache_ttl() == 0


def test_cache_ttl_positive(monkeypatch):
    monkeypatch.setenv("SWARM_LLM_CACHE_TTL", "300")
    assert cache_ttl() == 300


def test_cache_enabled_false(monkeypatch):
    monkeypatch.setenv("SWARM_LLM_CACHE_TTL", "0")
    assert cache_enabled() is False


def test_cache_enabled_true(monkeypatch):
    monkeypatch.setenv("SWARM_LLM_CACHE_TTL", "600")
    assert cache_enabled() is True


# ---------------------------------------------------------------------------
# _redis_socket_timeout_params
# ---------------------------------------------------------------------------

def test_redis_socket_timeout_params_default(monkeypatch):
    monkeypatch.delenv("REDIS_SOCKET_CONNECT_TIMEOUT", raising=False)
    monkeypatch.delenv("REDIS_SOCKET_TIMEOUT", raising=False)
    params = _redis_socket_timeout_params()
    assert params["socket_connect_timeout"] == 5.0
    assert params["socket_timeout"] == 30.0


def test_redis_socket_timeout_params_custom(monkeypatch):
    monkeypatch.setenv("REDIS_SOCKET_CONNECT_TIMEOUT", "10")
    monkeypatch.setenv("REDIS_SOCKET_TIMEOUT", "60")
    params = _redis_socket_timeout_params()
    assert params["socket_connect_timeout"] == 10.0
    assert params["socket_timeout"] == 60.0


def test_redis_socket_timeout_params_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("REDIS_SOCKET_CONNECT_TIMEOUT", "bad")
    params = _redis_socket_timeout_params()
    assert params["socket_connect_timeout"] == 5.0


def test_redis_socket_timeout_params_zero_falls_back(monkeypatch):
    monkeypatch.setenv("REDIS_SOCKET_TIMEOUT", "0")
    params = _redis_socket_timeout_params()
    assert params["socket_timeout"] == 30.0


# ---------------------------------------------------------------------------
# cache_key
# ---------------------------------------------------------------------------

def test_cache_key_returns_string():
    messages = [{"role": "user", "content": "hello"}]
    key = cache_key(messages, "llama3", 0.2)
    assert isinstance(key, str)
    assert key.startswith("swarm:llmc:")


def test_cache_key_same_inputs_same_key():
    messages = [{"role": "user", "content": "test"}]
    k1 = cache_key(messages, "llama3", 0.2)
    k2 = cache_key(messages, "llama3", 0.2)
    assert k1 == k2


def test_cache_key_different_model():
    messages = [{"role": "user", "content": "test"}]
    k1 = cache_key(messages, "llama3", 0.2)
    k2 = cache_key(messages, "gpt-4o", 0.2)
    assert k1 != k2


def test_cache_key_different_content():
    k1 = cache_key([{"role": "user", "content": "hello"}], "m", 0.2)
    k2 = cache_key([{"role": "user", "content": "world"}], "m", 0.2)
    assert k1 != k2


def test_cache_key_non_string_content():
    messages = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    key = cache_key(messages, "m", 0.2)
    assert isinstance(key, str)


def test_cache_key_multiple_messages():
    messages = [
        {"role": "system", "content": "you are helpful"},
        {"role": "user", "content": "hello"},
    ]
    key = cache_key(messages, "m", 0.0)
    assert isinstance(key, str)
    assert len(key) > 10


# ---------------------------------------------------------------------------
# get_cached
# ---------------------------------------------------------------------------

def test_get_cached_no_redis():
    with patch(
        "backend.App.integrations.infrastructure.llm.cache._redis_client",
        return_value=None,
    ):
        result = get_cached("some-key")
    assert result is None


def test_get_cached_miss():
    mock_client = MagicMock()
    mock_client.get.return_value = None
    with patch(
        "backend.App.integrations.infrastructure.llm.cache._redis_client",
        return_value=mock_client,
    ):
        result = get_cached("missing-key")
    assert result is None


def test_get_cached_hit():
    import json
    payload = json.dumps({"text": "cached text", "usage": {"input_tokens": 5}}).encode()
    mock_client = MagicMock()
    mock_client.get.return_value = payload
    with patch(
        "backend.App.integrations.infrastructure.llm.cache._redis_client",
        return_value=mock_client,
    ):
        result = get_cached("hit-key")
    assert result is not None
    assert result[0] == "cached text"
    assert result[1]["input_tokens"] == 5


def test_get_cached_exception_returns_none():
    mock_client = MagicMock()
    mock_client.get.side_effect = Exception("connection error")
    with patch(
        "backend.App.integrations.infrastructure.llm.cache._redis_client",
        return_value=mock_client,
    ):
        result = get_cached("error-key")
    assert result is None


# ---------------------------------------------------------------------------
# set_cached
# ---------------------------------------------------------------------------

def test_set_cached_no_redis():
    with patch(
        "backend.App.integrations.infrastructure.llm.cache._redis_client",
        return_value=None,
    ):
        set_cached("key", "text", {})  # should not raise


def test_set_cached_calls_setex(monkeypatch):
    monkeypatch.setenv("SWARM_LLM_CACHE_TTL", "300")
    monkeypatch.setattr("backend.App.integrations.infrastructure.llm.cache._redis_unavailable", False)
    mock_client = MagicMock()
    with patch(
        "backend.App.integrations.infrastructure.llm.cache._redis_client",
        return_value=mock_client,
    ):
        set_cached("my-key", "response text", {"input_tokens": 10})
    mock_client.setex.assert_called_once()
    args = mock_client.setex.call_args[0]
    assert args[0] == "my-key"
    assert args[1] == 300


def test_set_cached_exception_silenced():
    mock_client = MagicMock()
    mock_client.setex.side_effect = Exception("redis down")
    with patch(
        "backend.App.integrations.infrastructure.llm.cache._redis_client",
        return_value=mock_client,
    ):
        set_cached("key", "text", {})  # should not raise
