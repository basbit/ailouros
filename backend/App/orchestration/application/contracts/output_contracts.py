from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from backend.App.orchestration.application.context.delta_prompt import store_artifact

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
    return os.getenv("SWARM_OUTPUT_COMPRESSION", "1").strip() not in ("0", "false", "no", "off")


@dataclass
class CompressedReviewerOutput:
    verdict: str          # "OK" | "NEEDS_WORK"
    defects: list         # top-5 structured defect dicts (full list in artifact)
    defect_count: int     # total defect count (including those only in artifact)
    summary: str          # first 300 chars of review prose
    char_count: int       # original full text length
    artifact_ref: str     # artifact:sha256:<hex> — resolves to full prose


@dataclass
class CompressedDevLeadOutput:
    tasks: list               # parsed subtask list (dicts)
    deliverables: dict        # normalized deliverables dict
    has_deliverables: bool
    has_complete_deliverables: bool
    summary: str              # first 200 chars of raw LLM output
    char_count: int
    artifact_ref: str         # artifact:sha256:<hex> — resolves to raw output


@dataclass
class CompressedQASummary:
    verdict: str          # "OK" | "NEEDS_WORK"
    summary: str          # first 400 chars of QA output
    char_count: int
    artifact_ref: str     # artifact:sha256:<hex> — resolves to full output


def compress_reviewer_output(text: str) -> CompressedReviewerOutput:
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


def format_compressed_reviewer(c: CompressedReviewerOutput) -> str:
    return json.dumps({
        "verdict": c.verdict,
        "defect_count": c.defect_count,
        "summary": c.summary,
        "char_count": c.char_count,
        "artifact_ref": c.artifact_ref,
        "defects": c.defects,
    }, ensure_ascii=False)


def format_compressed_dev_lead(c: CompressedDevLeadOutput) -> str:
    return json.dumps({
        "tasks": c.tasks,
        "deliverables": c.deliverables,
        "artifact_ref": c.artifact_ref,
        "char_count": c.char_count,
    }, ensure_ascii=False)


def format_compressed_qa(c: CompressedQASummary) -> str:
    return json.dumps({
        "verdict": c.verdict,
        "summary": c.summary,
        "char_count": c.char_count,
        "artifact_ref": c.artifact_ref,
    }, ensure_ascii=False)


def reviewer_compact_for_prompt(c: CompressedReviewerOutput) -> str:
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
