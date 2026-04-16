"""Workspace instruction builders for Dev and QA pipeline nodes.

Extracted from _shared.py: _dev_workspace_instructions, _qa_workspace_verification_instructions,
_bare_repo_scaffold_instruction and path-hints helper.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from backend.App.orchestration.application.pipeline_state import PipelineState
from backend.App.workspace.infrastructure.workspace_io import command_exec_allowed

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Test-path detection — configurable, no hardcoded conventions
# ---------------------------------------------------------------------------
#
# Defaults below cover the most common test layouts as a starting point only.
# Operators MUST override via env vars or agent_config when their project uses
# a non-standard layout (§1: no hardcoded conventions).  Defaults are
# documented and overrideable, not embedded knowledge.
#
# Env vars (comma-separated):
#   SWARM_TEST_PATH_SUBSTRINGS   — substrings searched anywhere in path
#   SWARM_TEST_PATH_SEGMENTS     — exact path segments (between slashes)
#   SWARM_TEST_PATH_NAME_PREFIXES — basename prefixes
#   SWARM_TEST_PATH_STEM_SUFFIXES — basename-without-ext suffixes
#
# An empty value disables that category entirely.

_DEFAULT_TEST_SUBSTRINGS = ".test.,.spec."
_DEFAULT_TEST_SEGMENTS = "__tests__,tests,spec,specs"
_DEFAULT_TEST_NAME_PREFIXES = "test_"
_DEFAULT_TEST_STEM_SUFFIXES = "_test"


def _csv_env(name: str, default: str) -> tuple[str, ...]:
    raw = os.environ.get(name)
    if raw is None:
        raw = default
    return tuple(item.strip().lower() for item in raw.split(",") if item.strip())


def _workspace_root_str(state: PipelineState) -> str:
    return str(state.get("workspace_root") or "").strip()


def _path_hints_automated_tests(rel_path: str) -> bool:
    """Heuristic: does *rel_path* look like a test/spec file?

    Patterns are read from env vars at call time so operators can tune the
    detection per project without editing code.  See module docstring for
    the env var names.  Defaults are a starting point, not project policy.
    """
    p = rel_path.lower().replace("\\", "/")

    substrings = _csv_env("SWARM_TEST_PATH_SUBSTRINGS", _DEFAULT_TEST_SUBSTRINGS)
    if any(s in p for s in substrings):
        return True

    parts = [x for x in p.split("/") if x]
    segments = _csv_env("SWARM_TEST_PATH_SEGMENTS", _DEFAULT_TEST_SEGMENTS)
    if any(seg in segments for seg in parts):
        return True

    name = parts[-1] if parts else p
    base = name.rsplit(".", 1)[0] if "." in name else name
    name_prefixes = _csv_env("SWARM_TEST_PATH_NAME_PREFIXES", _DEFAULT_TEST_NAME_PREFIXES)
    stem_suffixes = _csv_env("SWARM_TEST_PATH_STEM_SUFFIXES", _DEFAULT_TEST_STEM_SUFFIXES)
    if any(name.startswith(pref) for pref in name_prefixes):
        return True
    if any(base.endswith(suf) for suf in stem_suffixes):
        return True
    return False


def _bare_repo_scaffold_instruction(state: PipelineState) -> str:
    """Подсказка из code_analysis: в снимке мало признаков автотестов — без привязки к конкретному стеку."""
    if not (state.get("workspace_root") or "").strip():
        return ""
    if not bool(state.get("workspace_apply_writes")):
        return ""
    _ca_raw = state.get("code_analysis")
    ca: dict[str, Any] = _ca_raw if isinstance(_ca_raw, dict) else {}
    files = ca.get("files")
    if not isinstance(files, list) or not files:
        return ""
    paths: list[str] = []
    for f in files:
        if isinstance(f, dict) and f.get("path"):
            paths.append(str(f["path"]).replace("\\", "/"))
    if not paths:
        return ""
    if any(_path_hints_automated_tests(x) for x in paths):
        return ""

    lines = [
        "\n\n[Orchestrator — static scan shows little or no automated test layout]",
        "If the specification expects verifiable quality gates, the **first** Dev subtask should add "
        "the **minimal dependency + test (and if applicable lint/typecheck) scaffolding** that matches "
        "the **Architect stack** and this repository — not a copy-paste template from another ecosystem. "
        "Use `<swarm_file>` / `<swarm_patch>`; DevOps uses `<swarm_shell>` only after operator confirmation "
        "when command execution is enabled.",
        "Required on the server for real writes/commands: `SWARM_ALLOW_WORKSPACE_WRITE=1`, "
        "`SWARM_ALLOW_COMMAND_EXEC=1`, and shell-gate confirmation in the UI for each command block.",
    ]
    return "\n".join(lines) + "\n"


def _dev_workspace_instructions(state: PipelineState) -> str:
    wr = (state.get("workspace_root") or "").strip()
    if not wr:
        return ""
    part = (
        "\n\n[Local project on the orchestrator host]\n"
        f"Root: {wr}\n"
        "Writing to disk (path relative to root, no ..):\n"
        "1) Full file replacement — use ONLY for new files or when a whole-file rewrite is truly required:\n"
        '<swarm_file path="relative/path.ext">\n'
        "full file contents\n"
        "</swarm_file>\n"
        "2) Preferred for existing files: partial edit (SEARCH must appear in the file exactly once):\n"
        '<swarm_patch path="relative/path.ext">\n'
        "<<<<<<< SEARCH\n"
        "old fragment\n"
        "=======\n"
        "new fragment\n"
        ">>>>>>> REPLACE\n"
        "</swarm_patch>\n"
        "Multiple hunks — multiple SEARCH/…/REPLACE blocks in sequence inside one swarm_patch.\n"
        "New file via patch: first SEARCH is empty, REPLACE = full contents.\n"
        "If the file already exists, prefer `<swarm_patch>` over `<swarm_file>` to minimize regressions.\n"
        "3) Unified diff (fewer tokens than a full file; requires ``patch``):\n"
        '<swarm_udiff path="relative/path.ext">\n'
        "--- a/relative/path.ext\n+++ b/relative/path.ext\n@@ -1,3 +1,3 @@\n …\n"
        "</swarm_udiff>\n"
        "4) Commands — ONLY as bare tags in the response text, NEVER inside ```bash or other fences:\n"
        "<swarm_shell>\n"
        "<one command per line; each token must match SWARM_SHELL_ALLOWLIST>\n"
        "</swarm_shell>\n"
        "5) MCP: ``agent_config.mcp.servers`` (stdio) — Dev/QA, OpenAI-compatible tool_calls; "
        "see docs/AIlourOS.md.\n"
    )
    apply_writes = bool(state.get("workspace_apply_writes"))
    cmd_exec = command_exec_allowed()

    if apply_writes:
        part += (
            "File writing ENABLED (SWARM_ALLOW_WORKSPACE_WRITE=1); "
            "<swarm_file>, <swarm_patch>, <swarm_udiff> blocks will be applied under the project root.\n"
            "Do **not** insert an 'example' like `<swarm_file path=\"…\">...</swarm_file>` in regular text — "
            "the orchestrator will treat it as a real write and **overwrite** files already produced by Dev. "
            "Describe format examples in words or without the actual tags.\n"
            "The `<swarm_shell>` tag must appear **only as bare text** in the response — NEVER inside ```bash, ```xml, "
            "```text or any other fences, otherwise commands will not reach the orchestrator.\n"
        )
    else:
        part += (
            "Automatic file writing DISABLED in this request — "
            "blocks are for manual application only.\n"
        )
    if cmd_exec:
        part += (
            "Command execution ENABLED (SWARM_ALLOW_COMMAND_EXEC=1); "
            "<swarm_shell> blocks run in the project root **after UI confirmation** (shell-gate), "
            "after writing files from the same response. "
            "Use only commands allowed by `SWARM_SHELL_ALLOWLIST` and appropriate for the **Architect** stack "
            "(dependency install, codegen, test runners, etc.).\n"
        )
    else:
        part += (
            "Command execution DISABLED on the server — "
            "use <swarm_shell> only as instructions for the operator (will not be executed automatically).\n"
        )
    return part + _bare_repo_scaffold_instruction(state)


def _qa_workspace_verification_instructions(state: PipelineState) -> str:
    """QA: not just 'how to test', but run checks via swarm_shell where possible."""
    wr = (state.get("workspace_root") or "").strip()
    if not wr:
        return ""
    bare = _bare_repo_scaffold_instruction(state)
    apply_writes = bool(state.get("workspace_apply_writes"))
    cmd_exec = command_exec_allowed()
    part = (
        bare
        + "\n\n[Workspace verification]\n"
        "Do not end with only an abstract checklist of 'how to test': where possible "
        "**execute** checks in the repository.\n"
    )
    if apply_writes and cmd_exec:
        part += (
            "With `SWARM_ALLOW_COMMAND_EXEC` enabled, add `<swarm_shell>` blocks (one command per line) using only "
            "binaries allowed by `SWARM_SHELL_ALLOWLIST` — the orchestrator runs them from the project root **after "
            "your UI confirmation**. Pick commands that match the **Architect** stack and repo layout (correct cwd / "
            "subproject if the spec says so). Include the **verification result**: what ran, exit code / brief log "
            "(or `artifacts/<task>/pipeline.json` → `shell_runs`). "
            "Do not assume a web stack: UI/E2E tools must fit the declared platform (API-only, mobile, desktop, browser, …).\n"
        )
    elif apply_writes:
        part += (
            "Add or edit **test files** via `<swarm_file>` / `<swarm_patch>`. "
            "Automatic command execution — only when `SWARM_ALLOW_COMMAND_EXEC=1` on the server.\n"
        )
    else:
        part += (
            "In this request `workspace_write` is disabled — describe the checks and if needed provide "
            "test snippets in `<swarm_file>` for manual copying.\n"
        )
    return part
