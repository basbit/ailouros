"""MCP (stdio) → HTTP ``POST /v1/chat/completions`` у оркестратора AIlourOS.

Нужен Python **≥ 3.10** и пакет ``mcp`` (см. ``requirements.txt`` / ``environment.yml``).

Запуск из корня репозитория::

    export PYTHONPATH=$(pwd)
    export SWARM_MCP_ORCHESTRATOR_URL=http://127.0.0.1:8000
    python -m backend.App.integrations.infrastructure.mcp.gateway.swarm_gateway

В Cursor / Claude Code: command ``python``, args ``-m``,
``backend.App.integrations.infrastructure.mcp.gateway.swarm_gateway``,
``cwd`` — корень репо, env ``PYTHONPATH`` = этот корень.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

logger = logging.getLogger(__name__)


def _require_py310() -> None:
    if sys.version_info < (3, 10):
        logger.error("mcp.gateway.swarm_gateway requires Python >= 3.10")
        sys.exit(1)


def _parse_sse_accumulate(raw: bytes) -> str:
    out: list[str] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[6:].strip()
        if payload == "[DONE]":
            break
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        choices = obj.get("choices") or []
        if not choices:
            continue
        delta = (choices[0].get("delta") or {})
        c = delta.get("content")
        if isinstance(c, str) and c:
            out.append(c)
    return "".join(out)


def _chat_sync(
    base_url: str,
    prompt: str,
    *,
    stream: bool,
    agent_config: dict[str, Any] | None,
    workspace_root: str,
    workspace_write: bool,
) -> str:
    import httpx

    url = base_url.rstrip("/") + "/v1/chat/completions"
    body: dict[str, Any] = {
        "model": "swarm-orchestrator",
        "messages": [{"role": "user", "content": prompt}],
        "stream": stream,
    }
    if agent_config is not None:
        body["agent_config"] = agent_config
    wr = workspace_root.strip()
    if wr:
        body["workspace_root"] = wr
        body["workspace_write"] = bool(workspace_write)

    timeout = httpx.Timeout(600.0, connect=30.0)
    with httpx.Client(timeout=timeout) as client:
        if stream:
            with client.stream("POST", url, json=body) as resp:
                resp.raise_for_status()
                return _parse_sse_accumulate(resp.read())
        resp = client.post(url, json=body)
        resp.raise_for_status()
        data = resp.json()
        return str(
            (data.get("choices") or [{}])[0]
            .get("message", {})
            .get("content")
            or ""
        )


def main() -> None:
    _require_py310()
    from mcp.server.fastmcp import FastMCP

    default_base = os.getenv("SWARM_MCP_ORCHESTRATOR_URL", "http://127.0.0.1:8000")
    mcp = FastMCP("ailouros-gateway")

    @mcp.tool()
    def swarm_chat_completion(
        prompt: str,
        base_url: str = default_base,
        stream: bool = False,
        agent_config_json: str = "",
        workspace_root: str = "",
        workspace_write: bool = False,
    ) -> str:
        """Прогон роя через тот же API, что UI (stream=false обычно проще для MCP)."""
        ac: dict[str, Any] | None = None
        raw = (agent_config_json or "").strip()
        if raw:
            ac = json.loads(raw)
            if not isinstance(ac, dict):
                raise ValueError("agent_config_json must be a JSON object")
        return _chat_sync(
            base_url,
            prompt,
            stream=stream,
            agent_config=ac,
            workspace_root=workspace_root,
            workspace_write=workspace_write,
        )

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
