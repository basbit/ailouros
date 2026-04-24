from __future__ import annotations

import contextlib
import contextvars
import logging
import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterable, Iterator
from typing import Any, Literal, Optional, Union

from backend.App.shared.domain.validators import is_under
from backend.App.workspace.domain.ports import (
    WORKSPACE_CONTEXT_MODE_DEFAULT,
    WORKSPACE_CONTEXT_MODE_TOOLS_ONLY,
)
from backend.App.workspace.infrastructure.workspace_index import (
    _append_workspace_index_omission_notes,
    _workspace_index_extra_ignore_dirs,
    _workspace_index_max_output_chars,
    _workspace_index_skip_large_bytes,
    _workspace_index_skip_suffixes,
    collect_workspace_file_index,
)
from backend.App.workspace.infrastructure.workspace_snapshot import (
    _DEFAULT_INPUT_MAX_CHARS,
    _truncate_snapshot_to_fit,
    build_input_with_workspace,
    collect_workspace_snapshot,
    collect_workspace_snapshot_async,
)

__all__ = [
    "WORKSPACE_CONTEXT_MODE_DEFAULT",
    "WORKSPACE_CONTEXT_MODE_TOOLS_ONLY",
    "_append_workspace_index_omission_notes",
    "_workspace_index_extra_ignore_dirs",
    "_workspace_index_max_output_chars",
    "_workspace_index_skip_large_bytes",
    "_workspace_index_skip_suffixes",
    "collect_workspace_file_index",
    "_DEFAULT_INPUT_MAX_CHARS",
    "_truncate_snapshot_to_fit",
    "build_input_with_workspace",
    "collect_workspace_snapshot",
    "collect_workspace_snapshot_async",
]

logger = logging.getLogger(__name__)

_DEFAULT_SUBPROCESS_ENV_ALLOWLIST = (
    "PATH,HOME,USER,LOGNAME,SHELL,LANG,LC_ALL,LC_CTYPE,TERM,"
    "TMPDIR,TEMP,TMP,NODE_ENV,npm_config_cache,"
    "VIRTUAL_ENV,CONDA_PREFIX,GOPATH,GOROOT,CARGO_HOME,RUSTUP_HOME,"
    "JAVA_HOME,MAVEN_HOME,PYTHONPATH,PYTHONHOME"
)
_SUBPROCESS_ENV_ALLOWLIST = frozenset(
    key.strip()
    for key in os.getenv("SWARM_SUBPROCESS_ENV_ALLOWLIST", _DEFAULT_SUBPROCESS_ENV_ALLOWLIST).split(",")
    if key.strip()
)


def _safe_subprocess_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key in _SUBPROCESS_ENV_ALLOWLIST}


_DEFAULT_IGNORE_DIRS = (
    ".git,.svn,.hg,.venv,venv,__pycache__,.pytest_cache,.mypy_cache,"
    "node_modules,.idea,.vscode,dist,build,.tox,artifacts"
)
_IGNORE_DIR_NAMES = frozenset(
    directory.strip()
    for directory in os.getenv("SWARM_WORKSPACE_IGNORE_DIRS", _DEFAULT_IGNORE_DIRS).split(",")
    if directory.strip()
)

WORKSPACE_CONTEXT_MODE_FULL = "full"
WORKSPACE_CONTEXT_MODE_INDEX_ONLY = "index_only"
WORKSPACE_CONTEXT_MODE_PRIORITY_PATHS = "priority_paths"
WORKSPACE_CONTEXT_MODE_POST_ANALYSIS_COMPACT = "post_analysis_compact"
WORKSPACE_CONTEXT_MODE_RETRIEVE = "retrieve"

VALID_WORKSPACE_CONTEXT_MODES: frozenset[str] = frozenset(
    {
        WORKSPACE_CONTEXT_MODE_FULL,
        WORKSPACE_CONTEXT_MODE_INDEX_ONLY,
        WORKSPACE_CONTEXT_MODE_PRIORITY_PATHS,
        WORKSPACE_CONTEXT_MODE_POST_ANALYSIS_COMPACT,
        WORKSPACE_CONTEXT_MODE_TOOLS_ONLY,
        WORKSPACE_CONTEXT_MODE_RETRIEVE,
    }
)

_DEFAULT_MAX_FILES = 200
_DEFAULT_MAX_TOTAL_BYTES = 400_000
_DEFAULT_MAX_FILE_BYTES = 60_000
_DEFAULT_MAX_CONTEXT_FILE_BYTES = 400_000


def workspace_write_allowed() -> bool:
    from backend.App.orchestration.application.enforcement.enforcement_policy import (
        swarm_env_strings_that_mean_enabled,
    )

    env_value = os.getenv("SWARM_ALLOW_WORKSPACE_WRITE", "").strip().lower()
    return env_value in swarm_env_strings_that_mean_enabled()


def command_exec_allowed() -> bool:
    from backend.App.orchestration.application.enforcement.enforcement_policy import (
        swarm_env_strings_that_mean_enabled,
    )

    env_value = os.getenv("SWARM_ALLOW_COMMAND_EXEC", "").strip().lower()
    return env_value in swarm_env_strings_that_mean_enabled()


def _load_default_shell_allowlist() -> str:
    from backend.App.shared.infrastructure.app_config_load import load_app_config_json

    shell_config = load_app_config_json("workspace_shell_allowlist.json")
    all_commands: list[str] = list(shell_config["readonly_commands"]) + list(shell_config["write_commands"])
    return ",".join(all_commands)


_DEFAULT_SHELL_ALLOWLIST = _load_default_shell_allowlist()


def _shell_allowlist() -> frozenset[str]:
    env_value = os.getenv("SWARM_SHELL_ALLOWLIST", _DEFAULT_SHELL_ALLOWLIST)
    return frozenset(command.strip().lower() for command in env_value.replace(";", ",").split(",") if command.strip())


_RUNTIME_SHELL_ALLOWLIST: contextvars.ContextVar[frozenset[str]] = contextvars.ContextVar(
    "swarm.shell.runtime_allowlist",
    default=frozenset(),
)


def _runtime_shell_allowlist() -> frozenset[str]:
    return _RUNTIME_SHELL_ALLOWLIST.get()


def extend_runtime_shell_allowlist(binaries: Iterable[str]) -> frozenset[str]:
    current = _RUNTIME_SHELL_ALLOWLIST.get()
    extra: set[str] = set()
    for raw in binaries:
        if not raw:
            continue
        name = Path(raw).name.lower()
        if name.endswith(".exe"):
            name = name[:-4]
        if name:
            extra.add(name)
    merged = frozenset(current | extra)
    _RUNTIME_SHELL_ALLOWLIST.set(merged)
    return merged


@contextlib.contextmanager
def scoped_runtime_shell_allowlist(
    initial: Optional[Iterable[str]] = None,
) -> Iterator[None]:
    token = _RUNTIME_SHELL_ALLOWLIST.set(
        frozenset()
        if initial is None
        else frozenset(Path(raw).name.lower().removesuffix(".exe") for raw in initial if raw)
    )
    try:
        yield
    finally:
        _RUNTIME_SHELL_ALLOWLIST.reset(token)


def extract_command_binary(line: str) -> Optional[str]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    try:
        parts = shlex.split(stripped, posix=os.name != "nt")
    except ValueError:
        return None
    if not parts:
        return None
    name = Path(parts[0]).name.lower()
    return name[:-4] if name.endswith(".exe") else name


def _shell_command_allowed(line: str) -> tuple[bool, str]:
    command_line = line.strip()
    if not command_line or command_line.startswith("#"):
        return False, "empty or comment"
    try:
        parts = shlex.split(command_line, posix=os.name != "nt")
    except ValueError as error:
        return False, f"shlex: {error}"
    if not parts:
        return False, "no argv"
    executable_name = Path(parts[0]).name.lower()
    if executable_name.endswith(".exe"):
        executable_name = executable_name[:-4]
    if executable_name == "sudo":
        return False, (
            "sudo is not supported by the automated shell (no TTY / no password "
            "prompt) — ask the user to run privileged setup manually, or use a "
            "user-level package manager. See docs/future-plan.md §24."
        )
    if executable_name in _shell_allowlist():
        return True, ""
    if executable_name in _runtime_shell_allowlist():
        return True, ""
    return False, (
        f"not in allowlist: {executable_name!r} "
        "(user approval can extend the per-task allowlist)"
    )


def _command_timeout_sec() -> int:
    try:
        return max(1, int(os.getenv("SWARM_COMMAND_TIMEOUT", "300")))
    except ValueError:
        return 300


@dataclass(frozen=True)
class _FileWriteAction:
    kind: Literal["file"]
    start: int
    rel: str
    body: str


@dataclass(frozen=True)
class _PatchAction:
    kind: Literal["patch"]
    start: int
    rel: str
    body: str


@dataclass(frozen=True)
class _ShellAction:
    kind: Literal["shell"]
    start: int
    body: str


@dataclass(frozen=True)
class _UdiffAction:
    kind: Literal["udiff"]
    start: int
    rel: str
    body: str


_Action = Union[_FileWriteAction, _PatchAction, _ShellAction, _UdiffAction]


# Backwards-compatible private aliases: canonical impls live in
# backend.App.shared.domain.validators. External imports inside the workspace
# domain still reference these names (e.g. patch_parser, project_settings), so
# we keep the aliases until those call sites are updated.
_is_under = is_under


def _assert_under_workspace(path: Path, workspace_root: "Path | str") -> None:
    # Thin workspace-specific wrapper over shared.assert_safe_path that keeps
    # the "outside workspace" wording consumed by workspace-domain tests.
    resolved = path.resolve()
    root_resolved = Path(workspace_root).resolve()
    if not is_under(root_resolved, resolved):
        raise ValueError(
            f"Path {path!r} resolves to {resolved!r} which is outside workspace "
            f"{root_resolved!r} — possible path traversal attempt"
        )


def validate_workspace_root(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        raise ValueError(f"workspace_root is not a directory: {resolved}")

    base_raw = os.getenv("SWARM_WORKSPACE_BASE", "").strip()
    if base_raw:
        base = Path(base_raw).expanduser().resolve()
        if not base.is_dir():
            raise ValueError("SWARM_WORKSPACE_BASE is not a directory")
        if not _is_under(base, resolved):
            raise ValueError(
                f"workspace_root must be inside SWARM_WORKSPACE_BASE ({base})"
            )
    return resolved


def normalize_workspace_context_mode(raw: str) -> str:
    mode_key = (raw or "").strip().lower()
    if mode_key in VALID_WORKSPACE_CONTEXT_MODES:
        return mode_key
    return WORKSPACE_CONTEXT_MODE_FULL


def normalize_domain_context_mode(raw: str) -> str:
    from backend.App.workspace.domain.context_mode import (
        normalize_workspace_context_mode as _normalize_domain,
    )
    return _normalize_domain(raw)


def resolve_workspace_context_mode(agent_config: Optional[dict[str, Any]]) -> str:
    swarm = (agent_config or {}).get("swarm")
    swarm_config = swarm if isinstance(swarm, dict) else {}
    explicit = str(swarm_config.get("workspace_context_mode") or "").strip()
    if explicit:
        return normalize_workspace_context_mode(explicit)
    return normalize_workspace_context_mode(
        os.getenv("SWARM_WORKSPACE_CONTEXT_MODE", WORKSPACE_CONTEXT_MODE_DEFAULT)
    )


def tools_only_workspace_placeholder(root_display: str) -> str:
    root_label = (root_display or "").strip() or "(not set)"
    return (
        f"# Workspace root (orchestrator host): {root_label}\n\n"
        "No file contents are inlined in this prompt. Use **MCP filesystem tools** "
        "(server name `workspace`) to read files under that root as needed.\n"
    )


def _priority_globs_from_env_and_file(root: Path) -> list[str]:
    patterns: list[str] = []
    context_file = root / ".swarm" / "context.txt"
    if context_file.is_file():
        try:
            context_text = context_file.read_text(encoding="utf-8")
        except OSError as error:
            raise OSError(
                f"priority_paths: could not read .swarm/context.txt: {error}"
            ) from error
        file_patterns: list[str] = []
        for line in context_text.splitlines():
            pattern = line.strip()
            if not pattern or pattern.startswith("#"):
                continue
            file_patterns.append(pattern)
        if file_patterns:
            logger.info(
                "priority_paths: using .swarm/context.txt: %d paths/globs",
                len(file_patterns),
            )
        patterns.extend(file_patterns)
    env_globs = (os.getenv("SWARM_WORKSPACE_PRIORITY_GLOBS") or "").strip()
    if env_globs:
        env_patterns: list[str] = []
        for part in env_globs.replace(";", ",").split(","):
            glob_pattern = part.strip()
            if glob_pattern:
                env_patterns.append(glob_pattern)
        if env_patterns:
            logger.info(
                "priority_paths: using SWARM_WORKSPACE_PRIORITY_GLOBS: %d paths/globs",
                len(env_patterns),
            )
        patterns.extend(env_patterns)
    return patterns


def collect_workspace_priority_snapshot(
    root: Path,
    *,
    max_total_bytes: Optional[int] = None,
    max_file_bytes: Optional[int] = None,
) -> tuple[str, int]:
    root = root.resolve()
    context_file = root / ".swarm" / "context.txt"
    patterns = _priority_globs_from_env_and_file(root)
    if not patterns:
        if not context_file.is_file() and not (os.getenv("SWARM_WORKSPACE_PRIORITY_GLOBS") or "").strip():
            raise ValueError(
                f".swarm/context.txt not found, required for priority_paths mode "
                f"(expected at {context_file}). "
                "Create the file with one glob or relative path per line, "
                "or set SWARM_WORKSPACE_PRIORITY_GLOBS."
            )
        raise ValueError(
            "workspace_context_mode=priority_paths requires non-empty patterns: "
            "add .swarm/context.txt (one glob or relative path per line) and/or set "
            "SWARM_WORKSPACE_PRIORITY_GLOBS (comma-separated globs relative to workspace root)"
        )
    total_byte_limit = max_total_bytes if max_total_bytes is not None else int(
        os.getenv("SWARM_WORKSPACE_MAX_BYTES", str(_DEFAULT_MAX_TOTAL_BYTES))
    )
    file_byte_limit = max_file_bytes if max_file_bytes is not None else int(
        os.getenv("SWARM_WORKSPACE_MAX_FILE_BYTES", str(_DEFAULT_MAX_FILE_BYTES))
    )

    seen: set[str] = set()
    matched: list[Path] = []
    for pattern in patterns:
        candidate_path = (root / pattern).resolve()
        if "*" not in pattern and "?" not in pattern and "[" not in pattern:
            if candidate_path.is_file() and _is_under(root, candidate_path):
                rel_str = str(candidate_path.relative_to(root).as_posix())
                if rel_str not in seen:
                    seen.add(rel_str)
                    matched.append(candidate_path)
            continue
        try:
            for hit in root.glob(pattern):
                if not hit.is_file():
                    continue
                hit_resolved = hit.resolve()
                if not _is_under(root, hit_resolved):
                    continue
                rel_str = str(hit_resolved.relative_to(root).as_posix())
                if rel_str not in seen:
                    seen.add(rel_str)
                    matched.append(hit_resolved)
        except OSError:
            continue

    matched.sort(key=lambda file_path: str(file_path.relative_to(root).as_posix()))
    pattern_note = f"Patterns: {', '.join(patterns[:20])}{' …' if len(patterns) > 20 else ''}"
    parts: list[str] = [
        f"# Workspace root: {root}\n",
        "\n## Priority paths (matched)\n\n",
        pattern_note + "\n\n",
    ]
    total = 0
    count = 0
    for matched_path in matched:
        relative_path = matched_path.relative_to(root)
        try:
            file_stat = matched_path.stat()
        except OSError:
            continue
        if file_stat.st_size > file_byte_limit:
            parts.append(f"\n## file: {relative_path.as_posix()} (skipped, {file_stat.st_size} bytes > max)\n")
            continue
        try:
            file_bytes = matched_path.read_bytes()
        except OSError:
            continue
        if b"\x00" in file_bytes[:8000]:
            parts.append(f"\n## file: {relative_path.as_posix()} (skipped, binary)\n")
            continue
        try:
            text = file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            parts.append(f"\n## file: {relative_path.as_posix()} (skipped, non-utf8)\n")
            continue
        block = f"\n## file: {relative_path.as_posix()}\n```\n{text}\n```\n"
        encoded_length = len(block.encode("utf-8"))
        if total + encoded_length > total_byte_limit:
            parts.append("\n# … [priority snapshot truncated: max_bytes]\n")
            break
        parts.append(block)
        total += encoded_length
        count += 1
    return "".join(parts), count


def resolve_project_context_path(raw: str, workspace_root: Optional[Path]) -> Path:
    path = Path(raw.strip()).expanduser()
    if path.is_absolute():
        return path.resolve()
    if workspace_root is not None:
        return (workspace_root / path).resolve()
    return (Path.cwd() / path).resolve()


def validate_readable_file(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_file():
        raise ValueError(f"project_context_file is not a file: {resolved}")
    base_raw = os.getenv("SWARM_WORKSPACE_BASE", "").strip()
    if base_raw:
        base = Path(base_raw).expanduser().resolve()
        if not base.is_dir():
            raise ValueError("SWARM_WORKSPACE_BASE is not a directory")
        if not _is_under(base, resolved):
            raise ValueError(
                f"project_context_file must be under SWARM_WORKSPACE_BASE ({base})"
            )
    return resolved


def read_project_context_file(
    path: Path,
    max_bytes: Optional[int] = None,
) -> str:
    byte_limit = max_bytes if max_bytes is not None else int(
        os.getenv(
            "SWARM_MAX_CONTEXT_FILE_BYTES",
            str(_DEFAULT_MAX_CONTEXT_FILE_BYTES),
        )
    )
    file_stat = path.stat()
    if file_stat.st_size > byte_limit:
        raise ValueError(
            f"project_context_file too large ({file_stat.st_size} bytes, max {byte_limit})"
        )
    return path.read_text(encoding="utf-8")
