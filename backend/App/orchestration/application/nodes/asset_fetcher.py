from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState
from backend.App.orchestration.application.nodes._shared import (
    _cfg_model,
    _llm_planning_agent_run,
    _pipeline_context_block,
    _project_knowledge_block,
    _remote_api_client_kwargs_for_role,
    _skills_extra_for_role_cfg,
    _swarm_prompt_prefix,
    _documentation_locale_line,
    _web_research_guidance_block,
    pipeline_user_task,
)
from backend.App.orchestration.infrastructure.agents.base_agent import (
    BaseAgent,
    load_prompt,
    resolve_agent_model,
    resolve_default_environment,
)

logger = logging.getLogger(__name__)


_ASSET_MANIFEST_TAG_RE = re.compile(
    r"<asset_manifest>\s*(\{.*?\})\s*</asset_manifest>",
    re.DOTALL | re.IGNORECASE,
)
_FENCED_ASSET_JSON_RE = re.compile(
    r"```(?:json)?\s*(\{[^`]*?\"assets\"\s*:\s*\[[^`]*?\][^`]*?\})\s*```",
    re.DOTALL,
)


def _max_assets_per_run() -> int:
    env_value = os.getenv("SWARM_ASSET_FETCHER_MAX_ASSETS", "").strip()
    if env_value.isdigit():
        parsed_int = int(env_value)
        if 1 <= parsed_int <= 200:
            return parsed_int
    return 12


def _make_asset_fetcher_agent(state: PipelineState) -> BaseAgent:
    agent_config = state.get("agent_config") or {}
    cfg_raw = agent_config.get("asset_fetcher") if isinstance(agent_config, dict) else None
    cfg: dict[str, Any] = cfg_raw if isinstance(cfg_raw, dict) else {}
    prompt_text_override = (cfg.get("prompt_text") or "").strip()
    prompt_path_override = (cfg.get("prompt_path") or cfg.get("prompt") or "").strip()
    if prompt_text_override:
        system_prompt = prompt_text_override
    elif prompt_path_override:
        system_prompt = load_prompt(
            prompt_path_override,
            fallback=(
                "You are the Asset Fetcher agent. Find free / Creative Commons / public-domain "
                "image and audio assets that match the design and audio specifications, then "
                "produce a JSON manifest the orchestrator can use to download them."
            ),
        )
    else:
        system_prompt = (
            "You are the Asset Fetcher agent. Find free / Creative Commons / public-domain "
            "image and audio assets matching the design and audio specifications. Use the "
            "available web_search and fetch_page tools to discover real, downloadable URLs "
            "from sources like OpenGameArt.org, freesound.org, Wikimedia Commons, Pixabay, "
            "Unsplash. Produce a JSON manifest the orchestrator will use to download them."
        )
    skills_extra = _skills_extra_for_role_cfg(state, cfg)
    if skills_extra:
        system_prompt = system_prompt.rstrip() + "\n\n" + skills_extra
    model_value = resolve_agent_model(_cfg_model(cfg), default_env_var="SWARM_ASSET_FETCHER_MODEL")
    environment_value = cfg.get("environment") or resolve_default_environment()
    remote_kwargs = _remote_api_client_kwargs_for_role(state, cfg)
    return BaseAgent(
        system_prompt=system_prompt,
        model=model_value,
        environment=environment_value,
        **remote_kwargs,
    )


def _parse_asset_manifest(raw_output: str) -> dict[str, Any]:
    if not raw_output:
        return {"assets": []}
    tag_match = _ASSET_MANIFEST_TAG_RE.search(raw_output)
    if tag_match:
        try:
            data = json.loads(tag_match.group(1))
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, ValueError) as tag_parse_error:
            logger.warning(
                "asset_fetcher: <asset_manifest> tag JSON parse failed — %s. "
                "Trying fenced JSON next. raw_preview=%r",
                tag_parse_error, tag_match.group(1)[:200],
            )
    fenced_match = _FENCED_ASSET_JSON_RE.search(raw_output)
    if fenced_match:
        try:
            data = json.loads(fenced_match.group(1))
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, ValueError) as fenced_parse_error:
            logger.warning(
                "asset_fetcher: fenced JSON parse failed — %s. raw_preview=%r",
                fenced_parse_error, fenced_match.group(1)[:200],
            )
    return {"assets": []}


def _normalise_asset_entries(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    assets_raw = manifest.get("assets") if isinstance(manifest, dict) else None
    if not isinstance(assets_raw, list):
        return []
    normalised: list[dict[str, Any]] = []
    for entry in assets_raw:
        if not isinstance(entry, dict):
            continue
        url_value = str(entry.get("url") or "").strip()
        target_path_value = str(entry.get("target_path") or entry.get("path") or "").strip()
        license_value = str(entry.get("license") or entry.get("license_name") or "").strip()
        kind_value = str(entry.get("kind") or entry.get("type") or "").strip().lower()
        source_value = str(entry.get("source") or entry.get("source_page") or "").strip()
        attribution_value = str(entry.get("attribution") or "").strip()
        if not url_value or not target_path_value:
            continue
        normalised.append({
            "url": url_value,
            "target_path": target_path_value,
            "license": license_value,
            "kind": kind_value,
            "source": source_value,
            "attribution": attribution_value,
        })
    return normalised


def _expected_mime_for_kind(kind: str) -> str:
    kind_normalised = (kind or "").strip().lower()
    if kind_normalised in ("image", "img", "picture", "sprite", "icon"):
        return "image/"
    if kind_normalised in ("audio", "sound", "sfx", "music", "voice"):
        return "audio/"
    if kind_normalised in ("video",):
        return "video/"
    if kind_normalised in ("font",):
        return "font/"
    return ""


def _download_assets(
    workspace_root: str,
    asset_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    from backend.App.integrations.infrastructure.mcp.web_search.download_binary import (
        download_to_workspace,
    )
    fetched_records: list[dict[str, Any]] = []
    for entry in asset_entries:
        expected_mime = _expected_mime_for_kind(entry.get("kind") or "")
        download_result = download_to_workspace(
            url=entry["url"],
            workspace_root=workspace_root,
            relative_target_path=entry["target_path"],
            expected_mime_prefix=expected_mime,
        )
        record = {
            **entry,
            "status": download_result.get("status", "skipped"),
            "bytes_written": int(download_result.get("bytes_written") or 0),
            "content_type": download_result.get("content_type", ""),
            "error": download_result.get("error", ""),
        }
        fetched_records.append(record)
    return fetched_records


def _empty_asset_fetcher_result(skip_message: str) -> dict[str, Any]:
    return {
        "asset_fetcher_output": skip_message,
        "asset_fetcher_manifest": {"assets": []},
        "asset_manifest": [],
    }


def _asset_fetcher_instruction_text(max_assets: int) -> str:
    return (
        "[Pipeline rule] Find downloadable free / CC / public-domain media assets that match "
        "the image and audio specifications below. Use the available `web_search` and "
        "`fetch_page` tools to discover real, working URLs. Recommended sources:\n"
        "  - OpenGameArt.org (CC0/CC-BY game assets)\n"
        "  - freesound.org (CC sound effects)\n"
        "  - Wikimedia Commons (CC images and audio)\n"
        "  - Pixabay / Unsplash (free image and music)\n"
        "  - Mixkit (free SFX and music)\n\n"
        "Output contract: emit a single `<asset_manifest>` block containing valid JSON with "
        "this exact schema:\n"
        '<asset_manifest>{"assets":[{'
        '"url":"https://direct-download-url.example.com/file.png",'
        '"target_path":"Assets/Images/sprite.png",'
        '"kind":"image|audio|font|video",'
        '"license":"CC0|CC-BY-4.0|public-domain|...",'
        '"source":"https://page-where-you-found-it.example.com",'
        '"attribution":"Artist Name (if license requires)"'
        "}]}</asset_manifest>\n\n"
        "Rules:\n"
        f"  - Limit to {max_assets} assets per run.\n"
        "  - `url` must be a DIRECT download URL (the actual file), not an HTML page.\n"
        "  - `target_path` is workspace-relative; use Assets/Images/, Assets/Audio/, etc.\n"
        "  - Only include assets you actually verified via web_search/fetch_page.\n"
        "  - Always include the license; reject assets without a clear free/CC license.\n"
        "  - Do not invent URLs — if you cannot find a real one, omit the asset entry."
    )


def _build_asset_fetcher_prompt(
    state: PipelineState,
    *,
    image_specs: str,
    audio_specs: str,
) -> str:
    pipeline_context_block = _pipeline_context_block(state, "asset_fetcher")
    user_task_text = pipeline_user_task(state)
    instruction = _asset_fetcher_instruction_text(_max_assets_per_run())
    return (
        pipeline_context_block
        + _swarm_prompt_prefix(state)
        + _documentation_locale_line(state)
        + _web_research_guidance_block(state, role="asset_fetcher")
        + _project_knowledge_block(state, step_id="asset_fetcher")
        + instruction
        + "\n\n"
        + f"User task:\n{user_task_text}\n\n"
        + f"Image specifications:\n{image_specs}\n\n"
        + f"Audio specifications:\n{audio_specs}\n"
    )


def _format_download_summary(fetched_records: list[dict[str, Any]]) -> str:
    downloaded_count = sum(1 for record in fetched_records if record.get("status") == "downloaded")
    skipped_count = len(fetched_records) - downloaded_count
    summary_lines: list[str] = [
        f"Asset fetcher: {downloaded_count} downloaded, {skipped_count} skipped.",
    ]
    for record in fetched_records:
        status = record.get("status", "?")
        target = record.get("target_path", "?")
        url = record.get("url", "")
        license_text = record.get("license", "")
        error = record.get("error", "")
        suffix = f" license={license_text}" if license_text else ""
        if status == "downloaded":
            bytes_written = record.get("bytes_written", 0)
            summary_lines.append(
                f"  - DOWNLOADED {target} ({bytes_written} bytes){suffix} ← {url}"
            )
        else:
            summary_lines.append(
                f"  - SKIPPED   {target}{suffix} ← {url} ({error})"
            )
    return "\n".join(summary_lines)


def run_asset_fetcher(state: PipelineState) -> dict[str, Any]:
    workspace_root_value = str(state.get("workspace_root") or "").strip()
    if not workspace_root_value:
        logger.info("asset_fetcher_node: skipping (no workspace_root)")
        return _empty_asset_fetcher_result("Asset fetcher skipped: workspace_root not set.")

    image_specs = state.get("image_generator_output") or ""
    audio_specs = state.get("audio_generator_output") or ""
    has_specs = bool(str(image_specs).strip()) or bool(str(audio_specs).strip())
    if not has_specs:
        logger.info(
            "asset_fetcher_node: skipping (no image_generator_output / audio_generator_output)"
        )
        return _empty_asset_fetcher_result(
            "Asset fetcher skipped: no image_generator_output or audio_generator_output found. "
            "Add image_generator and audio_generator steps to the pipeline before asset_fetcher."
        )

    agent = _make_asset_fetcher_agent(state)
    prompt = _build_asset_fetcher_prompt(
        state, image_specs=image_specs, audio_specs=audio_specs,
    )
    raw_output, used_model, used_provider = _llm_planning_agent_run(agent, prompt, state)
    manifest = _parse_asset_manifest(raw_output or "")
    asset_entries = _normalise_asset_entries(manifest)
    if len(asset_entries) > _max_assets_per_run():
        logger.warning(
            "asset_fetcher_node: %d assets requested, capping to %d",
            len(asset_entries), _max_assets_per_run(),
        )
        asset_entries = asset_entries[: _max_assets_per_run()]

    fetched_records = _download_assets(workspace_root_value, asset_entries)
    summary_text = _format_download_summary(fetched_records)
    asset_fetcher_output = (
        (raw_output or "").rstrip()
        + "\n\n## Download summary\n"
        + summary_text
        + "\n"
    )
    return {
        "asset_fetcher_output": asset_fetcher_output,
        "asset_fetcher_manifest": {"assets": asset_entries},
        "asset_fetcher_records": fetched_records,
        "asset_fetcher_model": used_model,
        "asset_fetcher_provider": used_provider,
        "asset_manifest": fetched_records,
    }
