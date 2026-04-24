from __future__ import annotations

import logging
from typing import Any

from backend.App.orchestration.domain.ports import ToolResult, ToolSchema, ToolsRuntimePort

logger = logging.getLogger(__name__)


class McpToolsAdapter(ToolsRuntimePort):
    def __init__(
        self,
        mcp_servers: list[dict[str, Any]],
        *,
        workspace_root: str = "",
    ) -> None:
        self._servers = mcp_servers
        self._workspace_root = workspace_root

    def list_tools(self) -> list[ToolSchema]:
        from backend.App.integrations.infrastructure.mcp.stdio.session import MCPPool

        with MCPPool(self._servers) as pool:
            raw = pool.openai_tools()

        schemas: list[ToolSchema] = []
        for t in raw:
            fn = t.get("function", {})
            schemas.append(ToolSchema(
                name=fn.get("name", ""),
                description=fn.get("description", ""),
                input_schema=fn.get("parameters", {}),
            ))
        logger.info("mcp_tools_adapter: list_tools count=%d", len(schemas))
        return schemas

    def call_tool(self, name: str, args: dict[str, Any]) -> ToolResult:
        from backend.App.integrations.infrastructure.mcp.stdio.session import MCPPool

        with MCPPool(self._servers) as pool:
            raw_result = pool.call_tool(name, args)  # type: ignore[attr-defined]

        content = raw_result if isinstance(raw_result, str) else str(raw_result)
        is_error = isinstance(raw_result, dict) and bool(raw_result.get("isError"))
        logger.info(
            "mcp_tools_adapter: call_tool name=%r result_chars=%d is_error=%s",
            name,
            len(content),
            is_error,
        )
        return ToolResult(content=content, is_error=is_error)
