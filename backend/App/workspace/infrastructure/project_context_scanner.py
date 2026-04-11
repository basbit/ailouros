"""ProjectContextScanner — lightweight rule-based project scanner.

Writes ``.swarm/project-context.md`` without calling an LLM.

Extracted from ``tasks.py`` (DECOMP-11).

Pure filesystem + heuristics: no LLM calls, no pipeline state.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_CONFIG_FILES: dict[str, str] = {
    "pyproject.toml": "Python",
    "setup.py": "Python",
    "requirements.txt": "Python",
    "package.json": "Node/JS/TS",
    "Cargo.toml": "Rust",
    "go.mod": "Go",
    "pom.xml": "Java/Maven",
    "composer.json": "PHP",
    "Gemfile": "Ruby",
    "build.gradle": "Java/Gradle",
    "CMakeLists.txt": "C/C++",
}
# Extend via env: SWARM_PROJECT_CONFIG_FILES_EXTRA="Makefile:Make,Dockerfile:Docker"
_extra_cfg = os.getenv("SWARM_PROJECT_CONFIG_FILES_EXTRA", "").strip()
if _extra_cfg:
    for pair in _extra_cfg.split(","):
        if ":" in pair:
            fname, lang = pair.split(":", 1)
            _CONFIG_FILES[fname.strip()] = lang.strip()

_IGNORE_DIRS: frozenset[str] = frozenset(
    d.strip() for d in os.getenv(
        "SWARM_PROJECT_SCANNER_IGNORE_DIRS",
        "node_modules,.git,__pycache__,.venv,venv,dist,build,.swarm"
    ).split(",") if d.strip()
)


def scan_project(root: Path) -> None:
    """Write ``.swarm/project-context.md`` via a quick rule-based scan.

    The scan detects language config files and lists top-level directories.
    File-system errors are propagated to the caller — callers that want
    best-effort behaviour should catch OSError themselves.

    Args:
        root: Absolute path to the project root directory.
    """
    detected_langs: list[str] = []
    config_found: list[str] = []
    for fname, lang in _CONFIG_FILES.items():
        if (root / fname).is_file():
            config_found.append(fname)
            if lang not in detected_langs:
                detected_langs.append(lang)

    top_dirs: list[str] = []
    try:
        top_dirs = sorted(
            d.name for d in root.iterdir()
            if d.is_dir() and not d.name.startswith(".") and d.name not in _IGNORE_DIRS
        )
    except OSError as exc:
        logger.warning("ProjectContextScanner: could not list top-level dirs for %s: %s", root, exc)

    lines = ["# Project Context\n", f"\n**Root:** `{root}`\n"]
    if detected_langs:
        lines.append(f"\n**Primary stack:** {', '.join(detected_langs)}\n")
    if config_found:
        lines.append(f"\n**Config files:** {', '.join(f'`{f}`' for f in config_found)}\n")
    if top_dirs:
        lines.append("\n## Top-level directories\n")
        for d in top_dirs[:20]:
            lines.append(f"- `{d}/`\n")
    lines.append(
        "\n*Quick-scan placeholder — will be replaced after full code analysis.*\n"
    )

    swarm_dir = root / ".swarm"
    swarm_dir.mkdir(exist_ok=True)
    (swarm_dir / "project-context.md").write_text("".join(lines), encoding="utf-8")
    logger.debug("ProjectContextScanner: wrote .swarm/project-context.md for %s", root)


class ProjectContextScanner:
    """Scans a project root and writes ``.swarm/project-context.md``.

    Usage::

        scanner = ProjectContextScanner()
        scanner.scan(Path("/path/to/project"))
    """

    def scan(self, root: Path) -> None:
        """Run a quick rule-based scan and write the context file.

        Args:
            root: Absolute path to the project root directory.
        """
        scan_project(root)
