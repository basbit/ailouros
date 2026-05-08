from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

from backend.App.orchestration.application.enforcement.verification_contract import (
    expected_trusted_verification_commands as _expected_trusted_commands,
    normalize_trusted_verification_commands,
)
from backend.App.orchestration.application.pipeline.ephemeral_state import (
    ephemeral_as_dict,
)
from backend.App.orchestration.application.pipeline.pipeline_runtime_support import (
    deliverable_write_mapping,
)
from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState

_logger = logging.getLogger(__name__)


def run_post_dev_verification_gates(state: PipelineState) -> list[dict[str, Any]]:
    from backend.App.orchestration.domain.gates import (
        DevManifest,
        TRUSTED_VERIFICATION_COMMANDS,
        parse_dev_manifest,
    )
    from backend.App.orchestration.application.enforcement.gate_runner import (
        gates_passed,
        run_all_gates,
    )
    from backend.App.workspace.infrastructure.patch_parser import apply_from_devops_and_dev_outputs

    state_dict = ephemeral_as_dict(state)
    workspace_root = str(state.get("workspace_root") or "").strip()
    if not workspace_root:
        return []

    workspace_path = Path(workspace_root).resolve()
    workspace_writes: dict[str, Any] = {"written": [], "patched": [], "udiff_applied": [], "parsed": 0}

    if bool(state.get("workspace_apply_writes")):
        from backend.App.workspace.infrastructure.workspace_backup import (
            snapshot_before_writes as _snapshot_before,
            finalize_change_manifest as _finalize_manifest,
            is_versioned as _is_versioned,
        )
        changed_files_hint_raw = state.get("dev_changed_files_hint")
        changed_files_hint = (
            changed_files_hint_raw if isinstance(changed_files_hint_raw, list) else []
        )
        snapshot_paths_pre = sorted({str(path) for path in changed_files_hint})
        backup_snapshots = _snapshot_before(workspace_path, snapshot_paths_pre)
        workspace_writes = apply_from_devops_and_dev_outputs(dict(state), workspace_path, run_shell=False)
        state_dict["workspace_writes"] = workspace_writes
        all_changed_for_manifest = sorted(set(
            list(workspace_writes.get("written") or [])
            + list(workspace_writes.get("patched") or [])
            + list(workspace_writes.get("udiff_applied") or [])
        ))
        if all_changed_for_manifest:
            top_up = _snapshot_before(workspace_path, [
                rel for rel in all_changed_for_manifest
                if rel not in backup_snapshots
            ])
            backup_snapshots.update(top_up)
        change_manifest = _finalize_manifest(
            workspace_path, backup_snapshots, all_changed_for_manifest,
        )
        state_dict["workspace_change_manifest"] = change_manifest
        if not _is_versioned(workspace_path):
            existing_warnings = state_dict.get("verification_gate_warnings", "")
            unversioned_note = (
                "workspace_safety: workspace is not under version control — "
                f"backup manifest written at {change_manifest.get('manifest_path', '?')}"
            )
            state_dict["verification_gate_warnings"] = (
                f"{existing_warnings}; {unversioned_note}" if existing_warnings else unversioned_note
            )

        from backend.App.workspace.infrastructure.workspace_diff import capture_workspace_diff
        all_changed = sorted(set(
            list(workspace_writes.get("written") or [])
            + list(workspace_writes.get("patched") or [])
            + list(workspace_writes.get("udiff_applied") or [])
        ))
        state_dict["dev_workspace_diff"] = capture_workspace_diff(workspace_path, all_changed)

        write_errors = list(workspace_writes.get("errors") or [])
        if write_errors:
            error_summary = "; ".join(str(write_error) for write_error in write_errors)
            _logger.warning(
                "write_integrity_gate: %d patch/write error(s) detected — surfacing to "
                "verification_gate_warnings so QA issues NEEDS_WORK and dev retries with "
                "corrective feedback. errors=%s",
                len(write_errors), error_summary,
            )
            state_dict.setdefault("_post_write_issues", []).append(
                f"write_integrity_gate: {error_summary}"
            )
            state_dict["_dev_patch_errors_for_retry"] = list(write_errors)

        healed_patches = list(workspace_writes.get("healed_patches") or [])
        if healed_patches:
            _logger.info(
                "Post-dev verification: %d swarm_patch block(s) auto-healed to swarm_file creates: %s",
                len(healed_patches), healed_patches,
            )
            state_dict["_swarm_patch_healed_files"] = healed_patches

        binary_assets = list(workspace_writes.get("binary_assets_requested") or [])
        if binary_assets:
            _logger.warning(
                "Post-dev verification: %d binary asset(s) requested via text tag — asset pipeline not yet implemented: %s",
                len(binary_assets), binary_assets,
            )
            state_dict.setdefault("_post_write_issues", []).append(f"binary_asset_requested: {binary_assets}")
            state_dict["_binary_assets_needed"] = binary_assets

        missing_after_write = [
            rel_path for rel_path in workspace_writes.get("written") or []
            if not (workspace_path / rel_path).is_file()
        ]
        if missing_after_write:
            _logger.error(
                "Post-dev verification: %d file(s) missing after write: %s",
                len(missing_after_write), missing_after_write,
            )
            state_dict.setdefault("_post_write_issues", []).append(
                f"file_existence_check: {missing_after_write}"
            )

        if (
            workspace_writes.get("parsed", 0) == 0
            and int(cast(Any, state.get("dev_mcp_write_count")) or 0) == 0
        ):
            _logger.warning(
                "verification gate: dev step produced no detected workspace writes "
                "(patch-parse=0, mcp_writes=0) — continuing; QA will validate final state"
            )

    mcp_write_actions = state.get("dev_mcp_write_actions")
    if isinstance(mcp_write_actions, list) and mcp_write_actions:
        merged_actions = list(workspace_writes.get("write_actions") or [])
        for action in mcp_write_actions:
            if isinstance(action, dict) and action not in merged_actions:
                merged_actions.append(action)
        workspace_writes["write_actions"] = merged_actions

    dev_output = str(state.get("dev_output") or "")
    manifest = parse_dev_manifest(dev_output)
    if manifest is None:
        changed_files: list[str] = []
        for key in ("written", "patched", "udiff_applied"):
            for rel in workspace_writes.get(key, []) or []:
                if rel not in changed_files:
                    changed_files.append(rel)
        manifest = DevManifest(changed_files=changed_files)
    if not manifest.changed_files and workspace_writes.get("written"):
        manifest.changed_files = list(workspace_writes.get("written") or [])

    expected_commands = _expected_trusted_commands(state)
    if expected_commands:
        unknown_commands = [
            entry["command"]
            for entry in expected_commands
            if entry["command"] not in TRUSTED_VERIFICATION_COMMANDS
        ]
        if unknown_commands:
            _logger.warning(
                "verification contract: unknown trusted verification commands %s (allowed=%s) — skipping unknown",
                unknown_commands, list(TRUSTED_VERIFICATION_COMMANDS),
            )
            expected_commands = [e for e in expected_commands if e["command"] not in unknown_commands]
        manifest_trusted_commands = normalize_trusted_verification_commands(
            manifest.to_dict().get("trusted_verification_commands")
        )
        if manifest_trusted_commands and manifest_trusted_commands != expected_commands:
            _logger.warning(
                "verification contract: dev manifest trusted_verification_commands do not match "
                "deliverables_artifact.verification_commands — using deliverables version"
            )
        manifest.trusted_verification_commands = list(expected_commands)

    must_exist_files = state.get("must_exist_files") if isinstance(state.get("must_exist_files"), list) else None
    spec_symbols = state.get("spec_symbols") if isinstance(state.get("spec_symbols"), list) else None
    production_paths = state.get("production_paths") if isinstance(state.get("production_paths"), list) else None
    placeholder_allow_list = (
        state.get("placeholder_allow_list") if isinstance(state.get("placeholder_allow_list"), list) else None
    )

    results = run_all_gates(
        workspace_root,
        manifest=manifest,
        must_exist_files=must_exist_files,
        spec_symbols=spec_symbols,
        production_paths=production_paths,
        stub_allow_list=cast(Any, placeholder_allow_list),
        workspace_writes=workspace_writes,
    )
    gate_names_run = [result.gate_name for result in results]
    missing_trusted = [entry for entry in expected_commands if entry["command"] not in gate_names_run]
    if missing_trusted:
        _logger.warning(
            "verification contract: trusted verification commands declared but not run: %s — continuing",
            [entry["command"] for entry in missing_trusted],
        )

    state_dict["verification_gates"] = [result.to_dict() for result in results]
    state_dict["dev_manifest"] = manifest.to_dict()
    state_dict["verification_contract"] = {
        "expected_trusted_commands": expected_commands,
        "manifest_trusted_commands": list(manifest.trusted_verification_commands),
        "gates_run": gate_names_run,
    }
    state_dict["deliverable_write_mapping"] = deliverable_write_mapping(state)

    post_write_issues = state_dict.pop("_post_write_issues", None)
    if post_write_issues:
        integrity_summary = "; ".join(post_write_issues)
        existing_warnings = state_dict.get("verification_gate_warnings", "")
        state_dict["verification_gate_warnings"] = (
            f"{existing_warnings}; {integrity_summary}" if existing_warnings else integrity_summary
        )
        _logger.warning("Post-write integrity issues added to gate warnings: %s", integrity_summary)

    from backend.App.orchestration.application.enforcement.source_corruption_scanner import (
        scan_changed_files as _scan_changed_files,
        scan_agent_output_for_fake_tool_calls as _scan_agent_output,
        summarize_findings as _summarize_corruption,
    )

    changed_for_scan = sorted(set(
        list(workspace_writes.get("written") or [])
        + list(workspace_writes.get("patched") or [])
        + list(workspace_writes.get("udiff_applied") or [])
    ))
    corruption_findings = _scan_changed_files(workspace_path, changed_for_scan)
    dev_text_for_scan = str(state.get("dev_output") or "")
    if dev_text_for_scan:
        corruption_findings.extend(_scan_agent_output(dev_text_for_scan))
    if corruption_findings:
        state_dict["source_corruption_findings"] = [
            finding.to_dict() for finding in corruption_findings
        ]
        state_dict["source_corruption_summary"] = _summarize_corruption(
            corruption_findings
        )
        from backend.App.shared.application.settings_resolver import get_setting_bool
        fail_on_corruption = get_setting_bool(
            "swarm.fail_on_source_corruption",
            workspace_root=workspace_path,
            env_key="SWARM_FAIL_ON_SOURCE_CORRUPTION",
            default=True,
        )
        if fail_on_corruption:
            preview = ", ".join(
                f"{finding.path}:{finding.line} [{finding.pattern_id}]"
                for finding in corruption_findings[:5]
            )
            state_dict["_failed_trusted_gates"] = list(
                state_dict.get("_failed_trusted_gates") or []
            ) + ["source_corruption"]
            state_dict["_failed_trusted_gates_summary"] = (
                f"source_corruption: {len(corruption_findings)} marker(s) detected; {preview}"
            )

    if not gates_passed(results):
        failed_gates = [result for result in results if not result.passed]
        failure_summary = "; ".join(
            f"{gate.gate_name}: {(gate.errors or [{'error': 'failed'}])[0].get('error', 'failed')}"
            for gate in failed_gates
        )
        state_dict.setdefault("_failed_trusted_gates", [])
        existing_failed = list(state_dict.get("_failed_trusted_gates") or [])
        existing_failed.extend(gate.gate_name for gate in failed_gates)
        state_dict["_failed_trusted_gates"] = existing_failed
        existing_summary = str(state_dict.get("_failed_trusted_gates_summary") or "")
        state_dict["_failed_trusted_gates_summary"] = (
            f"{existing_summary}; {failure_summary}" if existing_summary else failure_summary
        )

        stub_gate_result = next(
            (gate for gate in failed_gates if gate.gate_name == "stub_gate"), None
        )
        if stub_gate_result is not None and production_paths:
            stub_findings_detail = "; ".join(
                f"{error.get('file', '?')}:{error.get('line', '?')} pattern={error.get('pattern', '?')}"
                for error in (stub_gate_result.errors or [])[:5]
            )
            raise RuntimeError(
                f"stub_gate_production_block: stub code detected in production paths — "
                f"dev retry required. "
                f"operation=stub_gate "
                f"production_paths={production_paths!r} "
                f"findings={stub_findings_detail!r} "
                f"expected=no placeholder patterns in production files "
                f"actual=STUB_DETECTED"
            )

        _logger.warning(
            "verification gates failed before QA: %s — continuing to QA so it can report on failures",
            failure_summary,
        )
        existing_warnings = state_dict.get("verification_gate_warnings", "")
        state_dict["verification_gate_warnings"] = (
            f"{existing_warnings}; {failure_summary}" if existing_warnings else failure_summary
        )

    return [result.to_dict() for result in results]
