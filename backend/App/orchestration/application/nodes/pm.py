"""PM pipeline nodes: clarify_input, pm, review_pm, human_pm."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, cast

from backend.App.orchestration.infrastructure.agents.pm_agent import PMAgent
from backend.App.orchestration.infrastructure.agents.reviewer_agent import ReviewerAgent
from backend.App.integrations.infrastructure.cross_task_memory import format_cross_task_memory_block
from backend.App.integrations.infrastructure.pattern_memory import format_pattern_memory_block
from backend.App.orchestration.application.review_moa import run_reviewer_or_moa
from backend.App.orchestration.application.pipeline_state import PipelineState
from backend.App.orchestration.domain.quality_gate_policy import (
    CLARIFY_SIMPLE_ANSWER,
    CLARIFY_NEEDS_CLARIFICATION,
    CLARIFY_READY,
)
from backend.App.orchestration.application.nodes._shared import (
    _env_model_override,
    _llm_planning_agent_run,
    _make_human_agent,
    _make_reviewer_agent,
    _pipeline_context_block,
    planning_mcp_tool_instruction,
    _remote_api_client_kwargs_for_role,
    _reviewer_cfg,
    _skills_extra_for_role_cfg,
    _stream_progress_emit,
    _swarm_prompt_prefix,
    embedded_pipeline_input_for_review,
    embedded_review_artifact,
    planning_pipeline_user_context,
)

_log = logging.getLogger(__name__)
_CACHE_TTL_SECONDS = int(os.environ.get("SWARM_CLARIFY_CACHE_TTL_SEC", str(24 * 3600)))
_CLARIFY_CACHE_VERSION = "2026-04-09.v2"
_WEB_RESEARCH_HINTS = (
    "internet",
    "web",
    "website",
    "websites",
    "google",
    "search",
    "browse",
    "latest",
    "current",
    "recent",
    "найди",
    "найти",
    "поищи",
    "поиск",
    "интернет",
    "сайт",
    "сайты",
    "актуальн",
    "свеж",
)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _clarify_cache_identity(state: PipelineState, task_text: str) -> dict[str, str]:
    workspace_root = str(state.get("workspace_root") or "").strip()
    project_manifest = str(state.get("project_manifest") or "")
    workspace_snapshot = str(state.get("workspace_snapshot") or "")
    return {
        "version": _CLARIFY_CACHE_VERSION,
        "task_hash": _sha256_text(task_text),
        "workspace_root": workspace_root,
        "project_manifest_hash": _sha256_text(project_manifest),
        "workspace_snapshot_hash": _sha256_text(workspace_snapshot),
    }


def _clarify_cache_key(identity: dict[str, str]) -> str:
    """Derive clarify_input cache key from task + workspace identity."""
    stable_payload = json.dumps(identity, ensure_ascii=False, sort_keys=True)
    return hashlib.md5(stable_payload.encode("utf-8", errors="replace")).hexdigest()[:16]


def _clarify_cache_dir() -> Path:
    artifacts_dir = os.getenv("SWARM_ARTIFACTS_DIR", "var/artifacts")
    return Path(artifacts_dir) / "cache"


def _clarify_requires_fresh_research(state: PipelineState, task_text: str) -> bool:
    task_lower = str(task_text or "").lower()
    if any(marker in task_lower for marker in _WEB_RESEARCH_HINTS):
        return True
    agent_config = state.get("agent_config") or {}
    mcp_cfg = agent_config.get("mcp")
    if not isinstance(mcp_cfg, dict):
        return False
    servers = mcp_cfg.get("servers")
    if not isinstance(servers, list):
        return False
    for server in servers:
        if not isinstance(server, dict):
            continue
        name = str(server.get("name") or "").strip().lower()
        command = str(server.get("command") or "").strip().lower()
        if "search" in name or "browser" in name:
            return True
        if "browser" in command:
            return True
    return False


def _load_clarify_cache(
    cache_key: str,
    identity: dict[str, str],
    force_rerun: bool,
) -> dict[str, Any] | None:
    """Return cached clarify_input payload if fresh and identity matches exactly."""
    if force_rerun:
        return None
    cache_file = _clarify_cache_dir() / f"{cache_key}.json"
    if not cache_file.is_file():
        return None
    try:
        age = time.time() - cache_file.stat().st_mtime
        if age > _CACHE_TTL_SECONDS:
            return None
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        cached_identity = data.get("identity")
        if not isinstance(cached_identity, dict):
            return None
        for key, expected in identity.items():
            if str(cached_identity.get(key) or "") != str(expected):
                return None
        output = str(data.get("output", ""))
        if not output.strip():
            return None
        return {
            "output": output,
            "identity": cached_identity,
            "cache_key": cache_key,
        }
    except (OSError, json.JSONDecodeError, KeyError):
        return None


def _save_clarify_cache(cache_key: str, identity: dict[str, str], output: str) -> None:
    """Persist clarify_input output to the file-based cache."""
    cache_dir = _clarify_cache_dir()
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / f"{cache_key}.json"
        cache_file.write_text(
            json.dumps(
                {
                    "output": output,
                    "identity": identity,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        _log.warning("clarify_input cache write failed: %s", exc)


_CLARIFY_INPUT_PROMPT_PATH = str(
    Path(os.environ.get("SWARM_PROJECT_ROOT", "")).resolve() / "var" / "prompts" / "specialized" / "clarify-input.md"
    if os.environ.get("SWARM_PROJECT_ROOT")
    else Path(__file__).resolve().parents[5] / "var" / "prompts" / "specialized" / "clarify-input.md"
)

_PM_STACK_RULE = (
    ""
)

# Valid output prefixes for clarify_input step (single source of truth).
_CLARIFY_VALID_PREFIXES = (CLARIFY_READY, CLARIFY_NEEDS_CLARIFICATION, CLARIFY_SIMPLE_ANSWER)


def _clarify_has_valid_prefix(output: str) -> bool:
    """Return True if clarify_input output starts with one of the required routing prefixes."""
    stripped = (output or "").strip()
    return any(stripped.startswith(prefix) for prefix in _CLARIFY_VALID_PREFIXES)


def _compact_memory_lines(raw: str, *, max_items: int = 6, max_chars: int = 180) -> list[str]:
    items: list[str] = []
    for line in (raw or "").splitlines():
        text = line.strip()
        text = text.lstrip("-*# ").strip()
        if text[:2].isdigit() and "." in text[:4]:
            text = text.split(".", 1)[1].strip()
        if not text or text in items:
            continue
        if len(text) > max_chars:
            text = text[: max_chars - 1].rstrip() + "…"
        if text:
            items.append(text)
        if len(items) >= max_items:
            break
    return items


def _pm_memory_artifact(plan_ctx: str, pm_output: str) -> dict[str, list[str]]:
    decisions = _compact_memory_lines(pm_output, max_items=6, max_chars=180)
    constraints = _compact_memory_lines(plan_ctx, max_items=3, max_chars=180)
    return {
        "facts": [],
        "hypotheses": [],
        "decisions": decisions,
        "dead_ends": [],
        "constraints": constraints,
    }


def clarify_input_node(state: PipelineState) -> dict[str, Any]:
    """Pre-pipeline step: LLM identifies ambiguities and asks clarifying questions.

    Caches results by task + workspace identity for up to 24 hours.
    Set ``agent_config.swarm.force_rerun = True`` to bypass the cache.
    """
    plan_ctx = planning_pipeline_user_context(state)
    force_rerun = bool(
        (state.get("agent_config") or {}).get("swarm", {}).get("force_rerun", False)
    )
    task_text = str(state.get("input") or "")
    requires_fresh_research = _clarify_requires_fresh_research(state, task_text)
    cache_identity = _clarify_cache_identity(state, task_text)
    cache_key: str | None = _clarify_cache_key(cache_identity) if task_text.strip() else None

    cached = None
    if cache_key and not requires_fresh_research:
        cached = _load_clarify_cache(cache_key, cache_identity, force_rerun)
    if cached:
        cached_output = str(cached.get("output") or "")
        clarify_output = cached_output + "\n\n*(result from cache of previous run)*"
        state["clarify_input_output"] = clarify_output
        state["clarify_input_model"] = "cache"
        state["clarify_input_provider"] = "cache"
        state["clarify_input_cache"] = {
            "hit": True,
            "cache_key": cache_key,
            "identity": dict(cached.get("identity") or {}),
        }
        if clarify_output.strip().startswith(CLARIFY_SIMPLE_ANSWER):
            return {
                "clarify_input_output": clarify_output,
                "clarify_input_model": "cache",
                "clarify_input_provider": "cache",
                "clarify_input_cache": state["clarify_input_cache"],
                "_pipeline_stop_early": True,
            }
        # P0-1a: Cached NEEDS_CLARIFICATION must also pause the pipeline,
        # not silently continue to PM without user answers.
        # But only if the cached output actually contains real questions.
        if cached_output.strip().startswith(CLARIFY_NEEDS_CLARIFICATION):
            _cached_body = cached_output.strip()[len(CLARIFY_NEEDS_CLARIFICATION):].strip()
            if len(_cached_body) >= 50 and "?" in _cached_body:
                from backend.App.orchestration.domain.exceptions import HumanApprovalRequired
                raise HumanApprovalRequired(
                    step="clarify_input",
                    detail=clarify_output,
                    resume_pipeline_step="human_clarify_input",
                    partial_state={"clarify_input_output": clarify_output},
                )
            _log.warning(
                "clarify_input: cached NEEDS_CLARIFICATION has no real questions — "
                "invalidating cache and proceeding as READY."
            )
            clarify_output = CLARIFY_READY + "\n" + cached_output
            state["clarify_input_output"] = clarify_output
        return {
            "clarify_input_output": clarify_output,
            "clarify_input_model": "cache",
            "clarify_input_provider": "cache",
            "clarify_input_cache": state["clarify_input_cache"],
        }
    state["clarify_input_cache"] = {
        "hit": False,
        "cache_key": cache_key or "",
        "identity": cache_identity,
    }
    if requires_fresh_research:
        state["clarify_input_cache"]["reuse_blocked_reason"] = "fresh_external_research_required"

    mcp_hint = planning_mcp_tool_instruction(state)
    prompt = (
        mcp_hint
        + "Business requirement from the user:\n\n"
        f"{plan_ctx}\n\n"
        "Analyze the requirement and produce your output in the format described in your system prompt."
    )
    reviewer_cfg = _reviewer_cfg(state)
    _clarify_max_tokens = int(os.getenv("SWARM_CLARIFY_MAX_OUTPUT_TOKENS", "1000").strip() or "1000")
    # SWARM_CLARIFY_MODEL: model override for clarify_input (env var fallback when not set in config).
    # Allows routing clarify_input to a cheaper/faster model without changing the full reviewer model.
    agent = ReviewerAgent(
        system_prompt_path_override=_CLARIFY_INPUT_PROMPT_PATH,
        model_override=_env_model_override("SWARM_CLARIFY_MODEL", reviewer_cfg.get("model"), reviewer_cfg.get("_planner_capability")),
        environment_override=reviewer_cfg.get("environment"),
        max_output_tokens=_clarify_max_tokens,
        **_remote_api_client_kwargs_for_role(state, reviewer_cfg),
    )
    # SWARM_CLARIFY_DISABLE_TOOLS: disable MCP tool calls for clarify_input step.
    # Tool calls in clarify add noise and can trigger hallucinated plans (see 2026-04-09 analysis).
    # Default: disabled (0). Set to 1 to force tool-free clarify_input.
    _clarify_disable_tools = os.getenv("SWARM_CLARIFY_DISABLE_TOOLS", "0").strip() in ("1", "true", "yes", "on")
    clarify_output, _, _ = _llm_planning_agent_run(agent, prompt, state, disable_tools=_clarify_disable_tools)

    # P0: Format gate — clarify_input MUST begin with READY / NEEDS_CLARIFICATION / SIMPLE_ANSWER.
    # If the model produced a narrative response instead of the routing prefix, repair once.
    if not _clarify_has_valid_prefix(clarify_output):
        _log.warning(
            "clarify_input: output lacks required routing prefix (response_rejected_by_format_gate). "
            "len=%d. Sending repair-prompt.",
            len(clarify_output or ""),
        )
        _repair_prompt = (
            prompt
            + "\n\n[CRITICAL] Your previous response did not start with the required prefix. "
            "You MUST begin your response with exactly one of:\n"
            f"  {CLARIFY_READY}\n"
            f"  {CLARIFY_NEEDS_CLARIFICATION}\n"
            f"  {CLARIFY_SIMPLE_ANSWER}\n"
            "No preamble, no plan, no architecture. Only the routing prefix followed by your "
            "brief content (max 800 chars total)."
        )
        clarify_output, _, _ = _llm_planning_agent_run(agent, _repair_prompt, state)
        if not _clarify_has_valid_prefix(clarify_output):
            _log.warning(
                "clarify_input: STILL no valid prefix after repair — forcing READY prefix "
                "(treating as unambiguous task)."
            )
            clarify_output = CLARIFY_READY + "\n" + clarify_output

    # Must store output in state before raising so resume path can read it.
    state["clarify_input_output"] = clarify_output
    state["clarify_input_model"] = agent.used_model
    state["clarify_input_provider"] = agent.used_provider

    if clarify_output.strip().startswith(CLARIFY_SIMPLE_ANSWER):
        # Guard: if the "simple answer" is actually a long plan (>500 chars),
        # the model misrouted — treat as READY so the full pipeline runs.
        _simple_body = clarify_output.strip()[len(CLARIFY_SIMPLE_ANSWER):].strip()
        if len(_simple_body) > 500:
            _log.warning(
                "clarify_input: SIMPLE_ANSWER returned but content is %d chars "
                "(>500) — likely a misrouted plan. Treating as READY.",
                len(_simple_body),
            )
            clarify_output = CLARIFY_READY + "\n" + _simple_body
            state["clarify_input_output"] = clarify_output
        else:
            if cache_key is not None:
                _save_clarify_cache(cache_key, cache_identity, clarify_output)
            return {
                "clarify_input_output": clarify_output,
                "clarify_input_model": agent.used_model,
                "clarify_input_provider": agent.used_provider,
                "clarify_input_cache": state["clarify_input_cache"],
                "_pipeline_stop_early": True,
            }

    if clarify_output.strip().startswith(CLARIFY_NEEDS_CLARIFICATION):
        # Validate that the model actually produced questions, not just the prefix.
        # Weak models sometimes return "NEEDS_CLARIFICATION" with no questions.
        _clarify_body = clarify_output.strip()[len(CLARIFY_NEEDS_CLARIFICATION):].strip()
        if len(_clarify_body) < 50 or "?" not in _clarify_body:
            _log.warning(
                "clarify_input: NEEDS_CLARIFICATION returned but content has no real questions "
                "(%d chars, '?' present: %s). Retrying with explicit instruction.",
                len(_clarify_body), "?" in _clarify_body,
            )
            _retry_q_prompt = (
                prompt
                + "\n\n[CRITICAL] You returned NEEDS_CLARIFICATION but did not include any actual questions. "
                "Either list specific numbered questions the user must answer (each ending with '?'), "
                "or if the task is actually clear enough, respond with READY instead."
            )
            clarify_output, _, _ = _llm_planning_agent_run(agent, _retry_q_prompt, state)
            state["clarify_input_output"] = clarify_output
            # After retry: re-check — if still no questions, treat as READY
            if clarify_output.strip().startswith(CLARIFY_NEEDS_CLARIFICATION):
                _retry_body = clarify_output.strip()[len(CLARIFY_NEEDS_CLARIFICATION):].strip()
                if len(_retry_body) < 50 or "?" not in _retry_body:
                    _log.warning(
                        "clarify_input: retry still has no questions — forcing READY to avoid blocking."
                    )
                    clarify_output = CLARIFY_READY + "\n" + clarify_output
                    state["clarify_input_output"] = clarify_output
        if clarify_output.strip().startswith(CLARIFY_NEEDS_CLARIFICATION):
            if cache_key is not None:
                _save_clarify_cache(cache_key, cache_identity, clarify_output)
            from backend.App.orchestration.domain.exceptions import HumanApprovalRequired
            raise HumanApprovalRequired(
                step="clarify_input",
                detail=clarify_output,
                resume_pipeline_step="human_clarify_input",
                partial_state={"clarify_input_output": clarify_output},
            )

    if cache_key is not None:
        _save_clarify_cache(cache_key, cache_identity, clarify_output)
    return {
        "clarify_input_output": clarify_output,
        "clarify_input_model": agent.used_model,
        "clarify_input_provider": agent.used_provider,
        "clarify_input_cache": state["clarify_input_cache"],
    }


def human_clarify_input_node(state: PipelineState) -> dict[str, Any]:
    """Pause pipeline so user can answer clarifying questions (or confirm no changes needed).

    Possible ``human_clarify_status`` values returned:
    - ``skipped_by_router``: clarify_input reported READY so no human input needed.
    - ``answered_by_user``: user provided a non-empty answer.
    - ``empty_unexpected``: human agent returned empty output — logged as a warning;
      pipeline continues but callers may gate on this status.
    """
    questions = (state.get("clarify_input_output") or "").strip()
    if questions.startswith(CLARIFY_READY):
        return {
            "clarify_input_human_output": "[human:clarify_input] Input confirmed ready (no questions).",
            "human_clarify_status": "skipped_by_router",
        }
    bundle = (
        f"Business requirement:\n{planning_pipeline_user_context(state)}\n\n"
        f"Clarifying questions from the system:\n{questions}"
    )
    agent = _make_human_agent(state, "clarify_input")
    result = agent.run(bundle)
    if not (result or "").strip():
        _log.error(
            "human_clarify_input: human agent returned empty output "
            "(human_clarify_status=empty_unexpected) — stopping pipeline. "
            "The clarification step required a user response but received none."
        )
        return {
            "clarify_input_human_output": "",
            "human_clarify_status": "empty_unexpected",
            "_pipeline_stop_early": True,
            "_pipeline_stop_reason": (
                "human_clarify_input: empty response from human agent "
                "(NEEDS_CLARIFICATION was raised but no user answer received). "
                "Re-submit the task with explicit clarifications or mark it READY."
            ),
        }
    return {
        "clarify_input_human_output": result,
        "human_clarify_status": "answered_by_user",
    }


def _collect_pm_evidence_packet(workspace_root: str, task_text: str, *, max_chars: int = 3000) -> str:
    """Deterministically collect a compact evidence packet from the workspace before PM.

    Returns a formatted string block ready for injection into the PM prompt.
    Controlled by SWARM_PM_EVIDENCE_PREFETCH (default: 1 = enabled).
    """
    if os.getenv("SWARM_PM_EVIDENCE_PREFETCH", "1").strip() not in ("1", "true", "yes", "on"):
        _log.info("PM evidence prefetch DISABLED (SWARM_PM_EVIDENCE_PREFETCH != 1) — PM will use only inline context")
        return ""

    ws = workspace_root.strip() if workspace_root else ""
    if not ws or not os.path.isdir(ws):
        return ""

    parts: list[str] = []
    budget = max_chars

    # 1. Project manifest
    manifest_candidates = [
        "composer.json", "package.json", "pyproject.toml", "setup.py",
        "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
    ]
    for fname in manifest_candidates:
        fpath = os.path.join(ws, fname)
        if os.path.isfile(fpath):
            try:
                content = open(fpath, encoding="utf-8", errors="replace").read()
                snippet = content[:800]
                parts.append(f"# {fname}\n```\n{snippet}\n```")
                budget -= len(snippet) + 30
            except OSError:
                pass
            if budget <= 0:
                break

    # 1b. Project documentation — README, ARCHITECTURE, docs/
    # These describe the project's design patterns and architecture the team
    # agreed on. Reading them before PM/dev_lead prevents hallucinated paths.
    if budget > 200:
        doc_candidates = [
            "README.md", "readme.md", "README.rst",
            "ARCHITECTURE.md", "ARCHITECTURE.rst", "architecture.md",
            "CONTRIBUTING.md", "DESIGN.md", "OVERVIEW.md",
        ]
        for fname in doc_candidates:
            fpath = os.path.join(ws, fname)
            if os.path.isfile(fpath):
                try:
                    content = open(fpath, encoding="utf-8", errors="replace").read()
                    snippet = content[:600]
                    parts.append(f"# {fname}\n{snippet}")
                    budget -= len(snippet) + 20
                except OSError:
                    pass
                if budget <= 0:
                    break
        # Also check docs/ directory (one level)
        docs_dir = os.path.join(ws, "docs")
        if budget > 100 and os.path.isdir(docs_dir):
            try:
                for fname in sorted(os.listdir(docs_dir))[:4]:
                    if not fname.endswith((".md", ".rst", ".txt")):
                        continue
                    fpath = os.path.join(docs_dir, fname)
                    if not os.path.isfile(fpath):
                        continue
                    content = open(fpath, encoding="utf-8", errors="replace").read()
                    snippet = content[:400]
                    parts.append(f"# docs/{fname}\n{snippet}")
                    budget -= len(snippet) + 20
                    if budget <= 0:
                        break
            except OSError:
                pass

    # 2. Src tree (limited depth listing)
    if budget > 300:
        tree_lines: list[str] = []
        _ignored = {"node_modules", ".git", "__pycache__", ".venv", "venv", "vendor",
                    "dist", "build", ".next", ".nuxt", "coverage", ".mypy_cache"}
        try:
            for root, dirs, files in os.walk(ws):
                dirs[:] = [d for d in dirs if d not in _ignored and not d.startswith(".")]
                rel = os.path.relpath(root, ws)
                depth = 0 if rel == "." else rel.count(os.sep) + 1
                if depth > 3:
                    dirs.clear()
                    continue
                indent = "  " * depth
                dir_name = os.path.basename(root) if rel != "." else ws
                tree_lines.append(f"{indent}{dir_name}/")
                for f in sorted(files)[:20]:
                    tree_lines.append(f"{indent}  {f}")
                if len(tree_lines) > 120:
                    tree_lines.append("  ... (truncated)")
                    dirs.clear()
        except OSError:
            pass
        if tree_lines:
            tree_block = "\n".join(tree_lines[:120])
            parts.append(f"# Workspace tree\n```\n{tree_block}\n```")
            budget -= len(tree_block) + 30

    # 3. Find relevant files based on task keywords
    if budget > 200:
        task_words = set(
            w.lower() for w in task_text.replace("_", " ").split()
            if len(w) > 3 and w.isalpha()
        )
        _src_exts = {".php", ".py", ".ts", ".js", ".go", ".rb", ".java", ".cs", ".rs"}
        _ignored = {"node_modules", ".git", "__pycache__", ".venv", "venv", "vendor",
                    "dist", "build", ".next", ".nuxt", "coverage", ".mypy_cache"}
        relevant_paths: list[str] = []
        try:
            for root, dirs, files in os.walk(ws):
                dirs[:] = [d for d in dirs if d not in _ignored and not d.startswith(".")]
                rel_root = os.path.relpath(root, ws)
                if rel_root.count(os.sep) > 4:
                    dirs.clear()
                    continue
                for f in files:
                    if not any(f.endswith(ext) for ext in _src_exts):
                        continue
                    fname_lower = f.lower().replace("_", " ").replace("-", " ")
                    if any(w in fname_lower for w in task_words):
                        relevant_paths.append(os.path.join(root, f))
                    if len(relevant_paths) >= 10:
                        break
                if len(relevant_paths) >= 10:
                    break
        except OSError:
            pass

        shown = 0
        for fpath in relevant_paths[:5]:
            if budget <= 100:
                break
            try:
                content = open(fpath, encoding="utf-8", errors="replace").read()
                rel_fpath = os.path.relpath(fpath, ws)
                snippet = content[:min(400, budget - 80)]
                parts.append(f"# {rel_fpath}\n```\n{snippet}\n```")
                budget -= len(snippet) + 50
                shown += 1
            except OSError:
                pass
        if shown:
            _log.debug("PM evidence prefetch: injected %d relevant files", shown)

    if not parts:
        _log.info("PM evidence prefetch: no evidence collected (empty workspace or no matching files)")
        return ""

    _log.info(
        "PM evidence prefetch: injected %d block(s) (~%d chars total) into PM prompt",
        len(parts),
        sum(len(p) for p in parts),
    )
    return (
        "\n[Repository evidence — deterministically prefetched before PM]\n"
        + "\n\n".join(parts)
        + "\n\n"
    )


def pm_node(state: PipelineState) -> dict[str, Any]:
    plan_ctx = planning_pipeline_user_context(state)
    mem = format_pattern_memory_block(state, plan_ctx)
    xmem = format_cross_task_memory_block(
        state, plan_ctx, current_step="pm"
    )
    ctx = _pipeline_context_block(state, "pm")
    clarify_human = (state.get("clarify_input_human_output") or "").strip()
    _no_clarify = (
        not clarify_human
        or clarify_human.startswith("[human:clarify_input] Input confirmed ready")
        or clarify_human.startswith("[human:clarify_input] APPROVED (auto)")
        or clarify_human.startswith("[human:clarify_input] Confirmed manually")
    )
    if _no_clarify:
        raw_input = plan_ctx
    else:
        raw_input = (
            plan_ctx
            + "\n\n[User clarifications (answers to pre-pipeline questions)]\n"
            + clarify_human
        )
    # P0: Deterministic repo-evidence prefetch — collect compact evidence before calling PM LLM
    _workspace_root = str(state.get("workspace_root") or "").strip()
    _task_text = str(state.get("input") or plan_ctx or "")
    _evidence_block = _collect_pm_evidence_packet(_workspace_root, _task_text)
    # Store evidence in state so dev_lead can use the same workspace structure
    # context without re-scanning (prevents path hallucination).
    if _evidence_block and not state.get("workspace_evidence_brief"):
        cast(dict, state)["workspace_evidence_brief"] = _evidence_block
    # P0-6: Inject code analysis summary so PM knows the actual tech stack
    # (analyze_code runs before PM in the pipeline).
    _ca_block = ""
    _analyze_out = (state.get("analyze_code_output") or "").strip()
    if _analyze_out:
        _ca_block = (
            "\n[Repository code analysis — use this to determine the actual tech stack]\n"
            + _analyze_out[:4000]
            + "\n\n"
        )
    planning_retry_feedback = str((state.get("planning_review_feedback") or {}).get("pm") or "").strip()
    planning_retry_block = ""
    if planning_retry_feedback:
        planning_retry_block = (
            "\n[Reviewer feedback from previous PM attempt — fix all issues below before returning a new PM artifact]\n"
            + planning_retry_feedback[:4000]
            + "\n\n"
        )
    user_input = (
        mem
        + xmem
        + ctx
        + _swarm_prompt_prefix(state)
        + planning_mcp_tool_instruction(state)
        + _PM_STACK_RULE
        + _ca_block
        + _evidence_block
        + planning_retry_block
        + raw_input
    )
    # Deep planning: run 5-stage analysis before PM if SWARM_DEEP_PLANNING=1
    if os.getenv("SWARM_DEEP_PLANNING", "0") == "1":
        try:
            from backend.App.orchestration.application.deep_planning import DeepPlanner
            task_id = os.getenv("SWARM_CURRENT_TASK_ID", "unknown")
            workspace_root = str(state.get("workspace_root") or os.getenv("SWARM_WORKSPACE_ROOT", ""))
            _stream_progress_emit(state, "Deep planning: scanning workspace…")
            plan = DeepPlanner().analyze(
                task_id=task_id,
                task_spec=user_input,
                workspace_root=workspace_root,
            )
            if not plan.error:
                _stream_progress_emit(
                    state,
                    f"Deep planning complete — {len(plan.risks)} risks, {len(plan.alternatives)} alternatives, "
                    f"{len(plan.milestones)} milestones. Recommended: {plan.recommended_alternative or 'n/a'}",
                )
                summary = (
                    f"## Deep Planning Analysis\n\n"
                    f"Scan: {plan.scan_summary[:400]}\n"
                    f"Risks: {len(plan.risks)} identified\n"
                    f"Alternatives: {len(plan.alternatives)}\n"
                    f"Milestones: {len(plan.milestones)}\n"
                    f"Recommended: {plan.recommended_alternative}\n\n"
                )
                user_input = summary + user_input
                _log.info("pm_node: deep planning prepended (task=%s)", task_id)  # INV-1
            else:
                _stream_progress_emit(state, f"Deep planning failed: {plan.error} — proceeding without it")
                _log.warning("pm_node: deep planning failed (%s)", plan.error)  # INV-1
        except Exception as exc:
            _stream_progress_emit(state, f"Deep planning exception ({exc}) — proceeding without it")
            _log.warning("pm_node: deep planning exception (%s)", exc)  # INV-1

    cfg = (state.get("agent_config") or {}).get("pm") or {}
    # SWARM_PM_MODEL: model override for pm step (env var fallback when not set in config).
    # Useful for routing PM to a tool-free or cheaper model after deterministic evidence prefetch.
    agent = PMAgent(
        system_prompt_path_override=cfg.get("prompt_path") or cfg.get("prompt"),
        model_override=_env_model_override("SWARM_PM_MODEL", cfg.get("model"), cfg.get("_planner_capability")),
        environment_override=cfg.get("environment"),
        system_prompt_extra=_skills_extra_for_role_cfg(state, cfg),
        **_remote_api_client_kwargs_for_role(state, cfg),
    )
    pm_output, _, _ = _llm_planning_agent_run(agent, user_input, state)
    # Gate: reject output that is only raw <tool_call> envelopes (unparsed tool calls = broken output).
    _stripped_pm = (pm_output or "").strip()
    _tool_call_count = _stripped_pm.count("<tool_call>")
    _non_tool_chars = len(_stripped_pm) - sum(
        len(blk) for blk in _stripped_pm.split("<tool_call>")[1:]
    )
    if _tool_call_count >= 1 and _non_tool_chars < 200:
        _log.warning(
            "PM output appears to be raw tool_call envelopes (tool_call_count=%d, non_tool_chars=%d) — retrying",
            _tool_call_count,
            _non_tool_chars,
        )
        _retry_input = (
            user_input
            + "\n\n[CRITICAL] Your previous response contained only raw <tool_call> XML blocks and no readable text. "
            "Do NOT emit tool call syntax. Produce a structured human-readable task list directly."
        )
        pm_output, _, _ = _llm_planning_agent_run(agent, _retry_input, state)
    # Retry PM if output is too short (intent-only, no real tasks)
    _pm_min_chars = 300
    if pm_output and len(pm_output.strip()) < _pm_min_chars:
        _log.warning(
            "PM output too short (%d chars < %d) — retrying with explicit task-list instruction",
            len(pm_output.strip()), _pm_min_chars,
        )
        _retry_input = (
            user_input
            + "\n\n[CRITICAL] Your previous response was too brief and contained no task list. "
            "You MUST produce a structured list of development tasks with acceptance criteria. "
            "Do NOT describe what you plan to do — output the actual tasks NOW."
        )
        pm_output, _, _ = _llm_planning_agent_run(agent, _retry_input, state)
    if (pm_output or "").strip():
        from backend.App.workspace.application.doc_workspace import write_step_wiki
        write_step_wiki(state, "pm", pm_output)
    memory_artifact = _pm_memory_artifact(plan_ctx, pm_output)
    return {
        "pm_output": pm_output,
        "pm_model": agent.used_model,
        "pm_provider": agent.used_provider,
        "pm_memory_artifact": memory_artifact,
    }


def review_pm_node(state: PipelineState) -> dict[str, Any]:
    user_block = embedded_pipeline_input_for_review(state, log_node="review_pm_node")
    pm_art = embedded_review_artifact(
        state,
        state.get("pm_output"),
        log_node="review_pm_node",
        part_name="pm_output",
        env_name="SWARM_REVIEW_PM_OUTPUT_MAX_CHARS",
        default_max=60_000,
    )
    prompt = (
        "Step: pm (Project Manager).\n"
        "Checklist — issue VERDICT: NEEDS_WORK if ANY item fails:\n"
        "[ ] No specific technology stack chosen (stack belongs to Architect only)\n"
        "[ ] Tasks are decomposed into concrete subtasks with priorities\n"
        "[ ] Each task has clear acceptance/readiness criteria\n"
        "[ ] Scope is realistic (not a copy of raw user input)\n\n"
        f"User task:\n{user_block}\n\n"
        f"PM artifact:\n{pm_art}"
    )
    return run_reviewer_or_moa(
        state,
        pipeline_step="review_pm",
        prompt=prompt,
        output_key="pm_review_output",
        model_key="pm_review_model",
        provider_key="pm_review_provider",
        agent_factory=lambda: _make_reviewer_agent(state),
    )


def human_pm_node(state: PipelineState) -> dict[str, Any]:
    bundle = f"PM:\n{state['pm_output']}\n\nReview:\n{state['pm_review_output']}"
    agent = _make_human_agent(state, "pm")
    return {"pm_human_output": agent.run(bundle)}
