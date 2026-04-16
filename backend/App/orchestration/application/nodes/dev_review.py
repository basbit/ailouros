"""Dev review and human-gate pipeline nodes.

Extracted from dev.py: _review_dev_output_max_chars, _review_spec_max_chars,
review_dev_node, human_dev_node.

M-9 (output compression): after review_dev_node produces output the full
prose is stored as an artifact and a compact JSON summary
(``dev_review_compressed``) is added to the result.  human_dev_node uses the
compact form so the human-gate bundle stays small even after multi-round
review cycles.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

from backend.App.orchestration.application.review_moa import run_reviewer_or_moa
from backend.App.orchestration.application.pipeline_state import PipelineState
from backend.App.orchestration.domain.defect import parse_defect_report
from backend.App.orchestration.application.output_contracts import (
    compress_reviewer_output,
    format_compressed_reviewer,
    output_compression_enabled,
    reviewer_compact_for_prompt,
    CompressedReviewerOutput,
)

from backend.App.orchestration.application.nodes._shared import (
    _effective_spec_for_build,
    _make_human_agent,
    _make_reviewer_agent,
    _should_use_mcp_for_workspace,
    embedded_pipeline_input_for_review,
)

logger = logging.getLogger(__name__)


def _review_dev_output_max_chars() -> int:
    """Max chars for the dev_output block embedded in the reviewer prompt.

    Default 60_000 chars — keeps the reviewer prompt within a safe context window.
    Override via SWARM_REVIEW_DEV_OUTPUT_MAX_CHARS env var.
    """
    env_value = os.getenv("SWARM_REVIEW_DEV_OUTPUT_MAX_CHARS", "").strip()
    if env_value.isdigit():
        max_chars = int(env_value)
        if max_chars > 0:
            return max_chars
    return 60_000


def _review_spec_max_chars() -> int:
    """Max chars for the spec block embedded in reviewer prompts.

    Default 40_000 chars — enough for a full merged spec while keeping reviewer
    prompt size predictable.
    Override via SWARM_REVIEW_SPEC_MAX_CHARS env var.
    """
    env_value = os.getenv("SWARM_REVIEW_SPEC_MAX_CHARS", "").strip()
    if env_value.isdigit():
        max_chars = int(env_value)
        if max_chars > 0:
            return max_chars
    return 40_000


def review_dev_node(state: PipelineState) -> dict[str, Any]:
    task_id = (state.get("task_id") or "")[:36]
    use_mcp = _should_use_mcp_for_workspace(state)

    spec_full = _effective_spec_for_build(state)
    spec_limit = int(os.environ.get("SWARM_REVIEW_SPEC_MCP_MAX_CHARS", "3000")) if use_mcp else _review_spec_max_chars()
    if len(spec_full) > spec_limit:
        logger.warning(
            "review_dev_node: spec truncated from %d to %d chars "
            "(SWARM_REVIEW_SPEC_MAX_CHARS=%d). task_id=%s",
            len(spec_full), spec_limit, spec_limit, task_id,
        )
        spec = spec_full[:spec_limit] + "\n…[spec truncated — increase SWARM_REVIEW_SPEC_MAX_CHARS to see more]"
    else:
        spec = spec_full

    dev_output_full = state.get("dev_output") or ""
    dev_limit = int(os.environ.get("SWARM_REVIEW_DEV_MCP_MAX_CHARS", "4000")) if use_mcp else _review_dev_output_max_chars()
    if len(dev_output_full) > dev_limit:
        logger.warning(
            "review_dev_node: dev_output truncated from %d to %d chars "
            "(SWARM_REVIEW_DEV_OUTPUT_MAX_CHARS=%d). task_id=%s",
            len(dev_output_full), dev_limit, dev_limit, task_id,
        )
        dev_output = dev_output_full[:dev_limit] + "\n…[dev_output truncated — increase SWARM_REVIEW_DEV_OUTPUT_MAX_CHARS to see more]"
    else:
        dev_output = dev_output_full

    # FIX 10.3: Check for unwrapped code blocks (≥20 lines) vs <swarm_file> tags.
    # If dev produced large code fences without wrapping them in <swarm_file> tags,
    # inform the reviewer so it can flag the issue as a P1 defect.
    _long_fences = re.findall(r"```\w*\n((?:.*\n){20,}?)```", dev_output_full)
    _swarm_file_count = len(re.findall(r"<swarm_file", dev_output_full))
    _fence_count = len(_long_fences)
    _swarm_file_warning = ""
    if _fence_count > _swarm_file_count:
        _swarm_file_warning = (
            f"\nWARNING: Dev output contains {_fence_count} code block(s) with ≥20 lines "
            f"but only {_swarm_file_count} <swarm_file> wrapper(s). "
            "The reviewer MUST flag this as a P1 defect if files lack proper "
            '<swarm_file path="..."> wrapping.\n'
        )

    user_block = embedded_pipeline_input_for_review(state, log_node="review_dev_node")
    prompt = (
        "Step: dev (development).\n"
        "Checklist — issue VERDICT: NEEDS_WORK if ANY item fails:\n"
        "[ ] Implementation uses the Architect-approved stack (languages, frameworks, DB)\n"
        "[ ] Files intended for workspace use <swarm_file> tags (not just fenced code)\n"
        "[ ] All subtasks from Dev Lead plan are addressed\n"
        "[ ] No placeholder/stub code (functions that always return hardcoded values, 'TODO/FIXME', 'dummy', 'mock_*', 'placeholder' comments left as-is)\n"
        "[ ] External services specified in the spec (e.g. OpenAI API, external LLM, third-party SDKs) are actually called — not replaced by local models or hardcoded stubs\n"
        "[ ] All dependencies used in code are listed in the requirements/build file\n"
        "[ ] Endpoint paths match the spec exactly (no silent renames like /diagnose vs /api/v1/plant-diagnose)\n"
        "[ ] Error handling covers the main failure modes described in the spec (bad input, upstream failure, timeout)\n"
        "[ ] No stack or architectural decisions overridden without justification\n"
        "[ ] Naming conventions match the existing codebase (class names, function names, file names follow detected patterns)\n\n"
        "Output contract:\n"
        "1. Human-readable review summary.\n"
        "2. A machine-readable block `<defect_report>...</defect_report>` containing JSON object:\n"
        '{"defects":[{"id":"optional","title":"...","severity":"P0|P1|P2","file_paths":["..."],"expected":"...","actual":"...","repro_steps":["..."],"acceptance":["..."],"category":"...","fixed":false}],"test_scenarios":["..."],"edge_cases":["..."],"regression_checks":["..."]}\n'
        "3. Final line `VERDICT: OK` or `VERDICT: NEEDS_WORK`.\n"
        "If verdict is OK, defects may be empty but the block must still be present.\n\n"
        f"User task:\n{user_block}\n\n"
        f"Specification:\n{spec}\n\n"
        f"Dev artifact:\n{dev_output}"
        + _swarm_file_warning
    )
    result = run_reviewer_or_moa(
        state,
        pipeline_step="review_dev",
        prompt=prompt,
        output_key="dev_review_output",
        model_key="dev_review_model",
        provider_key="dev_review_provider",
        agent_factory=lambda: _make_reviewer_agent(state),
        require_json_defect_report=True,
    )
    review_text = str(result.get("dev_review_output") or "")
    result["dev_defect_report"] = parse_defect_report(review_text).to_dict()

    # M-9: compress reviewer output — store prose as artifact, keep compact
    # structured form in state.  Downstream prompts (human_dev, re-prompts)
    # use the compact form to avoid re-embedding 10-60 KB of reviewer prose.
    if output_compression_enabled() and review_text:
        compressed = compress_reviewer_output(review_text)
        result["dev_review_compressed"] = format_compressed_reviewer(compressed)
        logger.debug(
            "review_dev_node: compressed reviewer output %d chars → %d chars compact "
            "(artifact_ref=%s…)",
            compressed.char_count,
            len(result["dev_review_compressed"]),
            compressed.artifact_ref[-12:],
        )

    return result


def human_dev_node(state: PipelineState) -> dict[str, Any]:
    # M-9: prefer compressed review summary for the human-gate bundle.
    # The full prose is in the artifact store; the compact form gives the
    # human agent the verdict, top defects, and a brief summary — sufficient
    # for human-readable review without re-embedding the full 10-60 KB review.
    compressed_review = state.get("dev_review_compressed")
    if isinstance(compressed_review, str) and compressed_review and output_compression_enabled():
        import json as _json
        try:
            c_data = _json.loads(compressed_review)
            _c = CompressedReviewerOutput(
                verdict=c_data["verdict"],
                defects=c_data["defects"],
                defect_count=c_data["defect_count"],
                summary=c_data["summary"],
                char_count=c_data["char_count"],
                artifact_ref=c_data["artifact_ref"],
            )
            review_for_human = reviewer_compact_for_prompt(_c)
        except (_json.JSONDecodeError, KeyError) as exc:
            logger.warning("human_dev_node: failed to parse compressed review: %s", exc)
            review_for_human = str(state.get("dev_review_output") or "")
    else:
        review_for_human = str(state.get("dev_review_output") or "")

    bundle = f"Dev:\n{state.get('dev_output', '')}\n\nReview:\n{review_for_human}"
    agent = _make_human_agent(state, "dev")
    return {"dev_human_output": agent.run(bundle)}
