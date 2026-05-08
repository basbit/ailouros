from __future__ import annotations

import logging
import os
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

from backend.App.shared.infrastructure.app_config_load import load_app_config_json

logger = logging.getLogger(__name__)


def _policy() -> dict[str, Any]:
    value = load_app_config_json("pipeline_enforcement_policy.json").get(
        "incremental_workspace_writes",
    )
    if not isinstance(value, dict):
        raise RuntimeError("pipeline_enforcement_policy.incremental_workspace_writes is not configured")
    return value


def _string_list(key: str) -> list[str]:
    value = _policy().get(key)
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def workspace_write_roles() -> frozenset[str]:
    policy = _policy()
    environment_key = str(policy.get("write_roles_environment_key") or "").strip()
    environment_value = os.getenv(environment_key, "").strip() if environment_key else ""
    if environment_value:
        return frozenset(role.strip() for role in environment_value.split(",") if role.strip())
    return frozenset(_string_list("write_roles_default"))


def stream_incremental_workspace_enabled() -> bool:
    policy = _policy()
    environment_key = str(policy.get("stream_enabled_environment_key") or "").strip()
    environment_value = os.getenv(environment_key, "").strip().lower() if environment_key else ""
    if environment_value:
        enabled_values = {
            str(value).strip().lower()
            for value in _string_list("enabled_environment_values")
        }
        return environment_value in enabled_values
    return bool(policy.get("stream_enabled_default"))


@contextmanager
def incremental_workspace_write_context():
    from backend.App.workspace.infrastructure.workspace_io import scoped_runtime_shell_allowlist
    with scoped_runtime_shell_allowlist():
        yield


def should_apply_incremental_write(
    agent: str,
    agent_output: str,
    workspace_path: Optional[Path],
    workspace_apply_writes: bool,
) -> bool:
    from backend.App.workspace.infrastructure.workspace_io import workspace_write_allowed
    return bool(
        workspace_path
        and workspace_apply_writes
        and workspace_write_allowed()
        and agent in workspace_write_roles()
        and agent_output.strip()
    )


def apply_incremental_workspace_write(
    agent: str,
    agent_output: str,
    workspace_path: Path,
    task_id: str,
    task_store: Any,
    cancel_event: Optional[threading.Event],
) -> Generator[str, None, dict[str, Any]]:
    from backend.App.workspace.infrastructure.workspace_io import (
        _shell_allowlist,
        command_exec_allowed,
        extend_runtime_shell_allowlist,
        extract_command_binary,
    )
    from backend.App.workspace.infrastructure.patch_parser import (
        apply_workspace_pipeline,
        blocked_workspace_write_result,
        extract_shell_commands,
        validate_workspace_pipeline_before_apply,
    )
    from backend.App.orchestration.infrastructure.shell_approval import request_shell_approval
    from backend.App.orchestration.infrastructure.manual_shell_approval import request_manual_execution

    run_shell_flag = False
    if command_exec_allowed():
        shell_cmds = extract_shell_commands(agent_output)
        sudo_cmds = [c for c in shell_cmds if (extract_command_binary(c) or "") == "sudo"]
        if sudo_cmds:
            shell_cmds = [c for c in shell_cmds if c not in sudo_cmds]
            sudo_preview = ", ".join(f"`{c}`" for c in sudo_cmds[:3])
            if len(sudo_cmds) > 3:
                sudo_preview += f" … (+{len(sudo_cmds) - 3})"
            yield (
                f"[orchestrator] Cannot run {len(sudo_cmds)} sudo "
                f"command(s) — asking user to run manually: {sudo_preview}\n"
            )
            manual_done = request_manual_execution(
                task_id,
                sudo_cmds,
                task_store,
                reason=(
                    "sudo is not supported by the automated shell "
                    "(no TTY / password prompt). "
                    "Please run these commands yourself in your terminal."
                ),
                cancel_event=cancel_event,
            )
            yield (
                f"[orchestrator] user "
                f"{'confirmed manual execution' if manual_done else 'cancelled (command not run)'}: "
                f"{sudo_preview}\n"
            )

        if shell_cmds:
            env_allow = _shell_allowlist()
            already_allowed: list[str] = []
            needs_allowlist: list[str] = []
            for cmd in shell_cmds:
                binary = extract_command_binary(cmd)
                if not binary:
                    continue
                if binary in env_allow:
                    if binary not in already_allowed:
                        already_allowed.append(binary)
                else:
                    if binary not in needs_allowlist:
                        needs_allowlist.append(binary)

            preview = ", ".join(f"`{c}`" for c in shell_cmds[:5])
            if len(shell_cmds) > 5:
                preview += f" … (+{len(shell_cmds) - 5})"
            role_label = "devops" if agent == "devops" else "dev"
            allowlist_suffix = (
                " [requires allowlist extension: " + ", ".join(needs_allowlist) + "]"
                if needs_allowlist
                else ""
            )
            yield (
                f"[orchestrator] {role_label} requests to execute "
                f"{len(shell_cmds)} command(s): "
                f"{preview}{allowlist_suffix} — awaiting approval…\n"
            )
            approved = request_shell_approval(
                task_id,
                shell_cmds,
                task_store,
                cancel_event=cancel_event,
                needs_allowlist=needs_allowlist,
                already_allowed=already_allowed,
            )
            run_shell_flag = approved
            if approved and needs_allowlist:
                extend_runtime_shell_allowlist(needs_allowlist)
                logger.info(
                    "shell approval: extended runtime allowlist for task=%s with %s",
                    task_id,
                    needs_allowlist,
                )
            yield (
                f"[orchestrator] shell {'approved' if approved else 'rejected'} by user"
                + (
                    f" (allowlist extended: {', '.join(needs_allowlist)})"
                    if approved and needs_allowlist
                    else ""
                )
                + "\n"
            )
            task_store.update_task(
                task_id,
                status="in_progress",
                agent=agent,
                message="continuing after shell-gate",
            )

    validation = validate_workspace_pipeline_before_apply(agent_output, workspace_path)
    if not validation.get("ok"):
        partial = blocked_workspace_write_result(validation)
        yield (
            f"[orchestrator] incremental workspace after {agent}: "
            "pre-validation failed; no writes applied. "
            f"errors={partial.get('errors')!r}\n"
        )
    else:
        partial = apply_workspace_pipeline(agent_output, workspace_path, run_shell=run_shell_flag)
    yield (
        f"[orchestrator] incremental workspace after {agent}: "
        f"written={partial.get('written')!r} "
        f"patched={partial.get('patched')!r} "
        f"parsed={partial.get('parsed')} "
        f"errors={partial.get('errors')!r}\n"
    )

    for shell_run in partial.get("shell_runs") or []:
        cmd = shell_run.get("cmd", "")
        if shell_run.get("skipped"):
            sr_line = f"[shell] skipped: {cmd} ({shell_run.get('reason', '')})\n"
        elif shell_run.get("dry_run"):
            sr_line = f"[shell] dry-run: {cmd}\n"
        elif shell_run.get("error"):
            sr_line = f"[shell] error: {cmd} → {shell_run['error']}\n"
        else:
            rc = shell_run.get("returncode", 0)
            out_snippet = (shell_run.get("stdout") or "")[:200].strip()
            sr_line = (
                f"[shell] {'OK' if rc == 0 else f'exit {rc}'}: {cmd}"
                + (f"\n  {out_snippet}" if out_snippet else "")
                + "\n"
            )
        yield sr_line

    return partial
