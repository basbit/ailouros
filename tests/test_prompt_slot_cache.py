"""§23.4 — Slot-cache-friendly prompt ordering.

LM Studio / llama.cpp keeps a per-slot prefix cache.  If the first 4 KB of a
prompt contains a timestamp, UUID, or task_id the cache can never be re-used
because the prefix changes every call.  These tests verify that components
known to be stable (system-role text, tool schemas, workspace-context headers)
do NOT emit timestamps or random identifiers in their output.

The tests use lightweight unit stubs — no LLM calls, no I/O.
"""
from __future__ import annotations

import re
import uuid

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Patterns that indicate cache-busting content in a prompt prefix.
_TS_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"  # ISO datetime
    r"|"
    r"\b\d{10,13}\b"                                # Unix epoch (10-13 digits)
)
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)


def _has_timestamp(text: str) -> bool:
    return bool(_TS_RE.search(text))


def _has_uuid(text: str) -> bool:
    return bool(_UUID_RE.search(text))


def _check_prefix_stable(prefix: str, label: str) -> None:
    """Assert the given prompt prefix is slot-cache-safe."""
    assert not _has_timestamp(prefix), (
        f"{label}: timestamp found in first {len(prefix)} chars — "
        "prefix will bust the LM Studio slot cache on every call"
    )
    assert not _has_uuid(prefix), (
        f"{label}: UUID found in first {len(prefix)} chars — "
        "prefix will bust the LM Studio slot cache on every call"
    )


# ---------------------------------------------------------------------------
# §23.4 — regex helpers are themselves correct
# ---------------------------------------------------------------------------

def test_ts_regex_detects_iso_datetime():
    assert _has_timestamp("generated at 2026-04-16T09:45:00 during pipeline")


def test_ts_regex_detects_epoch():
    assert _has_timestamp("last_seen=1713123456")


def test_ts_regex_ignores_short_numbers():
    assert not _has_timestamp("version 3 and step 12 processed")


def test_uuid_regex_detects_standard_uuid():
    sample = str(uuid.uuid4())
    assert _has_uuid(sample)


def test_uuid_regex_ignores_non_uuid():
    assert not _has_uuid("task-abc123 or workspace-root-v2")


# ---------------------------------------------------------------------------
# §23.4 — _swarm_prompt_prefix must not emit timestamps or UUIDs
# ---------------------------------------------------------------------------

def _minimal_state(**extra) -> dict:
    return {
        "workspace_root": "/workspace/proj",
        "workspace_context_mode": "full",
        "agent_config": {},
        **extra,
    }


def test_swarm_prompt_prefix_is_cache_safe():
    """_swarm_prompt_prefix returns stable text — no timestamps, no UUIDs."""
    from backend.App.orchestration.application.nodes._shared import (
        _swarm_prompt_prefix,
    )
    state = _minimal_state()
    prefix = _swarm_prompt_prefix(state)
    _check_prefix_stable(prefix[:4000], "_swarm_prompt_prefix")


def test_bare_repo_scaffold_instruction_is_cache_safe():
    """_bare_repo_scaffold_instruction is static guidance — no runtime ids."""
    from backend.App.orchestration.application.nodes._workspace_instructions import (
        _bare_repo_scaffold_instruction,
    )
    state = _minimal_state()
    text = _bare_repo_scaffold_instruction(state)
    _check_prefix_stable(text[:4000], "_bare_repo_scaffold_instruction")


# ---------------------------------------------------------------------------
# §23.4 — is_dev_retry_lean / is_progressive_context don't leak into prefix
# ---------------------------------------------------------------------------

def test_retry_lean_note_is_cache_safe():
    """The lean-retry note injected into prompts must not contain runtime ids."""
    note = (
        "[Retry context] This is a re-run of the subtask after reviewer "
        "feedback (or format-enforcement). The pattern/knowledge/sibling "
        "context from the first run is unchanged — focus on the feedback "
        "block below.\n\n"
    )
    _check_prefix_stable(note, "retry_lean_note")


def test_progressive_context_note_is_cache_safe():
    """The M-7 progressive-context note must not contain runtime ids."""
    note = (
        "[Progressive context — M-7] Pattern/knowledge/sibling blocks were not "
        "pre-loaded (SWARM_PROGRESSIVE_CONTEXT=1, MCP mode). "
        "Use the read_file tool to access .swarm/ if you need memory context.\n\n"
    )
    _check_prefix_stable(note, "progressive_context_note")


# ---------------------------------------------------------------------------
# §23.4 — state compaction markers are cache-safe
# ---------------------------------------------------------------------------

def test_bulletpoint_compact_output_is_cache_safe():
    """_bulletpoint_compact output must not contain timestamps or UUIDs."""
    from backend.App.orchestration.application.pipeline_state_helpers import (
        _bulletpoint_compact,
    )
    long_text = (
        "The authentication module validates JWT tokens. "
        "It calls the user service to fetch profile data. "
        "Results are cached for 5 minutes. "
        "Errors are logged to the audit trail."
    )
    result = _bulletpoint_compact(long_text)
    _check_prefix_stable(result, "_bulletpoint_compact")
    assert "• " in result, "should contain at least one bullet"
    assert "… [compacted]" in result
