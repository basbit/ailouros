from __future__ import annotations

import gc
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait

from backend.App.integrations.infrastructure.qdrant_client import (
    InMemoryVectorStore,
)
from backend.App.shared.infrastructure import activity_recorder


def _default_concurrency() -> int:
    raw = (os.getenv("SWARM_CHAOS_TASKS") or "16").strip()
    try:
        value = int(raw)
    except ValueError:
        return 16
    return max(2, value)


def test_concurrent_upserts_to_shared_in_memory_store():
    store = InMemoryVectorStore()
    concurrency = _default_concurrency()
    barrier = threading.Barrier(concurrency)

    def worker(index: int) -> None:
        barrier.wait()
        for offset in range(20):
            store.upsert(
                "chaos_collection",
                f"{index}-{offset}",
                [float(index), float(offset)],
                {"index": index, "offset": offset},
            )

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(worker, idx) for idx in range(concurrency)]
        wait(futures)
        for future in futures:
            future.result()

    expected = concurrency * 20
    assert store.count("chaos_collection") == expected


def test_concurrent_activity_records_dedup_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ARTIFACTS_DIR", str(tmp_path))
    concurrency = _default_concurrency()
    barrier = threading.Barrier(concurrency)

    def worker(index: int) -> None:
        token = activity_recorder.set_active_task(f"chaos-task-{index}")
        try:
            barrier.wait()
            for offset in range(10):
                activity_recorder.record(
                    "mcp_calls",
                    {
                        "server": "workspace",
                        "tool": "read",
                        "args": {"index": index, "offset": offset},
                        "status": "ok",
                    },
                )
        finally:
            activity_recorder.reset_active_task(token)

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(worker, idx) for idx in range(concurrency)]
        wait(futures)
        for future in futures:
            future.result()

    for index in range(concurrency):
        path = (
            tmp_path / f"chaos-task-{index}" / "activity" / "mcp_calls.jsonl"
        )
        assert path.is_file()
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(lines) == 10


def test_long_running_loop_no_unbounded_memory():
    iterations = max(1000, _default_concurrency() * 10)
    store = InMemoryVectorStore()
    gc.collect()
    baseline = len(gc.get_objects())
    for index in range(iterations):
        store.upsert(
            "soak_collection",
            f"soak-{index}",
            [float(index % 10)],
            {"index": index},
        )
    gc.collect()
    after = len(gc.get_objects())
    growth = after - baseline
    assert growth < iterations * 20, (
        f"object growth {growth} > {iterations * 20} suggests a leak"
    )


def test_chaos_runs_under_time_budget():
    store = InMemoryVectorStore()
    concurrency = _default_concurrency()
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [
            pool.submit(
                store.upsert,
                "perf",
                f"p-{index}",
                [float(index)],
                {"index": index},
            )
            for index in range(concurrency * 50)
        ]
        wait(futures)
        for future in futures:
            future.result()
    elapsed = time.monotonic() - start
    assert elapsed < 30.0, f"chaos run exceeded time budget: {elapsed:.1f}s"
