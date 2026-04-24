from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LoopTelemetry:
    tool_call_rounds: int = 0
    tool_parser_failures: int = 0
    files_read_count: int = 0
    file_read_cache_hits: int = 0
    file_read_cache_misses: int = 0
    mcp_write_count: int = 0
    mcp_write_actions: list[dict[str, str]] = field(default_factory=list)
    time_to_first_tool: Optional[float] = None
    time_after_last_tool_until_finish: Optional[float] = None
    loop_start_time: float = field(default_factory=time.monotonic)
    time_last_tool: Optional[float] = None
