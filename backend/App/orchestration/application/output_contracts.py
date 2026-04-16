"""M-9 — Output compression contracts.

Compact JSON schemas for Dev Lead deliverables, reviewer defect reports, and
QA summaries.  Long narrative content is stored once as an in-memory artifact
(content-addressed, shared store with ``delta_prompt``); downstream prompts
reference it by sha256 hash instead of re-embedding the full text.

This bounds prompt growth across long pipelines where review / defect cycles
accumulate KB of reviewer prose in state.

Schema contract per output type
--------------------------------
CompressedReviewerOutput
    { verdict, defect_count, summary, char_count, artifact_ref, defects[:5] }

CompressedDevLeadOutput
    { tasks, deliverables, artifact_ref, char_count }

CompressedQASummary
    { verdict, summary, char_count, artifact_ref }

Environment
-----------
SWARM_OUTPUT_COMPRESSION=1  (default) — enable compression
SWARM_OUTPUT_COMPRESSION=0  — disable (full text stored in state as before)
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from backend.App.orchestration.application.delta_prompt import store_artifact

__all__ = [
    "output_compression_enabled",
    "CompressedReviewerOutput",
    "CompressedDevLeadOutput",
    "CompressedQASummary",
    "compress_reviewer_output",
    "compress_dev_lead_output",
    "compress_qa_output",
    "format_compressed_reviewer",
    "format_compressed_dev_lead",
    "format_compressed_qa",
]


def output_compression_enabled() -> bool:
    """Return True when SWARM_OUTPUT_COMPRESSION is not explicitly disabled.

    Default: ON (SWARM_OUTPUT_COMPRESSION=1).
    """
    return os.getenv("SWARM_OUTPUT_COMPRESSION", "1").strip() not in ("0", "false", "no", "off")


# ---------------------------------------------------------------------------
# Compressed output dataclasses
# ---------------------------------------------------------------------------

@dataclass
class CompressedReviewerOutput:
    """Compact representation of a reviewer's prose output.

    The full narrative is stored as an artifact; this carries only the
    structured data needed by downstream pipeline steps:
    verdict, structured defect list (top-5 inline), and a brief summary.
    """
    verdict: str          # "OK" | "NEEDS_WORK"
    defects: list         # top-5 structured defect dicts (full list in artifact)
    defect_count: int     # total defect count (including those only in artifact)
    summary: str          # first 300 chars of review prose
    char_count: int       # original full text length
    artifact_ref: str     # artifact:sha256:<hex> — resolves to full prose


@dataclass
class CompressedDevLeadOutput:
    """Compact representation of a Dev Lead plan output.

    The JSON plan (tasks + deliverables) is kept inline because downstream
    steps (dev, qa) need the structured data.  The narrative wrapping text
    is stored as an artifact to avoid re-embedding 5-10 KB of prose.
    """
    tasks: list               # parsed subtask list (dicts)
    deliverables: dict        # normalized deliverables dict
    has_deliverables: bool
    has_complete_deliverables: bool
    summary: str              # first 200 chars of raw LLM output
    char_count: int
    artifact_ref: str         # artifact:sha256:<hex> — resolves to raw output


@dataclass
class CompressedQASummary:
    """Compact representation of a QA run output.

    Verdict + brief summary kept inline; full test output as artifact.
    """
    verdict: str          # "OK" | "NEEDS_WORK"
    summary: str          # first 400 chars of QA output
    char_count: int
    artifact_ref: str     # artifact:sha256:<hex> — resolves to full output


# ---------------------------------------------------------------------------
# Compression functions
# ---------------------------------------------------------------------------

def compress_reviewer_output(text: str) -> CompressedReviewerOutput:
    """Compress reviewer prose output.  Full text stored as artifact.

    Extracts structured defects via ``parse_defect_report`` and the VERDICT
    line.  Top-5 defects are kept inline; the full prose is artifact-stored.

    Args:
        text: Raw reviewer LLM output (prose + optional <defect_report> block).

    Returns:
        :class:`CompressedReviewerOutput` with structured data + artifact ref.
    """
    from backend.App.orchestration.domain.defect import parse_defect_report

    defect_report = parse_defect_report(text)
    defects = [d.to_dict() for d in defect_report.defects]

    match = re.search(r"VERDICT\s*:\s*(OK|NEEDS_WORK)", text, re.IGNORECASE)
    verdict = match.group(1).upper() if match else "NEEDS_WORK"

    ref = store_artifact(text)
    summary = text[:300].replace("\n", " ").strip()

    return CompressedReviewerOutput(
        verdict=verdict,
        defects=defects[:5],
        defect_count=len(defects),
        summary=summary,
        char_count=len(text),
        artifact_ref=ref,
    )


def compress_dev_lead_output(text: str) -> CompressedDevLeadOutput:
    """Compress Dev Lead output.  JSON plan kept inline; narrative as artifact.

    Uses ``parse_dev_lead_plan`` to extract the structured plan, stores the
    full raw output as an artifact for audit/retry use.

    Args:
        text: Raw dev_lead LLM output (prose + embedded JSON plan).

    Returns:
        :class:`CompressedDevLeadOutput` with parsed plan + artifact ref.
    """
    from backend.App.orchestration.application.nodes.dev_subtasks import parse_dev_lead_plan

    plan = parse_dev_lead_plan(text)
    ref = store_artifact(text)
    summary = text[:200].replace("\n", " ").strip()

    return CompressedDevLeadOutput(
        tasks=plan["tasks"],
        deliverables=plan["deliverables"],
        has_deliverables=bool(plan.get("has_deliverables")),
        has_complete_deliverables=bool(plan.get("has_complete_deliverables")),
        summary=summary,
        char_count=len(text),
        artifact_ref=ref,
    )


def compress_qa_output(text: str) -> CompressedQASummary:
    """Compress QA output.  Full text stored as artifact.

    Extracts the VERDICT line (supports OK / PASS / NEEDS_WORK / FAIL).

    Args:
        text: Raw QA LLM output.

    Returns:
        :class:`CompressedQASummary` with verdict + artifact ref.
    """
    match = re.search(r"VERDICT\s*:\s*(OK|NEEDS_WORK|PASS|FAIL)", text, re.IGNORECASE)
    if match:
        raw_v = match.group(1).upper()
        verdict = "OK" if raw_v in ("OK", "PASS") else "NEEDS_WORK"
    else:
        verdict = "NEEDS_WORK"

    ref = store_artifact(text)
    summary = text[:400].replace("\n", " ").strip()

    return CompressedQASummary(
        verdict=verdict,
        summary=summary,
        char_count=len(text),
        artifact_ref=ref,
    )


# ---------------------------------------------------------------------------
# Format helpers — compact string representation for state / prompt use
# ---------------------------------------------------------------------------

def format_compressed_reviewer(c: CompressedReviewerOutput) -> str:
    """Return compact JSON string for state storage.

    Embeds top-5 defects inline.  Full reviewer prose is in the artifact.
    The string still contains the required section names
    (must_exist_files etc.) when they appear inside defect fields, so
    ``_validate_dev_lead_output``-style substring checks remain valid
    on the reviewer side.
    """
    return json.dumps({
        "verdict": c.verdict,
        "defect_count": c.defect_count,
        "summary": c.summary,
        "char_count": c.char_count,
        "artifact_ref": c.artifact_ref,
        "defects": c.defects,
    }, ensure_ascii=False)


def format_compressed_dev_lead(c: CompressedDevLeadOutput) -> str:
    """Return compact JSON string: parsed plan + artifact ref for narrative.

    This is suitable as the value stored in ``state["dev_lead_output"]``
    when compression is enabled — it contains all the fields that
    downstream validators and prompt builders need:
    ``tasks``, ``deliverables``, ``must_exist_files`` etc. (nested in
    deliverables), so substring-based validation keeps working.
    """
    return json.dumps({
        "tasks": c.tasks,
        "deliverables": c.deliverables,
        "artifact_ref": c.artifact_ref,
        "char_count": c.char_count,
    }, ensure_ascii=False)


def format_compressed_qa(c: CompressedQASummary) -> str:
    """Return compact JSON string for state storage."""
    return json.dumps({
        "verdict": c.verdict,
        "summary": c.summary,
        "char_count": c.char_count,
        "artifact_ref": c.artifact_ref,
    }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Prompt-embedding helper
# ---------------------------------------------------------------------------

def reviewer_compact_for_prompt(c: CompressedReviewerOutput) -> str:
    """Return a concise reviewer summary suitable for embedding in follow-up prompts.

    Used when a downstream agent (e.g., human_dev) needs to reference the
    review without receiving the full prose.  Top-3 defects are shown inline
    with severity and title; full details are in the artifact.
    """
    lines = [
        f"[Review verdict: {c.verdict} | {c.defect_count} defect(s) | "
        f"{c.char_count} chars — artifact ref: {c.artifact_ref[-20:]}]",
        f"Summary: {c.summary[:200]}",
    ]
    if c.defects:
        lines.append("Top defects:")
        for d in c.defects[:3]:
            sev = d.get("severity", "P1")
            title = d.get("title", "")[:80]
            lines.append(f"  [{sev}] {title}")
    return "\n".join(lines)
