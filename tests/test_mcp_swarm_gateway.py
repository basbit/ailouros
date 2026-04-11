"""Парсинг SSE и хелперы MCP-шлюза (без поднятия stdio-сервера)."""

from __future__ import annotations

import json

from backend.App.integrations.infrastructure.mcp.gateway.swarm_gateway import _parse_sse_accumulate


def test_parse_sse_accumulate_joins_deltas():
    line = json.dumps(
        {"choices": [{"delta": {"content": "hel"}}]},
        ensure_ascii=False,
    )
    line2 = json.dumps(
        {"choices": [{"delta": {"content": "lo"}}]},
        ensure_ascii=False,
    )
    raw = f"data: {line}\n\ndata: {line2}\n\ndata: [DONE]\n\n".encode()
    assert _parse_sse_accumulate(raw) == "hello"


def test_main_entrypoint_is_callable_without_loading_fastmcp():
    """Импорт модуля не тянет FastMCP (он только внутри main()); py3.9 ок для CI-импорта."""
    import backend.App.integrations.infrastructure.mcp.gateway.swarm_gateway as g

    assert callable(g.main)
