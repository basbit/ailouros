from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from backend.App.spec.domain.python_module_summary import (
    ModuleSummary,
    summarise_python_module,
)
from backend.App.spec.domain.spec_document import (
    SpecDocument,
    SpecFrontmatter,
    parse_spec,
    render_spec,
)
from backend.App.spec.infrastructure.spec_repository_fs import (
    FilesystemSpecRepository,
    SpecRepositoryError,
)

logger = logging.getLogger(__name__)

_PY_SUFFIX = ".py"


class ExtractError(Exception):
    pass


def _public_signatures_block(summary: ModuleSummary) -> str:
    if not summary.functions and not summary.classes:
        return "# (no public functions or classes detected)"
    parts: list[str] = []
    for function in summary.functions:
        parts.append(function.signature)
    for klass in summary.classes:
        bases = ", ".join(klass.bases) if klass.bases else ""
        header = f"class {klass.name}({bases}):" if bases else f"class {klass.name}:"
        parts.append(header)
        if not klass.methods:
            parts.append("    ...")
        else:
            for method in klass.methods:
                parts.append(f"    {method.signature}")
        parts.append("")
    return "\n".join(part for part in parts if part is not None).strip()


def _draft_body(summary: ModuleSummary, *, codegen_target: str) -> str:
    purpose = summary.docstring.strip() or (
        f"Describe the responsibility of ``{codegen_target}`` here."
    )
    signatures = _public_signatures_block(summary)
    sections = [
        "## Purpose",
        purpose,
        "## Ubiquitous Language",
        "- Document terms introduced by this module.",
        "## Public Contract",
        "```python {dsl=python-sig}",
        signatures,
        "```",
        "## Behaviour",
        "- The module shall expose the public surface declared above.",
        "- Add one EARS clause per observable behaviour.",
        "## Invariants",
        "- List facts that must hold across all behaviours.",
        "## Errors & Failures",
        "- Document the failure modes and how callers observe them.",
        "## Examples",
        "- Add minimal input/output pairs to drive parametrised tests.",
        "## Out of Scope",
        "- What this spec deliberately does not promise.",
        "## Open Questions",
        "- Resolve before promoting beyond ``status: draft``.",
    ]
    return "\n\n".join(sections) + "\n"


def _spec_id_for_code_path(code_path: Path, workspace_root: Path) -> str:
    relative = code_path.relative_to(workspace_root).with_suffix("")
    return relative.as_posix()


def extract_spec_from_code(
    workspace_root: str | Path,
    code_path: str | Path,
    *,
    spec_id_override: Optional[str] = None,
    save: bool = False,
) -> SpecDocument:
    workspace = Path(workspace_root).expanduser().resolve()
    if not workspace.is_dir():
        raise ExtractError(f"workspace_root is not a directory: {workspace}")
    target = Path(code_path).expanduser()
    if not target.is_absolute():
        target = (workspace / target).resolve()
    else:
        target = target.resolve()
    if not target.is_file():
        raise ExtractError(f"source file not found: {target}")
    if target.suffix != _PY_SUFFIX:
        raise ExtractError(
            f"extract currently supports Python files only: {target.name}"
        )
    try:
        target.relative_to(workspace)
    except ValueError as exception:
        raise ExtractError(
            f"source file is outside workspace_root: {target}"
        ) from exception

    source = target.read_text(encoding="utf-8")
    summary = summarise_python_module(source, module_path=target.relative_to(workspace).as_posix())

    spec_id = spec_id_override or _spec_id_for_code_path(target, workspace)
    if spec_id == "_project" or spec_id == "_schema":
        raise ExtractError(
            f"spec_id {spec_id!r} is reserved; pass an override"
        )
    body = _draft_body(summary, codegen_target=summary.module_path)
    frontmatter = SpecFrontmatter(
        spec_id=spec_id,
        version=1,
        status="draft",
        privacy="internal",
        title=summary.module_path,
        hash_inputs=("Public Contract", "Behaviour", "Invariants"),
        codegen_targets=(summary.module_path,),
    )
    draft = SpecDocument(frontmatter=frontmatter, body="\n" + body, sections=())
    document = parse_spec(render_spec(draft))

    if save:
        try:
            repository = FilesystemSpecRepository(workspace)
        except SpecRepositoryError as exception:
            raise ExtractError(str(exception)) from exception
        repository.save(document)
    return document


__all__ = ["ExtractError", "extract_spec_from_code"]
