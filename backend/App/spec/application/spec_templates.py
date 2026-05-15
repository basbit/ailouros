from __future__ import annotations

from textwrap import dedent

from backend.App.spec.domain.spec_document import (
    SpecDocument,
    SpecFrontmatter,
)


def project_seed_document(*, title: str, summary: str) -> SpecDocument:
    body = dedent(
        f"""

        {summary.strip() or 'High-level intent for this project.'}

        - **Project root** — the directory the swarm operates inside.
        - **Spec** — a markdown document under ``.swarm/specs/`` that
          declares the contract for a module, class, or feature.

        This is the project-level anchor. It declares the bounded
        contexts that live inside this workspace.

        The project follows spec-driven development: code is generated
        from the specs under ``.swarm/specs/``; the user reviews,
        edits, and asks the swarm to regenerate.

        - Every generated file has a sidecar ``.codegen_meta.json`` and
          is reachable from a spec in this folder.

        - If a stable spec has stale code, the drift auditor blocks
          merges and asks the user to either regenerate or update the
          spec.

        - Add a new module: ``swarm spec init <bounded-context>/<module>``,
          fill in the sections, run ``swarm spec generate``.

        - Self-hosted SaaS infrastructure (out of the engine's concern).

        - What is the initial bounded-context layout for this project?
        """
    )
    frontmatter = SpecFrontmatter(
        spec_id="_project",
        version=1,
        status="draft",
        privacy="internal",
        title=title,
        hash_inputs=("Public Contract", "Behaviour", "Invariants"),
    )
    return SpecDocument(frontmatter=frontmatter, body=body, sections=())


def schema_reference_document() -> SpecDocument:
    body = dedent(
        """

        Reference for the spec file format used by this workspace.

        Every spec is markdown with YAML frontmatter declaring at
        minimum: ``spec_id``, ``version``, ``status``, ``privacy``.

        Optional frontmatter keys:
        - ``hash_inputs``: section names that participate in the codegen hash.
        - ``codegen_targets``: paths produced by this spec.
        - ``depends_on``: sibling spec ids this one depends on.
        - ``title``, ``last_reviewed_by``, ``last_reviewed_at``.

        - On every save, ``swarm spec validate`` runs.
        - On every codegen, the engine writes a sidecar
          ``<file>.codegen_meta.json`` next to each ``codegen_target``.
        - When a stable spec's hash changes, generated code becomes
          stale until ``swarm spec regen`` is invoked.

        - Spec id must not contain ``..`` or absolute paths.
        - Spec status is one of: draft, reviewed, stable, deprecated.
        - Spec privacy is one of: public, internal, secret.

        - A missing required section is a validation error.
        - A cyclic ``depends_on`` graph is a validation error.

        See ``_project.md`` for the workspace-level anchor.

        - Multi-tenant spec stores. One workspace = one spec set.

        - none
        """
    )
    frontmatter = SpecFrontmatter(
        spec_id="_schema",
        version=1,
        status="stable",
        privacy="public",
        title="Spec schema reference",
        hash_inputs=("Public Contract", "Behaviour", "Invariants"),
    )
    return SpecDocument(frontmatter=frontmatter, body=body, sections=())


def module_seed_document(*, spec_id: str, title: str) -> SpecDocument:
    body = dedent(
        f"""

        Describe what {spec_id!r} owns and why it exists. One paragraph.

        - List terms introduced or refined by this spec.

        ```python {{dsl=python-sig}}
        ```

        Use EARS clauses, one per observable behaviour:

        - The module shall …
        - When …, the module shall …
        - While …, the module shall …
        - Where …, the module shall …
        - If …, then the module shall …

        - List facts that must hold across all behaviours.

        - What causes each documented failure mode and how callers see it.

        Minimal worked input/output pairs — these double as parametrised tests.

        - What this spec deliberately does not promise.

        - Block ``status: stable`` until this list is empty.
        """
    )
    frontmatter = SpecFrontmatter(
        spec_id=spec_id,
        version=1,
        status="draft",
        privacy="internal",
        title=title or spec_id,
        hash_inputs=("Public Contract", "Behaviour", "Invariants"),
    )
    return SpecDocument(frontmatter=frontmatter, body=body, sections=())


__all__ = [
    "module_seed_document",
    "project_seed_document",
    "schema_reference_document",
]
