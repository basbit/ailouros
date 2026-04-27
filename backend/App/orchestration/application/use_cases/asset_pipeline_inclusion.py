from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_ASSET_INTENT_KEYWORDS_LOWER: tuple[str, ...] = (
    "картин", "image", "images", "sprite", "sprites", "icon", "icons",
    "аудио", "audio", "звук", "sound", "music", "музык", "soundtrack",
    "asset", "assets", "ресурс", "resources", "graphic", "graphics",
    "art", "artwork", "ассет",
)
_INTERNET_INTENT_KEYWORDS_LOWER: tuple[str, ...] = (
    "интернет", "internet", "web", "поиск", "search", "найди в",
    "найти в", "скачай", "download", "из сети", "online",
)


def _user_prompt_signals_asset_intent(user_prompt: str) -> bool:
    if not user_prompt:
        return False
    prompt_lower = user_prompt.lower()
    has_asset_keyword = any(keyword in prompt_lower for keyword in _ASSET_INTENT_KEYWORDS_LOWER)
    has_internet_keyword = any(keyword in prompt_lower for keyword in _INTERNET_INTENT_KEYWORDS_LOWER)
    return has_asset_keyword and has_internet_keyword


def _is_auto_inclusion_enabled(swarm_cfg: dict[str, Any]) -> bool:
    if "auto_include_asset_steps" in swarm_cfg:
        return bool(swarm_cfg.get("auto_include_asset_steps"))
    env_value = os.getenv("SWARM_AUTO_INCLUDE_ASSET_STEPS", "").strip().lower()
    if env_value in ("0", "false", "no", "off"):
        return False
    return True


def _insert_step_if_missing(steps: list[str], new_step: str, anchor: str) -> bool:
    if new_step in steps:
        return False
    if anchor in steps:
        anchor_index = steps.index(anchor)
        steps.insert(anchor_index, new_step)
    else:
        steps.append(new_step)
    return True


def _append_step_if_missing(steps: list[str], new_step: str, after: str) -> bool:
    if new_step in steps:
        return False
    if after in steps:
        after_index = steps.index(after)
        steps.insert(after_index + 1, new_step)
    else:
        steps.append(new_step)
    return True


def augment_pipeline_steps_for_assets(
    pipeline_steps: list[str],
    user_prompt: str,
    agent_config: dict[str, Any],
) -> tuple[list[str], list[str]]:
    if not pipeline_steps:
        return (list(pipeline_steps), [])
    swarm_cfg_raw = agent_config.get("swarm") if isinstance(agent_config, dict) else None
    swarm_cfg: dict[str, Any] = swarm_cfg_raw if isinstance(swarm_cfg_raw, dict) else {}
    if not _is_auto_inclusion_enabled(swarm_cfg):
        return (list(pipeline_steps), [])
    if not _user_prompt_signals_asset_intent(user_prompt):
        return (list(pipeline_steps), [])

    augmented_steps = list(pipeline_steps)
    inserted: list[str] = []

    if "image_generator" not in augmented_steps:
        if _insert_step_if_missing(augmented_steps, "image_generator", "analyze_code"):
            inserted.append("image_generator")
    if "audio_generator" not in augmented_steps:
        if "image_generator" in augmented_steps:
            if _append_step_if_missing(augmented_steps, "audio_generator", "image_generator"):
                inserted.append("audio_generator")
        elif _insert_step_if_missing(augmented_steps, "audio_generator", "analyze_code"):
            inserted.append("audio_generator")

    if "asset_fetcher" not in augmented_steps:
        anchor_after = "audio_generator" if "audio_generator" in augmented_steps else (
            "image_generator" if "image_generator" in augmented_steps else "ui_designer"
        )
        if _append_step_if_missing(augmented_steps, "asset_fetcher", anchor_after):
            inserted.append("asset_fetcher")

    if inserted:
        logger.info(
            "Asset auto-inclusion: prompt mentions assets+internet — added steps %s "
            "to pipeline (final order: %s)",
            inserted, augmented_steps,
        )
    return (augmented_steps, inserted)
