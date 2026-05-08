#!/usr/bin/env python3
"""Run scenario replay golden cases.

Each golden case lives at app/tests/golden/scenarios/<case>/{input,expected}.json
and exercises the resolver + preview path without invoking any LLM.

Usage:
    python scripts/replay_scenarios.py            # run all cases, exit non-zero on failure
    python scripts/replay_scenarios.py --json     # machine-readable output
    python scripts/replay_scenarios.py --root path/to/cases
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend.App.orchestration.application.scenarios.replay import (  # noqa: E402
    default_golden_root,
    run_all,
)


def _format_human(results: list, root: Path) -> int:
    print(f"Replay root: {root}")
    print(f"Cases: {len(results)}")
    failed = 0
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(f"  [{status}] {result.case_name} (scenario={result.scenario_id})")
        if not result.passed:
            failed += 1
            for failure in result.failures:
                print(f"      - {failure}")
    print()
    print(f"Total: {len(results)}, passed: {len(results) - failed}, failed: {failed}")
    return failed


def _format_json(results: list) -> int:
    payload = {
        "total": len(results),
        "passed": sum(1 for result in results if result.passed),
        "failed": sum(1 for result in results if not result.passed),
        "results": [
            {
                "case_name": result.case_name,
                "scenario_id": result.scenario_id,
                "passed": result.passed,
                "failures": list(result.failures),
            }
            for result in results
        ],
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return payload["failed"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay scenario golden cases.")
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Override the golden root directory.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of human-readable output.",
    )
    args = parser.parse_args()

    root = args.root if args.root is not None else default_golden_root()
    if not root.is_dir():
        print(f"error: replay root does not exist: {root}", file=sys.stderr)
        return 2

    results = run_all(root)
    failed = _format_json(results) if args.json else _format_human(results, root)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
