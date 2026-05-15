from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState
from backend.App.spec.application.document_graph_service import (
    validate_workspace_documents,
)

logger = logging.getLogger(__name__)


def _workspace_root(state: PipelineState) -> Path | None:
    raw = state.get("workspace_root")
    if not isinstance(raw, str) or not raw.strip():
        return None
    candidate = Path(raw.strip()).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return candidate.resolve()


def docs_validator_node(state: PipelineState) -> dict[str, Any]:
    workspace = _workspace_root(state)
    if workspace is None:
        logger.warning(
            "docs_validator: no workspace_root in state — skipping validation"
        )
        return {"docs_validator_output": "skipped: no workspace_root configured"}
    report = validate_workspace_documents(workspace)
    payload = report.to_dict()
    logger.info(
        "docs_validator: workspace=%s verdict=%s errors=%d warnings=%d",
        workspace,
        payload["verdict"],
        payload["errors"],
        payload["warnings"],
    )
    summary_lines = [
        f"verdict: {payload['verdict']}",
        f"errors: {payload['errors']}",
        f"warnings: {payload['warnings']}",
    ]
    for finding in payload["findings"]:
        summary_lines.append(
            f"- [{finding['severity']}] {finding['check']} ({finding['spec_id']}): {finding['detail']}"
        )
    return {
        "docs_validator_output": "\n".join(summary_lines),
        "docs_validator_report": payload,
    }


__all__ = ["docs_validator_node"]
