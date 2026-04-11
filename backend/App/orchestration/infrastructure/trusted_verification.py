"""Infrastructure helpers for trusted verification command discovery/execution."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Optional


def _config_path() -> Path:
    project_root = (
        Path(os.environ.get("SWARM_PROJECT_ROOT", "")).resolve()
        if os.environ.get("SWARM_PROJECT_ROOT")
        else Path(__file__).resolve().parents[4]
    )
    return project_root / "config" / "trusted_verification_command_rules.json"


@lru_cache(maxsize=1)
def load_trusted_command_rules() -> list[tuple[str, list[list[str]]]]:
    raw = json.loads(_config_path().read_text(encoding="utf-8"))
    rules: list[tuple[str, list[list[str]]]] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        marker = str(item.get("marker") or "").strip()
        commands = item.get("commands") or []
        if marker:
            rules.append(
                (
                    marker,
                    [
                        [str(part) for part in command]
                        for command in commands
                        if isinstance(command, list) and command
                    ],
                )
            )
    return rules


def command_available(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def discover_trusted_commands(
    workspace_root: str,
    changed_files: Optional[list[str]] = None,
) -> list[list[str]]:
    if not workspace_root:
        return []

    root = Path(workspace_root)
    commands: list[list[str]] = []
    changed_paths = [
        root / f if not os.path.isabs(f) else Path(f)
        for f in (changed_files or [])
    ]

    for marker, cmds in load_trusted_command_rules():
        if (root / marker).exists():
            commands.extend(cmds)
            if marker == "Makefile":
                makefile_text = (root / marker).read_text(errors="replace")
                for target in ("lint", "check", "verify"):
                    if re.search(rf"^{target}\s*:", makefile_text, re.MULTILINE):
                        commands.append(["make", target])
                if re.search(r"^ci\s*:", makefile_text, re.MULTILINE):
                    commands.append(["make", "ci"])

    if (root / "pyproject.toml").exists() or (root / "setup.py").exists():
        py_files = [str(p) for p in changed_paths if p.suffix == ".py" and p.exists()]
        if py_files:
            commands.append(["python", "-m", "py_compile", *py_files])
        for linter in ("ruff", "flake8"):
            if command_available(linter):
                commands.append([linter, "check", "."])
                break

    php_files = [str(p) for p in changed_paths if p.suffix == ".php" and p.exists()]
    if php_files and command_available("php"):
        commands.extend([["php", "-l", path] for path in php_files])

    return commands


def run_trusted_command(
    cmd: list[str],
    *,
    cwd: str,
    timeout_sec: int = 120,
) -> tuple[int, str]:
    del timeout_sec
    shell_cmd = f"cd {shlex.quote(cwd)} && {' '.join(shlex.quote(part) for part in cmd)} 2>&1"
    pipe = os.popen(shell_cmd)
    try:
        output = pipe.read()
    finally:
        status = pipe.close()
    if status is None:
        return 0, output
    return int(status) >> 8, output
