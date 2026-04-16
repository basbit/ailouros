"""Low-level swarm tag parsers: regex patterns, lift helpers, parse/apply functions.

Extracted from patch_parser.py to keep that file under 500 lines.
Contains: regex patterns, fence lift helpers, parse_swarm_patch_hunks,
_apply_patch_block, _apply_udiff_block, _run_shell_block, _collect_ordered_actions.
"""
from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

from backend.App.orchestration.infrastructure.sandbox_exec import exec_backend, run_allowlisted_command
from backend.App.workspace.infrastructure.workspace_io import (
    _Action,
    _FileWriteAction,
    _PatchAction,
    _ShellAction,
    _UdiffAction,
    _shell_command_allowed,
    _safe_subprocess_env,
    _command_timeout_sec,
)

logger = logging.getLogger(__name__)

_PAT_FILE = re.compile(
    r'<swarm_file\s+path=["\']([^"\']+)["\']>(.*?)</swarm_file>',
    re.DOTALL | re.IGNORECASE,
)
_PAT_PATCH = re.compile(
    r'<swarm_patch\s+path=["\']([^"\']+)["\']>(.*?)</swarm_patch>',
    re.DOTALL | re.IGNORECASE,
)
_PAT_SHELL = re.compile(
    r"<swarm_shell>(.*?)</swarm_shell>|<swarm-command>(.*?)</swarm-command>",
    re.DOTALL | re.IGNORECASE,
)


def _shell_block_body_from_match(m: re.Match[str]) -> str:
    """Группа 1 или 2 в зависимости от того, какой тег совпал."""
    g1, g2 = m.group(1), m.group(2)
    return g1 if g1 is not None else (g2 or "")


_LIFT_SHELL_FROM_XML_FENCE = re.compile(
    r"```xml\s*\r?\n"
    r"(\s*"
    r"(?:<swarm_shell>.*?</swarm_shell>|<swarm-command>.*?</swarm-command>)"
    r"\s*)\r?\n```",
    re.DOTALL | re.IGNORECASE,
)


def _lift_swarm_shell_from_prompt_style_xml_fences(text: str) -> str:
    """Поднимает изолированные теги shell из ```xml … ``` (копипаста из доки)."""
    return _LIFT_SHELL_FROM_XML_FENCE.sub(lambda m: m.group(1).strip(), text)


_PAT_SWARM_SHELL_OR_CMD = re.compile(
    r"<swarm_shell>.*?</swarm_shell>|<swarm-command>.*?</swarm-command>",
    re.DOTALL | re.IGNORECASE,
)


def _bash_sh_fence_body_is_only_swarm_shell(inner: str) -> bool:
    """True если внутри фенса только комментарии # и теги swarm_shell / swarm-command."""
    stripped = _PAT_SWARM_SHELL_OR_CMD.sub("", inner)
    for line in stripped.splitlines():
        t = line.strip()
        if not t:
            continue
        if t.startswith("#"):
            continue
        return False
    return True


def _lift_swarm_shell_from_bash_sh_fences(text: str) -> str:
    """Поднимает swarm_shell из ```bash|sh|shell … ``` если там нет постороннего bash-текста."""
    out: list[str] = []
    pos = 0
    pat_open = re.compile(r"```(bash|sh|shell)\s*\r?\n", re.IGNORECASE)
    while True:
        m = pat_open.search(text, pos)
        if not m:
            out.append(text[pos:])
            break
        out.append(text[pos:m.start()])
        start_body = m.end()
        end_fence = text.find("```", start_body)
        if end_fence < 0:
            out.append(text[m.start():])
            break
        inner = text[start_body:end_fence]
        if _bash_sh_fence_body_is_only_swarm_shell(inner):
            out.append(inner.strip())
        else:
            out.append(text[m.start(): end_fence + 3])
        pos = end_fence + 3
    return "".join(out)


_PAT_UDIFF = re.compile(
    r'<swarm_udiff\s+path=["\']([^"\']+)["\']>(.*?)</swarm_udiff>',
    re.DOTALL | re.IGNORECASE,
)

_PAT_BASH_FENCE = re.compile(
    r"```(?:bash|sh|shell)\s*\r?\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)


def _markdown_fence_spans(text: str) -> list[tuple[int, int]]:
    """Интервалы [start, end) — позиции от первого до последнего символа fenced-блока ```…```."""
    spans: list[tuple[int, int]] = []
    i = 0
    n = len(text)
    while i < n:
        j = text.find("```", i)
        if j < 0:
            break
        k = text.find("```", j + 3)
        if k < 0:
            break
        spans.append((j, k + 3))
        i = k + 3
    return spans


def _position_inside_fences(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(s <= pos < e for s, e in spans)


def parse_swarm_patch_hunks(body: str) -> list[tuple[str, str]]:
    """Парсит тело <swarm_patch>: повторяющиеся блоки <<<<<<< SEARCH / ======= / >>>>>>> REPLACE."""
    hunks: list[tuple[str, str]] = []
    rest = body
    while True:
        key = "<<<<<<< SEARCH"
        i = rest.find(key)
        if i < 0:
            break
        rest = rest[i + len(key):]
        if rest.startswith("\r\n"):
            rest = rest[2:]
        elif rest.startswith("\n"):
            rest = rest[1:]
        sep_plain = "\n=======\n"
        sep_win = "\r\n=======\r\n"
        si = rest.find(sep_plain)
        sep_len = len(sep_plain)
        if si < 0:
            si = rest.find(sep_win)
            sep_len = len(sep_win)
        if si < 0:
            raise ValueError("no ======= separator between SEARCH and REPLACE")
        old = rest[:si]
        rest = rest[si + sep_len:]
        end_plain = "\n>>>>>>> REPLACE"
        end_win = "\r\n>>>>>>> REPLACE"
        ei = rest.find(end_plain)
        end_len = len(end_plain)
        if ei < 0:
            ei = rest.find(end_win)
            end_len = len(end_win)
        if ei < 0:
            raise ValueError("missing >>>>>>> REPLACE closing marker")
        new = rest[:ei]
        rest = rest[ei + end_len:]
        hunks.append((old, new))
    if not hunks and body.strip():
        raise ValueError("no <<<<<<< SEARCH … >>>>>>> REPLACE blocks found")
    return hunks


def _collect_ordered_actions(text: str) -> list[_Action]:
    events: list[_Action] = []
    fence_spans = _markdown_fence_spans(text)
    for m in _PAT_FILE.finditer(text):
        events.append(
            _FileWriteAction("file", m.start(), m.group(1).strip(), m.group(2))
        )
    for m in _PAT_PATCH.finditer(text):
        events.append(_PatchAction("patch", m.start(), m.group(1).strip(), m.group(2)))
    shell_found = False
    for m in _PAT_SHELL.finditer(text):
        if _position_inside_fences(m.start(), fence_spans):
            continue
        events.append(_ShellAction("shell", m.start(), _shell_block_body_from_match(m)))
        shell_found = True
    if not shell_found:
        body_lines: list[str] = []
        for m in _PAT_BASH_FENCE.finditer(text):
            for line in m.group(1).splitlines():
                line_text = line.strip()
                if line_text and not line_text.startswith("#") and "<swarm_" not in line_text.lower():
                    body_lines.append(line_text)
        if body_lines:
            events.append(_ShellAction("shell", len(text), "\n".join(body_lines)))
    for m in _PAT_UDIFF.finditer(text):
        events.append(_UdiffAction("udiff", m.start(), m.group(1).strip(), m.group(2)))
    events.sort(key=lambda a: a.start)
    return events


def _apply_patch_block(
    root: Path,
    rel: str,
    raw_body: str,
    *,
    dry_run: bool,
) -> tuple[bool, list[str]]:
    from backend.App.workspace.infrastructure.patch_parser import safe_relative_path
    try:
        hunks = parse_swarm_patch_hunks(raw_body)
    except ValueError as e:
        return False, [f"patch {rel!r}: {e}"]
    if not hunks:
        return False, [f"patch {rel!r}: empty block"]

    try:
        dest = safe_relative_path(root, rel)
    except ValueError as e:
        return False, [f"patch {rel!r}: {e}"]

    exists = dest.is_file()
    if not exists:
        o0, n0 = hunks[0]
        if o0.strip() != "":
            return False, [
                f"patch {rel!r}: file does not exist — first SEARCH must be empty "
                "(create entire file from REPLACE)"
            ]
        content = n0
        start_idx = 1
    else:
        try:
            content = dest.read_text(encoding="utf-8")
        except OSError as e:
            return False, [f"patch {rel!r}: read failed: {e}"]
        start_idx = 0

    for hi in range(start_idx, len(hunks)):
        old, new = hunks[hi]
        cnt = content.count(old)
        if cnt != 1:
            return False, [
                f"patch {rel!r}: hunk {hi + 1}: SEARCH must occur exactly 1 time, found {cnt}"
            ]
        content = content.replace(old, new, 1)

    if not dry_run:
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
        except OSError as e:
            return False, [f"patch {rel!r}: write failed: {e}"]
    return True, []


def _apply_udiff_block(
    root: Path,
    rel: str,
    raw_body: str,
    *,
    dry_run: bool,
) -> tuple[bool, list[str]]:
    """Unified diff (формат patch). Нужен бинарник ``patch`` (GNU/BSD)."""
    from backend.App.workspace.infrastructure.patch_parser import safe_relative_path
    try:
        safe_relative_path(root, rel)
    except ValueError as e:
        return False, [f"udiff {rel!r}: {e}"]

    body = raw_body.strip()
    if not body:
        return False, [f"udiff {rel!r}: empty diff"]
    if not body.startswith("---"):
        body = f"--- a/{rel}\n+++ b/{rel}\n" + body

    root = root.resolve()
    cmd = [
        "patch",
        "-d",
        str(root),
        "-p0",
        "--forward",
        "--batch",
    ]
    if dry_run:
        cmd.append("-C")
    try:
        subprocess_result = subprocess.run(
            cmd,
            input=body.encode("utf-8"),
            capture_output=True,
            timeout=min(_command_timeout_sec(), 120),
        )
    except FileNotFoundError:
        msg = (
            f"udiff {rel!r}: patch command not found; "
            "install patch or use <swarm_patch>"
        )
        return False, [msg]
    except subprocess.TimeoutExpired:
        return False, [f"udiff {rel!r}: timeout"]

    if subprocess_result.returncode != 0:
        err = (subprocess_result.stderr or b"").decode("utf-8", errors="replace")[:2000]
        return False, [f"udiff {rel!r}: patch exit {subprocess_result.returncode}: {err}"]
    return True, []


def _run_shell_block(
    root: Path,
    body: str,
    *,
    dry_run: bool,
    run_shell: bool,
) -> tuple[int, list[dict[str, Any]], list[str]]:
    """Возвращает (parsed_commands, shell_runs, errors)."""
    shell_runs: list[dict[str, Any]] = []
    errors: list[str] = []
    parsed = 0
    for line in body.splitlines():
        line_text = line.strip()
        if not line_text or line_text.startswith("#"):
            continue
        ok, reason = _shell_command_allowed(line)
        if not ok:
            shell_runs.append({"cmd": line_text, "skipped": True, "reason": reason})
            continue
        if not run_shell:
            shell_runs.append(
                {
                    "cmd": line_text,
                    "skipped": True,
                    "reason": "SWARM_ALLOW_COMMAND_EXEC is off",
                }
            )
            continue
        if dry_run:
            shell_runs.append({"cmd": line_text, "dry_run": True})
            parsed += 1
            continue
        try:
            argv = shlex.split(line, posix=os.name != "nt")
            resolved_bin = shutil.which(argv[0])
            if resolved_bin:
                resolved_bin_path = Path(resolved_bin).resolve()
                workspace_resolved = root.resolve()
                try:
                    resolved_bin_path.relative_to(workspace_resolved)
                    shell_runs.append({
                        "cmd": line_text,
                        "skipped": True,
                        "reason": "binary resolves inside workspace (possible PATH hijack)",
                    })
                    errors.append(f"security: binary inside workspace: {argv[0]!r}")
                    continue
                except ValueError:
                    pass
            if exec_backend() != "host":
                rec = run_allowlisted_command(
                    argv,
                    root.resolve(),
                    timeout_sec=_command_timeout_sec(),
                    env=_safe_subprocess_env(),
                )
                shell_runs.append(rec)
                parsed += 1
                if rec.get("returncode") not in (None, 0):
                    errors.append(
                        f"shell exit {rec.get('returncode')}: {line_text!r}"
                    )
                if rec.get("error"):
                    errors.append(f"shell: {line_text!r}: {rec.get('error')}")
                continue

            subprocess_result = subprocess.run(
                argv,
                cwd=str(root.resolve()),
                timeout=_command_timeout_sec(),
                capture_output=True,
                text=True,
                env=_safe_subprocess_env(),
                # Never let the subprocess read from the orchestrator's stdin —
                # any command that prompts interactively (sudo, ssh-agent, npm
                # login, etc.) would otherwise block the whole pipeline until
                # the hard timeout. With DEVNULL it fails fast and the agent
                # gets an actionable error on retry. See §24 in future-plan.md.
                stdin=subprocess.DEVNULL,
            )
            shell_runs.append(
                {
                    "cmd": line_text,
                    "returncode": subprocess_result.returncode,
                    "stdout": (subprocess_result.stdout or "")[-8000:],
                    "stderr": (subprocess_result.stderr or "")[-8000:],
                }
            )
            parsed += 1
            if subprocess_result.returncode != 0:
                errors.append(f"shell exit {subprocess_result.returncode}: {line_text!r}")
        except subprocess.TimeoutExpired:
            shell_runs.append({"cmd": line_text, "error": "timeout"})
            errors.append(f"shell timeout: {line_text!r}")
            parsed += 1
        except OSError as e:
            shell_runs.append({"cmd": line_text, "error": str(e)})
            errors.append(f"shell: {line_text!r}: {e}")
            parsed += 1
    return parsed, shell_runs, errors
