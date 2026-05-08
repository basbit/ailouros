from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


_FENCED_JSON = re.compile(r"```json\s*(.*?)```", re.DOTALL | re.IGNORECASE)


@dataclass(frozen=True)
class SourceRecord:
    title: str
    url: str
    source_type: str
    why_relevant: str
    authority: str
    recency: str
    bias: str
    kept: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceMatrix:
    rows: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"rows": list(self.rows)}


def _extract_fenced_json(text: str) -> list[Any]:
    if not text:
        return []
    blocks: list[Any] = []
    for match in _FENCED_JSON.finditer(text):
        chunk = match.group(1).strip()
        try:
            parsed = json.loads(chunk)
        except (json.JSONDecodeError, ValueError):
            continue
        blocks.append(parsed)
    return blocks


def parse_sources(text: str) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for block in _extract_fenced_json(text):
        if not isinstance(block, list):
            continue
        for item in block:
            if not isinstance(item, dict):
                continue
            if "title" not in item and "url" not in item and "url_or_query" not in item:
                continue
            records.append(
                SourceRecord(
                    title=str(item.get("title") or ""),
                    url=str(item.get("url") or item.get("url_or_query") or ""),
                    source_type=str(item.get("source_type") or ""),
                    why_relevant=str(item.get("why_relevant") or ""),
                    authority=str(item.get("authority") or ""),
                    recency=str(item.get("recency") or ""),
                    bias=str(item.get("bias") or ""),
                    kept=bool(item.get("keep")) if "keep" in item
                    else bool(item.get("kept", True)),
                )
            )
    return records


def parse_evidence_matrix(text: str) -> EvidenceMatrix:
    matrix = EvidenceMatrix()
    for block in _extract_fenced_json(text):
        if isinstance(block, dict) and "matrix" in block:
            matrix_data = block.get("matrix")
            if isinstance(matrix_data, dict):
                for competitor, dimensions in matrix_data.items():
                    if not isinstance(dimensions, dict):
                        continue
                    matrix.rows.append({
                        "subject": str(competitor),
                        "values": {
                            str(key): str(value) if value is not None else "unknown"
                            for key, value in dimensions.items()
                        },
                    })
        elif isinstance(block, list):
            for item in block:
                if not isinstance(item, dict):
                    continue
                if "claim" in item and "supporting_angles" in item:
                    matrix.rows.append({
                        "subject": str(item.get("angle_id") or item.get("claim") or ""),
                        "values": {
                            "claim": str(item.get("claim") or ""),
                            "support": str(item.get("supporting_angles") or ""),
                        },
                    })
    return matrix


def parse_calculations(text: str) -> list[dict[str, Any]]:
    calculations: list[dict[str, Any]] = []
    for block in _extract_fenced_json(text):
        if isinstance(block, dict) and isinstance(block.get("metrics"), list):
            for metric in block["metrics"]:
                if isinstance(metric, dict):
                    calculations.append({
                        "name": str(metric.get("name") or ""),
                        "formula": str(metric.get("formula_pseudo") or metric.get("formula") or ""),
                        "group_by": list(metric.get("group_by") or []),
                        "filter": str(metric.get("filter_pseudo") or metric.get("filter") or ""),
                    })
    return calculations


def parse_chart_manifest(text: str) -> list[dict[str, Any]]:
    charts: list[dict[str, Any]] = []
    for block in _extract_fenced_json(text):
        if isinstance(block, dict) and isinstance(block.get("charts"), list):
            for chart in block["charts"]:
                if isinstance(chart, dict):
                    charts.append({
                        "metric": str(chart.get("metric") or ""),
                        "chart_type": str(chart.get("chart_type") or ""),
                        "x": str(chart.get("x") or ""),
                        "y": str(chart.get("y") or ""),
                        "group_by": str(chart.get("group_by") or ""),
                        "title": str(chart.get("title") or ""),
                    })
    return charts


def parse_unverified_claims(text: str) -> list[str]:
    claims: list[str] = []
    for block in _extract_fenced_json(text):
        if isinstance(block, dict) and isinstance(block.get("unverified_claims"), list):
            for claim in block["unverified_claims"]:
                if isinstance(claim, str) and claim.strip():
                    claims.append(claim.strip())
    return claims
