from __future__ import annotations

import subprocess
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
SMOKE_SCRIPT = APP_ROOT / "scripts" / "e2e" / "smoke.py"


def test_smoke_script_exits_zero() -> None:
    result = subprocess.run(
        [sys.executable, str(SMOKE_SCRIPT)],
        capture_output=True,
        text=True,
        cwd=str(APP_ROOT),
        env={
            **__import__("os").environ,
            "SWARM_SHARED_HISTORY_ENABLED": "1",
            "PYTHONPATH": str(APP_ROOT),
        },
    )
    assert result.returncode == 0, (
        f"smoke.py exited {result.returncode}\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )
