from backend.App.orchestration.domain.research_signals import (
    requires_source_research,
)


def test_requires_source_research_for_urls() -> None:
    assert requires_source_research("Find parsers for https://example.com/events")


def test_requires_source_research_for_web_terms() -> None:
    assert requires_source_research("Найди сайты с событиями и посмотри, как их парсить")


def test_requires_source_research_respects_explicit_override_false() -> None:
    assert not requires_source_research(
        "Найди сайты с событиями и посмотри, как их парсить",
        {"swarm": {"require_source_research": False}},
    )
