"""Регрессия: ключ LLM-кеша не должен сериализовать весь промпт через json.dumps."""

from __future__ import annotations

import time

from backend.App.integrations.infrastructure.llm.cache import cache_key


def test_cache_key_large_prompt_fast_and_stable():
    huge = "x" * 500_000
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": huge}]
    t0 = time.perf_counter()
    k1 = cache_key(msgs, "qwen2.5-coder:14b", 0.2)
    elapsed = time.perf_counter() - t0
    assert elapsed < 3.0, f"cache_key too slow: {elapsed:.1f}s"
    k2 = cache_key(msgs, "qwen2.5-coder:14b", 0.2)
    assert k1 == k2
    msgs2 = [{"role": "system", "content": "s"}, {"role": "user", "content": huge + "y"}]
    assert cache_key(msgs2, "qwen2.5-coder:14b", 0.2) != k1


def test_cache_key_differs_by_model_and_temperature():
    m = [{"role": "user", "content": "hi"}]
    assert cache_key(m, "a", 0.1) != cache_key(m, "b", 0.1)
    assert cache_key(m, "a", 0.1) != cache_key(m, "a", 0.2)
