from unittest.mock import patch

from backend.App.orchestration.application.nodes.asset_fetcher import (
    _expected_mime_for_kind,
    _normalise_asset_entries,
    _parse_asset_manifest,
    run_asset_fetcher,
)


def test_parse_asset_manifest_explicit_tag():
    raw = (
        "<asset_manifest>"
        '{"assets":[{"url":"https://x.com/a.png","target_path":"Assets/a.png","kind":"image","license":"CC0"}]}'
        "</asset_manifest>"
    )
    manifest = _parse_asset_manifest(raw)
    assert len(manifest["assets"]) == 1
    assert manifest["assets"][0]["url"] == "https://x.com/a.png"


def test_parse_asset_manifest_fenced_json():
    raw = """```json
{"assets":[{"url":"https://y.com/b.mp3","target_path":"Assets/b.mp3","kind":"audio","license":"CC-BY"}]}
```
"""
    manifest = _parse_asset_manifest(raw)
    assert len(manifest["assets"]) == 1
    assert manifest["assets"][0]["target_path"] == "Assets/b.mp3"


def test_parse_asset_manifest_no_match_returns_empty():
    raw = "no manifest here"
    manifest = _parse_asset_manifest(raw)
    assert manifest == {"assets": []}


def test_normalise_asset_entries_skips_invalid():
    manifest = {
        "assets": [
            {"url": "https://x.com/a.png", "target_path": "Assets/a.png", "kind": "image"},
            {"url": "", "target_path": "Assets/b.png"},
            {"url": "https://x.com/c.png", "target_path": ""},
            "not a dict",
        ]
    }
    entries = _normalise_asset_entries(manifest)
    assert len(entries) == 1
    assert entries[0]["url"] == "https://x.com/a.png"


def test_normalise_asset_entries_includes_metadata():
    manifest = {
        "assets": [{
            "url": "https://x.com/a.mp3",
            "target_path": "Assets/Audio/a.mp3",
            "kind": "audio",
            "license": "CC0",
            "source": "https://freesound.org/foo",
            "attribution": "John Doe",
        }]
    }
    entries = _normalise_asset_entries(manifest)
    assert entries[0]["license"] == "CC0"
    assert entries[0]["source"] == "https://freesound.org/foo"
    assert entries[0]["attribution"] == "John Doe"


def test_expected_mime_for_kind_mappings():
    assert _expected_mime_for_kind("image") == "image/"
    assert _expected_mime_for_kind("Audio") == "audio/"
    assert _expected_mime_for_kind("sfx") == "audio/"
    assert _expected_mime_for_kind("video") == "video/"
    assert _expected_mime_for_kind("font") == "font/"
    assert _expected_mime_for_kind("unknown") == ""


def test_run_asset_fetcher_skips_when_no_workspace_root():
    state = {"workspace_root": ""}
    result = run_asset_fetcher(state)
    assert result["asset_manifest"] == []
    assert "skipped" in result["asset_fetcher_output"].lower()


def test_run_asset_fetcher_skips_when_no_specs(tmp_path):
    state = {
        "workspace_root": str(tmp_path),
        "image_generator_output": "",
        "audio_generator_output": "",
    }
    result = run_asset_fetcher(state)
    assert result["asset_manifest"] == []
    assert "no image_generator_output" in result["asset_fetcher_output"]


def test_run_asset_fetcher_calls_llm_and_downloads(tmp_path):
    state = {
        "workspace_root": str(tmp_path),
        "image_generator_output": "Need a CC0 sprite for the puzzle pieces.",
        "audio_generator_output": "",
        "agent_config": {"asset_fetcher": {"environment": "lmstudio", "model": "test-model"}},
    }
    raw_llm_output = (
        "Here are some assets I found:\n"
        '<asset_manifest>{"assets":[{'
        '"url":"https://example.com/sprite.png",'
        '"target_path":"Assets/Images/sprite.png",'
        '"kind":"image","license":"CC0"}]}</asset_manifest>'
    )
    with patch(
        "backend.App.orchestration.application.nodes.asset_fetcher._make_asset_fetcher_agent",
    ), patch(
        "backend.App.orchestration.application.nodes.asset_fetcher._llm_planning_agent_run",
        return_value=(raw_llm_output, "test-model", "lmstudio"),
    ), patch(
        "backend.App.integrations.infrastructure.mcp.web_search.download_binary.download_to_workspace",
        return_value={
            "url": "https://example.com/sprite.png",
            "target_path": "Assets/Images/sprite.png",
            "status": "downloaded",
            "bytes_written": 256,
            "content_type": "image/png",
            "error": "",
        },
    ):
        result = run_asset_fetcher(state)

    assert result["asset_fetcher_records"]
    assert result["asset_fetcher_records"][0]["status"] == "downloaded"
    assert "DOWNLOADED" in result["asset_fetcher_output"]
    assert result["asset_manifest"][0]["target_path"] == "Assets/Images/sprite.png"
