from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from backend.App.integrations.application.postmortem_retrieval import (
    format_postmortems_for_prompt,
    retrieve_postmortems,
)
from backend.App.integrations.domain.postmortem import (
    Postmortem,
    PostmortemQuery,
    serialise_postmortem,
)
from backend.App.integrations.infrastructure.qdrant_client import VectorHit


def _make_pm(
    spec_id: str = "auth/login",
    agent: str = "stub",
    failure_kind: str = "verifier_error",
    tags: tuple[str, ...] | None = None,
) -> Postmortem:
    return Postmortem(
        id="pm-001",
        spec_id=spec_id,
        agent=agent,
        failure_kind=failure_kind,
        summary="E501 line too long on attempt 1",
        findings_excerpt=("E501 line too long",),
        recovery_attempted="1 retry attempt(s) made",
        outcome="failed",
        recorded_at=datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc),
        tags=tags or (spec_id, agent, failure_kind),
    )


def _hit(pm: Postmortem, score: float = 0.9) -> VectorHit:
    return VectorHit(id=pm.id, score=score, payload=serialise_postmortem(pm))


def test_retrieve_returns_postmortems_matching_spec_id():
    pm = _make_pm(spec_id="auth/login")
    pm_other = _make_pm(spec_id="billing/invoice")

    store = MagicMock()
    store.search.return_value = [_hit(pm), _hit(pm_other)]

    provider = MagicMock()
    provider.embed.return_value = [[0.1, 0.2]]

    query = PostmortemQuery(spec_id="auth/login", k=5)
    results = retrieve_postmortems(query, store, provider, "auth login")

    assert len(results) == 1
    assert results[0].spec_id == "auth/login"


def test_retrieve_filters_by_failure_kind():
    pm_verifier = _make_pm(failure_kind="verifier_error")
    pm_exception = _make_pm(failure_kind="exception")

    store = MagicMock()
    store.search.return_value = [_hit(pm_verifier), _hit(pm_exception)]

    provider = MagicMock()
    provider.embed.return_value = [[0.1, 0.2]]

    query = PostmortemQuery(failure_kind="verifier_error", k=5)
    results = retrieve_postmortems(query, store, provider, "verifier error")

    assert all(r.failure_kind == "verifier_error" for r in results)


def test_retrieve_filters_by_tag():
    pm_with_tag = _make_pm(tags=("auth/login", "stub", "verifier_error", "critical"))
    pm_without_tag = _make_pm(tags=("auth/login", "stub", "verifier_error"))

    store = MagicMock()
    store.search.return_value = [_hit(pm_with_tag), _hit(pm_without_tag)]

    provider = MagicMock()
    provider.embed.return_value = [[0.1, 0.2]]

    query = PostmortemQuery(tag="critical", k=5)
    results = retrieve_postmortems(query, store, provider, "critical")

    assert len(results) == 1
    assert "critical" in results[0].tags


def test_retrieve_empty_result_is_empty_tuple():
    store = MagicMock()
    store.search.return_value = []

    provider = MagicMock()
    provider.embed.return_value = [[0.1, 0.2]]

    query = PostmortemQuery(spec_id="nonexistent", k=5)
    results = retrieve_postmortems(query, store, provider, "nonexistent")

    assert results == ()


def test_retrieve_no_embedding_provider_uses_scroll():
    pm = _make_pm()
    store = MagicMock()
    store.scroll.return_value = [_hit(pm)]

    query = PostmortemQuery(k=5)
    results = retrieve_postmortems(query, store, None, "auth login")

    store.scroll.assert_called_once()
    assert len(results) == 1


def test_retrieve_skips_malformed_payload():
    bad_hit = VectorHit(id="bad", score=0.9, payload={"broken": True})
    pm = _make_pm()
    store = MagicMock()
    store.scroll.return_value = [bad_hit, _hit(pm)]

    query = PostmortemQuery(k=5)
    results = retrieve_postmortems(query, store, None, "")

    assert len(results) == 1
    assert results[0].spec_id == "auth/login"


def test_format_empty_postmortems_returns_empty_string():
    result = format_postmortems_for_prompt(())
    assert result == ""


def test_format_single_postmortem_contains_summary():
    pm = _make_pm()
    result = format_postmortems_for_prompt((pm,))
    assert "[past failures to avoid]" in result
    assert pm.summary in result
    assert pm.recovery_attempted in result


def test_format_multiple_postmortems_has_all_summaries():
    pm1 = _make_pm(spec_id="auth/login")
    pm2 = _make_pm(spec_id="billing/invoice")
    pm2 = Postmortem(
        id="pm-002",
        spec_id="billing/invoice",
        agent="stub",
        failure_kind="retry_exhausted",
        summary="mypy found missing return type",
        findings_excerpt=("return type missing",),
        recovery_attempted="2 retry attempt(s) made",
        outcome="failed",
        recorded_at=datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc),
        tags=("billing/invoice",),
    )
    result = format_postmortems_for_prompt((pm1, pm2))
    assert pm1.summary in result
    assert pm2.summary in result
