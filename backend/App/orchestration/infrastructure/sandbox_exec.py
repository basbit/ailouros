"""Исполнение shell-команд: хост или изолированный Docker (опционально E2B)."""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Any, Optional

_SUBPROCESS_ENV_ALLOWLIST = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL",
    "LANG", "LC_ALL", "LC_CTYPE", "TERM",
    "TMPDIR", "TEMP", "TMP",
    "NODE_ENV", "npm_config_cache",
    "VIRTUAL_ENV", "CONDA_PREFIX",
    "GOPATH", "GOROOT", "CARGO_HOME", "RUSTUP_HOME",
    "JAVA_HOME", "MAVEN_HOME",
    "PYTHONPATH", "PYTHONHOME",
})


def _safe_subprocess_env() -> dict[str, str]:
    """Return a minimal safe copy of os.environ for subprocess calls."""
    return {k: v for k, v in os.environ.items() if k in _SUBPROCESS_ENV_ALLOWLIST}


def exec_backend() -> str:
    return (os.getenv("SWARM_EXEC_BACKEND", "host") or "host").strip().lower()


def docker_image() -> str:
    return (os.getenv("SWARM_DOCKER_EXEC_IMAGE", "python:3.11-slim") or "python:3.11-slim").strip()


_STDOUT_TAIL_CHARS = int(os.getenv("SWARM_EXEC_STDOUT_TAIL_CHARS", "8000"))


def e2b_enabled() -> bool:
    return bool((os.getenv("SWARM_E2B_API_KEY") or "").strip())


def run_allowlisted_command(
    argv: list[str],
    cwd: Path,
    *,
    timeout_sec: int,
    env: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """
    Запуск одной команды (уже распарсенной). backend=docker: ``docker run`` с bind-mount cwd.
    E2B: заглушка — вернёт ошибку с подсказкой установить SDK (без обязательной зависимости).
    """
    backend = exec_backend()
    cwd = cwd.resolve()
    env = env if env is not None else _safe_subprocess_env()

    if backend in ("e2b", "e2b_sandbox"):
        if not e2b_enabled():
            return {
                "cmd": " ".join(argv),
                "error": (
                    "SWARM_E2B_API_KEY не задан; задайте ключ или "
                    "SWARM_EXEC_BACKEND=docker"
                ),
            }
        return {
            "cmd": " ".join(argv),
            "error": (
                "E2B: установите пакет e2b и реализуйте шаблон в sandbox_exec "
                "(см. docs/AIlourOS.md)."
            ),
        }

    if backend in ("docker", "container"):
        # Монтируем весь workspace в /workspace
        vol = f"{cwd}:/workspace"
        inner = shlex.join(argv)
        docker_argv = [
            "docker",
            "run",
            "--rm",
            "-v",
            vol,
            "-w",
            "/workspace",
            docker_image(),
            "sh",
            "-lc",
            inner,
        ]
        try:
            r = subprocess.run(
                docker_argv,
                cwd=str(cwd),
                timeout=timeout_sec,
                capture_output=True,
                text=True,
                env=env,
            )
            return {
                "cmd": " ".join(argv),
                "backend": "docker",
                "returncode": r.returncode,
                "stdout": (r.stdout or "")[-_STDOUT_TAIL_CHARS:],
                "stderr": (r.stderr or "")[-_STDOUT_TAIL_CHARS:],
            }
        except FileNotFoundError:
            return {
                "cmd": " ".join(argv),
                "error": "docker не найден в PATH",
            }
        except subprocess.TimeoutExpired:
            return {"cmd": " ".join(argv), "error": "timeout"}

    try:
        r = subprocess.run(
            argv,
            cwd=str(cwd),
            timeout=timeout_sec,
            capture_output=True,
            text=True,
            env=env,
        )
        return {
            "cmd": " ".join(argv),
            "backend": "host",
            "returncode": r.returncode,
            "stdout": (r.stdout or "")[-_STDOUT_TAIL_CHARS:],
            "stderr": (r.stderr or "")[-_STDOUT_TAIL_CHARS:],
        }
    except subprocess.TimeoutExpired:
        return {"cmd": " ".join(argv), "error": "timeout"}
    except OSError as e:
        return {"cmd": " ".join(argv), "error": str(e)}
