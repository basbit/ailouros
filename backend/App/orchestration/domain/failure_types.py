"""Failure classification — domain value objects.

Rules (INV-7): no fastapi/redis/httpx/openai/anthropic/langgraph imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FailureType(str, Enum):
    TIMEOUT = "timeout"
    CONTEXT_OVERFLOW = "context_overflow"
    LOGIC_ERROR = "logic_error"
    EXTERNAL_API = "external_api"
    MODEL_REFUSAL = "model_refusal"
    MCP_FAILURE = "mcp_failure"
    UNKNOWN = "unknown"


@dataclass
class ClassifiedFailure:
    failure_type: FailureType
    original_error: str
    suggested_mitigation: str
    retryable: bool
