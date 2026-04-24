
from __future__ import annotations

import logging

from backend.App.orchestration.domain.failure_types import ClassifiedFailure, FailureType

logger = logging.getLogger(__name__)


def classify_failure(exc: Exception) -> ClassifiedFailure:
    msg = (str(exc) + " " + type(exc).__name__).lower()

    if any(kw in msg for kw in ("timeout", "timed out", "deadline", "asyncio.timeouterror")):
        result = ClassifiedFailure(
            failure_type=FailureType.TIMEOUT,
            original_error=str(exc),
            suggested_mitigation="Retry with increased timeout (SWARM_RETRY_TIMEOUT_MULTIPLIER=1.5)",
            retryable=True,
        )
    elif any(
        kw in msg
        for kw in (
            "context length",
            "context_length_exceeded",
            "token limit",
            "max_tokens",
            "too long",
            "maximum context",
            "context window",
        )
    ):
        result = ClassifiedFailure(
            failure_type=FailureType.CONTEXT_OVERFLOW,
            original_error=str(exc),
            suggested_mitigation="Retry with reduced context (drop low-priority sections or use index_only mode)",
            retryable=True,
        )
    elif any(
        kw in msg
        for kw in ("connection", "502", "503", "rate limit", "429", "servererror", "remotedisconnected")
    ):
        result = ClassifiedFailure(
            failure_type=FailureType.EXTERNAL_API,
            original_error=str(exc),
            suggested_mitigation="Exponential backoff then retry (1s, 2s, 4s); circuit breaker applies",
            retryable=True,
        )
    elif any(
        kw in msg
        for kw in ("refused", "safety", "content policy", "i cannot", "i'm unable", "i am unable")
    ):
        result = ClassifiedFailure(
            failure_type=FailureType.MODEL_REFUSAL,
            original_error=str(exc),
            suggested_mitigation="Switch to alternative fallback model",
            retryable=True,
        )
    elif any(kw in msg for kw in ("mcp", "tools/call", "stdio", "toolcallerror", "tool_call")):
        result = ClassifiedFailure(
            failure_type=FailureType.MCP_FAILURE,
            original_error=str(exc),
            suggested_mitigation="Retry without MCP tools (tools_off=True)",
            retryable=True,
        )
    elif any(
        kw in msg
        for kw in ("keyerror", "attributeerror", "typeerror", "valueerror", "indexerror", "nameerror")
    ):
        result = ClassifiedFailure(
            failure_type=FailureType.LOGIC_ERROR,
            original_error=str(exc),
            suggested_mitigation="Retry with error feedback prepended to prompt",
            retryable=True,
        )
    else:
        result = ClassifiedFailure(
            failure_type=FailureType.UNKNOWN,
            original_error=str(exc),
            suggested_mitigation="Manual investigation required",
            retryable=False,
        )

    logger.info(
        "failure_classifier: type=%s retryable=%s mitigation=%r exc_type=%s",
        result.failure_type.value,
        result.retryable,
        result.suggested_mitigation,
        type(exc).__name__,
    )
    return result
