from __future__ import annotations

import re
from pathlib import Path

from backend.App.orchestration.application.routing.step_registry import PIPELINE_STEP_SEQUENCE


def _pipeline_options_base_ids_from_ts(ts_source: str) -> list[str]:
    marker = "export const PIPELINE_OPTIONS_BASE"
    start = ts_source.index(marker)
    end = ts_source.index("];", start)
    block = ts_source[start:end]
    return re.findall(r'\["([a-z][a-z0-9_]*)",', block)


def test_pipeline_options_base_ids_match_backend_step_sequence():
    app_root = Path(__file__).resolve().parents[2]
    ts_path = app_root / "frontend" / "src" / "shared" / "lib" / "pipeline-schema.ts"
    ts_source = ts_path.read_text(encoding="utf-8")
    frontend_ids = _pipeline_options_base_ids_from_ts(ts_source)
    backend_ids = [t[0] for t in PIPELINE_STEP_SEQUENCE]

    frontend_set = set(frontend_ids)
    backend_set = set(backend_ids)

    assert frontend_set <= backend_set, (
        "pipeline-schema.ts contains unknown step ids: "
        f"{sorted(frontend_set - backend_set)}"
    )
    assert backend_set - frontend_set == {"e2e"}, (
        "PIPELINE_OPTIONS_BASE must match PIPELINE_STEP_SEQUENCE except e2e "
        f"(UI omit). backend only: {sorted(backend_set - frontend_set)}, "
        f"frontend only: {sorted(frontend_set - backend_set)}"
    )
    assert len(frontend_ids) == len(frontend_set), (
        "duplicate step id in PIPELINE_OPTIONS_BASE: "
        f"{[x for x in frontend_ids if frontend_ids.count(x) > 1]}"
    )
