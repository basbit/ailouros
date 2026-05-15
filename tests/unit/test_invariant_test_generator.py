from __future__ import annotations

import ast

import pytest

from backend.App.spec.application.invariant_test_generator import (
    InvariantGeneratorError,
    generate_property_test_module,
)

_INVARIANTS = [
    {"name": "ok_check", "predicate": "result.ok == True"},
    {"name": "findings_immutable", "predicate": "isinstance(result.findings, tuple)"},
]

_FIXTURE_MODULE = "my_package.fixtures.result_fixture"


def test_output_is_valid_python():
    source = generate_property_test_module("spec_a", _INVARIANTS, fixture_module=_FIXTURE_MODULE)
    ast.parse(source)


def test_fixture_import_line_present():
    source = generate_property_test_module("spec_a", _INVARIANTS, fixture_module=_FIXTURE_MODULE)
    assert "from my_package.fixtures.result_fixture import result_fixture" in source


def test_hypothesis_import_present():
    source = generate_property_test_module("spec_a", _INVARIANTS, fixture_module=_FIXTURE_MODULE)
    assert "from hypothesis import" in source


def test_per_invariant_test_function_emitted():
    source = generate_property_test_module("spec_a", _INVARIANTS, fixture_module=_FIXTURE_MODULE)
    assert "def test_ok_check(" in source
    assert "def test_findings_immutable(" in source


def test_given_decorator_present_for_each_test():
    source = generate_property_test_module("spec_a", _INVARIANTS, fixture_module=_FIXTURE_MODULE)
    assert source.count("@given(") == len(_INVARIANTS)


def test_assertion_includes_invariant_name():
    source = generate_property_test_module("spec_a", _INVARIANTS, fixture_module=_FIXTURE_MODULE)
    assert "ok_check" in source
    assert "findings_immutable" in source


def test_predicate_expression_appears_in_assert():
    source = generate_property_test_module("spec_a", _INVARIANTS, fixture_module=_FIXTURE_MODULE)
    assert "result.ok == True" in source
    assert "isinstance(result.findings, tuple)" in source


def test_empty_fixture_module_raises():
    with pytest.raises(InvariantGeneratorError, match="fixture_module"):
        generate_property_test_module("spec_a", _INVARIANTS, fixture_module="")


def test_whitespace_fixture_module_raises():
    with pytest.raises(InvariantGeneratorError, match="fixture_module"):
        generate_property_test_module("spec_a", _INVARIANTS, fixture_module="   ")


def test_empty_invariants_list_raises():
    with pytest.raises(InvariantGeneratorError, match="empty"):
        generate_property_test_module("spec_a", [], fixture_module=_FIXTURE_MODULE)


def test_predicate_with_no_bindings_raises():
    no_binding = [{"name": "always_true", "predicate": "True"}]
    with pytest.raises(InvariantGeneratorError, match="no free bindings"):
        generate_property_test_module("spec_a", no_binding, fixture_module=_FIXTURE_MODULE)


def test_invalid_predicate_syntax_raises():
    bad = [{"name": "broken", "predicate": "x ==="}]
    with pytest.raises(Exception):
        generate_property_test_module("spec_a", bad, fixture_module=_FIXTURE_MODULE)


def test_fixture_function_name_derived_from_module():
    source = generate_property_test_module(
        "spec_b",
        [{"name": "chk", "predicate": "x > 0"}],
        fixture_module="pkg.sub.my_fixture",
    )
    assert "from pkg.sub.my_fixture import my_fixture" in source
    assert "result = my_fixture(" in source
