from __future__ import annotations

from backend.App.spec.domain.document_validator import (
    validate_documents,
    validate_one,
)
from backend.App.spec.domain.spec_document import (
    SpecDocument,
    SpecFrontmatter,
    parse_spec,
)


_BASE_BODY = (
    "\n## Purpose\n\nHash passwords safely.\n\n"
    "## Public Contract\n\n```\nhash_password(plain: str) -> str\n```\n\n"
    "## Behaviour\n\nThe module shall hash all inputs.\n"
)


def _document(
    *,
    spec_id: str = "auth/password",
    status: str = "draft",
    depends_on: tuple[str, ...] = (),
    body: str = _BASE_BODY,
    codegen_targets: tuple[str, ...] = (),
) -> SpecDocument:
    text = (
        "---\n"
        f"spec_id: {spec_id}\n"
        "version: 1\n"
        f"status: {status}\n"
        "privacy: internal\n"
    )
    if depends_on:
        text += "depends_on:\n"
        for entry in depends_on:
            text += f"  - {entry}\n"
    if codegen_targets:
        text += "codegen_targets:\n"
        for target in codegen_targets:
            text += f"  - {target}\n"
    text += "---\n" + body
    return parse_spec(text)


def test_happy_path_no_findings():
    result = validate_one(_document())
    assert result.ok is True
    assert result.findings == ()


def test_missing_required_section_is_error():
    body = (
        "\n## Purpose\n\np\n\n"
        "## Public Contract\n\npc\n"
    )
    result = validate_one(_document(body=body))
    assert result.ok is False
    codes = {finding.code for finding in result.findings}
    assert "missing_required_section" in codes


def test_reviewed_status_requires_empty_open_questions():
    body = (
        _BASE_BODY
        + "\n## Open Questions\n\n- Should we hash twice?\n"
    )
    result = validate_one(_document(status="reviewed", body=body))
    codes = {finding.code for finding in result.findings}
    assert "open_questions_blocks_review" in codes


def test_reviewed_status_with_none_questions_passes():
    body = _BASE_BODY + "\n## Open Questions\n\n- none\n"
    result = validate_one(_document(status="reviewed", body=body))
    assert result.ok is True


def test_unknown_dependency_is_error():
    document = _document(depends_on=("ghost/missing",))
    result = validate_one(document)
    codes = {finding.code for finding in result.findings}
    assert "missing_dependency" in codes


def test_resolved_dependency_passes():
    a = _document(spec_id="a")
    b = _document(spec_id="b", depends_on=("a",))
    result = validate_documents([a, b])
    assert result.ok is True


def test_cycle_is_detected():
    a = _document(spec_id="a", depends_on=("b",))
    b = _document(spec_id="b", depends_on=("c",))
    c = _document(spec_id="c", depends_on=("a",))
    result = validate_documents([a, b, c])
    codes = {finding.code for finding in result.findings}
    assert "dependency_cycle" in codes


def test_empty_codegen_target_flagged():
    document = SpecDocument(
        frontmatter=SpecFrontmatter(
            spec_id="x",
            codegen_targets=("",),
        ),
        body=_BASE_BODY,
        sections=(),
    )
    result = validate_one(document)
    codes = {finding.code for finding in result.findings}
    assert "empty_codegen_target" in codes
