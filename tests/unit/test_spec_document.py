from __future__ import annotations

import pytest

from backend.App.spec.domain.spec_document import (
    SpecParseError,
    parse_spec,
    render_spec,
)


SAMPLE = """\
---
spec_id: orchestration/spec_validator
version: 2
status: reviewed
privacy: internal
hash_inputs: ["Public Contract", "Behaviour"]
codegen_targets:
  - backend/App/orchestration/application/spec/spec_validator.py
  - tests/unit/test_spec_validator.py
depends_on:
  - orchestration/_context
last_reviewed_by: baster
last_reviewed_at: 2026-05-14
title: "Spec validator"
---

## Purpose

Validate the consistency of a Specification.

## Public Contract

The validator returns ok=True iff no error-severity findings exist.

## Behaviour

When required acceptance criteria are missing, the validator emits a
``missing_acceptance`` finding.

## Examples

- Empty spec → ok=True.
"""


def test_parse_frontmatter_pulls_typed_fields():
    document = parse_spec(SAMPLE)
    frontmatter = document.frontmatter
    assert frontmatter.spec_id == "orchestration/spec_validator"
    assert frontmatter.version == 2
    assert frontmatter.status == "reviewed"
    assert frontmatter.privacy == "internal"
    assert frontmatter.hash_inputs == ("Public Contract", "Behaviour")
    assert frontmatter.codegen_targets == (
        "backend/App/orchestration/application/spec/spec_validator.py",
        "tests/unit/test_spec_validator.py",
    )
    assert frontmatter.depends_on == ("orchestration/_context",)
    assert frontmatter.title == "Spec validator"


def test_parse_sections_are_indexed():
    document = parse_spec(SAMPLE)
    assert document.section("Purpose").startswith("Validate")
    assert "missing_acceptance" in document.section("Behaviour")
    assert document.section("Out of scope") == ""


def test_codegen_hash_changes_on_contract_edit():
    document_one = parse_spec(SAMPLE)
    other = SAMPLE.replace(
        "ok=True iff no error-severity",
        "ok=False iff no error-severity",
    )
    document_two = parse_spec(other)
    assert document_one.codegen_hash() != document_two.codegen_hash()


def test_codegen_hash_unchanged_on_purpose_edit():
    document_one = parse_spec(SAMPLE)
    other = SAMPLE.replace(
        "Validate the consistency of a Specification.",
        "Validate the spec.",
    )
    document_two = parse_spec(other)
    assert document_one.codegen_hash() == document_two.codegen_hash()


def test_missing_frontmatter_raises():
    with pytest.raises(SpecParseError):
        parse_spec("no frontmatter here\n## Purpose\nx")


def test_invalid_status_raises():
    bad = SAMPLE.replace("status: reviewed", "status: bogus")
    with pytest.raises(SpecParseError):
        parse_spec(bad)


def test_invalid_privacy_raises():
    bad = SAMPLE.replace("privacy: internal", "privacy: bogus")
    with pytest.raises(SpecParseError):
        parse_spec(bad)


def test_render_roundtrip_preserves_body():
    document = parse_spec(SAMPLE)
    rendered = render_spec(document)
    second = parse_spec(rendered)
    assert second.frontmatter == document.frontmatter
    assert second.section("Purpose") == document.section("Purpose")
