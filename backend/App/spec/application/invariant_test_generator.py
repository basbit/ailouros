from __future__ import annotations

from backend.App.spec.domain.invariant_predicate import InvariantPredicate, parse_predicate


class InvariantGeneratorError(ValueError):
    pass


def _fixture_name(fixture_module: str) -> str:
    return fixture_module.rsplit(".", 1)[-1]


def _render_given_decorator(bindings: tuple[str, ...]) -> str:
    args = ", ".join(f"{b}=st.integers()" for b in bindings)
    return f"@given({args})"


def _render_test_function(predicate: InvariantPredicate, fixture_fn: str) -> str:
    params = ", ".join(predicate.bindings)
    call_args = ", ".join(predicate.bindings)
    lines = [
        _render_given_decorator(predicate.bindings),
        f"def test_{predicate.name}({params}):",
        f"    result = {fixture_fn}({call_args})",
        f"    assert {predicate.expression}, f\"invariant {predicate.name!r} violated\"",
    ]
    return "\n".join(lines)


def generate_property_test_module(
    spec_id: str,
    invariants: list[dict[str, str]],
    *,
    fixture_module: str,
) -> str:
    if not fixture_module.strip():
        raise InvariantGeneratorError(
            f"spec {spec_id!r}: fixture_module must be a non-empty dotted module path"
        )
    if not invariants:
        raise InvariantGeneratorError(
            f"spec {spec_id!r}: invariants list is empty; nothing to generate"
        )

    parsed: list[InvariantPredicate] = []
    for entry in invariants:
        name = entry.get("name", "")
        expression = entry.get("predicate", "")
        predicate = parse_predicate(name, expression)
        if not predicate.bindings:
            raise InvariantGeneratorError(
                f"spec {spec_id!r}, invariant {name!r}: predicate {expression!r} "
                "has no free bindings; cannot generate a parameterised test"
            )
        parsed.append(predicate)

    fixture_fn = _fixture_name(fixture_module)

    header_lines = [
        "from __future__ import annotations",
        "",
        "from hypothesis import given, settings, strategies as st",
        f"from {fixture_module} import {fixture_fn}",
        "",
        "",
    ]

    body_blocks: list[str] = []
    for predicate in parsed:
        body_blocks.append(_render_test_function(predicate, fixture_fn))

    return "\n".join(header_lines) + "\n\n".join(body_blocks) + "\n"


__all__ = ["InvariantGeneratorError", "generate_property_test_module"]
