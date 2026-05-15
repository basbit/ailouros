from __future__ import annotations

import base64
import json
import mimetypes
import os
from pathlib import Path
from typing import Any

from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState
from backend.App.orchestration.infrastructure.agents.ui_designer_agent import UIDesignerAgent
from backend.App.testing.domain.ports import (
    VisualEvidenceManifest,
    VisualProbeConfig,
    VisualViewport,
)

from backend.App.orchestration.application.nodes._shared import (
    _cfg_model,
    _documentation_locale_line,
    _effective_spec_for_build,
    _llm_planning_agent_run,
    _pipeline_context_block,
    _project_knowledge_block,
    _remote_api_client_kwargs_for_role,
    _skills_extra_for_role_cfg,
    _stream_progress_emit,
    _swarm_prompt_prefix,
    pipeline_user_task,
)

_FALSE_VALUES = {"0", "false", "no", "off"}


def visual_probe_node(state: PipelineState) -> dict[str, Any]:
    visual_settings = _visual_probe_settings(state)
    if _is_false(visual_settings.get("enabled")):
        return _skip_visual_probe("Visual probe skipped: disabled by configuration.")

    workspace_root = str(state.get("workspace_root") or "").strip()
    if not workspace_root:
        return _skip_visual_probe("Visual probe skipped: workspace_root is not configured.")

    start_directory = str(
        visual_settings.get("start_directory")
        or visual_settings.get("cwd")
        or visual_settings.get("start_cwd")
        or ""
    ).strip()
    workspace_path = Path(workspace_root).expanduser().resolve()
    probe_directory = (
        (workspace_path / start_directory).resolve()
        if start_directory
        else workspace_path
    )

    base_url = _setting_or_environment(
        visual_settings,
        "base_url",
        "SWARM_VISUAL_BASE_URL",
    )
    start_command = _setting_or_environment(
        visual_settings,
        "start_command",
        "SWARM_VISUAL_START_COMMAND",
    )
    if not base_url and not start_command and not _looks_like_browser_project(probe_directory):
        return _skip_visual_probe(
            "Visual probe skipped: no base_url/start_command and no browser project markers found."
        )

    from backend.App.paths import artifacts_root
    from backend.App.testing.application.use_cases.run_visual_probe import RunVisualProbe
    from backend.App.testing.infrastructure.visual_probe import (
        LocalProjectLauncher,
        LocalVisualArtifactStore,
        PlaywrightVisualProbe,
    )

    task_id = str(state.get("task_id") or "unknown")
    config = VisualProbeConfig(
        workspace_root=workspace_root,
        task_id=task_id,
        artifacts_dir="",
        base_url=base_url,
        start_command=start_command,
        start_directory=start_directory,
        ready_path=str(
            visual_settings.get("ready_path")
            or visual_settings.get("readiness_path")
            or "/"
        ),
        pages=_pages_from_settings(visual_settings),
        viewports=_viewports_from_settings(visual_settings),
        port=_integer_setting(visual_settings, "port", 0),
        startup_timeout_sec=_integer_setting(visual_settings, "startup_timeout_sec", 60),
        page_timeout_ms=_integer_setting(visual_settings, "page_timeout_ms", 30_000),
        global_timeout_sec=_integer_setting(visual_settings, "global_timeout_sec", 180),
        max_pages=_integer_setting(visual_settings, "max_pages", 5),
        capture_har=_boolean_setting(visual_settings, "capture_har", False),
        capture_trace=_boolean_setting(visual_settings, "capture_trace", False),
    )

    _stream_progress_emit(state, "Visual probe: launching project and collecting screenshots...")
    use_case = RunVisualProbe(
        launcher=LocalProjectLauncher(),
        browser_probe=PlaywrightVisualProbe(working_dir=workspace_root),
        artifact_store=LocalVisualArtifactStore(str(artifacts_root())),
    )
    manifest = use_case.execute(config)
    output = _format_visual_probe_output(manifest)
    _stream_progress_emit(state, f"Visual probe: {manifest.status} ({len(manifest.pages)} captures)")
    return {
        "visual_probe_output": output,
        "visual_probe_status": manifest.status,
        "visual_artifacts_dir": manifest.artifacts_dir,
        "visual_probe_manifest": manifest.to_dict(),
    }


def visual_design_review_node(state: PipelineState) -> dict[str, Any]:
    manifest = state.get("visual_probe_manifest")
    if not isinstance(manifest, dict) or not manifest.get("pages"):
        return {
            "visual_design_review_output": (
                "Visual design review skipped: no visual_probe_manifest with screenshots."
            ),
            "visual_design_review_model": "",
            "visual_design_review_provider": "",
        }

    agent_config = state.get("agent_config") or {}
    review_settings = agent_config.get("visual_design_review") or agent_config.get("ui_designer") or {}
    if not isinstance(review_settings, dict):
        review_settings = {}

    agent = UIDesignerAgent(
        system_prompt_path_override=review_settings.get("prompt_path") or review_settings.get("prompt"),
        model_override=_cfg_model(review_settings),
        environment_override=review_settings.get("environment"),
        system_prompt_extra=_skills_extra_for_role_cfg(state, review_settings),
        **_remote_api_client_kwargs_for_role(state, review_settings),
    )
    manifest_block = _compact_json(manifest, limit=20_000)
    prompt = (
        _pipeline_context_block(state, "visual_design_review")
        + _swarm_prompt_prefix(state)
        + _documentation_locale_line(state)
        + _project_knowledge_block(state)
        + "[Pipeline rule] Review the implemented UI using the visual runtime evidence.\n"
        "- Compare the screenshots/evidence with the UI Designer, UX Architect, and specification intent.\n"
        "- Treat console errors, blank pages, horizontal overflow, and missing mobile coverage as design blockers.\n"
        "- Call out layout, spacing, hierarchy, responsive, accessibility, and polish issues with screenshot paths.\n"
        "- Do not approve a browser UI if visual_probe failed or produced no screenshots.\n\n"
        f"User task:\n{pipeline_user_task(state)}\n\n"
        f"Specification:\n{_effective_spec_for_build(state)}\n\n"
        f"UI Designer output:\n{state.get('ui_designer_output', '')}\n\n"
        f"UX Architect output:\n{state.get('ux_architect_output', '')}\n\n"
        f"Visual evidence manifest:\n{manifest_block}\n\n"
        "Output contract:\n"
        "1. Human-readable visual design review with screenshot-path evidence.\n"
        "2. Prioritized defects and polish notes.\n"
        "3. Final line `VERDICT: OK` or `VERDICT: NEEDS_WORK`.\n"
    )
    if _multimodal_review_requested(state, review_settings):
        result, used_model, used_provider = _run_visual_review_with_images(
            agent,
            prompt,
            manifest,
            state,
            review_settings,
        )
    else:
        result, used_model, used_provider = _llm_planning_agent_run(agent, prompt, state)
    return {
        "visual_design_review_output": result,
        "visual_design_review_model": used_model,
        "visual_design_review_provider": used_provider,
    }


def _visual_probe_settings(state: PipelineState) -> dict[str, Any]:
    agent_config = state.get("agent_config") or {}
    swarm = agent_config.get("swarm") if isinstance(agent_config, dict) else {}
    sources = []
    if isinstance(swarm, dict):
        sources.extend([swarm.get("visual_probe"), swarm.get("visual")])
    if isinstance(agent_config, dict):
        sources.append(agent_config.get("visual_probe"))
    merged: dict[str, Any] = {}
    for source in sources:
        if isinstance(source, dict):
            merged.update(source)
    return merged


def _setting_or_environment(
    settings: dict[str, Any],
    key: str,
    environment_key: str,
) -> str:
    value = str(settings.get(key) or "").strip()
    return value or os.getenv(environment_key, "").strip()


def _is_false(value: Any) -> bool:
    if value is False:
        return True
    return str(value or "").strip().lower() in _FALSE_VALUES


def _boolean_setting(settings: dict[str, Any], key: str, default: bool) -> bool:
    raw_value = settings.get(key)
    if raw_value is None:
        return default
    if isinstance(raw_value, bool):
        return raw_value
    value = str(raw_value).strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in _FALSE_VALUES:
        return False
    return default


def _integer_setting(settings: dict[str, Any], key: str, default: int) -> int:
    raw_value = settings.get(key)
    if raw_value is None:
        return default
    try:
        parsed = int(raw_value)
        return parsed if parsed >= 0 else default
    except (TypeError, ValueError):
        return default


def _pages_from_settings(settings: dict[str, Any]) -> list[str]:
    raw_value = settings.get("pages") or settings.get("paths") or os.getenv("SWARM_VISUAL_PAGES", "")
    if isinstance(raw_value, list):
        pages = [str(item).strip() for item in raw_value if str(item).strip()]
    else:
        pages = [part.strip() for part in str(raw_value or "").split(",") if part.strip()]
    return pages or ["/"]


def _viewports_from_settings(settings: dict[str, Any]) -> list[VisualViewport]:
    raw_value = settings.get("viewports")
    if not isinstance(raw_value, list):
        return []
    viewports: list[VisualViewport] = []
    for item in raw_value:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        raw_width = item.get("width")
        raw_height = item.get("height")
        if raw_width is None or raw_height is None:
            continue
        try:
            width = int(raw_width)
            height = int(raw_height)
        except (TypeError, ValueError):
            continue
        if name and width > 0 and height > 0:
            viewports.append(VisualViewport(name=name, width=width, height=height))
    return viewports


def _multimodal_review_requested(state: PipelineState, review_settings: dict[str, Any]) -> bool:
    visual_settings = _visual_probe_settings(state)
    return (
        _boolean_setting(review_settings, "multimodal", False)
        or _boolean_setting(review_settings, "image_review", False)
        or _boolean_setting(visual_settings, "multimodal_review", False)
    )


def _run_visual_review_with_images(
    agent: UIDesignerAgent,
    prompt: str,
    manifest: dict[str, Any],
    state: PipelineState,
    review_settings: dict[str, Any],
) -> tuple[str, str, str]:
    visual_settings = _visual_probe_settings(state)
    max_images = _integer_setting(review_settings, "max_review_images", 0) or _integer_setting(
        visual_settings,
        "max_review_images",
        4,
    )
    max_bytes = _integer_setting(review_settings, "max_image_bytes", 0) or _integer_setting(
        visual_settings,
        "max_image_bytes",
        4_000_000,
    )
    image_parts = _screenshot_image_parts(
        manifest,
        max_images=max_images,
        max_bytes=max_bytes,
    )
    if not image_parts:
        prompt = (
            prompt
            + "\n\n[Multimodal screenshot review requested, but no readable screenshot "
            "files were available. Treat missing readable screenshots as a review risk.]\n"
        )
        return _llm_planning_agent_run(agent, prompt, state)

    from backend.App.integrations.infrastructure.llm.client import ask_model
    from backend.App.orchestration.infrastructure.agents.llm_backend_selector import (
        LLMBackendSelector,
    )
    from backend.App.orchestration.infrastructure.agents.base_agent import (
        resolve_default_environment,
    )

    selector = LLMBackendSelector()
    effective_environment = agent.environment or resolve_default_environment()
    selected = selector.select(
        role=agent.role,
        model=agent.model,
        environment=effective_environment,
        remote_provider=agent.remote_provider,
        remote_api_key=agent.remote_api_key,
        remote_base_url=agent.remote_base_url,
        max_tokens=agent.max_tokens,
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": agent.effective_system_prompt()},
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        prompt
                        + "\n\n[Attached screenshots]\n"
                        "Inspect the attached screenshots directly. Cite screenshot "
                        "paths from the manifest when reporting issues.\n"
                    ),
                },
                *image_parts,
            ],
        },
    ]
    output, usage = ask_model(
        messages=messages,
        model=agent.model,
        **selector.ask_kwargs(selected),
    )
    agent.last_usage = usage
    agent.used_model = agent.model
    agent.used_provider = selected.provider_label or f"local:{effective_environment}"
    return output, agent.used_model, agent.used_provider


def _screenshot_image_parts(
    manifest: dict[str, Any],
    *,
    max_images: int,
    max_bytes: int,
) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    pages = manifest.get("pages") if isinstance(manifest, dict) else []
    if not isinstance(pages, list):
        return parts
    for page in pages:
        if len([part for part in parts if part.get("type") == "image_url"]) >= max_images:
            break
        if not isinstance(page, dict):
            continue
        screenshot = page.get("screenshot")
        if not isinstance(screenshot, dict):
            continue
        screenshot_path = str(screenshot.get("path") or "").strip()
        if not screenshot_path:
            continue
        path = Path(screenshot_path)
        try:
            if not path.is_file() or path.stat().st_size > max_bytes:
                continue
            data = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError:
            continue
        mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
        label = (
            f"Screenshot: {page.get('viewport') or screenshot.get('viewport') or '?'} "
            f"{page.get('page_path') or page.get('url') or screenshot_path}\n"
            f"Path: {screenshot_path}"
        )
        parts.append({"type": "text", "text": label})
        parts.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime_type};base64,{data}",
                    "detail": "high",
                },
            }
        )
    return parts


def _looks_like_browser_project(path: Path) -> bool:
    if not path.is_dir():
        return False
    package_json = path / "package.json"
    if package_json.is_file():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return True
        scripts = data.get("scripts") if isinstance(data, dict) else {}
        if isinstance(scripts, dict):
            return any(str(scripts.get(name) or "").strip() for name in ("dev", "preview", "start"))
        return True
    return (path / "index.html").is_file()


def _skip_visual_probe(message: str) -> dict[str, Any]:
    manifest = VisualEvidenceManifest(status="skipped", summary=message)
    output = _format_visual_probe_output(manifest)
    return {
        "visual_probe_output": output,
        "visual_probe_status": "skipped",
        "visual_artifacts_dir": "",
        "visual_probe_manifest": manifest.to_dict(),
    }


def _format_visual_probe_output(manifest: VisualEvidenceManifest) -> str:
    manifest_json = json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2)
    return (
        f"{manifest.summary or 'Visual probe completed.'}\n\n"
        f"Artifacts dir: {manifest.artifacts_dir or '(none)'}\n"
        f"Base URL: {manifest.base_url or '(none)'}\n"
        f"Status: {manifest.status}\n\n"
        f"<visual_evidence_manifest>\n{manifest_json}\n</visual_evidence_manifest>"
    )


def _compact_json(value: dict[str, Any], *, limit: int) -> str:
    text = json.dumps(value, ensure_ascii=False, indent=2)
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[visual evidence truncated]"
