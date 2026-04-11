"""Tests for backend/App/integrations/infrastructure/observability/logging_config.py."""
from __future__ import annotations

import json
import logging


from backend.App.integrations.infrastructure.observability.logging_config import (
    StructuredFormatter,
    configure_logging,
    get_request_id,
    get_step,
    get_task_id,
    set_request_id,
    set_step,
    set_task_id,
)


# ---------------------------------------------------------------------------
# Context variable accessors
# ---------------------------------------------------------------------------

def test_set_get_task_id():
    set_task_id("task-abc-123")
    assert get_task_id() == "task-abc-123"
    set_task_id("")  # cleanup


def test_set_get_step():
    set_step("dev")
    assert get_step() == "dev"
    set_step("")  # cleanup


def test_set_get_request_id():
    set_request_id("req-xyz")
    assert get_request_id() == "req-xyz"
    set_request_id("")  # cleanup


def test_default_task_id():
    set_task_id("")
    assert get_task_id() == ""


def test_default_step():
    set_step("")
    assert get_step() == ""


def test_default_request_id():
    set_request_id("")
    assert get_request_id() == ""


# ---------------------------------------------------------------------------
# StructuredFormatter
# ---------------------------------------------------------------------------

def _make_record(message: str, level: int = logging.INFO) -> logging.LogRecord:
    record = logging.LogRecord(
        name="test.logger",
        level=level,
        pathname="test_file.py",
        lineno=42,
        msg=message,
        args=(),
        exc_info=None,
    )
    return record


def test_structured_formatter_basic():
    set_task_id("")
    set_step("")
    set_request_id("")
    formatter = StructuredFormatter()
    record = _make_record("hello world")
    output = formatter.format(record)
    data = json.loads(output)
    assert data["msg"] == "hello world"
    assert data["level"] == "INFO"
    assert "ts" in data
    assert "logger" in data


def test_structured_formatter_with_task_id():
    set_task_id("task-999")
    formatter = StructuredFormatter()
    record = _make_record("with task")
    output = formatter.format(record)
    data = json.loads(output)
    assert data.get("task_id") == "task-999"
    set_task_id("")


def test_structured_formatter_with_step():
    set_step("review_pm")
    formatter = StructuredFormatter()
    record = _make_record("at step")
    output = formatter.format(record)
    data = json.loads(output)
    assert data.get("step") == "review_pm"
    set_step("")


def test_structured_formatter_with_request_id():
    set_request_id("req-111")
    formatter = StructuredFormatter()
    record = _make_record("request")
    output = formatter.format(record)
    data = json.loads(output)
    assert data.get("request_id") == "req-111"
    set_request_id("")


def test_structured_formatter_no_task_id_excluded():
    set_task_id("")
    formatter = StructuredFormatter()
    record = _make_record("no task")
    output = formatter.format(record)
    data = json.loads(output)
    assert "task_id" not in data


def test_structured_formatter_with_exc_info():
    formatter = StructuredFormatter()
    try:
        raise ValueError("test error")
    except ValueError:
        import sys
        exc_info = sys.exc_info()
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="",
            lineno=0,
            msg="error occurred",
            args=(),
            exc_info=exc_info,
        )
    output = formatter.format(record)
    data = json.loads(output)
    assert "exc_info" in data
    assert "ValueError" in data["exc_info"]


def test_structured_formatter_error_level():
    formatter = StructuredFormatter()
    record = _make_record("error msg", level=logging.ERROR)
    output = formatter.format(record)
    data = json.loads(output)
    assert data["level"] == "ERROR"


def test_structured_formatter_warning_level():
    formatter = StructuredFormatter()
    record = _make_record("warn msg", level=logging.WARNING)
    output = formatter.format(record)
    data = json.loads(output)
    assert data["level"] == "WARNING"


def test_structured_formatter_message_interpolation():
    formatter = StructuredFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="hello %s, count=%d",
        args=("world", 42),
        exc_info=None,
    )
    output = formatter.format(record)
    data = json.loads(output)
    assert data["msg"] == "hello world, count=42"


# ---------------------------------------------------------------------------
# configure_logging
# ---------------------------------------------------------------------------

def test_configure_logging_json(monkeypatch):
    monkeypatch.setenv("LOG_JSON", "1")
    configure_logging(level="DEBUG", json_logs=True)
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert len(root.handlers) >= 1
    assert isinstance(root.handlers[0].formatter, StructuredFormatter)


def test_configure_logging_plain(monkeypatch):
    configure_logging(level="INFO", json_logs=False)
    root = logging.getLogger()
    assert len(root.handlers) >= 1
    assert not isinstance(root.handlers[0].formatter, StructuredFormatter)


def test_configure_logging_idempotent():
    configure_logging(level="INFO", json_logs=True)
    configure_logging(level="INFO", json_logs=True)
    root = logging.getLogger()
    # Should not have duplicate handlers
    assert len(root.handlers) == 1


def test_configure_logging_from_env(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setenv("LOG_JSON", "0")
    configure_logging()
    root = logging.getLogger()
    assert root.level == logging.WARNING


def test_configure_logging_invalid_level():
    configure_logging(level="INVALID_LEVEL", json_logs=False)
    root = logging.getLogger()
    # Falls back to INFO
    assert root.level == logging.INFO
