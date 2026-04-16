"""Extended tests for backend/App/integrations/infrastructure/llm/client.py."""
from unittest.mock import MagicMock, patch

import pytest

from backend.App.integrations.infrastructure.llm.client import (
    _accumulate_thread_usage,
    _is_local_openai_compat_base_url,
    _local_llm_serialize_http_enabled,
    _local_llm_serialize_lock_acquire_timeout_sec,
    _resolve_openai_max_retries,
    get_and_reset_thread_usage,
    make_openai_client,
    merge_openai_compat_max_tokens,
    openai_http_timeout_seconds,
    reset_thread_usage,
)


# ---------------------------------------------------------------------------
# openai_http_timeout_seconds
# ---------------------------------------------------------------------------

def test_openai_http_timeout_seconds_empty(monkeypatch):
    monkeypatch.setenv("SWARM_OPENAI_HTTP_TIMEOUT_SEC", "")
    assert openai_http_timeout_seconds() is None


def test_openai_http_timeout_seconds_none_literal(monkeypatch):
    monkeypatch.setenv("SWARM_OPENAI_HTTP_TIMEOUT_SEC", "none")
    assert openai_http_timeout_seconds() is None


def test_openai_http_timeout_seconds_numeric(monkeypatch):
    monkeypatch.setenv("SWARM_OPENAI_HTTP_TIMEOUT_SEC", "120")
    assert openai_http_timeout_seconds() == 120.0


def test_openai_http_timeout_seconds_invalid(monkeypatch):
    monkeypatch.setenv("SWARM_OPENAI_HTTP_TIMEOUT_SEC", "bad")
    assert openai_http_timeout_seconds() is None


def test_openai_http_timeout_seconds_zero(monkeypatch):
    monkeypatch.setenv("SWARM_OPENAI_HTTP_TIMEOUT_SEC", "0")
    assert openai_http_timeout_seconds() is None


# ---------------------------------------------------------------------------
# _is_local_openai_compat_base_url
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "http://localhost:11434/v1",
    "http://127.0.0.1:11434/v1",
    "http://host.docker.internal:11434",
    "http://ollama:11434",
    "http://lmstudio:1234/v1",
])
def test_is_local_openai_compat_base_url_true(url):
    assert _is_local_openai_compat_base_url(url) is True


@pytest.mark.parametrize("url", [
    "https://api.openai.com/v1",
    "https://api.anthropic.com",
    "https://openrouter.ai/api/v1",
])
def test_is_local_openai_compat_base_url_false(url):
    assert _is_local_openai_compat_base_url(url) is False


def test_is_local_openai_compat_base_url_empty():
    assert _is_local_openai_compat_base_url("") is False


# ---------------------------------------------------------------------------
# merge_openai_compat_max_tokens
# ---------------------------------------------------------------------------

def test_merge_openai_compat_max_tokens_passes_existing():
    kwargs = {"max_tokens": 512, "model": "m"}
    result = merge_openai_compat_max_tokens(kwargs, base_url="https://api.openai.com")
    assert result["max_tokens"] == 512


def test_merge_openai_compat_max_tokens_max_completion_tokens():
    kwargs = {"max_completion_tokens": 1024}
    result = merge_openai_compat_max_tokens(kwargs, base_url="https://api.openai.com")
    assert "max_tokens" not in result


def test_merge_openai_compat_max_tokens_remote_default(monkeypatch):
    monkeypatch.delenv("SWARM_OPENAI_COMPAT_MAX_TOKENS", raising=False)
    kwargs = {"model": "gpt-4o"}
    result = merge_openai_compat_max_tokens(kwargs, base_url="https://api.openai.com/v1")
    assert result["max_tokens"] == 4096


def test_merge_openai_compat_max_tokens_local_no_default(monkeypatch):
    monkeypatch.delenv("SWARM_OPENAI_COMPAT_MAX_TOKENS", raising=False)
    kwargs = {"model": "llama3"}
    result = merge_openai_compat_max_tokens(kwargs, base_url="http://localhost:11434/v1")
    assert "max_tokens" not in result


def test_merge_openai_compat_max_tokens_env_override(monkeypatch):
    monkeypatch.setenv("SWARM_OPENAI_COMPAT_MAX_TOKENS", "8192")
    kwargs = {"model": "gpt-4o"}
    result = merge_openai_compat_max_tokens(kwargs, base_url="https://api.openai.com/v1")
    assert result["max_tokens"] == 8192


def test_merge_openai_compat_max_tokens_env_zero_is_ignored(monkeypatch):
    monkeypatch.setenv("SWARM_OPENAI_COMPAT_MAX_TOKENS", "0")
    kwargs = {"model": "gpt-4o"}
    result = merge_openai_compat_max_tokens(kwargs, base_url="https://api.openai.com/v1")
    assert result.get("max_tokens") == 4096  # falls through to remote default


# ---------------------------------------------------------------------------
# _local_llm_serialize_http_enabled
# ---------------------------------------------------------------------------

def test_local_llm_serialize_disabled(monkeypatch):
    monkeypatch.setenv("SWARM_LOCAL_LLM_SERIALIZE", "0")
    assert _local_llm_serialize_http_enabled("http://localhost:11434") is False


def test_local_llm_serialize_enabled_local(monkeypatch):
    monkeypatch.setenv("SWARM_LOCAL_LLM_SERIALIZE", "1")
    assert _local_llm_serialize_http_enabled("http://localhost:11434") is True


def test_local_llm_serialize_enabled_remote(monkeypatch):
    monkeypatch.setenv("SWARM_LOCAL_LLM_SERIALIZE", "1")
    assert _local_llm_serialize_http_enabled("https://api.openai.com/v1") is False


# ---------------------------------------------------------------------------
# _local_llm_serialize_lock_acquire_timeout_sec
# ---------------------------------------------------------------------------

def test_local_llm_serialize_lock_acquire_timeout_none(monkeypatch):
    monkeypatch.setenv("SWARM_LLM_SERIALIZE_ACQUIRE_TIMEOUT_SEC", "")
    assert _local_llm_serialize_lock_acquire_timeout_sec() is None


def test_local_llm_serialize_lock_acquire_timeout_value(monkeypatch):
    monkeypatch.setenv("SWARM_LLM_SERIALIZE_ACQUIRE_TIMEOUT_SEC", "30")
    assert _local_llm_serialize_lock_acquire_timeout_sec() == 30.0


def test_local_llm_serialize_lock_acquire_timeout_negative(monkeypatch):
    monkeypatch.setenv("SWARM_LLM_SERIALIZE_ACQUIRE_TIMEOUT_SEC", "-1")
    assert _local_llm_serialize_lock_acquire_timeout_sec() is None


def test_local_llm_serialize_lock_acquire_timeout_invalid(monkeypatch):
    monkeypatch.setenv("SWARM_LLM_SERIALIZE_ACQUIRE_TIMEOUT_SEC", "bad")
    assert _local_llm_serialize_lock_acquire_timeout_sec() is None


# ---------------------------------------------------------------------------
# _resolve_openai_max_retries
# ---------------------------------------------------------------------------

def test_resolve_openai_max_retries_local(monkeypatch):
    monkeypatch.delenv("SWARM_OPENAI_MAX_RETRIES", raising=False)
    assert _resolve_openai_max_retries("http://localhost:11434") == 0


def test_resolve_openai_max_retries_remote(monkeypatch):
    monkeypatch.delenv("SWARM_OPENAI_MAX_RETRIES", raising=False)
    assert _resolve_openai_max_retries("https://api.openai.com") == 2


def test_resolve_openai_max_retries_env_override(monkeypatch):
    monkeypatch.setenv("SWARM_OPENAI_MAX_RETRIES", "5")
    assert _resolve_openai_max_retries("http://localhost:11434") == 5


def test_resolve_openai_max_retries_env_zero(monkeypatch):
    monkeypatch.setenv("SWARM_OPENAI_MAX_RETRIES", "0")
    assert _resolve_openai_max_retries("https://api.openai.com") == 0


# ---------------------------------------------------------------------------
# Thread usage accumulator
# ---------------------------------------------------------------------------

def test_reset_thread_usage():
    reset_thread_usage()
    result = get_and_reset_thread_usage()
    assert result["input_tokens"] == 0
    assert result["output_tokens"] == 0


def test_accumulate_thread_usage():
    reset_thread_usage()
    _accumulate_thread_usage({"input_tokens": 100, "output_tokens": 50})
    _accumulate_thread_usage({"input_tokens": 200, "output_tokens": 75})
    result = get_and_reset_thread_usage()
    assert result["input_tokens"] == 300
    assert result["output_tokens"] == 125


def test_accumulate_thread_usage_missing_keys():
    reset_thread_usage()
    _accumulate_thread_usage({})  # no tokens keys
    result = get_and_reset_thread_usage()
    assert result["input_tokens"] == 0


def test_get_and_reset_clears_after_call():
    reset_thread_usage()
    _accumulate_thread_usage({"input_tokens": 10, "output_tokens": 5})
    get_and_reset_thread_usage()
    result = get_and_reset_thread_usage()
    assert result["input_tokens"] == 0


# ---------------------------------------------------------------------------
# make_openai_client
# ---------------------------------------------------------------------------

def test_make_openai_client_caches_same_pair():
    with patch(
        "backend.App.integrations.infrastructure.llm.client._make_openai_client_uncached",
    ) as mock_make:
        mock_client = MagicMock()
        mock_make.return_value = mock_client
        # First call
        c1 = make_openai_client(base_url="http://localhost:11434/v1", api_key="test")
        # Second call — should return cached
        c2 = make_openai_client(base_url="http://localhost:11434/v1", api_key="test")
    assert c1 is c2


# ---------------------------------------------------------------------------
# ask_model — OpenAI compat path
# ---------------------------------------------------------------------------

def test_ask_model_openai_compat_path(monkeypatch):
    monkeypatch.delenv("SWARM_LLM_CACHE_TTL", raising=False)
    monkeypatch.setenv("SWARM_LLM_CACHE_TTL", "0")

    mock_choice = MagicMock()
    mock_choice.message.content = "response text"
    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 10
    mock_usage.completion_tokens = 5
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage = mock_usage

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_response

    with patch(
        "backend.App.integrations.infrastructure.llm.client._litellm_enabled",
        return_value=False,
    ), patch(
        "backend.App.integrations.infrastructure.llm.client._use_anthropic_backend",
        return_value=False,
    ), patch(
        "backend.App.integrations.infrastructure.llm.client.cache_enabled",
        return_value=False,
    ), patch(
        "backend.App.integrations.infrastructure.llm.client._build_client",
        return_value=mock_client,
    ), patch(
        "backend.App.integrations.infrastructure.llm.client._local_llm_serialize_http_enabled",
        return_value=False,
    ):
        from backend.App.integrations.infrastructure.llm.client import ask_model
        text, usage = ask_model(
            messages=[{"role": "user", "content": "hello"}],
            model="llama3",
            base_url="http://localhost:11434/v1",
            api_key="ollama",
        )

    assert text == "response text"
    assert usage["input_tokens"] == 10
    assert usage["output_tokens"] == 5


def test_ask_model_with_cache_hit(monkeypatch):
    monkeypatch.setenv("SWARM_LLM_CACHE_TTL", "300")

    with patch(
        "backend.App.integrations.infrastructure.llm.client.cache_enabled",
        return_value=True,
    ), patch(
        "backend.App.integrations.infrastructure.llm.client.cache_key",
        return_value="test-key",
    ), patch(
        "backend.App.integrations.infrastructure.llm.client.get_cached",
        return_value=("cached response", {"input_tokens": 5, "output_tokens": 3}),
    ):
        from backend.App.integrations.infrastructure.llm.client import ask_model
        text, usage = ask_model(
            messages=[{"role": "user", "content": "test"}],
            model="llama3",
        )

    assert text == "cached response"
    assert usage["cached"] is True


def test_ask_model_anthropic_path(monkeypatch):
    monkeypatch.setenv("SWARM_LLM_CACHE_TTL", "0")

    with patch(
        "backend.App.integrations.infrastructure.llm.client._litellm_enabled",
        return_value=False,
    ), patch(
        "backend.App.integrations.infrastructure.llm.client._use_anthropic_backend",
        return_value=True,
    ), patch(
        "backend.App.integrations.infrastructure.llm.client.cache_enabled",
        return_value=False,
    ), patch(
        "backend.App.integrations.infrastructure.llm.client._ask_anthropic",
        return_value=("anthropic text", {"input_tokens": 20, "output_tokens": 10}),
    ):
        from backend.App.integrations.infrastructure.llm.client import ask_model
        text, usage = ask_model(
            messages=[{"role": "user", "content": "hello"}],
            model="claude-3-5-sonnet",
            anthropic_api_key="key",
        )

    assert text == "anthropic text"
    assert usage["input_tokens"] == 20


def test_ask_model_empty_choices_raises(monkeypatch):
    monkeypatch.setenv("SWARM_LLM_CACHE_TTL", "0")

    mock_response = MagicMock()
    mock_response.choices = []
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_response

    with patch(
        "backend.App.integrations.infrastructure.llm.client._litellm_enabled",
        return_value=False,
    ), patch(
        "backend.App.integrations.infrastructure.llm.client._use_anthropic_backend",
        return_value=False,
    ), patch(
        "backend.App.integrations.infrastructure.llm.client.cache_enabled",
        return_value=False,
    ), patch(
        "backend.App.integrations.infrastructure.llm.client._build_client",
        return_value=mock_client,
    ), patch(
        "backend.App.integrations.infrastructure.llm.client._local_llm_serialize_http_enabled",
        return_value=False,
    ):
        from backend.App.integrations.infrastructure.llm.client import ask_model
        with pytest.raises(ValueError, match="empty choices"):
            ask_model(
                messages=[{"role": "user", "content": "hello"}],
                model="llama3",
            )


def test_ask_model_applies_reasoning_budget_for_local_reasoning_model(monkeypatch):
    """Regression: bug aec02899 — ``ask_model`` (the BaseAgent path) must apply
    the same ``thinking_budget_tokens`` cap that ``LLMRouter.ask`` applies.

    Without the cap, local reasoning models (``qwen3``, ``deepseek-r1``,
    ``*-ud-mlx``) can enter an unbounded ``<thinking>`` loop and hold the HTTP
    connection open for 3+ hours. Every agent in the pipeline runs through
    ``BaseAgent.run`` → ``ask_model`` → here, so this cap is critical.
    """
    monkeypatch.setenv("SWARM_LLM_CACHE_TTL", "0")
    monkeypatch.delenv("SWARM_LOCAL_LLM_REASONING_BUDGET", raising=False)

    mock_choice = MagicMock()
    mock_choice.message.content = "ok"
    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 5
    mock_usage.completion_tokens = 1

    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage = mock_usage

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_response

    with patch(
        "backend.App.integrations.infrastructure.llm.client._litellm_enabled",
        return_value=False,
    ), patch(
        "backend.App.integrations.infrastructure.llm.client._use_anthropic_backend",
        return_value=False,
    ), patch(
        "backend.App.integrations.infrastructure.llm.client.cache_enabled",
        return_value=False,
    ), patch(
        "backend.App.integrations.infrastructure.llm.client._build_client",
        return_value=mock_client,
    ), patch(
        "backend.App.integrations.infrastructure.llm.client._local_llm_serialize_http_enabled",
        return_value=False,
    ):
        from backend.App.integrations.infrastructure.llm.client import ask_model
        ask_model(
            messages=[{"role": "user", "content": "hi"}],
            model="qwen3.5-9b-ud-mlx",  # matches _REASONING_MODEL_KEYWORDS
            base_url="http://localhost:1234/v1",  # LM Studio, matches local url
            api_key="lm-studio",
        )

    # ``chat.completions.create`` must receive ``extra_body.thinking_budget_tokens``.
    create_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert "extra_body" in create_kwargs, (
        "ask_model must inject thinking_budget_tokens for local reasoning models"
    )
    assert "thinking_budget_tokens" in create_kwargs["extra_body"]
    assert create_kwargs["extra_body"]["thinking_budget_tokens"] > 0


def test_ask_model_skips_reasoning_budget_for_non_reasoning_model(monkeypatch):
    """Only known reasoning-model keywords should get the budget cap.

    Models like ``openai/gpt-oss-20b`` (used for PM/BA/Architect) don't use
    chain-of-thought tokens and must not get an ``extra_body`` injection —
    LM Studio returns 400 on unknown keys for some loaders.
    """
    monkeypatch.setenv("SWARM_LLM_CACHE_TTL", "0")

    mock_choice = MagicMock()
    mock_choice.message.content = "ok"
    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 5
    mock_usage.completion_tokens = 1

    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage = mock_usage

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_response

    with patch(
        "backend.App.integrations.infrastructure.llm.client._litellm_enabled",
        return_value=False,
    ), patch(
        "backend.App.integrations.infrastructure.llm.client._use_anthropic_backend",
        return_value=False,
    ), patch(
        "backend.App.integrations.infrastructure.llm.client.cache_enabled",
        return_value=False,
    ), patch(
        "backend.App.integrations.infrastructure.llm.client._build_client",
        return_value=mock_client,
    ), patch(
        "backend.App.integrations.infrastructure.llm.client._local_llm_serialize_http_enabled",
        return_value=False,
    ):
        from backend.App.integrations.infrastructure.llm.client import ask_model
        ask_model(
            messages=[{"role": "user", "content": "hi"}],
            model="openai/gpt-oss-20b",
            base_url="http://localhost:1234/v1",
            api_key="lm-studio",
        )

    create_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert "extra_body" not in create_kwargs, (
        "Non-reasoning models must NOT get thinking_budget_tokens injected "
        "(some LM Studio loaders 400 on unknown keys)."
    )


# ---------------------------------------------------------------------------
# _make_openai_client_uncached — timeout branches
# ---------------------------------------------------------------------------

def test_make_openai_client_uncached_with_timeout(monkeypatch):
    """When openai_http_timeout_seconds returns a value, it's used."""
    monkeypatch.setenv("SWARM_OPENAI_HTTP_TIMEOUT_SEC", "30.0")

    from backend.App.integrations.infrastructure.llm.client import _make_openai_client_uncached

    mock_openai = MagicMock()
    mock_http_client = MagicMock()

    with patch("httpx.Client", return_value=mock_http_client), patch(
        "backend.App.integrations.infrastructure.llm.client.OpenAI",
        return_value=mock_openai,
    ):
        result = _make_openai_client_uncached(
            base_url="https://api.openai.com/v1", api_key="test-key"
        )

    assert result is mock_openai


def test_make_openai_client_uncached_local_url_no_timeout(monkeypatch):
    """Local URLs get unlimited timeout."""
    monkeypatch.delenv("SWARM_OPENAI_HTTP_TIMEOUT_SEC", raising=False)

    from backend.App.integrations.infrastructure.llm.client import _make_openai_client_uncached

    mock_openai = MagicMock()
    with patch("httpx.Client", return_value=MagicMock()), patch(
        "backend.App.integrations.infrastructure.llm.client.OpenAI",
        return_value=mock_openai,
    ):
        result = _make_openai_client_uncached(
            base_url="http://localhost:11434/v1", api_key="ollama"
        )

    assert result is mock_openai


def test_make_openai_client_uncached_remote_url_no_timeout(monkeypatch):
    """Remote URLs (non-local) get 600s timeout."""
    monkeypatch.delenv("SWARM_OPENAI_HTTP_TIMEOUT_SEC", raising=False)

    from backend.App.integrations.infrastructure.llm.client import _make_openai_client_uncached

    mock_openai = MagicMock()
    with patch("httpx.Client", return_value=MagicMock()), patch(
        "backend.App.integrations.infrastructure.llm.client.OpenAI",
        return_value=mock_openai,
    ):
        result = _make_openai_client_uncached(
            base_url="https://api.openai.com/v1", api_key="sk-test"
        )

    assert result is mock_openai


def test_make_openai_client_uncached_disable_keepalive(monkeypatch):
    """SWARM_HTTPX_DISABLE_KEEPALIVE=1 sets max_keepalive_connections=0."""
    monkeypatch.setenv("SWARM_HTTPX_DISABLE_KEEPALIVE", "1")
    monkeypatch.delenv("SWARM_OPENAI_HTTP_TIMEOUT_SEC", raising=False)

    from backend.App.integrations.infrastructure.llm.client import _make_openai_client_uncached

    limits_used = {}

    def fake_limits(**kwargs):
        limits_used.update(kwargs)
        return MagicMock()

    with patch("httpx.Limits", side_effect=fake_limits), patch(
        "httpx.Client", return_value=MagicMock()
    ), patch(
        "backend.App.integrations.infrastructure.llm.client.OpenAI",
        return_value=MagicMock(),
    ):
        _make_openai_client_uncached(
            base_url="http://localhost:11434/v1", api_key="ollama"
        )

    assert limits_used.get("max_keepalive_connections") == 0


# ---------------------------------------------------------------------------
# make_openai_client — stale key eviction
# ---------------------------------------------------------------------------

def test_make_openai_client_evicts_stale_keys():
    """When api_key changes for same base_url, old client is evicted and closed."""
    from backend.App.integrations.infrastructure.llm.client import (
        make_openai_client,
        _openai_client_cache,
        _openai_client_cache_lock,
    )

    old_client = MagicMock()
    new_client = MagicMock()

    base_url = "https://example.com/v1"
    old_key = "old-api-key"
    new_key = "new-api-key"

    # Pre-populate cache with old key
    with _openai_client_cache_lock:
        _openai_client_cache[(base_url, old_key)] = old_client

    with patch(
        "backend.App.integrations.infrastructure.llm.client._make_openai_client_uncached",
        return_value=new_client,
    ):
        result = make_openai_client(base_url=base_url, api_key=new_key)

    assert result is new_client
    old_client.close.assert_called_once()

    # Cleanup
    with _openai_client_cache_lock:
        _openai_client_cache.pop((base_url, new_key), None)


# ---------------------------------------------------------------------------
# ask_model — litellm path
# ---------------------------------------------------------------------------

def test_ask_model_litellm_path(monkeypatch):
    monkeypatch.setenv("SWARM_LLM_CACHE_TTL", "0")

    with patch(
        "backend.App.integrations.infrastructure.llm.client._litellm_enabled",
        return_value=True,
    ), patch(
        "backend.App.integrations.infrastructure.llm.client.cache_enabled",
        return_value=False,
    ), patch(
        "backend.App.integrations.infrastructure.llm.client._ask_litellm",
        return_value=("litellm result", {"input_tokens": 30, "output_tokens": 15}),
    ):
        from backend.App.integrations.infrastructure.llm.client import ask_model
        text, usage = ask_model(
            messages=[{"role": "user", "content": "hello"}],
            model="claude-haiku",
            api_key="test-key",
        )

    assert text == "litellm result"
    assert usage["input_tokens"] == 30


def test_ask_model_litellm_path_with_cache_store(monkeypatch):
    monkeypatch.setenv("SWARM_LLM_CACHE_TTL", "300")

    with patch(
        "backend.App.integrations.infrastructure.llm.client._litellm_enabled",
        return_value=True,
    ), patch(
        "backend.App.integrations.infrastructure.llm.client.cache_enabled",
        return_value=True,
    ), patch(
        "backend.App.integrations.infrastructure.llm.client.cache_key",
        return_value="test-key-lit",
    ), patch(
        "backend.App.integrations.infrastructure.llm.client.get_cached",
        return_value=None,  # Cache miss
    ), patch(
        "backend.App.integrations.infrastructure.llm.client._ask_litellm",
        return_value=("litellm cached result", {"input_tokens": 10, "output_tokens": 5}),
    ), patch(
        "backend.App.integrations.infrastructure.llm.client.set_cached",
    ) as mock_set:
        from backend.App.integrations.infrastructure.llm.client import ask_model
        text, usage = ask_model(
            messages=[{"role": "user", "content": "hello"}],
            model="claude-haiku",
        )

    assert text == "litellm cached result"
    mock_set.assert_called_once()


# ---------------------------------------------------------------------------
# ask_model — serialize lock path
# ---------------------------------------------------------------------------

def test_ask_model_with_serialize_lock(monkeypatch):
    """When _local_llm_serialize_http_enabled is True, uses lock with no timeout."""
    monkeypatch.setenv("SWARM_LLM_CACHE_TTL", "0")

    mock_choice = MagicMock()
    mock_choice.message.content = "locked response"
    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 5
    mock_usage.completion_tokens = 3
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage = mock_usage

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_response

    with patch(
        "backend.App.integrations.infrastructure.llm.client._litellm_enabled",
        return_value=False,
    ), patch(
        "backend.App.integrations.infrastructure.llm.client._use_anthropic_backend",
        return_value=False,
    ), patch(
        "backend.App.integrations.infrastructure.llm.client.cache_enabled",
        return_value=False,
    ), patch(
        "backend.App.integrations.infrastructure.llm.client._build_client",
        return_value=mock_client,
    ), patch(
        "backend.App.integrations.infrastructure.llm.client._local_llm_serialize_http_enabled",
        return_value=True,
    ), patch(
        "backend.App.integrations.infrastructure.llm.client._local_llm_serialize_lock_acquire_timeout_sec",
        return_value=None,  # No timeout → use context manager
    ):
        from backend.App.integrations.infrastructure.llm.client import ask_model
        text, usage = ask_model(
            messages=[{"role": "user", "content": "hello"}],
            model="llama3",
            base_url="http://localhost:11434/v1",
            api_key="ollama",
        )

    assert text == "locked response"
