from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.App.integrations.infrastructure import role_budgets_loader
from backend.App.integrations.infrastructure.role_budgets_loader import (
    load_role_budgets,
    reset_role_budgets_cache,
)


_ALL_ROLES = [
    "pm",
    "ba",
    "architect",
    "dev_lead",
    "dev",
    "qa",
    "review_dev",
    "review_pm",
    "review_ba",
    "human_qa",
    "spec_drafter",
    "codegen_agent",
    "code_verifier",
]


def _write_role_budgets(tmp_path: Path, payload) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    path = config_dir / "role_budgets.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return tmp_path


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_role_budgets_cache()
    yield
    reset_role_budgets_cache()


def _full_payload():
    return {
        role: {
            "prompt_tokens_max": 100,
            "reasoning_tokens_max": 50,
            "completion_tokens_max": 80,
            "total_tokens_ceiling": 200,
        }
        for role in _ALL_ROLES
    }


def test_load_role_budgets_reads_default_config():
    budgets = load_role_budgets()
    assert set(budgets.keys()) == set(_ALL_ROLES)
    assert budgets["pm"].prompt_tokens_max == 8192


def test_load_role_budgets_memoised(monkeypatch):
    calls = {"n": 0}
    real_parse = role_budgets_loader.parse_role_budgets

    def counting(raw):
        calls["n"] += 1
        return real_parse(raw)

    monkeypatch.setattr(role_budgets_loader, "parse_role_budgets", counting)
    reset_role_budgets_cache()
    load_role_budgets()
    load_role_budgets()
    load_role_budgets()
    assert calls["n"] == 1


def test_load_role_budgets_picks_up_override(monkeypatch, tmp_path):
    root = _write_role_budgets(tmp_path, _full_payload())
    monkeypatch.setenv("SWARM_PROJECT_ROOT", str(root))
    reset_role_budgets_cache()
    budgets = load_role_budgets()
    assert budgets["pm"].prompt_tokens_max == 100
    assert budgets["dev"].reasoning_tokens_max == 50


def test_load_role_budgets_raises_on_missing_file(monkeypatch, tmp_path):
    monkeypatch.setenv("SWARM_PROJECT_ROOT", str(tmp_path))
    reset_role_budgets_cache()
    with pytest.raises((FileNotFoundError, OSError)):
        load_role_budgets()


def test_load_role_budgets_raises_on_malformed_payload(monkeypatch, tmp_path):
    payload = _full_payload()
    payload["pm"]["prompt_tokens_max"] = -1
    _write_role_budgets(tmp_path, payload)
    monkeypatch.setenv("SWARM_PROJECT_ROOT", str(tmp_path))
    reset_role_budgets_cache()
    with pytest.raises(ValueError, match="non-negative"):
        load_role_budgets()


def test_load_role_budgets_raises_on_missing_known_role(monkeypatch, tmp_path):
    payload = _full_payload()
    payload.pop("dev")
    _write_role_budgets(tmp_path, payload)
    monkeypatch.setenv("SWARM_PROJECT_ROOT", str(tmp_path))
    reset_role_budgets_cache()
    with pytest.raises(ValueError, match="missing required role"):
        load_role_budgets()


def test_get_role_budget_returns_none_for_unknown_role(monkeypatch, tmp_path):
    _write_role_budgets(tmp_path, _full_payload())
    monkeypatch.setenv("SWARM_PROJECT_ROOT", str(tmp_path))
    reset_role_budgets_cache()
    from backend.App.integrations.infrastructure.role_budgets_loader import (
        get_role_budget,
    )
    assert get_role_budget("nonexistent_role") is None
    assert get_role_budget("pm") is not None
