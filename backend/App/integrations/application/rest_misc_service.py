from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_FRONTEND_ROLE_ORDER: tuple[str, ...] = (
    "pm",
    "ba",
    "architect",
    "code_quality_architect",
    "reviewer",
    "stack_reviewer",
    "ux_researcher",
    "ux_architect",
    "ui_designer",
    "image_generator",
    "audio_generator",
    "dev",
    "qa",
    "problem_spotter",
    "refactor_plan",
    "code_diagram",
    "doc_generate",
    "devops",
    "dev_lead",
    "seo_specialist",
    "ai_citation_strategist",
    "app_store_optimizer",
)

_PROMPT_DEFAULTS: dict[str, str] = {
    "pm": "project-management/project-manager-senior.md",
    "ba": "product/product-requirements-analyst.md",
    "architect": "engineering/engineering-software-architect.md",
    "code_quality_architect": "engineering/code-quality-architect.md",
    "reviewer": "specialized/specialized-reviewer.md",
    "stack_reviewer": "specialized/specialized-reviewer.md",
    "dev": "engineering/engineering-senior-developer.md",
    "qa": "specialized/software-qa-engineer.md",
    "problem_spotter": "specialized/code-problem-spotter.md",
    "refactor_plan": "specialized/code-refactor-planner.md",
    "code_diagram": "specialized/code-structure-diagram.md",
    "doc_generate": "specialized/code-doc-generator.md",
    "devops": "engineering/devops-setup.md",
    "dev_lead": "project-management/dev-lead.md",
    "ux_researcher": "design/design-ux-researcher.md",
    "ux_architect": "design/design-ux-architect.md",
    "ui_designer": "design/design-ui-designer.md",
    "image_generator": "design/design-image-prompt-engineer.md",
    "audio_generator": "game-development/game-audio-engineer.md",
    "seo_specialist": "marketing/marketing-seo-specialist.md",
    "ai_citation_strategist": "marketing/marketing-ai-citation-strategist.md",
    "app_store_optimizer": "marketing/marketing-app-store-optimizer.md",
}

_MODEL_DEFAULTS: dict[str, dict[str, str]] = {
    role: {
        "ollama": "qwen2.5-coder:14b",
        "lmstudio": "qwen2.5-coder:14b",
        "cloud": "claude-3-5-sonnet-latest",
    }
    for role in _FRONTEND_ROLE_ORDER
}
_MODEL_DEFAULTS.update(
    {
        "pm": {
            "ollama": "qwen3-coder:30b",
            "lmstudio": "qwen3-coder:30b",
            "cloud": "claude-3-5-sonnet-latest",
        },
        "dev_lead": {
            "ollama": "qwen3-coder:30b",
            "lmstudio": "qwen3-coder:30b",
            "cloud": "claude-3-5-sonnet-latest",
        },
        "ux_researcher": {
            "ollama": "qwen2.5:14b",
            "lmstudio": "qwen2.5:14b",
            "cloud": "claude-3-5-sonnet-latest",
        },
        "ui_designer": {
            "ollama": "qwen2.5:14b",
            "lmstudio": "qwen2.5:14b",
            "cloud": "claude-3-5-sonnet-latest",
        },
        "image_generator": {
            "ollama": "qwen2.5:14b",
            "lmstudio": "qwen2.5:14b",
            "cloud": "gpt-4o",
        },
        "audio_generator": {
            "ollama": "qwen2.5:14b",
            "lmstudio": "qwen2.5:14b",
            "cloud": "gpt-4o",
        },
        "seo_specialist": {
            "ollama": "qwen2.5:14b",
            "lmstudio": "qwen2.5:14b",
            "cloud": "claude-3-5-sonnet-latest",
        },
        "ai_citation_strategist": {
            "ollama": "qwen2.5:14b",
            "lmstudio": "qwen2.5:14b",
            "cloud": "claude-3-5-sonnet-latest",
        },
        "app_store_optimizer": {
            "ollama": "qwen2.5:14b",
            "lmstudio": "qwen2.5:14b",
            "cloud": "claude-3-5-sonnet-latest",
        },
    }
)

_PROMPT_CHOICES: dict[str, list[tuple[str, str]]] = {
    role: [
        (_PROMPT_DEFAULTS[role], role.replace("_", " ").title()),
        ("__custom__", "Custom..."),
    ]
    for role in _FRONTEND_ROLE_ORDER
}
_PROMPT_CHOICES.update(
    {
        "pm": [
            ("project-management/project-manager-senior.md", "PM senior"),
            ("project-management/project-management-project-shepherd.md", "Shepherd"),
            ("__custom__", "Custom..."),
        ],
        "ba": [
            ("product/product-requirements-analyst.md", "BA requirements"),
            ("product/product-manager.md", "Product manager"),
            ("__custom__", "Custom..."),
        ],
        "architect": [
            ("engineering/engineering-software-architect.md", "Architect"),
            ("__custom__", "Custom..."),
        ],
        "code_quality_architect": [
            ("engineering/code-quality-architect.md", "Code quality architect"),
            ("engineering/engineering-software-architect.md", "Software architect"),
            ("__custom__", "Custom..."),
        ],
        "image_generator": [
            ("design/design-image-prompt-engineer.md", "Image prompt engineer"),
            ("design/design-visual-storyteller.md", "Visual storyteller"),
            ("design/design-inclusive-visuals-specialist.md", "Inclusive visuals"),
            ("__custom__", "Custom..."),
        ],
        "audio_generator": [
            ("game-development/game-audio-engineer.md", "Audio engineer"),
            ("marketing/marketing-podcast-strategist.md", "Podcast strategist"),
            ("__custom__", "Custom..."),
        ],
    }
)


def system_update_status() -> dict[str, Any]:
    from backend.App.integrations.infrastructure.update_check import status_as_dict

    return status_as_dict()


def start_update_check_background() -> None:
    from backend.App.integrations.infrastructure.update_check import run_update_check_in_background

    run_update_check_in_background()


async def health_payload(task_store: Any) -> tuple[dict[str, Any], int]:
    from backend.App.integrations.application.system_metrics import metrics_payload

    try:
        tasks = task_store.list_tasks() if hasattr(task_store, "list_tasks") else []
    except Exception as exc:
        logger.debug("health_payload: task_store.list_tasks failed: %s", exc)
        tasks = []

    active = sum(1 for t in tasks if isinstance(t, dict) and t.get("status") == "running")
    payload = {
        "status": "ok",
        "active_tasks": active,
        **metrics_payload(),
    }
    return payload, 200


def prometheus_metrics_response_or_none() -> Any:
    try:
        from backend.App.integrations.infrastructure.observability.prometheus import (
            prometheus_enabled,
            prometheus_metrics_response,
        )

        if not prometheus_enabled():
            return None
        return prometheus_metrics_response()
    except Exception as exc:
        logger.debug("prometheus_metrics_response_or_none failed: %s", exc)
        return None


def observability_metrics_payload() -> dict[str, Any]:
    try:
        from backend.App.integrations.application.system_metrics import metrics_payload

        return metrics_payload()
    except Exception as exc:
        logger.debug("observability_metrics_payload failed: %s", exc)
        return {}


def defaults_payload() -> dict[str, Any]:
    from backend.App.integrations.infrastructure.agent_registry import load_registry_raw
    from backend.App.orchestration.application.routing.step_registry import (
        DEFAULT_PIPELINE_STEP_IDS,
    )

    registry = load_registry_raw()
    roles = list(_FRONTEND_ROLE_ORDER)
    for role in registry.get("roles", {}):
        if role not in roles:
            roles.append(role)
    return {
        "defaults": registry.get("defaults", {}),
        "roles": roles,
        "model_defaults": {role: _MODEL_DEFAULTS.get(role, {}) for role in roles},
        "prompt_defaults": {role: _PROMPT_DEFAULTS.get(role, "") for role in roles},
        "prompt_choices": {role: _PROMPT_CHOICES.get(role, []) for role in roles},
        "remote_api_base_presets": {
            "anthropic": "",
            "openai_compatible": "https://api.openai.com/v1",
            "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
            "groq": "https://api.groq.com/openai/v1",
            "cerebras": "https://api.cerebras.ai/v1",
            "openrouter": "https://openrouter.ai/api/v1",
            "deepseek": "https://api.deepseek.com/v1",
            "ollama_cloud": "",
        },
        "remote_profile_provider_options": [
            ("anthropic", "Anthropic (Claude)"),
            ("openai_compatible", "OpenAI / compatible URL"),
            ("gemini", "Google Gemini"),
            ("groq", "Groq"),
            ("cerebras", "Cerebras"),
            ("openrouter", "OpenRouter"),
            ("deepseek", "DeepSeek API"),
            ("ollama_cloud", "Ollama Cloud (custom URL)"),
        ],
        "default_pipeline_order": [
            step_id for step_id in DEFAULT_PIPELINE_STEP_IDS if step_id != "e2e"
        ],
        "default_role_environment": "ollama",
        "default_remote_api_provider": "anthropic",
        "default_swarm_provider": "ollama",
    }


async def pipeline_plan_payload(
    goal: str,
    agent_config: Any = None,
    constraints: Any = None,
) -> dict[str, Any]:
    from backend.App.integrations.infrastructure.swarm_planner import plan_pipeline_steps

    steps = await asyncio.to_thread(
        plan_pipeline_steps,
        goal,
        agent_config=agent_config,
        constraints=str(constraints or ""),
    )
    return {"steps": steps}


def mcp_cache_stats_payload(mcp_tool_cache: Any) -> dict[str, Any]:
    if mcp_tool_cache is None:
        return {"cache_enabled": False}
    try:
        return mcp_tool_cache.stats() if hasattr(mcp_tool_cache, "stats") else {"cache_enabled": True}
    except Exception as exc:
        logger.debug("mcp_cache_stats_payload: stats() failed: %s", exc)
        return {"cache_enabled": True}


def workspace_files_payload(workspace_root: str) -> tuple[dict[str, Any], int]:
    if not workspace_root or not workspace_root.strip():
        return {"files": [], "workspace_root": ""}, 200

    from backend.App.workspace.application.use_cases.list_workspace_files import list_workspace_files

    try:
        files = list_workspace_files(workspace_root)
    except (OSError, ValueError) as exc:
        return {"detail": str(exc)}, 400
    return {"files": files, "workspace_root": workspace_root}, 200


def prompts_list_payload() -> dict[str, Any]:
    """Return all prompt .md files under PROMPTS_DIR (overrides + upstream).

    Used by the UI prompt-path dropdown in Custom Roles and Agent Roles.
    Paths are returned in the same rel-path form the prompt loader accepts:
    "<category>/<file>.md" — no "overrides/" or "upstream/" prefix.
    Overrides shadow upstream paths (deduplicated by rel path, overrides win).
    """
    from backend.App.orchestration.infrastructure.agents.base_agent import PROMPTS_DIR

    seen: dict[str, dict[str, str]] = {}

    def _walk(base: Path, source: str) -> None:
        if not base.is_dir():
            return
        for path in base.rglob("*.md"):
            if not path.is_file():
                continue
            try:
                rel = path.relative_to(base).as_posix()
            except ValueError:
                continue
            # Skip non-prompt docs at prompt-dir root (README, LICENSE).
            if "/" not in rel and rel.lower() in {"readme.md", "contributing.md", "license.md"}:
                continue
            # Overrides beat upstream — keep the first-seen when source=overrides.
            if rel in seen and seen[rel]["source"] == "overrides":
                continue
            title = path.stem.replace("-", " ").replace("_", " ").strip()
            if title:
                title = title[:1].upper() + title[1:]
            seen[rel] = {"path": rel, "title": title, "source": source}

    _walk(PROMPTS_DIR / "overrides", "overrides")
    _walk(PROMPTS_DIR / "upstream", "upstream")

    prompts = sorted(seen.values(), key=lambda p: p["path"])
    return {"prompts": prompts, "root": str(PROMPTS_DIR)}


_SKILL_SEARCH_DIRS: tuple[str, ...] = (
    ".claude/skills",
    ".cursor/skills",
    "skills",
)


def skills_list_payload(workspace_root: str) -> tuple[dict[str, Any], int]:
    """Return SKILL.md files discovered in the given workspace.

    Scans common skill directories (`.claude/skills`, `.cursor/skills`,
    `skills`) for `SKILL.md` files. Skill id is taken from the containing
    directory name; title is the first H1 in the file (falls back to id).
    """
    from backend.App.orchestration.infrastructure.agents.base_agent import _strip_skill_frontmatter

    ws = (workspace_root or "").strip()
    if not ws:
        return {"skills": [], "workspace_root": ""}, 200

    try:
        root = Path(ws).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        return {"detail": str(exc)}, 400
    if not root.is_dir():
        return {"skills": [], "workspace_root": str(root)}, 200

    found: dict[str, dict[str, str]] = {}
    for rel_dir in _SKILL_SEARCH_DIRS:
        base = root / rel_dir
        if not base.is_dir():
            continue
        for skill_md in base.rglob("SKILL.md"):
            if not skill_md.is_file():
                continue
            try:
                skill_md.resolve().relative_to(root)
            except ValueError:
                continue
            skill_id = skill_md.parent.name.strip().lower()
            if not skill_id:
                continue
            if skill_id in found:
                continue
            title = skill_id
            try:
                text = _strip_skill_frontmatter(skill_md.read_text(encoding="utf-8"))
                for line in text.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("# "):
                        title = stripped[2:].strip() or skill_id
                        break
            except OSError:
                pass
            try:
                rel_path = skill_md.resolve().relative_to(root).as_posix()
            except ValueError:
                rel_path = skill_md.as_posix()
            found[skill_id] = {"id": skill_id, "title": title, "path": rel_path}

    skills = sorted(found.values(), key=lambda s: s["id"])
    return {"skills": skills, "workspace_root": str(root)}, 200
