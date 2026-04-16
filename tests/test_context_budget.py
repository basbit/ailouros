"""Unit tests for ``orchestration.application.context_budget`` (H-1)."""

from __future__ import annotations

import json
from dataclasses import fields

import pytest

from backend.App.orchestration.application import context_budget as cb_module
from backend.App.orchestration.application.context_budget import (
    DEFAULT_BUDGET,
    ContextBudget,
    context_budget_as_dict,
    get_context_budget,
    reload_profiles,
)


@pytest.fixture(autouse=True)
def _reset_profiles_after_test():
    """Re-load the shipped profiles after each test in case one swapped them."""
    yield
    # Clear any test-injected env override before reloading.
    import os
    os.environ.pop("SWARM_CONTEXT_BUDGET_PROFILES", None)
    reload_profiles()


# ---------------------------------------------------------------------------
# Profile selection
# ---------------------------------------------------------------------------


def test_default_budget_for_unknown_step_id():
    """Steps that aren't in any profile or tier fall back to DEFAULT_BUDGET."""
    budget = get_context_budget("totally_unknown_step")
    assert budget == DEFAULT_BUDGET


def test_role_profile_used_for_known_step():
    """``pm`` has its own profile in the shipped JSON and differs from DEFAULT_BUDGET."""
    budget = get_context_budget("pm")
    # Sanity: shipped profile gives PM a wider wiki + knowledge budget.
    assert budget.wiki_chars > DEFAULT_BUDGET.wiki_chars
    assert budget.knowledge_chars > DEFAULT_BUDGET.knowledge_chars


def test_review_tier_default_used_for_review_steps():
    """Any review_* step without an explicit profile uses the lean tier."""
    budget = get_context_budget("review_pm")
    assert budget.wiki_chars == 2000
    assert budget.pattern_memory_chars == 0
    assert budget.cross_task_memory_chars == 0
    assert budget.state_max_chars == 120_000


def test_human_tier_default_used_for_human_gates():
    """Any human_* gate without an explicit profile uses the minimal tier."""
    budget = get_context_budget("human_arch")
    assert budget.wiki_chars == 0
    assert budget.knowledge_chars == 0
    assert budget.include_summaries is False
    assert budget.state_max_chars == 80_000


def test_crole_tier_falls_back_to_default():
    """Custom role steps default to the planning-style budget."""
    budget = get_context_budget("crole_my_custom_role")
    assert budget == DEFAULT_BUDGET


def test_dev_profile_drops_narrative_sections():
    """Dev should explicitly zero out wiki / knowledge / cross_task memory."""
    budget = get_context_budget("dev")
    assert budget.wiki_chars == 0
    assert budget.knowledge_chars == 0
    assert budget.cross_task_memory_chars == 0
    # Code analysis must stay generous.
    assert budget.code_analysis_chars >= 15_000


def test_qa_profile_minimises_memory_blocks():
    """QA reads spec + code, not memory."""
    budget = get_context_budget("qa")
    assert budget.wiki_chars == 0
    assert budget.cross_task_memory_chars == 0
    assert budget.pattern_memory_chars <= 4000
    assert budget.code_analysis_chars >= 10_000


# ---------------------------------------------------------------------------
# agent_config overrides
# ---------------------------------------------------------------------------


def test_agent_config_default_applies_to_all_steps():
    """``context_budgets.default`` overrides DEFAULT/profile fields."""
    cfg = {
        "swarm": {
            "context_budgets": {
                "default": {"wiki_chars": 1234, "knowledge_chars": 567},
            }
        }
    }
    pm = get_context_budget("pm", cfg)
    architect = get_context_budget("architect", cfg)
    assert pm.wiki_chars == 1234
    assert pm.knowledge_chars == 567
    assert architect.wiki_chars == 1234
    assert architect.knowledge_chars == 567


def test_agent_config_per_step_overrides_default():
    cfg = {
        "swarm": {
            "context_budgets": {
                "default": {"wiki_chars": 1000},
                "pm": {"wiki_chars": 2222},
            }
        }
    }
    assert get_context_budget("pm", cfg).wiki_chars == 2222
    assert get_context_budget("ba", cfg).wiki_chars == 1000


def test_agent_config_unknown_keys_ignored():
    """Garbage keys in agent_config don't crash; they're just dropped."""
    cfg = {
        "swarm": {
            "context_budgets": {
                "pm": {"unknown_field": 999, "wiki_chars": 4321},
            }
        }
    }
    budget = get_context_budget("pm", cfg)
    assert budget.wiki_chars == 4321
    assert not hasattr(budget, "unknown_field")


def test_agent_config_can_set_new_dataclass_fields():
    """The new fields (pattern_memory_chars etc.) are settable via config."""
    cfg = {
        "swarm": {
            "context_budgets": {
                "ba": {
                    "pattern_memory_chars": 100,
                    "cross_task_memory_chars": 200,
                    "state_max_chars": 75_000,
                }
            }
        }
    }
    budget = get_context_budget("ba", cfg)
    assert budget.pattern_memory_chars == 100
    assert budget.cross_task_memory_chars == 200
    assert budget.state_max_chars == 75_000


def test_invalid_int_value_raises_value_error():
    cfg = {"swarm": {"context_budgets": {"pm": {"wiki_chars": "not-a-number"}}}}
    with pytest.raises(ValueError, match="wiki_chars"):
        get_context_budget("pm", cfg)


def test_include_summaries_accepts_truthy_strings():
    cfg = {"swarm": {"context_budgets": {"pm": {"include_summaries": "yes"}}}}
    assert get_context_budget("pm", cfg).include_summaries is True
    cfg["swarm"]["context_budgets"]["pm"]["include_summaries"] = "0"
    assert get_context_budget("pm", cfg).include_summaries is False


def test_legacy_three_field_config_still_applies(monkeypatch):
    """Pre-H-1 agent_config (only the 3 legacy fields) keeps working."""
    cfg = {
        "swarm": {
            "context_budgets": {
                "default": {
                    "wiki_chars": 1000,
                    "knowledge_chars": 1000,
                    "include_summaries": False,
                },
                "pm": {"wiki_chars": 1500, "include_summaries": True},
            }
        }
    }
    pm = get_context_budget("pm", cfg)
    ba = get_context_budget("ba", cfg)
    assert pm.wiki_chars == 1500 and pm.include_summaries is True
    assert ba.wiki_chars == 1000 and ba.include_summaries is False


def test_none_agent_config_uses_profile_only():
    """Empty / missing agent_config → profile values (no overrides)."""
    pm_baseline = get_context_budget("pm")
    dev_baseline = get_context_budget("dev")
    assert get_context_budget("pm", None) == pm_baseline
    assert get_context_budget("dev", {}) == dev_baseline
    assert get_context_budget("dev", {"swarm": "not-a-dict"}) == dev_baseline


# ---------------------------------------------------------------------------
# Env overrides
# ---------------------------------------------------------------------------


def test_env_global_override_applies_to_all_steps(monkeypatch):
    monkeypatch.setenv("SWARM_CONTEXT_WIKI_CHARS", "4242")
    assert get_context_budget("pm").wiki_chars == 4242
    assert get_context_budget("ba").wiki_chars == 4242
    assert get_context_budget("dev").wiki_chars == 4242


def test_env_step_specific_overrides_global(monkeypatch):
    monkeypatch.setenv("SWARM_CONTEXT_WIKI_CHARS", "1000")
    monkeypatch.setenv("SWARM_CONTEXT_WIKI_CHARS_PM", "5000")
    assert get_context_budget("pm").wiki_chars == 5000
    assert get_context_budget("ba").wiki_chars == 1000


def test_env_overrides_agent_config(monkeypatch):
    monkeypatch.setenv("SWARM_CONTEXT_WIKI_CHARS_PM", "9999")
    cfg = {"swarm": {"context_budgets": {"pm": {"wiki_chars": 1}}}}
    assert get_context_budget("pm", cfg).wiki_chars == 9999


def test_env_invalid_value_raises_value_error(monkeypatch):
    monkeypatch.setenv("SWARM_CONTEXT_WIKI_CHARS", "boom")
    with pytest.raises(ValueError, match="SWARM_CONTEXT_WIKI_CHARS"):
        get_context_budget("pm")


def test_env_state_max_chars_step_specific(monkeypatch):
    monkeypatch.setenv("SWARM_CONTEXT_STATE_MAX_CHARS_DEV", "150000")
    assert get_context_budget("dev").state_max_chars == 150_000
    # other roles unaffected
    assert get_context_budget("pm").state_max_chars == 200_000


def test_env_include_summaries_off(monkeypatch):
    monkeypatch.setenv("SWARM_CONTEXT_INCLUDE_SUMMARIES_PM", "0")
    assert get_context_budget("pm").include_summaries is False


def test_legacy_env_alias_summaries_still_works(monkeypatch):
    """Pre-H-1 deployments may still set SWARM_CONTEXT_SUMMARIES_<STEP>."""
    monkeypatch.delenv("SWARM_CONTEXT_INCLUDE_SUMMARIES", raising=False)
    monkeypatch.delenv("SWARM_CONTEXT_INCLUDE_SUMMARIES_PM", raising=False)
    monkeypatch.setenv("SWARM_CONTEXT_SUMMARIES_PM", "0")
    assert get_context_budget("pm").include_summaries is False


def test_canonical_env_overrides_legacy_alias(monkeypatch):
    """If both names are set, the canonical SWARM_CONTEXT_INCLUDE_SUMMARIES_* wins."""
    monkeypatch.setenv("SWARM_CONTEXT_INCLUDE_SUMMARIES_PM", "1")
    monkeypatch.setenv("SWARM_CONTEXT_SUMMARIES_PM", "0")
    assert get_context_budget("pm").include_summaries is True


# ---------------------------------------------------------------------------
# context_budget_as_dict
# ---------------------------------------------------------------------------


def test_as_dict_contains_every_field():
    snap = context_budget_as_dict(DEFAULT_BUDGET)
    expected = {f.name for f in fields(ContextBudget)}
    assert set(snap) == expected


def test_as_dict_legacy_keys_present():
    """Legacy callers expect these three keys."""
    snap = context_budget_as_dict(DEFAULT_BUDGET)
    for key in ("wiki_chars", "knowledge_chars", "include_summaries"):
        assert key in snap


# ---------------------------------------------------------------------------
# Profile sanity checks (operate on every shipped profile)
# ---------------------------------------------------------------------------


def _all_shipped_profiles() -> dict[str, ContextBudget]:
    """Combined view of step + tier profiles loaded from the JSON file."""
    return {**cb_module._STEP_PROFILES, **cb_module._TIER_PROFILES}


def test_every_shipped_profile_is_dataclass_instance():
    for name, profile in _all_shipped_profiles().items():
        assert isinstance(profile, ContextBudget), f"{name} is not a ContextBudget"


def test_every_shipped_state_max_chars_is_positive():
    """No profile may set a non-positive ceiling — would disable compaction."""
    for name, profile in _all_shipped_profiles().items():
        assert profile.state_max_chars > 0, f"{name} has non-positive state_max_chars"
    assert DEFAULT_BUDGET.state_max_chars > 0


def test_no_shipped_profile_exceeds_default_state_max():
    """Profiles should never *raise* the default state ceiling silently."""
    for name, profile in _all_shipped_profiles().items():
        assert profile.state_max_chars <= DEFAULT_BUDGET.state_max_chars, (
            f"{name} raises state_max_chars above default"
        )


# ---------------------------------------------------------------------------
# Custom profile file via SWARM_CONTEXT_BUDGET_PROFILES
# ---------------------------------------------------------------------------


def test_custom_profiles_file_replaces_shipped_defaults(monkeypatch, tmp_path):
    """An operator-supplied JSON replaces the shipped defaults entirely."""
    custom = tmp_path / "custom_budgets.json"
    custom.write_text(json.dumps({
        "pm": {"wiki_chars": 1234},
        "review_": {"wiki_chars": 999},
    }))
    monkeypatch.setenv("SWARM_CONTEXT_BUDGET_PROFILES", str(custom))
    reload_profiles()

    pm_budget = get_context_budget("pm")
    assert pm_budget.wiki_chars == 1234
    # Fields not in the custom file fall through to DEFAULT_BUDGET.
    assert pm_budget.knowledge_chars == DEFAULT_BUDGET.knowledge_chars

    review_budget = get_context_budget("review_pm")
    assert review_budget.wiki_chars == 999

    # Steps that aren't in the custom file fall through entirely.
    unknown = get_context_budget("ba")
    assert unknown == DEFAULT_BUDGET


def test_invalid_profiles_file_logs_warning_and_uses_default(monkeypatch, tmp_path, caplog):
    bad = tmp_path / "broken.json"
    bad.write_text("{not valid json")
    monkeypatch.setenv("SWARM_CONTEXT_BUDGET_PROFILES", str(bad))
    with caplog.at_level("WARNING"):
        reload_profiles()
    assert any("failed to load" in record.message for record in caplog.records)
    # Falls back to DEFAULT_BUDGET for every step.
    assert get_context_budget("pm") == DEFAULT_BUDGET
    assert get_context_budget("dev") == DEFAULT_BUDGET


def test_profiles_file_skips_metadata_and_invalid_entries(monkeypatch, tmp_path, caplog):
    custom = tmp_path / "mixed.json"
    custom.write_text(json.dumps({
        "_comment": "ignore me",
        "_schema": ["also ignored"],
        "pm": {"wiki_chars": 4321},
        "bad_entry": "not a dict",
        "garbage": {"wiki_chars": "not-a-number"},
    }))
    monkeypatch.setenv("SWARM_CONTEXT_BUDGET_PROFILES", str(custom))
    with caplog.at_level("WARNING"):
        reload_profiles()
    # PM profile picked up; metadata skipped silently; bad entries logged.
    assert get_context_budget("pm").wiki_chars == 4321
    warnings = [rec.message for rec in caplog.records]
    assert any("bad_entry" in msg for msg in warnings)
    assert any("garbage" in msg for msg in warnings)


# ---------------------------------------------------------------------------
# reasoning_budget_tokens — role-aware cap for local reasoning models (§23.2)
# ---------------------------------------------------------------------------


def test_reasoning_budget_default_matches_full_cap():
    """Unknown step falls back to the full 4096-token cap."""
    budget = get_context_budget("totally_unknown_step")
    assert budget.reasoning_budget_tokens == 4096


def test_reasoning_budget_narrow_for_dev():
    """Dev subtasks have 1024 — narrow scope, 1–3K output — over-thinking
    is wasted wall-clock on MLX (~5 s/call)."""
    assert get_context_budget("dev").reasoning_budget_tokens == 1024
    assert get_context_budget("qa").reasoning_budget_tokens == 1024


def test_reasoning_budget_balanced_for_review_tier():
    """Reviewers need some thinking but not 4K — 2048 is the sweet spot."""
    for step in ("review_dev", "review_qa", "review_pm", "review_stack"):
        assert get_context_budget(step).reasoning_budget_tokens == 2048


def test_reasoning_budget_full_for_architecture_class():
    """Architect / debate / spec_merge do multi-constraint reasoning —
    keep the full 4096-token cap."""
    assert get_context_budget("architect").reasoning_budget_tokens == 4096
    assert get_context_budget("ba_arch_debate").reasoning_budget_tokens == 4096
    assert get_context_budget("spec_merge").reasoning_budget_tokens == 4096


def test_reasoning_budget_env_override(monkeypatch):
    """``SWARM_CONTEXT_REASONING_BUDGET_TOKENS_DEV=2048`` bumps the Dev
    cap without touching review_* or Architect."""
    monkeypatch.setenv("SWARM_CONTEXT_REASONING_BUDGET_TOKENS_DEV", "2048")
    assert get_context_budget("dev").reasoning_budget_tokens == 2048
    # Others untouched:
    assert get_context_budget("review_dev").reasoning_budget_tokens == 2048
    assert get_context_budget("architect").reasoning_budget_tokens == 4096
