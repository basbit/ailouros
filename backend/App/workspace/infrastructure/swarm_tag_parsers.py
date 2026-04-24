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


def _shell_block_body_from_match(match: re.Match[str]) -> str:
    group1, group2 = match.group(1), match.group(2)
    return group1 if group1 is not None else (group2 or "")


_LIFT_SHELL_FROM_XML_FENCE = re.compile(
    r"```xml\s*\r?\n"
    r"(\s*"
    r"(?:<swarm_shell>.*?</swarm_shell>|<swarm-command>.*?</swarm-command>)"
    r"\s*)\r?\n```",
    re.DOTALL | re.IGNORECASE,
)


def _lift_swarm_shell_from_prompt_style_xml_fences(text: str) -> str:
    return _LIFT_SHELL_FROM_XML_FENCE.sub(lambda match: match.group(1).strip(), text)


_PAT_SWARM_SHELL_OR_CMD = re.compile(
    r"<swarm_shell>.*?</swarm_shell>|<swarm-command>.*?</swarm-command>",
    re.DOTALL | re.IGNORECASE,
)


def _bash_sh_fence_body_is_only_swarm_shell(inner: str) -> bool:
    stripped = _PAT_SWARM_SHELL_OR_CMD.sub("", inner)
    for line in stripped.splitlines():
        token = line.strip()
        if not token:
            continue
        if token.startswith("#"):
            continue
        return False
    return True


def _lift_swarm_shell_from_bash_sh_fences(text: str) -> str:
    out: list[str] = []
    position = 0
    pat_open = re.compile(r"```(bash|sh|shell)\s*\r?\n", re.IGNORECASE)
    while True:
        match = pat_open.search(text, position)
        if not match:
            out.append(text[position:])
            break
        out.append(text[position:match.start()])
        start_body = match.end()
        end_fence = text.find("```", start_body)
        if end_fence < 0:
            out.append(text[match.start():])
            break
        inner = text[start_body:end_fence]
        if _bash_sh_fence_body_is_only_swarm_shell(inner):
            out.append(inner.strip())
        else:
            out.append(text[match.start(): end_fence + 3])
        position = end_fence + 3
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
    spans: list[tuple[int, int]] = []
    cursor = 0
    text_len = len(text)
    while cursor < text_len:
        open_pos = text.find("```", cursor)
        if open_pos < 0:
            break
        close_pos = text.find("```", open_pos + 3)
        if close_pos < 0:
            break
        spans.append((open_pos, close_pos + 3))
        cursor = close_pos + 3
    return spans


def _position_inside_fences(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(start <= pos < end for start, end in spans)


def parse_swarm_patch_hunks(body: str) -> list[tuple[str, str]]:
    hunks: list[tuple[str, str]] = []
    rest = body
    while True:
        search_marker = "<<<<<<< SEARCH"
        index = rest.find(search_marker)
        if index < 0:
            break
        rest = rest[index + len(search_marker):]
        if rest.startswith("\r\n"):
            rest = rest[2:]
        elif rest.startswith("\n"):
            rest = rest[1:]
        sep_plain = "\n=======\n"
        sep_win = "\r\n=======\r\n"
        sep_index = rest.find(sep_plain)
        sep_len = len(sep_plain)
        if sep_index < 0:
            sep_index = rest.find(sep_win)
            sep_len = len(sep_win)
        if sep_index < 0:
            raise ValueError("no ======= separator between SEARCH and REPLACE")
        old = rest[:sep_index]
        rest = rest[sep_index + sep_len:]
        end_plain = "\n>>>>>>> REPLACE"
        end_win = "\r\n>>>>>>> REPLACE"
        end_index = rest.find(end_plain)
        end_len = len(end_plain)
        if end_index < 0:
            end_index = rest.find(end_win)
            end_len = len(end_win)
        if end_index < 0:
            raise ValueError("missing >>>>>>> REPLACE closing marker")
        new = rest[:end_index]
        rest = rest[end_index + end_len:]
        hunks.append((old, new))
    if not hunks and body.strip():
        raise ValueError("no <<<<<<< SEARCH … >>>>>>> REPLACE blocks found")
    return hunks


def _collect_ordered_actions(text: str) -> list[_Action]:
    events: list[_Action] = []
    fence_spans = _markdown_fence_spans(text)
    for match in _PAT_FILE.finditer(text):
        events.append(
            _FileWriteAction("file", match.start(), match.group(1).strip(), match.group(2))
        )
    for match in _PAT_PATCH.finditer(text):
        events.append(_PatchAction("patch", match.start(), match.group(1).strip(), match.group(2)))
    shell_found = False
    for match in _PAT_SHELL.finditer(text):
        if _position_inside_fences(match.start(), fence_spans):
            continue
        events.append(_ShellAction("shell", match.start(), _shell_block_body_from_match(match)))
        shell_found = True
    if not shell_found:
        body_lines: list[str] = []
        for match in _PAT_BASH_FENCE.finditer(text):
            for line in match.group(1).splitlines():
                line_text = line.strip()
                if line_text and not line_text.startswith("#") and "<swarm_" not in line_text.lower():
                    body_lines.append(line_text)
        if body_lines:
            events.append(_ShellAction("shell", len(text), "\n".join(body_lines)))
    for match in _PAT_UDIFF.finditer(text):
        events.append(_UdiffAction("udiff", match.start(), match.group(1).strip(), match.group(2)))
    events.sort(key=lambda action: action.start)
    return events


def _apply_patch_block(
    root: Path,
    rel_path: str,
    raw_body: str,
    *,
    dry_run: bool,
) -> tuple[bool, list[str]]:
    from backend.App.workspace.infrastructure.patch_parser import safe_relative_path
    try:
        hunks = parse_swarm_patch_hunks(raw_body)
    except ValueError as e:
        return False, [f"patch {rel_path!r}: {e}"]
    if not hunks:
        return False, [f"patch {rel_path!r}: empty block"]

    try:
        dest = safe_relative_path(root, rel_path)
    except ValueError as e:
        return False, [f"patch {rel_path!r}: {e}"]

    exists = dest.is_file()
    if not exists:
        old0, new0 = hunks[0]
        if old0.strip() != "":
            return False, [
                f"patch {rel_path!r}: file does not exist — first SEARCH must be empty "
                "(create entire file from REPLACE)"
            ]
        content = new0
        start_index = 1
    else:
        try:
            content = dest.read_text(encoding="utf-8")
        except OSError as e:
            return False, [f"patch {rel_path!r}: read failed: {e}"]
        start_index = 0

    for hunk_index in range(start_index, len(hunks)):
        old, new = hunks[hunk_index]
        count = content.count(old)
        if count != 1:
            return False, [
                f"patch {rel_path!r}: hunk {hunk_index + 1}: SEARCH must occur exactly 1 time, found {count}"
            ]
        content = content.replace(old, new, 1)

    if not dry_run:
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
        except OSError as e:
            return False, [f"patch {rel_path!r}: write failed: {e}"]
    return True, []


def _apply_udiff_block(
    root: Path,
    rel_path: str,
    raw_body: str,
    *,
    dry_run: bool,
) -> tuple[bool, list[str]]:
    from backend.App.workspace.infrastructure.patch_parser import safe_relative_path
    try:
        safe_relative_path(root, rel_path)
    except ValueError as e:
        return False, [f"udiff {rel_path!r}: {e}"]

    body = raw_body.strip()
    if not body:
        return False, [f"udiff {rel_path!r}: empty diff"]
    if not body.startswith("---"):
        body = f"--- a/{rel_path}\n+++ b/{rel_path}\n" + body

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
            f"udiff {rel_path!r}: patch command not found; "
            "install patch or use <swarm_patch>"
        )
        return False, [msg]
    except subprocess.TimeoutExpired:
        return False, [f"udiff {rel_path!r}: timeout"]

    if subprocess_result.returncode != 0:
        err = (subprocess_result.stderr or b"").decode("utf-8", errors="replace")[:2000]
        return False, [f"udiff {rel_path!r}: patch exit {subprocess_result.returncode}: {err}"]
    return True, []


def _run_shell_block(
    root: Path,
    body: str,
    *,
    dry_run: bool,
    run_shell: bool,
) -> tuple[int, list[dict[str, Any]], list[str]]:
    shell_runs: list[dict[str, Any]] = []
    errors: list[str] = []
    shell_parsed = 0
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
            shell_parsed += 1
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
                record = run_allowlisted_command(
                    argv,
                    root.resolve(),
                    timeout_sec=_command_timeout_sec(),
                    env=_safe_subprocess_env(),
                )
                shell_runs.append(record)
                shell_parsed += 1
                if record.get("returncode") not in (None, 0):
                    errors.append(
                        f"shell exit {record.get('returncode')}: {line_text!r}"
                    )
                if record.get("error"):
                    errors.append(f"shell: {line_text!r}: {record.get('error')}")
                continue

            subprocess_result = subprocess.run(
                argv,
                cwd=str(root.resolve()),
                timeout=_command_timeout_sec(),
                capture_output=True,
                text=True,
                env=_safe_subprocess_env(),
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
            shell_parsed += 1
            if subprocess_result.returncode != 0:
                errors.append(f"shell exit {subprocess_result.returncode}: {line_text!r}")
        except subprocess.TimeoutExpired:
            shell_runs.append({"cmd": line_text, "error": "timeout"})
            errors.append(f"shell timeout: {line_text!r}")
            shell_parsed += 1
        except OSError as e:
            shell_runs.append({"cmd": line_text, "error": str(e)})
            errors.append(f"shell: {line_text!r}: {e}")
            shell_parsed += 1
    return shell_parsed, shell_runs, errors
