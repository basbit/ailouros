"""Tests for utility functions in nodes/qa.py."""
from __future__ import annotations


from backend.App.orchestration.application.nodes.qa import (
    _qa_dev_output_max_chars,
    _review_dev_output_max_chars,
    _review_spec_max_chars,
)


# ---------------------------------------------------------------------------
# _qa_dev_output_max_chars
# ---------------------------------------------------------------------------

def test_qa_dev_output_max_chars_default(monkeypatch):
    monkeypatch.delenv("SWARM_QA_DEV_OUTPUT_MAX_CHARS", raising=False)
    assert _qa_dev_output_max_chars() == 80_000


def test_qa_dev_output_max_chars_custom(monkeypatch):
    monkeypatch.setenv("SWARM_QA_DEV_OUTPUT_MAX_CHARS", "50000")
    assert _qa_dev_output_max_chars() == 50_000


def test_qa_dev_output_max_chars_zero_uses_default(monkeypatch):
    monkeypatch.setenv("SWARM_QA_DEV_OUTPUT_MAX_CHARS", "0")
    assert _qa_dev_output_max_chars() == 80_000


def test_qa_dev_output_max_chars_non_digit_uses_default(monkeypatch):
    monkeypatch.setenv("SWARM_QA_DEV_OUTPUT_MAX_CHARS", "abc")
    assert _qa_dev_output_max_chars() == 80_000


# ---------------------------------------------------------------------------
# _review_dev_output_max_chars
# ---------------------------------------------------------------------------

def test_review_dev_output_max_chars_default(monkeypatch):
    monkeypatch.delenv("SWARM_REVIEW_DEV_OUTPUT_MAX_CHARS", raising=False)
    assert _review_dev_output_max_chars() == 60_000


def test_review_dev_output_max_chars_custom(monkeypatch):
    monkeypatch.setenv("SWARM_REVIEW_DEV_OUTPUT_MAX_CHARS", "30000")
    assert _review_dev_output_max_chars() == 30_000


def test_review_dev_output_max_chars_zero_uses_default(monkeypatch):
    monkeypatch.setenv("SWARM_REVIEW_DEV_OUTPUT_MAX_CHARS", "0")
    assert _review_dev_output_max_chars() == 60_000


# ---------------------------------------------------------------------------
# _review_spec_max_chars
# ---------------------------------------------------------------------------

def test_review_spec_max_chars_default(monkeypatch):
    monkeypatch.delenv("SWARM_REVIEW_SPEC_MAX_CHARS", raising=False)
    assert _review_spec_max_chars() == 40_000


def test_review_spec_max_chars_custom(monkeypatch):
    monkeypatch.setenv("SWARM_REVIEW_SPEC_MAX_CHARS", "20000")
    assert _review_spec_max_chars() == 20_000


def test_review_spec_max_chars_zero_uses_default(monkeypatch):
    monkeypatch.setenv("SWARM_REVIEW_SPEC_MAX_CHARS", "0")
    assert _review_spec_max_chars() == 40_000
