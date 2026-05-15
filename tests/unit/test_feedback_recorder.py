from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from backend.App.integrations.domain.codegen_feedback import (
    CodegenFeedback,
)
from backend.App.integrations.application.feedback_recorder import (
    format_feedback_for_prompt,
    record_feedback,
    retrieve_feedback,
)


@dataclass
class _Hit:
    payload: dict[str, Any]


class _StubStore:
    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    def upsert(self, collection: str, doc_id: str, vector: list[float], payload: dict[str, Any]) -> None:
        self._data[doc_id] = payload

    def search(self, collection: str, vector: list[float], limit: int) -> list[_Hit]:
        return [_Hit(payload=v) for v in list(self._data.values())[:limit]]

    def scroll(self, collection: str, limit: int) -> list[_Hit]:
        return [_Hit(payload=v) for v in list(self._data.values())[:limit]]


def _make_fb(
    spec_id: str = "auth/login",
    target_file: str = "src/auth/login.py",
    verdict: str = "accept",
    reason: str | None = None,
) -> CodegenFeedback:
    from typing import Literal, cast
    return CodegenFeedback(
        id="test-id",
        spec_id=spec_id,
        agent="coder",
        target_file=target_file,
        verdict=cast(Literal["accept", "reject", "edit"], verdict),
        user_edit_diff=None,
        reason=reason,
        recorded_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        tags=(),
    )


def test_record_then_retrieve_exact_match():
    store = _StubStore()
    fb = _make_fb()
    record_feedback(fb, store, None)
    results = retrieve_feedback("auth/login", "src/auth/login.py", store, None)
    assert len(results) == 1
    assert results[0].id == "test-id"


def test_retrieve_filters_by_spec_id():
    store = _StubStore()
    fb_a_id = CodegenFeedback(
        id="id-a",
        spec_id="spec-a",
        agent="coder",
        target_file="a.py",
        verdict="accept",
        user_edit_diff=None,
        reason=None,
        recorded_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        tags=(),
    )
    fb_b_id = CodegenFeedback(
        id="id-b",
        spec_id="spec-b",
        agent="coder",
        target_file="a.py",
        verdict="reject",
        user_edit_diff=None,
        reason=None,
        recorded_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        tags=(),
    )
    record_feedback(fb_a_id, store, None)
    record_feedback(fb_b_id, store, None)
    results = retrieve_feedback("spec-a", "a.py", store, None)
    assert all(r.spec_id == "spec-a" for r in results)


def test_retrieve_filters_by_target_file():
    store = _StubStore()
    from typing import Literal, cast
    fb1 = CodegenFeedback(
        id="id-1",
        spec_id="s",
        agent="a",
        target_file="src/foo.py",
        verdict=cast(Literal["accept", "reject", "edit"], "accept"),
        user_edit_diff=None,
        reason=None,
        recorded_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        tags=(),
    )
    fb2 = CodegenFeedback(
        id="id-2",
        spec_id="s",
        agent="a",
        target_file="src/bar.py",
        verdict=cast(Literal["accept", "reject", "edit"], "accept"),
        user_edit_diff=None,
        reason=None,
        recorded_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        tags=(),
    )
    record_feedback(fb1, store, None)
    record_feedback(fb2, store, None)
    results = retrieve_feedback("s", "src/foo.py", store, None)
    assert all(r.target_file == "src/foo.py" for r in results)


def test_retrieve_returns_empty_when_none_match():
    store = _StubStore()
    results = retrieve_feedback("missing-spec", "missing.py", store, None)
    assert results == ()


def test_retrieve_skips_corrupt_payloads():
    store = _StubStore()
    store._data["bad"] = {"spec_id": "s", "target_file": "f.py", "verdict": "bad_value", "agent": "a", "id": "bad", "recorded_at": "2026-01-01T00:00:00Z"}
    results = retrieve_feedback("s", "f.py", store, None)
    assert results == ()


def test_format_feedback_empty():
    assert format_feedback_for_prompt(()) == ""


def test_format_feedback_accept():
    fb = _make_fb(verdict="accept", reason="looks great")
    result = format_feedback_for_prompt((fb,))
    assert "[past user feedback]" in result
    assert "ACCEPT" in result
    assert "looks great" in result


def test_format_feedback_reject_no_reason():
    fb = _make_fb(verdict="reject")
    result = format_feedback_for_prompt((fb,))
    assert "REJECT" in result


def test_format_feedback_edit_with_diff():
    from typing import Literal, cast
    fb = CodegenFeedback(
        id="x",
        spec_id="s",
        agent="a",
        target_file="f.py",
        verdict=cast(Literal["accept", "reject", "edit"], "edit"),
        user_edit_diff="@@ -1 +1 @@\n-old\n+new",
        reason=None,
        recorded_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        tags=(),
    )
    result = format_feedback_for_prompt((fb,))
    assert "EDIT" in result
    assert "@@ -1 +1 @@" in result


def test_record_uses_embedding_provider():
    store = _StubStore()

    class _FakeProvider:
        called_with: list[str] = []

        def embed(self, texts: list[str]) -> list[list[float]]:
            self.called_with.extend(texts)
            return [[0.1, 0.2]]

    provider = _FakeProvider()
    fb = _make_fb()
    record_feedback(fb, store, provider)
    assert len(provider.called_with) == 1
