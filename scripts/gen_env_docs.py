#!/usr/bin/env python3
"""Scan source files for os.getenv() calls and report variables missing from docs/AGENT_SWARM.md (§11).

Usage:
    python scripts/gen_env_docs.py              # report missing vars
    python scripts/gen_env_docs.py --check      # exit 1 if any are missing (for CI)
    python scripts/gen_env_docs.py --list       # just print all found env vars
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
DOCS_FILE = ROOT / "docs" / "AGENT_SWARM.md"
# Scan canonical code roots (legacy top-level packages may be empty after DDD migration).
SOURCE_DIRS = ["backend", "orchestrator", "integrations", "agents", "pipeline", "code_analysis"]
SOURCE_GLOBS = ["**/*.py"]

# Pattern: os.getenv("VAR_NAME", ...) or os.getenv('VAR_NAME', ...)
_GETENV_RE = re.compile(r'os\.getenv\(\s*["\']([A-Z][A-Z0-9_]{2,})["\']')
# Also catch os.environ.get("VAR", ...)
_ENVIRON_RE = re.compile(r'os\.environ\.get\(\s*["\']([A-Z][A-Z0-9_]{2,})["\']')
# And os.environ["VAR"]
_ENVIRON_KEY_RE = re.compile(r'os\.environ\[\s*["\']([A-Z][A-Z0-9_]{2,})["\']')


def find_env_vars() -> dict[str, list[str]]:
    """Return {VAR_NAME: [file:line, ...]} for all env vars found in source."""
    found: dict[str, list[str]] = {}
    for src_dir in SOURCE_DIRS:
        base = ROOT / src_dir
        if not base.exists():
            continue
        for glob in SOURCE_GLOBS:
            for path in sorted(base.glob(glob)):
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                rel = str(path.relative_to(ROOT))
                for lineno, line in enumerate(text.splitlines(), 1):
                    for pat in (_GETENV_RE, _ENVIRON_RE, _ENVIRON_KEY_RE):
                        for m in pat.finditer(line):
                            var = m.group(1)
                            found.setdefault(var, []).append(f"{rel}:{lineno}")
    return found


def load_documented_vars() -> set[str]:
    """Return set of VAR_NAMEs mentioned in backticks in docs/AGENT_SWARM.md."""
    if not DOCS_FILE.exists():
        return set()
    text = DOCS_FILE.read_text(encoding="utf-8")
    return set(re.findall(r"`([A-Z][A-Z0-9_]{2,})`", text))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Exit 1 if missing vars found")
    parser.add_argument("--list", action="store_true", help="Print all found vars and exit")
    args = parser.parse_args()

    found = find_env_vars()
    documented = load_documented_vars()

    if args.list:
        for var in sorted(found):
            refs = found[var]
            print(f"{var}  ({refs[0]}{'...' if len(refs) > 1 else ''})")
        return 0

    missing = {v: refs for v, refs in found.items() if v not in documented}

    if missing:
        print(f"[gen_env_docs] {len(missing)} env var(s) found in source but not in docs/AGENT_SWARM.md:\n")
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
            print("Run with --list to see all vars. Add missing entries to docs/AGENT_SWARM.md (§11).")
            return 1
    else:
        print(f"[gen_env_docs] All {len(found)} env vars are documented in docs/AGENT_SWARM.md.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
