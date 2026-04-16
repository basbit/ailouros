"""Чтение снимка проекта с диска: swarm_file, swarm_patch, swarm_shell."""

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
    k.strip() for k in os.getenv("SWARM_SUBPROCESS_ENV_ALLOWLIST", _DEFAULT_SUBPROCESS_ENV_ALLOWLIST).split(",") if k.strip()
)


def _safe_subprocess_env() -> dict[str, str]:
    """Return a minimal safe copy of os.environ for subprocess calls."""
    return {k: v for k, v in os.environ.items() if k in _SUBPROCESS_ENV_ALLOWLIST}


_DEFAULT_IGNORE_DIRS = (
    ".git,.svn,.hg,.venv,venv,__pycache__,.pytest_cache,.mypy_cache,"
    "node_modules,.idea,.vscode,dist,build,.tox,artifacts"
)
_IGNORE_DIR_NAMES = frozenset(
    d.strip() for d in os.getenv("SWARM_WORKSPACE_IGNORE_DIRS", _DEFAULT_IGNORE_DIRS).split(",") if d.strip()
)

# Режимы контекста файлов в промпте (оркестратор / agent_config.swarm.workspace_context_mode / ENV).
WORKSPACE_CONTEXT_MODE_FULL = "full"
WORKSPACE_CONTEXT_MODE_INDEX_ONLY = "index_only"
WORKSPACE_CONTEXT_MODE_PRIORITY_PATHS = "priority_paths"
WORKSPACE_CONTEXT_MODE_POST_ANALYSIS_COMPACT = "post_analysis_compact"
# Как в IDE: в промпт не кладём тела файлов — модель читает репозиторий через MCP;
# если MCP недоступен, явный fallback на индекс путей (см. prepare_workspace).
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
    env_value = os.getenv("SWARM_ALLOW_WORKSPACE_WRITE", "").strip().lower()
    return env_value in ("1", "true", "yes", "on")


def command_exec_allowed() -> bool:
    """Выполнение команд из <swarm_shell> (отдельно от записи файлов)."""
    env_value = os.getenv("SWARM_ALLOW_COMMAND_EXEC", "").strip().lower()
    return env_value in ("1", "true", "yes", "on")


_DEFAULT_SHELL_ALLOWLIST = (
    "npm,npx,yarn,pnpm,corepack,bun,node,pip,pip3,python,python3,uv,make,cargo,rustc,"
    "go,composer,php,dotnet,gradle,mvn,pytest,eslint,prettier,tsc,jest,vitest,ruff,flake8"
)


def _shell_allowlist() -> frozenset[str]:
    env_value = os.getenv("SWARM_SHELL_ALLOWLIST", _DEFAULT_SHELL_ALLOWLIST)
    return frozenset(x.strip().lower() for x in env_value.replace(";", ",").split(",") if x.strip())


# Per-task runtime extension to the shell allowlist. Populated by the SSE
# handler after the user explicitly approves a batch of shell commands whose
# binaries are not in ``SWARM_SHELL_ALLOWLIST``. Scoped via a ContextVar so
# concurrent tasks cannot leak into each other.
#
# Design rationale (2026-04-16): the old behaviour silently dropped any
# command whose binary wasn't in the static env allowlist, even after the
# user approved it. That made it impossible for devops to bootstrap a
# project with engine-specific tooling (e.g. ``godot``, ``flutter``) without
# the operator editing env vars. The runtime extension makes the approval
# UI the authoritative source of "yes, run this binary for this task".
_RUNTIME_SHELL_ALLOWLIST: contextvars.ContextVar[frozenset[str]] = contextvars.ContextVar(
    "swarm.shell.runtime_allowlist",
    default=frozenset(),
)


def _runtime_shell_allowlist() -> frozenset[str]:
    return _RUNTIME_SHELL_ALLOWLIST.get()


def extend_runtime_shell_allowlist(binaries: Iterable[str]) -> frozenset[str]:
    """Add binaries to the current task's runtime shell allowlist.

    Binaries are normalised to lowercase and ``.exe`` stripped so the probe
    in ``_shell_command_allowed`` matches regardless of how the command was
    written. Returns the resulting (union) allowlist.

    Must be called inside a ``scoped_runtime_shell_allowlist`` context so the
    extension stays scoped to a single task. Outside of a scope, the call is
    a no-op so production code can't accidentally grow a global allowlist.
    """
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
    """Enter a fresh per-task runtime allowlist scope.

    Anything set via :func:`extend_runtime_shell_allowlist` inside the
    ``with`` block is discarded on exit, so a misbehaving task cannot extend
    the allowlist for later tasks that share the process.
    """
    token = _RUNTIME_SHELL_ALLOWLIST.set(
        frozenset()
        if initial is None
        else frozenset(Path(x).name.lower().removesuffix(".exe") for x in initial if x)
    )
    try:
        yield
    finally:
        _RUNTIME_SHELL_ALLOWLIST.reset(token)


def extract_command_binary(line: str) -> Optional[str]:
    """Return the lowercased binary name for *line* (or None on parse error).

    Useful for UIs that want to show the user *which* binaries the agent is
    asking to add to the allowlist.
    """
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
    except ValueError as e:
        return False, f"shlex: {e}"
    if not parts:
        return False, "no argv"
    executable_name = Path(parts[0]).name.lower()
    if executable_name.endswith(".exe"):
        executable_name = executable_name[:-4]
    # sudo is structurally unsupported: the orchestrator runs subprocesses
    # with no TTY and no interactive stdin, so any sudo call that isn't
    # pre-authorised via NOPASSWD sits blocking the pipeline until the
    # hard timeout (5 min by default) — see docs/future-plan.md §24 for the
    # planned password-prompt UI. Reject up-front with a clear reason that
    # reaches the agent on retry instead of letting it hang.
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


def _is_under(parent: Path, child: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _assert_under_workspace(path: Path, workspace_root: "Path | str") -> None:
    """Raise ValueError if *path* (after resolve) is not under *workspace_root*.

    Prevents path traversal and symlink-redirect attacks.  Call this after any
    path construction that is not already routed through ``safe_relative_path``.
    Accepts ``workspace_root`` as ``str`` or ``Path`` for defensive compatibility.
    """
    resolved = path.resolve()
    root_resolved = Path(workspace_root).resolve()
    if not _is_under(root_resolved, resolved):
        raise ValueError(
            f"Path {path!r} resolves to {resolved!r} which is outside workspace "
            f"{root_resolved!r} — possible path traversal attempt"
        )


def validate_workspace_root(path: Path) -> Path:
    """Путь должен существовать и быть каталогом; опционально под SWARM_WORKSPACE_BASE."""
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


# H-2: backward-compat re-export of the domain-layer normalizer.
# Use this when working with WorkspaceContextMode enum values (retrieve_mcp, retrieve_fs, etc.).
# The function above handles the orchestrator-layer mode strings (retrieve, full, index_only, etc.).
normalize_domain_context_mode = None  # will be replaced on successful import below
try:
    from backend.App.workspace.domain.context_mode import (
        normalize_workspace_context_mode as _normalize_domain_ctx,
    )
    normalize_domain_context_mode = _normalize_domain_ctx
except ImportError as _imp_err:
    logger.debug("workspace_io: context_mode import unavailable: %s", _imp_err)


def resolve_workspace_context_mode(agent_config: Optional[dict[str, Any]]) -> str:
    swarm = (agent_config or {}).get("swarm")
    swarm_cfg = swarm if isinstance(swarm, dict) else {}
    explicit = str(swarm_cfg.get("workspace_context_mode") or "").strip()
    if explicit:
        return normalize_workspace_context_mode(explicit)
    return normalize_workspace_context_mode(
        os.getenv("SWARM_WORKSPACE_CONTEXT_MODE", WORKSPACE_CONTEXT_MODE_DEFAULT)
    )


def tools_only_workspace_placeholder(root_display: str) -> str:
    """Текст секции workspace без содержимого файлов — только корень и указание на MCP."""
    root_label = (root_display or "").strip() or "(not set)"
    return (
        f"# Workspace root (orchestrator host): {root_label}\n\n"
        "No file contents are inlined in this prompt. Use **MCP filesystem tools** "
        "(server name `workspace`) to read files under that root as needed.\n"
    )


def _priority_globs_from_env_and_file(root: Path) -> list[str]:
    patterns: list[str] = []
    ctx = root / ".swarm" / "context.txt"
    if ctx.is_file():
        try:
            context_text = ctx.read_text(encoding="utf-8")
        except OSError as e:
            raise OSError(
                f"priority_paths: could not read .swarm/context.txt: {e}"
            ) from e
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
            g = part.strip()
            if g:
                env_patterns.append(g)
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
    """Содержимое файлов по путям/globs из `.swarm/context.txt` и/или `SWARM_WORKSPACE_PRIORITY_GLOBS`.

    Без явных паттернов — `ValueError`: оркестратор не подставляет имена файлов под конкретный стек.
    """
    root = root.resolve()
    ctx_file = root / ".swarm" / "context.txt"
    patterns = _priority_globs_from_env_and_file(root)
    if not patterns:
        if not ctx_file.is_file() and not (os.getenv("SWARM_WORKSPACE_PRIORITY_GLOBS") or "").strip():
            raise ValueError(
                f".swarm/context.txt not found, required for priority_paths mode "
                f"(expected at {ctx_file}). "
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
    for pat in patterns:
        candidate_path = (root / pat).resolve()
        if "*" not in pat and "?" not in pat and "[" not in pat:
            if candidate_path.is_file() and _is_under(root, candidate_path):
                rel_s = str(candidate_path.relative_to(root).as_posix())
                if rel_s not in seen:
                    seen.add(rel_s)
                    matched.append(candidate_path)
            continue
        try:
            for hit in root.glob(pat):
                if not hit.is_file():
                    continue
                hit_r = hit.resolve()
                if not _is_under(root, hit_r):
                    continue
                rel_s = str(hit_r.relative_to(root).as_posix())
                if rel_s not in seen:
                    seen.add(rel_s)
                    matched.append(hit_r)
        except OSError:
            continue

    matched.sort(key=lambda file_path: str(file_path.relative_to(root).as_posix()))
    pat_note = f"Patterns: {', '.join(patterns[:20])}{' …' if len(patterns) > 20 else ''}"
    parts: list[str] = [
        f"# Workspace root: {root}\n",
        "\n## Priority paths (matched)\n\n",
        pat_note + "\n\n",
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
        enc_len = len(block.encode("utf-8"))
        if total + enc_len > total_byte_limit:
            parts.append("\n# … [priority snapshot truncated: max_bytes]\n")
            break
        parts.append(block)
        total += enc_len
        count += 1
    return "".join(parts), count


def resolve_project_context_path(raw: str, workspace_root: Optional[Path]) -> Path:
    """Относительный путь — от workspace_root, иначе от cwd."""
    p = Path(raw.strip()).expanduser()
    if p.is_absolute():
        return p.resolve()
    if workspace_root is not None:
        return (workspace_root / p).resolve()
    return (Path.cwd() / p).resolve()


def validate_readable_file(path: Path) -> Path:
    """Файл для чтения; при SWARM_WORKSPACE_BASE — только внутри базы."""
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
