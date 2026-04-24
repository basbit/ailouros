from __future__ import annotations

from backend.App.workspace.application.wiki_service import build_wiki_graph
from backend.App.workspace.domain.wiki_entities import build_wiki_entity_index, extract_wiki_entities


def test_extract_wiki_entities_from_title_tags_and_headings() -> None:
    entities = extract_wiki_entities(
        "Agent Identity",
        ["agents", "memory"],
        "# Scratchpad\n\nBody text",
    )
    names = {entity.name for entity in entities}

    assert "Agent Identity" in names
    assert "agents" in names
    assert "Scratchpad" in names


def test_build_wiki_entity_index_maps_entities_to_nodes() -> None:
    index = build_wiki_entity_index([
        {"id": "agents/identity", "entities": [{"name": "Agent Identity"}]},
        {"id": "architecture/memory", "entities": [{"name": "Agent Identity"}]},
    ])

    assert index["entities"]["agent identity"] == ["agents/identity", "architecture/memory"]


def test_build_wiki_graph_includes_entity_index(tmp_path) -> None:
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    (wiki_root / "identity.md").write_text(
        "---\ntitle: Agent Identity\ntags: [agents]\nlinks: []\n---\n# Scratchpad\n",
        encoding="utf-8",
    )

    graph = build_wiki_graph(wiki_root)

    assert graph["nodes"][0]["entities"]
    assert "agent identity" in graph["entity_index"]["entities"]
