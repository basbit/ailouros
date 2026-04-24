"""Tests for M-8 prompt fragment caching (prompt_cache.py)."""
from __future__ import annotations

from backend.App.integrations.infrastructure.llm.prompt_cache import (
    prompt_caching_enabled,
    apply_anthropic_cache_control,
    _CACHE_CONTROL_EPHEMERAL,
)


# ---------------------------------------------------------------------------
# prompt_caching_enabled
# ---------------------------------------------------------------------------

def test_enabled_default(monkeypatch):
    monkeypatch.delenv("SWARM_PROMPT_CACHE", raising=False)
    assert prompt_caching_enabled() is True


def test_disabled_by_0(monkeypatch):
    monkeypatch.setenv("SWARM_PROMPT_CACHE", "0")
    assert prompt_caching_enabled() is False


def test_disabled_by_false(monkeypatch):
    monkeypatch.setenv("SWARM_PROMPT_CACHE", "false")
    assert prompt_caching_enabled() is False


def test_disabled_by_off(monkeypatch):
    monkeypatch.setenv("SWARM_PROMPT_CACHE", "off")
    assert prompt_caching_enabled() is False


def test_enabled_explicit_1(monkeypatch):
    monkeypatch.setenv("SWARM_PROMPT_CACHE", "1")
    assert prompt_caching_enabled() is True


# ---------------------------------------------------------------------------
# apply_anthropic_cache_control — disabled path
# ---------------------------------------------------------------------------

def test_returns_plain_string_when_disabled(monkeypatch):
    monkeypatch.setenv("SWARM_PROMPT_CACHE", "0")
    system = "You are a helpful assistant."
    chat_msgs = [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]
    sys_out, msgs_out = apply_anthropic_cache_control(system, chat_msgs)
    assert sys_out == system
    assert msgs_out is chat_msgs or msgs_out == chat_msgs


# ---------------------------------------------------------------------------
# apply_anthropic_cache_control — enabled path
# ---------------------------------------------------------------------------

def _large_user_msg(chars: int = 2000) -> list[dict]:
    return [{"role": "user", "content": [{"type": "text", "text": "x" * chars}]}]


def test_system_param_becomes_list_when_enabled(monkeypatch):
    monkeypatch.setenv("SWARM_PROMPT_CACHE", "1")
    sys_out, _ = apply_anthropic_cache_control("system text", _large_user_msg())
    assert isinstance(sys_out, list)
    assert len(sys_out) == 1
    assert sys_out[0]["type"] == "text"
    assert sys_out[0]["text"] == "system text"


def test_system_param_has_cache_control(monkeypatch):
    monkeypatch.setenv("SWARM_PROMPT_CACHE", "1")
    sys_out, _ = apply_anthropic_cache_control("system text", _large_user_msg())
    assert sys_out[0]["cache_control"] == _CACHE_CONTROL_EPHEMERAL


def test_large_user_message_gets_cache_control(monkeypatch):
    monkeypatch.setenv("SWARM_PROMPT_CACHE", "1")
    monkeypatch.setenv("SWARM_PROMPT_CACHE_MIN_CHARS", "100")
    msgs = _large_user_msg(chars=500)
    _, msgs_out = apply_anthropic_cache_control("sys", msgs)
    first_user = next(m for m in msgs_out if m["role"] == "user")
    assert first_user["content"][0].get("cache_control") == _CACHE_CONTROL_EPHEMERAL


def test_small_user_message_not_marked(monkeypatch):
    monkeypatch.setenv("SWARM_PROMPT_CACHE", "1")
    monkeypatch.setenv("SWARM_PROMPT_CACHE_MIN_CHARS", "1000")
    msgs = [{"role": "user", "content": [{"type": "text", "text": "short"}]}]
    _, msgs_out = apply_anthropic_cache_control("sys", msgs)
    first_user = next(m for m in msgs_out if m["role"] == "user")
    assert "cache_control" not in first_user["content"][0]


def test_does_not_mutate_original_messages(monkeypatch):
    monkeypatch.setenv("SWARM_PROMPT_CACHE", "1")
    monkeypatch.setenv("SWARM_PROMPT_CACHE_MIN_CHARS", "100")
    original_block = {"type": "text", "text": "x" * 500}
    original_msgs = [{"role": "user", "content": [original_block]}]
    apply_anthropic_cache_control("sys", original_msgs)
    # Original block must not have been mutated
    assert "cache_control" not in original_block


def test_only_first_user_message_marked(monkeypatch):
    monkeypatch.setenv("SWARM_PROMPT_CACHE", "1")
    monkeypatch.setenv("SWARM_PROMPT_CACHE_MIN_CHARS", "100")
    text_long = "y" * 500
    msgs = [
        {"role": "user", "content": [{"type": "text", "text": text_long}]},
        {"role": "assistant", "content": [{"type": "text", "text": "response"}]},
        {"role": "user", "content": [{"type": "text", "text": text_long}]},
    ]
    _, msgs_out = apply_anthropic_cache_control("sys", msgs)
    user_msgs = [m for m in msgs_out if m["role"] == "user"]
    # First user message: marked
    assert "cache_control" in user_msgs[0]["content"][0]
    # Second user message: not marked
    assert "cache_control" not in user_msgs[1]["content"][0]


def test_empty_chat_messages(monkeypatch):
    monkeypatch.setenv("SWARM_PROMPT_CACHE", "1")
    sys_out, msgs_out = apply_anthropic_cache_control("system", [])
    assert isinstance(sys_out, list)
    assert msgs_out == []


def test_cache_control_ephemeral_value():
    assert _CACHE_CONTROL_EPHEMERAL == {"type": "ephemeral"}
