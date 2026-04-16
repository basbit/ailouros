"""Per-agent context budgets (H-1 from ``docs/future-plan.md``).

A single :class:`ContextBudget` dataclass that holds every per-section
character cap a pipeline step needs to assemble its prompt and bound
its working state. Replaces the previous mix of:

- the small ``_DEFAULT_CONTEXT_BUDGET`` dict (3 fields: ``wiki_chars``,
  ``knowledge_chars``, ``include_summaries``) in ``_prompt_builders.py``;
- the orthogonal ``_context_budget_profile`` (3 fields:
  ``code_analysis_max_chars``, ``code_analysis_max_files``,
  ``fix_cycle_summary_max_chars``);
- the per-call ``max_chars`` arguments hard-coded inside
  ``format_pattern_memory_block`` / ``format_cross_task_memory_block``;
- the single global ``SWARM_STATE_MAX_CHARS`` ceiling used by
  ``_compact_state_if_needed``.

Resolution order (per field, first non-empty wins)
--------------------------------------------------

1. ``SWARM_CONTEXT_<FIELD>_<STEP>`` env var (most specific).
2. ``SWARM_CONTEXT_<FIELD>`` env var (global override).
3. ``agent_config['swarm']['context_budgets'][step_id][field]``.
4. ``agent_config['swarm']['context_budgets']['default'][field]``.
5. Step-specific entry in :data:`context_budget_profiles.json` (shipped
   default; operators may edit / replace this file).
6. Tier entry (key ending with ``_``, e.g. ``review_``, ``human_``,
   ``crole_``) in the same JSON.
7. :data:`DEFAULT_BUDGET` — the conservative fallback.

The split between code and config matters for ``review-rules.md §3``:
the code holds *no* per-role workflow knowledge — values like "PM gets
8000 wiki chars" live entirely in the JSON profile file, which can be
swapped or extended without touching Python.

Backwards compatibility
-----------------------

The dict returned by :func:`context_budget_as_dict` always contains
**all** :class:`ContextBudget` fields (so new callers can use them) plus
the three legacy keys ``wiki_chars``, ``knowledge_chars`` and
``include_summaries`` — these legacy keys are *also* fields on
:class:`ContextBudget`, so no aliasing is needed.

Older ``agent_config['swarm']['context_budgets']`` values that only set
the legacy three fields continue to apply: any field name listed in
:class:`ContextBudget` is consumed; unknown keys are ignored.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ContextBudget:
    """All character caps a pipeline step uses while assembling its prompt.

    Fields are intentionally flat so the dataclass is trivially
    serialisable to JSON / YAML / agent_config without nested types.
    """

    # ── Memory layers ────────────────────────────────────────────────
    wiki_chars: int                  # injected wiki memory block
    pattern_memory_chars: int        # PatternMemory hits block
    cross_task_memory_chars: int     # CrossTaskMemory episodes block
    knowledge_chars: int             # project knowledge / doc-source block

    # ── Pipeline narrative ───────────────────────────────────────────
    summaries_chars: int             # per-summary truncation in [Pipeline context]
    include_summaries: bool          # turn the previous-agents block off entirely

    # ── Code / workspace ─────────────────────────────────────────────
    code_analysis_chars: int         # static code-analysis JSON
    code_analysis_max_files: int     # number of files in compact analysis
    fix_cycle_summary_chars: int     # fix-cycle summary block

    # ── Working state ceiling ────────────────────────────────────────
    state_max_chars: int             # _compact_state_if_needed threshold

    # ── LLM thinking-budget cap (local reasoning models only) ────────
    # Forwarded as ``extra_body.thinking_budget_tokens`` for qwen3* /
    # deepseek-r1 / *-ud-mlx. 0 = skip the injection entirely (let the
    # model / server default apply). Non-reasoning models ignore the field.
    reasoning_budget_tokens: int


# Conservative defaults that match the previous (un-budgeted) behaviour.
# Any field not overridden by a profile / config / env still produces
# exactly the values shipped before H-1 — so a freshly-loaded swarm
# behaves identically to the old code path when no profile applies.
DEFAULT_BUDGET: ContextBudget = ContextBudget(
    wiki_chars=6000,
    pattern_memory_chars=6000,
    cross_task_memory_chars=8000,
    knowledge_chars=2500,
    summaries_chars=300,
    include_summaries=True,
    code_analysis_chars=12_000,
    code_analysis_max_files=120,
    fix_cycle_summary_chars=4_000,
    state_max_chars=200_000,
    # Matches the historical router.py default. Profiles tighten this for
    # Dev/QA (narrow subtasks) and review_* (reviewers don't need 4K of
    # thinking); Architect/Judge/Debate keep the full cap.
    reasoning_budget_tokens=4096,
)


_FIELD_SET: frozenset[str] = frozenset(f.name for f in fields(ContextBudget))


# Pre-H-1 env var names that operators may already have set in their
# deployments. We honour them in addition to the canonical
# ``SWARM_CONTEXT_<FIELD>(_<STEP>)`` form so this change doesn't silently
# break anyone's tuning. The canonical name still wins when both are set.
_LEGACY_ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "include_summaries": ("SWARM_CONTEXT_SUMMARIES",),
    # Pre-H-1 global state ceiling. Honoured as a fallback so existing
    # deployments keep working; the per-step canonical
    # ``SWARM_CONTEXT_STATE_MAX_CHARS(_<STEP>)`` still wins.
    "state_max_chars": ("SWARM_STATE_MAX_CHARS",),
}


_PROFILES_FILENAME = "context_budget_profiles.json"


def _coerce(field_name: str, raw: Any) -> Any:
    """Coerce a config / env value to the target field's Python type."""
    if field_name == "include_summaries":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Invalid context-budget value for {field_name}={raw!r}: expected int. {exc}"
        ) from exc


def _apply_overrides(budget: ContextBudget, overrides: Any) -> ContextBudget:
    """Apply field overrides from a dict, ignoring unknown keys."""
    if not isinstance(overrides, dict):
        return budget
    updates: dict[str, Any] = {}
    for key, value in overrides.items():
        if key in _FIELD_SET and value is not None:
            updates[key] = _coerce(key, value)
    return replace(budget, **updates) if updates else budget


def _profiles_path() -> Path:
    """Path to the shipped role / tier profile JSON.

    Operators can override by setting ``SWARM_CONTEXT_BUDGET_PROFILES``
    to an absolute path; useful for forks that want to swap defaults
    without forking the repo.
    """
    override = os.getenv("SWARM_CONTEXT_BUDGET_PROFILES", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).parent / _PROFILES_FILENAME


def _load_profiles() -> tuple[dict[str, ContextBudget], dict[str, ContextBudget]]:
    """Load (step_profiles, tier_profiles) from the shipped JSON file.

    *step_profiles* is keyed by exact ``step_id`` (``"pm"``, ``"dev"``…).
    *tier_profiles* is keyed by prefix (``"review_"``, ``"human_"``…).

    Returns two empty dicts on missing or invalid file — the caller will
    then fall back to :data:`DEFAULT_BUDGET` for every step. We log a
    warning rather than raise so a malformed user-supplied file can't
    take down the whole pipeline; the failure is loud but recoverable.
    """
    path = _profiles_path()
    if not path.is_file():
        logger.debug("context_budget: no profiles file at %s — using DEFAULT_BUDGET only", path)
        return {}, {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "context_budget: failed to load %s (%s); using DEFAULT_BUDGET only",
            path, exc,
        )
        return {}, {}
    if not isinstance(raw, dict):
        logger.warning(
            "context_budget: %s is not a JSON object; using DEFAULT_BUDGET only", path,
        )
        return {}, {}

    step_profiles: dict[str, ContextBudget] = {}
    tier_profiles: dict[str, ContextBudget] = {}
    for key, fields_dict in raw.items():
        if not isinstance(key, str) or key.startswith("_"):
            continue  # skip metadata keys (_comment, _schema, …)
        if not isinstance(fields_dict, dict):
            logger.warning("context_budget: profile %r is not a dict; skipped", key)
            continue
        try:
            profile = _apply_overrides(DEFAULT_BUDGET, fields_dict)
        except ValueError as exc:
            logger.warning("context_budget: profile %r has invalid value (%s); skipped", key, exc)
            continue
        if key.endswith("_"):
            tier_profiles[key] = profile
        else:
            step_profiles[key] = profile
    logger.info(
        "context_budget: loaded %d step profile(s) + %d tier profile(s) from %s",
        len(step_profiles), len(tier_profiles), path,
    )
    return step_profiles, tier_profiles


# Loaded once at import. Tests reset via :func:`reload_profiles`.
_STEP_PROFILES, _TIER_PROFILES = _load_profiles()


def reload_profiles() -> None:
    """Re-read the JSON profile file. Used by tests + for live ops tweaks."""
    global _STEP_PROFILES, _TIER_PROFILES
    _STEP_PROFILES, _TIER_PROFILES = _load_profiles()


def _profile_for(step_id: str) -> ContextBudget:
    profile = _STEP_PROFILES.get(step_id)
    if profile is not None:
        return profile
    for prefix, tier in _TIER_PROFILES.items():
        if step_id.startswith(prefix):
            return tier
    return DEFAULT_BUDGET


def _apply_env_overrides(budget: ContextBudget, step_id: str) -> ContextBudget:
    """Honour ``SWARM_CONTEXT_<FIELD>(_<STEP>)`` env vars.

    Step-specific takes precedence over global so deployments can pin a
    knob for one step without disturbing the rest. Legacy aliases (see
    :data:`_LEGACY_ENV_ALIASES`) are checked last so existing
    deployments don't lose their current tuning, but the canonical name
    always wins.
    """
    updates: dict[str, Any] = {}
    step_key = step_id.upper()
    for field_name in _FIELD_SET:
        env_base = f"SWARM_CONTEXT_{field_name.upper()}"
        candidates: list[str] = [f"{env_base}_{step_key}", env_base]
        for legacy_base in _LEGACY_ENV_ALIASES.get(field_name, ()):
            candidates.extend([f"{legacy_base}_{step_key}", legacy_base])
        for candidate in candidates:
            raw = os.environ.get(candidate, "").strip()
            if raw:
                try:
                    updates[field_name] = _coerce(field_name, raw)
                except ValueError as exc:
                    raise ValueError(f"{candidate}: {exc}") from exc
                break
    return replace(budget, **updates) if updates else budget


def get_context_budget(
    step_id: str,
    agent_config: Optional[dict[str, Any]] = None,
) -> ContextBudget:
    """Resolve the :class:`ContextBudget` for *step_id*.

    See module docstring for the full resolution order.
    """
    budget = _profile_for(step_id)

    # Layer 1: agent_config defaults + per-step overrides.
    if isinstance(agent_config, dict):
        swarm_cfg = agent_config.get("swarm")
        if isinstance(swarm_cfg, dict):
            budgets_cfg = swarm_cfg.get("context_budgets")
            if isinstance(budgets_cfg, dict):
                budget = _apply_overrides(budget, budgets_cfg.get("default"))
                budget = _apply_overrides(budget, budgets_cfg.get(step_id))

    # Layer 2: env overrides — most specific, last to apply.
    return _apply_env_overrides(budget, step_id)


def context_budget_as_dict(budget: ContextBudget) -> dict[str, Any]:
    """Return a plain ``dict`` snapshot of *budget*.

    Includes every :class:`ContextBudget` field. The legacy three keys
    (``wiki_chars``, ``knowledge_chars``, ``include_summaries``) are
    fields on the dataclass, so legacy callers that look those up by
    name keep working without translation.
    """
    return asdict(budget)


__all__ = [
    "ContextBudget",
    "DEFAULT_BUDGET",
    "context_budget_as_dict",
    "get_context_budget",
    "reload_profiles",
]
