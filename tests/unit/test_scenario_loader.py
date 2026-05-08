"""Тесты для ScenarioFileLoader."""

import json

import pytest

from backend.App.orchestration.infrastructure.scenario_loader import ScenarioFileLoader
from backend.App.orchestration.domain.scenarios.errors import ScenarioRegistryError


def test_loads_json_files(tmp_path):
    data = {"id": "s1", "title": "S1"}
    (tmp_path / "s1.json").write_text(json.dumps(data), encoding="utf-8")
    (tmp_path / "s2.json").write_text(json.dumps({"id": "s2"}), encoding="utf-8")
    loader = ScenarioFileLoader(tmp_path)
    results = loader.load_all()
    assert len(results) == 2
    ids = {r[1].get("id") for r in results}
    assert ids == {"s1", "s2"}


def test_skips_dunder_files(tmp_path):
    (tmp_path / "__init__.json").write_text("{}", encoding="utf-8")
    (tmp_path / "real.json").write_text('{"id": "real"}', encoding="utf-8")
    loader = ScenarioFileLoader(tmp_path)
    results = loader.load_all()
    assert len(results) == 1
    assert results[0][1]["id"] == "real"


def test_missing_dir_returns_empty(tmp_path):
    loader = ScenarioFileLoader(tmp_path / "nonexistent")
    assert loader.load_all() == []


def test_malformed_json_raises_with_filename(tmp_path):
    (tmp_path / "bad.json").write_text("not json!", encoding="utf-8")
    loader = ScenarioFileLoader(tmp_path)
    with pytest.raises(ScenarioRegistryError) as exc_info:
        loader.load_all()
    assert "bad.json" in str(exc_info.value)
