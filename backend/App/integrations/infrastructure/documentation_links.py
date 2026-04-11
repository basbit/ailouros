"""Внешние ссылки на документацию (agent_config.swarm) — блок для промптов.

Как в Cursor «Docs»: оператор задаёт канонические URL; в промпт попадает список ссылок.
Содержимое страниц по умолчанию не подгружается (нет авто-fetch) — агенту явно указано
сверяться по URL или через MCP/клиент.
"""

from __future__ import annotations

from typing import Any, Optional


def iter_documentation_sources(sw: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Возвращает список (url, title, note); URL только http(s), без дубликатов."""
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    def add(url: str, title: str = "", note: str = "") -> None:
        u = (url or "").strip()
        if not u:
            return
        low = u.lower()
        if not (low.startswith("http://") or low.startswith("https://")):
            return
        if u in seen:
            return
        seen.add(u)
        out.append((u, (title or "").strip(), (note or "").strip()))

    raw = sw.get("documentation_sources")
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                add(item)
            elif isinstance(item, dict):
                u = item.get("url") or item.get("href") or ""
                add(
                    str(u),
                    str(item.get("title") or ""),
                    str(item.get("note") or item.get("description") or ""),
                )

    for key in ("documentation_urls", "doc_links"):
        urls = sw.get(key)
        if isinstance(urls, list):
            for u in urls:
                if isinstance(u, str):
                    add(u)
                elif isinstance(u, dict):
                    add(
                        str(u.get("url") or u.get("href") or ""),
                        str(u.get("title") or ""),
                        str(u.get("note") or ""),
                    )

    return out


def _format_fetched_manifest_block(manifest: list[dict[str, Any]]) -> str:
    lines = [
        "",
        "[Fetched copies (SWARM_DOC_FETCH)]",
        "Read files via MCP (filesystem) tools using paths relative to workspace root "
        "if available; otherwise use the artifacts directory on the orchestrator host.",
    ]
    for m in manifest:
        title = (m.get("title") or "").strip()
        url = str(m.get("url") or "")
        rel = (m.get("workspace_rel_path") or "").strip()
        art = (m.get("artifact_dir") or "").strip()
        ok = m.get("ok")
        err = m.get("error")
        head = f"- {title} — {url}" if title else f"- {url}"
        lines.append(head)
        if rel:
            lines.append(f"  workspace (MCP): {rel} (+ meta.json nearby)")
        if art:
            lines.append(f"  artifacts: {art}/body.txt")
        if ok is False and err:
            lines.append(f"  status: {err}")
    lines.append("")
    return "\n".join(lines)


def format_documentation_links_block(
    sw: dict[str, Any],
    *,
    fetched_manifest: Optional[list[dict[str, Any]]] = None,
) -> str:
    """Текстовый блок для подмешивания в user-промпт агентов."""
    rows = iter_documentation_sources(sw)
    man = list(fetched_manifest or [])
    parts: list[str] = []

    if man:
        parts.append(_format_fetched_manifest_block(man))

    if rows:
        lines = [
            "",
            "[External documentation / release notes]",
            "Reference URLs provided by the operator.",
        ]
        if man:
            lines.append(
                "Some pages may have been saved to files — see "
                "[Fetched copies] block above; otherwise verify via links or MCP."
            )
        else:
            lines.extend(
                [
                    "Page content is not fetched into the prompt automatically —",
                    (
                        "verify API versions, breaking changes and changelog via links "
                        "or through MCP/fetch."
                    ),
                ]
            )
        for url, title, note in rows:
            if title:
                lines.append(f"- {title} — {url}")
            else:
                lines.append(f"- {url}")
            if note:
                lines.append(f"  ({note})")
        lines.append(
            "When spec or code conflicts with documentation, prioritize official sources by URL."
        )
        parts.append("\n".join(lines) + "\n")

    return "".join(parts)
