#!/usr/bin/env python3
"""Scan source files for os.getenv() calls and keep docs/AIlourOS.md §11.1 in sync.

Usage:
    python scripts/gen_env_docs.py              # report missing vars
    python scripts/gen_env_docs.py --check      # exit 1 if any are missing (for CI)
    python scripts/gen_env_docs.py --list       # just print all found env vars
    python scripts/gen_env_docs.py --write      # rewrite §11.1 with undocumented vars grouped by module
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

# `scripts/` lives under `app/`, but the user-facing docs live at the repo root
# (`docs/AIlourOS.md`). Walk up one more level for docs, keep sources under app/.
APP_ROOT = Path(__file__).parent.parent
REPO_ROOT = APP_ROOT.parent
DOCS_FILE = REPO_ROOT / "docs" / "AIlourOS.md"
# Scan canonical code roots (legacy top-level packages may be empty after DDD migration).
SOURCE_DIRS = ["backend", "orchestrator", "integrations", "agents", "pipeline", "code_analysis"]
SOURCE_GLOBS = ["**/*.py"]

# Pattern: os.getenv("VAR_NAME", ...) or os.getenv('VAR_NAME', ...)
_GETENV_RE = re.compile(r'os\.getenv\(\s*["\']([A-Z][A-Z0-9_]{2,})["\']')
# Also catch os.environ.get("VAR", ...)
_ENVIRON_RE = re.compile(r'os\.environ\.get\(\s*["\']([A-Z][A-Z0-9_]{2,})["\']')
# And os.environ["VAR"]
_ENVIRON_KEY_RE = re.compile(r'os\.environ\[\s*["\']([A-Z][A-Z0-9_]{2,})["\']')
_SETTING_RE = re.compile(
    r'get_setting(?:_bool|_int)?\(\s*["\']([^"\']+)["\'][\s\S]*?env_key\s*=\s*["\']([A-Z][A-Z0-9_]{2,})["\']',
    re.MULTILINE,
)


def find_env_vars() -> dict[str, list[str]]:
    """Return {VAR_NAME: [file:line, ...]} for all env vars found in source."""
    found: dict[str, list[str]] = {}
    for src_dir in SOURCE_DIRS:
        base = APP_ROOT / src_dir
        if not base.exists():
            continue
        for glob in SOURCE_GLOBS:
            for path in sorted(base.glob(glob)):
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                rel = str(path.relative_to(APP_ROOT))
                for lineno, line in enumerate(text.splitlines(), 1):
                    for pat in (_GETENV_RE, _ENVIRON_RE, _ENVIRON_KEY_RE):
                        for m in pat.finditer(line):
                            var = m.group(1)
                            found.setdefault(var, []).append(f"{rel}:{lineno}")
    return found


def find_settings_migrations() -> dict[str, tuple[str, list[str]]]:
    found: dict[str, tuple[str, list[str]]] = {}
    for src_dir in SOURCE_DIRS:
        base = APP_ROOT / src_dir
        if not base.exists():
            continue
        for glob in SOURCE_GLOBS:
            for path in sorted(base.glob(glob)):
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                rel = str(path.relative_to(APP_ROOT))
                line_starts = [0]
                for match in re.finditer(r"\n", text):
                    line_starts.append(match.end())
                for match in _SETTING_RE.finditer(text):
                    settings_key, env_var = match.group(1), match.group(2)
                    line_number = 1 + sum(1 for offset in line_starts if offset <= match.start()) - 1
                    references = found.setdefault(env_var, (settings_key, []))[1]
                    references.append(f"{rel}:{line_number}")
    return found


_SECTION_HEADER = "### 11.1 Дополнительные переменные в коде"
_SECTION_END = "---"


def load_documented_vars(*, include_autogen: bool = True) -> set[str]:
    """Return set of VAR_NAMEs mentioned in backticks in docs/AIlourOS.md.

    With ``include_autogen=True`` (default, used by ``--check`` / no-arg run),
    §11.1 auto-generated entries count as "documented" — a var listed there is
    at least acknowledged. With ``include_autogen=False`` (used by ``--write``),
    §11.1 is stripped so that regeneration sees the full universe of
    undocumented vars and does not lose previously auto-listed entries.
    """
    if not DOCS_FILE.exists():
        return set()
    text = DOCS_FILE.read_text(encoding="utf-8")
    if not include_autogen:
        start = text.find(_SECTION_HEADER)
        if start != -1:
            end = text.find(f"\n{_SECTION_END}\n", start)
            if end != -1:
                text = text[:start] + text[end:]
    return set(re.findall(r"`([A-Z][A-Z0-9_]{2,})`", text))


def _module_label(ref: str) -> str:
    """Group a `backend/App/foo/bar/baz.py:123` ref into a short module label."""
    path = ref.split(":", 1)[0]
    parts = path.split("/")
    # Collapse into first 3 significant components, skip top-level "backend"
    if parts and parts[0] == "backend":
        parts = parts[1:]
    if parts and parts[0] == "App":
        parts = parts[1:]
    # Take first two remaining path components for the label
    return "/".join(parts[:2]) if parts else path


def _render_missing_section(missing: dict[str, list[str]]) -> str:
    """Return the markdown body for §11.1 with undocumented vars grouped by module."""
    groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for var in sorted(missing):
        first_ref = missing[var][0]
        groups[_module_label(first_ref)].append((var, first_ref))

    lines: list[str] = [
        _SECTION_HEADER,
        "",
        "Переменные, найденные в исходниках, но не покрытые таблицами выше. "
        "Раздел сгенерирован `python scripts/gen_env_docs.py --write` — правьте "
        "таблицы §11.* или добавляйте описания здесь. Повторный запуск не трогает "
        "уже задокументированные имена.",
        "",
    ]

    if not groups:
        lines.append("_Все переменные покрыты таблицами §11._")
        lines.append("")
        return "\n".join(lines)

    for module in sorted(groups):
        lines.append(f"#### {module or '(root)'}")
        lines.append("")
        lines.append("| Variable | First reference |")
        lines.append("|---|---|")
        for var, ref in groups[module]:
            lines.append(f"| `{var}` | `{ref}` |")
        lines.append("")

    return "\n".join(lines)


def _render_settings_keys_section(migrations: dict[str, tuple[str, list[str]]]) -> str:
    lines = [
        "### 11.2 settings.json key map",
        "",
        "Generated by `python scripts/gen_env_docs.py --write` from shared settings resolver calls.",
        "",
        "| Legacy env var | settings.json path | First reference |",
        "|---|---|---|",
    ]
    for env_var in sorted(migrations):
        settings_key, references = migrations[env_var]
        lines.append(f"| `{env_var}` | `{settings_key}` | `{references[0]}` |")
    lines.append("")
    return "\n".join(lines)


def _rewrite_section(docs_text: str, new_body: str) -> str:
    """Replace the §11.1 section body with new_body. Keeps surrounding structure."""
    start = docs_text.find(_SECTION_HEADER)
    if start == -1:
        raise SystemExit(f"[gen_env_docs] section header not found: {_SECTION_HEADER!r}")
    # End marker: first horizontal rule after header (between §11.1 and §12)
    end = docs_text.find(f"\n{_SECTION_END}\n", start)
    if end == -1:
        raise SystemExit(f"[gen_env_docs] section terminator '{_SECTION_END}' not found after §11.1")
    return docs_text[:start] + new_body + "\n" + docs_text[end + 1:]


def _rewrite_settings_keys_section(docs_text: str, new_body: str) -> str:
    header = "### 11.2 settings.json key map"
    start = docs_text.find(header)
    if start == -1:
        insertion = docs_text.find("\n## 12.")
        if insertion == -1:
            return docs_text.rstrip() + "\n\n" + new_body + "\n"
        return docs_text[:insertion] + "\n\n" + new_body + docs_text[insertion:]
    next_section = docs_text.find("\n## 12.", start)
    if next_section == -1:
        return docs_text[:start] + new_body + "\n"
    return docs_text[:start] + new_body + "\n" + docs_text[next_section:]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Exit 1 if missing vars found")
    parser.add_argument("--list", action="store_true", help="Print all found vars and exit")
    parser.add_argument(
        "--write",
        action="store_true",
        help=f"Rewrite §11.1 of {DOCS_FILE.name} with undocumented vars grouped by module",
    )
    args = parser.parse_args()

    found = find_env_vars()
    migrations = find_settings_migrations()
    # --write regenerates §11.1 from scratch and must not count its own prior
    # output as documentation; --check / bare run treat §11.1 as acknowledged.
    documented = load_documented_vars(include_autogen=not args.write)

    if args.list:
        for var in sorted(found):
            refs = found[var]
            print(f"{var}  ({refs[0]}{'...' if len(refs) > 1 else ''})")
        return 0

    missing = {v: refs for v, refs in found.items() if v not in documented}

    if args.write:
        if not DOCS_FILE.exists():
            print(f"[gen_env_docs] docs file not found: {DOCS_FILE}", file=sys.stderr)
            return 2
        body = _render_missing_section(missing)
        new_text = _rewrite_section(DOCS_FILE.read_text(encoding="utf-8"), body)
        new_text = _rewrite_settings_keys_section(new_text, _render_settings_keys_section(migrations))
        DOCS_FILE.write_text(new_text, encoding="utf-8")
        print(
            f"[gen_env_docs] rewrote §11.1 in {DOCS_FILE.relative_to(REPO_ROOT)}: "
            f"{len(missing)} undocumented var(s) across {len({_module_label(refs[0]) for refs in missing.values()})} module(s)."
        )
        return 0

    if missing:
        print(f"[gen_env_docs] {len(missing)} env var(s) found in source but not in {DOCS_FILE.relative_to(REPO_ROOT)}:\n")
        for var in sorted(missing):
            refs = missing[var][:3]
            suffix = f" (+{len(missing[var]) - 3} more)" if len(missing[var]) > 3 else ""
            print(f"  {var}")
            for r in refs:
                print(f"    {r}")
            if suffix:
                print(f"    {suffix}")
        print()
        if args.check:
            print(f"Run `python scripts/gen_env_docs.py --write` to regenerate §11.1 in {DOCS_FILE.name}.")
            return 1
    else:
        print(f"[gen_env_docs] All {len(found)} env vars are documented in {DOCS_FILE.relative_to(REPO_ROOT)}.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
