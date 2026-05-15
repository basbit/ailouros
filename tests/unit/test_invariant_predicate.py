from __future__ import annotations

import pytest

from backend.App.spec.domain.invariant_predicate import (
    InvariantPredicate,
    InvariantPredicateError,
    parse_predicate,
)


def test_parse_simple_expression_returns_predicate():
    p = parse_predicate("ok_check", "result.ok == True")
    assert isinstance(p, InvariantPredicate)
    assert p.name == "ok_check"
    assert p.expression == "result.ok == True"


def test_bindings_extracted_from_expression():
    p = parse_predicate("check", "result.ok == (not any(f.severity == 'error' for f in result.findings))")
    assert "result" in p.bindings


def test_bindings_exclude_builtins():
    p = parse_predicate("check", "isinstance(result.findings, tuple)")
    assert "isinstance" not in p.bindings
    assert "tuple" not in p.bindings
    assert "result" in p.bindings


def test_bindings_multiple_free_names():
    p = parse_predicate("multi", "a > b and c == d")
    assert set(p.bindings) == {"a", "b", "c", "d"}


def test_bindings_no_duplicates():
    p = parse_predicate("dup", "x + x + x")
    assert p.bindings.count("x") == 1


def test_syntax_error_raises_invariant_predicate_error():
    with pytest.raises(InvariantPredicateError, match="not valid Python"):
        parse_predicate("bad", "result.ok ==")


def test_syntax_error_message_includes_name_and_expression():
    with pytest.raises(InvariantPredicateError) as exc_info:
        parse_predicate("my_inv", "x ===")
    msg = str(exc_info.value)
    assert "my_inv" in msg
    assert "x ===" in msg


def test_empty_expression_raises():
    with pytest.raises(InvariantPredicateError, match="empty"):
        parse_predicate("blank", "")


def test_whitespace_only_expression_raises():
    with pytest.raises(InvariantPredicateError, match="empty"):
        parse_predicate("ws", "   ")


def test_statement_not_expression_raises():
    with pytest.raises(InvariantPredicateError, match="not valid Python"):
        parse_predicate("stmt", "x = 1")


def test_bindings_are_tuple():
    p = parse_predicate("t", "a == b")
    assert isinstance(p.bindings, tuple)


def test_parse_literal_expression_no_free_names():
    p = parse_predicate("lit", "True")
    assert p.bindings == ()


def test_predicate_is_frozen():
    p = parse_predicate("frozen", "x > 0")
    with pytest.raises((AttributeError, TypeError)):
        p.name = "other"  # type: ignore[misc]
