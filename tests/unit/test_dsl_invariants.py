from __future__ import annotations

from backend.App.spec.domain.dsl_block import extract_dsl_blocks
from backend.App.spec.domain.dsl_invariants import InvariantsParser
from backend.App.spec.domain.dsl_registry import make_default_registry


def _block(body: str):
    markdown = "```yaml {dsl=invariants}\n" + body + "\n```\n"
    return extract_dsl_blocks(markdown)[0]


def test_invariants_single_entry_happy_path():
    block = _block(
        '- name: response_ok\n'
        '  predicate: "result.ok == True"'
    )
    result = InvariantsParser().parse(block)
    assert result.findings == ()
    invariants = result.payload["invariants"]
    assert invariants == [{"name": "response_ok", "predicate": "result.ok == True"}]


def test_invariants_multiple_entries():
    block = _block(
        '- name: alpha\n'
        '  predicate: "x > 0"\n'
        '- name: beta\n'
        '  predicate: "y < 1"'
    )
    result = InvariantsParser().parse(block)
    assert result.findings == ()
    invariants = result.payload["invariants"]
    assert [item["name"] for item in invariants] == ["alpha", "beta"]


def test_invariants_empty_block_is_error():
    block = _block("")
    result = InvariantsParser().parse(block)
    severities = {finding.severity for finding in result.findings}
    assert "error" in severities


def test_invariants_duplicate_names_are_error():
    block = _block(
        '- name: dup\n'
        '  predicate: "a"\n'
        '- name: dup\n'
        '  predicate: "b"'
    )
    result = InvariantsParser().parse(block)
    severities = {finding.severity for finding in result.findings}
    assert "error" in severities


def test_invariants_missing_name_is_error():
    block = _block(
        '- predicate: "x > 0"'
    )
    result = InvariantsParser().parse(block)
    severities = {finding.severity for finding in result.findings}
    assert "error" in severities


def test_invariants_missing_predicate_is_error():
    block = _block(
        '- name: lonely'
    )
    result = InvariantsParser().parse(block)
    severities = {finding.severity for finding in result.findings}
    assert "error" in severities


def test_invariants_unconventional_name_is_warning():
    block = _block(
        '- name: CamelCase\n'
        '  predicate: "x > 0"'
    )
    result = InvariantsParser().parse(block)
    severities = {finding.severity for finding in result.findings}
    assert "warning" in severities
    assert "error" not in severities
    invariants = result.payload["invariants"]
    assert invariants[0]["name"] == "CamelCase"


def test_invariants_long_predicate_is_warning():
    long_predicate = "x == " + ("a + " * 200) + "0"
    block = _block(
        '- name: long_one\n'
        f'  predicate: "{long_predicate}"'
    )
    result = InvariantsParser().parse(block)
    severities = {finding.severity for finding in result.findings}
    assert "warning" in severities
    assert "error" not in severities


def test_default_registry_knows_invariants():
    registry = make_default_registry()
    assert "invariants" in registry.known_kinds()
    assert registry.known_kinds() == ("invariants", "python-sig", "ts-sig")
