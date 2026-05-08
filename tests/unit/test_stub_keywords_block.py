from backend.App.orchestration.application.nodes._workspace_instructions import (
    _stub_keywords_block,
)


def test_returns_empty_when_no_production_paths():
    assert _stub_keywords_block({}) == ""
    assert _stub_keywords_block({"production_paths": []}) == ""
    assert _stub_keywords_block({"production_paths": None}) == ""


def test_returns_empty_when_production_paths_blank_strings():
    assert _stub_keywords_block({"production_paths": ["  ", ""]}) == ""


def test_lists_paths_and_forbidden_keywords():
    state = {"production_paths": ["Domain/", "Services/"]}
    block = _stub_keywords_block(state)
    assert "Domain/" in block
    assert "Services/" in block
    assert "TODO" in block
    assert "FIXME" in block
    assert "placeholder" in block
    assert "raise NotImplementedError" in block
    assert "production-path stub policy" in block


def test_emits_hard_fail_warning():
    state = {"production_paths": ["Assets/Scripts/"]}
    block = _stub_keywords_block(state)
    assert "HARD-FAIL" in block
    assert "stub_gate" in block
