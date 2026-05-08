from __future__ import annotations

import logging
import os

from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState

logger = logging.getLogger(__name__)


_SPEC_FOR_BUILD_MAX_CHARS = int(
    os.getenv("SWARM_SPEC_FOR_BUILD_MAX_CHARS", "60000")
)
_SPEC_SUMMARY_MAX_CHARS = int(os.getenv("SWARM_SPEC_SUMMARY_MAX_CHARS", "5000"))


def spec_summary_for_subtask(
    full_spec: str,
    development_scope: str,
    *,
    max_chars: int = 0,
) -> str:
    if max_chars < 0:
        raise ValueError(f"max_chars must be non-negative, got {max_chars}")
    if max_chars == 0:
        max_chars = _SPEC_SUMMARY_MAX_CHARS
    spec_text = full_spec.strip()
    scope_text = development_scope.strip()
    if not spec_text and not scope_text:
        return ""
    parts: list[str] = []
    if spec_text:
        truncated = spec_text[:max_chars]
        marker = (
            ""
            if len(spec_text) <= max_chars
            else "\n…[spec truncated to max_chars]"
        )
        parts.append("[Approved specification — context for subtask]")
        parts.append(truncated + marker)
    if scope_text:
        parts.append("\n[Subtask development scope]")
        parts.append(scope_text)
    return "\n".join(parts)


def effective_spec_for_build(state: PipelineState) -> str:
    spec = (state.get("spec_output") or "").strip()
    if spec:
        if len(spec) > _SPEC_FOR_BUILD_MAX_CHARS:
            logger.warning(
                "pipeline build: spec_output truncated from %d to %d chars "
                "(SWARM_SPEC_FOR_BUILD_MAX_CHARS)",
                len(spec), _SPEC_FOR_BUILD_MAX_CHARS,
            )
            spec = spec[:_SPEC_FOR_BUILD_MAX_CHARS] + "\n…[spec truncated]"
        return spec
    pm_output = (state.get("pm_output") or "").strip()
    ba = (state.get("ba_output") or "").strip()
    arch = (state.get("arch_output") or "").strip()
    part_cap = _SPEC_FOR_BUILD_MAX_CHARS // 3
    parts: list[str] = []
    if pm_output:
        parts.append("[PM — plan and tasks]\n" + pm_output[:part_cap])
    if ba:
        parts.append("[BA — requirements]\n" + ba[:part_cap])
    if arch:
        parts.append("[Architect — stack and boundaries]\n" + arch[:part_cap])
    if parts:
        merged = "\n\n---\n\n".join(parts)
        logger.info(
            "pipeline build: spec_output empty; using PM/BA/Architect context "
            "(%d chars). Add a spec_merge step for a single approved spec "
            "(task_id=%s)",
            len(merged),
            (state.get("task_id") or "")[:36],
        )
        return merged
    logger.warning(
        "pipeline build: spec_output empty and no pm_output/ba_output/arch_output — "
        "Dev/DevOps/Dev Lead running without spec (add steps or spec_merge; task_id=%s)",
        (state.get("task_id") or "")[:36],
    )
    return ""


def spec_for_build_mcp_safe(state: PipelineState, *, mcp_active: bool) -> str:
    spec = effective_spec_for_build(state)
    if not mcp_active:
        return spec
    env_value = os.getenv("SWARM_MCP_SPEC_MAX_CHARS", "").strip()
    max_chars = (
        int(env_value)
        if env_value.isdigit() and int(env_value) > 0
        else 3000
    )
    if len(spec) <= max_chars:
        return spec
    logger.warning(
        "MCP build: spec truncated from %d to %d chars (SWARM_MCP_SPEC_MAX_CHARS=%d). "
        "Increase SWARM_MCP_SPEC_MAX_CHARS or raise model n_ctx.",
        len(spec), max_chars, max_chars,
    )
    return (
        spec[:max_chars]
        + f"\n…[spec truncated — set SWARM_MCP_SPEC_MAX_CHARS to increase "
        f"(current: {max_chars})]"
    )


def spec_arch_context_for_docs(
    state: PipelineState,
    *,
    max_each: int = 12000,
) -> str:
    spec = (state.get("spec_output") or "").strip()
    arch = (state.get("arch_output") or "").strip()
    parts: list[str] = []
    if spec:
        parts.append("[Approved specification (spec_merge)]\n" + spec[:max_each])
    if arch:
        parts.append("[Architect output (pipeline)]\n" + arch[:max_each])
    return "\n\n".join(parts) if parts else ""


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        value = int(raw) if raw else 0
    except ValueError:
        return default
    return value if value > 0 else default


def doc_spec_max_each_chars() -> int:
    return _int_env("SWARM_DOC_SPEC_MAX_CHARS", 12_000)


def doc_chain_spec_max_chars() -> int:
    return _int_env("SWARM_DOC_CHAIN_SPEC_MAX_CHARS", 24_000)


def doc_generate_second_pass_analysis_max_chars() -> int:
    return _int_env("SWARM_DOCUMENTATION_DOC_PASS_MAX_ANALYSIS_CHARS", 9000)


def effective_spec_block_for_doc_chain(
    state: PipelineState,
    *,
    log_node: str,
) -> str:
    full_spec = effective_spec_for_build(state).strip()
    if not full_spec:
        return ""
    max_chars = doc_chain_spec_max_chars()
    task_id_prefix = (state.get("task_id") or "")[:36]
    if len(full_spec) <= max_chars:
        return full_spec
    logger.warning(
        "%s: effective spec for doc chain truncated from %d to %d chars "
        "(SWARM_DOC_CHAIN_SPEC_MAX_CHARS=%d). task_id=%s",
        log_node,
        len(full_spec),
        max_chars,
        max_chars,
        task_id_prefix,
    )
    return (
        full_spec[:max_chars]
        + "\n…[truncated — increase SWARM_DOC_CHAIN_SPEC_MAX_CHARS]"
    )


def documentation_product_context_block(
    state: PipelineState,
    *,
    log_node: str,
) -> str:
    max_each = doc_spec_max_each_chars()
    spec_arch_block = spec_arch_context_for_docs(state, max_each=max_each)
    if spec_arch_block.strip():
        return spec_arch_block
    return effective_spec_block_for_doc_chain(state, log_node=log_node)


__all__ = (
    "spec_summary_for_subtask",
    "effective_spec_for_build",
    "spec_for_build_mcp_safe",
    "spec_arch_context_for_docs",
    "doc_spec_max_each_chars",
    "doc_chain_spec_max_chars",
    "doc_generate_second_pass_analysis_max_chars",
    "effective_spec_block_for_doc_chain",
    "documentation_product_context_block",
)
