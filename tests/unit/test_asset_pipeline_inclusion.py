from backend.App.orchestration.application.use_cases.asset_pipeline_inclusion import (
    augment_pipeline_steps_for_assets,
    _user_prompt_signals_asset_intent,
)


def test_user_prompt_with_assets_and_internet_triggers():
    assert _user_prompt_signals_asset_intent(
        "найди картинки и аудио в интернете для игры"
    )
    assert _user_prompt_signals_asset_intent(
        "search the internet for free game assets"
    )


def test_user_prompt_assets_without_internet_no_trigger():
    assert not _user_prompt_signals_asset_intent("create images for the game")


def test_user_prompt_internet_without_assets_no_trigger():
    assert not _user_prompt_signals_asset_intent("research best practices online")


def test_empty_prompt_no_trigger():
    assert not _user_prompt_signals_asset_intent("")


def test_augment_adds_asset_fetcher_after_design_steps():
    steps = ["pm", "architect", "ui_designer", "dev", "qa"]
    result, added = augment_pipeline_steps_for_assets(
        steps,
        "find images and audio on the internet",
        agent_config={},
    )
    assert "asset_fetcher" in result
    assert "image_generator" in result
    assert "audio_generator" in result
    assert "asset_fetcher" in added
    assert result.index("asset_fetcher") > result.index("audio_generator")


def test_augment_no_change_when_no_intent():
    steps = ["pm", "dev", "qa"]
    result, added = augment_pipeline_steps_for_assets(
        steps,
        "fix the bug in payment service",
        agent_config={},
    )
    assert result == steps
    assert added == []


def test_augment_disabled_via_swarm_config():
    steps = ["pm", "dev", "qa"]
    result, added = augment_pipeline_steps_for_assets(
        steps,
        "find audio on the internet",
        agent_config={"swarm": {"auto_include_asset_steps": False}},
    )
    assert result == steps
    assert added == []


def test_augment_disabled_via_env(monkeypatch):
    monkeypatch.setenv("SWARM_AUTO_INCLUDE_ASSET_STEPS", "0")
    steps = ["pm", "dev", "qa"]
    result, added = augment_pipeline_steps_for_assets(
        steps,
        "find audio on the internet",
        agent_config={},
    )
    assert added == []


def test_augment_does_not_duplicate_existing_steps():
    steps = ["pm", "image_generator", "audio_generator", "asset_fetcher", "dev"]
    result, added = augment_pipeline_steps_for_assets(
        steps,
        "find audio on the internet",
        agent_config={},
    )
    assert result == steps
    assert added == []


def test_augment_inserts_image_generator_before_analyze_code():
    steps = ["pm", "architect", "analyze_code", "dev"]
    result, added = augment_pipeline_steps_for_assets(
        steps,
        "find images and audio on the internet",
        agent_config={},
    )
    assert "image_generator" in result
    assert result.index("image_generator") < result.index("analyze_code")


def test_augment_with_empty_pipeline_returns_empty():
    result, added = augment_pipeline_steps_for_assets(
        [],
        "find images on the internet",
        agent_config={},
    )
    assert result == []
    assert added == []
