"""Tests for K-2: Adaptive Retry with Failure Classification."""
from __future__ import annotations

from backend.App.orchestration.domain.failure_classifier import classify_failure
from backend.App.orchestration.domain.failure_types import FailureType


def test_classify_timeout():
    exc = TimeoutError("Operation timed out after 30s")
    result = classify_failure(exc)
    assert result.failure_type == FailureType.TIMEOUT
    assert result.retryable is True
    assert "timeout" in result.suggested_mitigation.lower()


def test_classify_timeout_deadline():
    exc = Exception("deadline exceeded for request")
    result = classify_failure(exc)
    assert result.failure_type == FailureType.TIMEOUT
    assert result.retryable is True


def test_classify_context_overflow():
    exc = Exception("context length exceeded: 150000 tokens > 128000 limit")
    result = classify_failure(exc)
    assert result.failure_type == FailureType.CONTEXT_OVERFLOW
    assert result.retryable is True
    assert "context" in result.suggested_mitigation.lower()


def test_classify_context_overflow_token_limit():
    exc = ValueError("max_tokens parameter exceeds model limit")
    result = classify_failure(exc)
    assert result.failure_type == FailureType.CONTEXT_OVERFLOW
    assert result.retryable is True


def test_classify_model_refusal():
    exc = Exception("The model refused to complete this request due to safety policy")
    result = classify_failure(exc)
    assert result.failure_type == FailureType.MODEL_REFUSAL
    assert result.retryable is True
    assert "model" in result.suggested_mitigation.lower()


def test_classify_model_refusal_cannot():
    exc = Exception("I cannot assist with this request as it violates content policy")
    result = classify_failure(exc)
    assert result.failure_type == FailureType.MODEL_REFUSAL
    assert result.retryable is True


def test_classify_external_api_rate_limit():
    exc = Exception("429 rate limit exceeded, retry after 60s")
    result = classify_failure(exc)
    assert result.failure_type == FailureType.EXTERNAL_API
    assert result.retryable is True
    assert "backoff" in result.suggested_mitigation.lower()


def test_classify_external_api_502():
    exc = Exception("502 bad gateway from upstream server")
    result = classify_failure(exc)
    assert result.failure_type == FailureType.EXTERNAL_API
    assert result.retryable is True


def test_classify_external_api_connection():
    exc = ConnectionError("connection refused to api.example.com:443")
    result = classify_failure(exc)
    assert result.failure_type == FailureType.EXTERNAL_API
    assert result.retryable is True


def test_classify_mcp_failure():
    exc = Exception("mcp tools/call failed: stdio subprocess exited with code 1")
    result = classify_failure(exc)
    assert result.failure_type == FailureType.MCP_FAILURE
    assert result.retryable is True
    assert "mcp" in result.suggested_mitigation.lower() or "tool" in result.suggested_mitigation.lower()


def test_classify_logic_error_keyerror():
    exc = KeyError("'output'")
    result = classify_failure(exc)
    assert result.failure_type == FailureType.LOGIC_ERROR
    assert result.retryable is True
    assert "feedback" in result.suggested_mitigation.lower()


def test_classify_logic_error_typeerror():
    exc = TypeError("unsupported operand type(s) for +: 'int' and 'str'")
    result = classify_failure(exc)
    assert result.failure_type == FailureType.LOGIC_ERROR
    assert result.retryable is True


def test_classify_unknown():
    exc = Exception("something completely unrecognised happened in the system")
    result = classify_failure(exc)
    assert result.failure_type == FailureType.UNKNOWN
    assert result.retryable is False
    assert "manual" in result.suggested_mitigation.lower()


def test_classify_preserves_original_error():
    msg = "unique error message abc123"
    exc = Exception(msg)
    result = classify_failure(exc)
    assert msg in result.original_error
