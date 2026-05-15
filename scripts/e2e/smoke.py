#!/usr/bin/env python3
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from fastapi.testclient import TestClient  # noqa: E402


def _fail(step: str, expected: str, got: Any) -> None:
    print(f"FAIL [{step}]")
    print(f"  expected: {expected}")
    print(f"  got:      {got}")
    sys.exit(1)


def _assert_status(step: str, response: Any, code: int) -> None:
    if response.status_code != code:
        _fail(
            step,
            f"status_code={code}",
            f"status_code={response.status_code} body={response.text[:400]}",
        )


def _assert_keys(step: str, body: dict[str, Any], *keys: str) -> None:
    missing = [k for k in keys if k not in body]
    if missing:
        _fail(step, f"keys present: {list(keys)}", f"missing keys: {missing} in {list(body.keys())}")


def _assert_type(step: str, body: dict[str, Any], key: str, expected_type: type) -> None:
    if not isinstance(body.get(key), expected_type):
        _fail(
            step,
            f"body[{key!r}] is {expected_type.__name__}",
            f"body[{key!r}]={body.get(key)!r} (type={type(body.get(key)).__name__})",
        )


def run_step(number: int, name: str, fn: Any, *args: Any, **kwargs: Any) -> float:
    label = f"[STEP {number:02d}] {name}"
    print(label)
    t0 = time.monotonic()
    fn(*args, **kwargs)
    duration_ms = (time.monotonic() - t0) * 1000
    print(f"  OK {duration_ms:.0f}ms")
    return duration_ms


def _make_client() -> TestClient:
    from backend.App.shared.infrastructure.rest.app import app
    return TestClient(app, raise_server_exceptions=True)


def step_01_liveness(client: TestClient) -> None:
    r = client.get("/live")
    _assert_status("01 GET /live", r, 200)
    body = r.json()
    _assert_keys("01 GET /live", body, "status")
    if body["status"] != "ok":
        _fail("01 GET /live", "status=ok", f"status={body['status']!r}")


def step_02_readiness(client: TestClient) -> None:
    r = client.get("/ready")
    _assert_status("02 GET /ready", r, 200)
    body = r.json()
    _assert_keys("02 GET /ready", body, "status")


def step_03_health(client: TestClient) -> None:
    r = client.get("/health")
    _assert_status("03 GET /health", r, 200)
    body = r.json()
    _assert_keys("03 GET /health", body, "status")


def step_04_v1_health(client: TestClient) -> None:
    r = client.get("/v1/health")
    _assert_status("04 GET /v1/health", r, 200)
    body = r.json()
    _assert_keys("04 GET /v1/health", body, "status", "subsystems")
    _assert_type("04 GET /v1/health subsystems", body, "subsystems", list)
    if body["status"] not in ("ok", "degraded", "error"):
        _fail("04 GET /v1/health", "status in {ok,degraded,error}", f"status={body['status']!r}")


def step_05_spec_init(client: TestClient, workspace: str) -> None:
    r = client.post(
        "/v1/spec/init",
        json={
            "workspace_root": workspace,
            "project_title": "Smoke Test Project",
            "project_summary": "e2e smoke",
        },
    )
    _assert_status("05 POST /v1/spec/init", r, 200)
    body = r.json()
    _assert_keys("05 POST /v1/spec/init", body, "workspace_root", "specs_root", "created_spec_ids", "bootstrapped")
    _assert_type("05 POST /v1/spec/init", body, "created_spec_ids", list)
    _assert_type("05 POST /v1/spec/init", body, "bootstrapped", bool)


def step_06_spec_list(client: TestClient, workspace: str) -> None:
    r = client.get("/v1/spec/list", params={"workspace_root": workspace})
    _assert_status("06 GET /v1/spec/list", r, 200)
    body = r.json()
    _assert_keys("06 GET /v1/spec/list", body, "spec_ids")
    spec_ids = body["spec_ids"]
    for required in ("_project", "_schema"):
        if required not in spec_ids:
            _fail(
                "06 GET /v1/spec/list",
                f"{required!r} present in spec_ids",
                f"spec_ids={spec_ids}",
            )


def step_07_spec_show(client: TestClient, workspace: str) -> None:
    r = client.get("/v1/spec/show", params={"workspace_root": workspace, "spec_id": "_project"})
    _assert_status("07 GET /v1/spec/show", r, 200)
    body = r.json()
    _assert_keys("07 GET /v1/spec/show", body, "spec", "dependencies", "dependants")
    spec = body["spec"]
    _assert_keys("07 GET /v1/spec/show spec", spec, "spec_id", "body")
    if spec["spec_id"] != "_project":
        _fail("07 GET /v1/spec/show", "spec_id=_project", f"spec_id={spec['spec_id']!r}")


def step_08_spec_put(client: TestClient, workspace: str) -> str:
    r = client.put(
        "/v1/spec/smoke_test_manual",
        json={
            "workspace_root": workspace,
            "body": "Smoke test spec body.",
            "frontmatter": {
                "spec_id": "smoke_test_manual",
                "status": "draft",
                "privacy": "internal",
                "title": "Smoke Manual Spec",
            },
        },
    )
    _assert_status("08 PUT /v1/spec/smoke_test_manual", r, 200)
    body = r.json()
    _assert_keys("08 PUT /v1/spec/smoke_test_manual", body, "spec_id", "saved_path", "codegen_hash")
    if body["spec_id"] != "smoke_test_manual":
        _fail("08 PUT", "spec_id=smoke_test_manual", f"spec_id={body['spec_id']!r}")
    return str(body["saved_path"])


def step_09_spec_validate(client: TestClient, workspace: str) -> None:
    r = client.post(
        "/v1/spec/smoke_test_manual/validate",
        json={"workspace_root": workspace},
    )
    _assert_status("09 POST /v1/spec/smoke_test_manual/validate", r, 200)
    body = r.json()
    _assert_keys("09 POST /v1/spec/smoke_test_manual/validate", body, "spec_id", "ok", "findings")
    _assert_type("09 POST validate", body, "findings", list)
    if body["spec_id"] != "smoke_test_manual":
        _fail("09 POST validate", "spec_id=smoke_test_manual", f"spec_id={body['spec_id']!r}")


def step_10_spec_graph(client: TestClient, workspace: str) -> None:
    r = client.get("/v1/spec/graph", params={"workspace_root": workspace})
    _assert_status("10 GET /v1/spec/graph", r, 200)
    body = r.json()
    _assert_keys("10 GET /v1/spec/graph", body, "nodes", "edges")
    _assert_type("10 GET /v1/spec/graph nodes", body, "nodes", list)
    _assert_type("10 GET /v1/spec/graph edges", body, "edges", list)


def step_11_spec_orphans(client: TestClient, workspace: str) -> None:
    r = client.get("/v1/spec/orphans", params={"workspace_root": workspace})
    _assert_status("11 GET /v1/spec/orphans", r, 200)
    body = r.json()
    _assert_keys("11 GET /v1/spec/orphans", body, "anchor", "orphans")
    _assert_type("11 GET /v1/spec/orphans orphans", body, "orphans", list)


def step_12_spec_extract(client: TestClient, workspace: str) -> None:
    module_src = (
        "def add(x: int, y: int) -> int:\n"
        "    return x + y\n"
    )
    module_path = Path(workspace) / "smoke_module.py"
    module_path.write_text(module_src)

    r = client.post(
        "/v1/spec/extract",
        json={
            "workspace_root": workspace,
            "code_path": str(module_path),
            "save": False,
        },
    )
    _assert_status("12 POST /v1/spec/extract", r, 200)
    body = r.json()
    _assert_keys("12 POST /v1/spec/extract", body, "spec", "saved")
    _assert_keys("12 POST /v1/spec/extract spec", body["spec"], "spec_id", "body")
    if body["saved"] is not False:
        _fail("12 POST /v1/spec/extract", "saved=False", f"saved={body['saved']!r}")


def step_13_plugins_list(client: TestClient) -> None:
    r = client.get("/v1/plugins")
    _assert_status("13 GET /v1/plugins", r, 200)
    body = r.json()
    _assert_keys("13 GET /v1/plugins", body, "plugins")
    _assert_type("13 GET /v1/plugins plugins", body, "plugins", list)


def step_14_plugins_registries(client: TestClient) -> None:
    r = client.get("/v1/plugins/registries")
    _assert_status("14 GET /v1/plugins/registries", r, 200)
    body = r.json()
    _assert_keys("14 GET /v1/plugins/registries", body, "registries")
    if not isinstance(body["registries"], (list, dict)):
        _fail("14 GET /v1/plugins/registries", "registries is list or dict", f"type={type(body['registries']).__name__}")


def step_17_scenario_estimate(client: TestClient) -> None:
    r = client.get("/v1/scenarios/build_feature/estimate")
    _assert_status("17 GET /v1/scenarios/build_feature/estimate", r, 200)
    body = r.json()
    _assert_keys("17 GET /v1/scenarios/build_feature/estimate", body, "scenario_id", "steps", "total_seconds", "essential_seconds")
    if body["scenario_id"] != "build_feature":
        _fail("17 GET estimate", "scenario_id=build_feature", f"scenario_id={body['scenario_id']!r}")
    _assert_type("17 GET estimate steps", body, "steps", list)
    if not body["steps"]:
        _fail("17 GET estimate", "steps non-empty", "steps=[]")


def step_18_qdrant_health(client: TestClient) -> None:
    r = client.get("/v1/health/qdrant")
    _assert_status("18 GET /v1/health/qdrant", r, 200)
    body = r.json()
    _assert_keys("18 GET /v1/health/qdrant", body, "subsystem", "status", "latency_ms", "detail")
    if body["subsystem"] != "qdrant":
        _fail("18 GET /v1/health/qdrant", "subsystem=qdrant", f"subsystem={body['subsystem']!r}")
    if body["status"] not in ("ok", "degraded", "error"):
        _fail("18 GET /v1/health/qdrant", "status in {ok,degraded,error}", f"status={body['status']!r}")


def main() -> None:
    import tempfile

    print("=== smoke-e2e: golden-path REST roundtrip ===")
    t_start = time.monotonic()

    client = _make_client()

    with tempfile.TemporaryDirectory() as tmp_dir:
        workspace = str(Path(tmp_dir) / "smoke_workspace")
        Path(workspace).mkdir()

        durations: list[float] = []
        durations.append(run_step(1,  "GET /live",                                    step_01_liveness,        client))
        durations.append(run_step(2,  "GET /ready",                                   step_02_readiness,       client))
        durations.append(run_step(3,  "GET /health",                                  step_03_health,          client))
        durations.append(run_step(4,  "GET /v1/health",                               step_04_v1_health,       client))
        durations.append(run_step(5,  "POST /v1/spec/init",                           step_05_spec_init,       client, workspace))
        durations.append(run_step(6,  "GET /v1/spec/list",                            step_06_spec_list,       client, workspace))
        durations.append(run_step(7,  "GET /v1/spec/show?spec_id=_project",           step_07_spec_show,       client, workspace))
        durations.append(run_step(8,  "PUT /v1/spec/smoke_test_manual",               step_08_spec_put,        client, workspace))
        durations.append(run_step(9,  "POST /v1/spec/smoke_test_manual/validate",     step_09_spec_validate,   client, workspace))
        durations.append(run_step(10, "GET /v1/spec/graph",                           step_10_spec_graph,      client, workspace))
        durations.append(run_step(11, "GET /v1/spec/orphans",                         step_11_spec_orphans,    client, workspace))
        durations.append(run_step(12, "POST /v1/spec/extract",                        step_12_spec_extract,    client, workspace))
        durations.append(run_step(13, "GET /v1/plugins",                              step_13_plugins_list,    client))
        durations.append(run_step(14, "GET /v1/plugins/registries",                   step_14_plugins_registries, client))
        durations.append(run_step(15, "GET /v1/scenarios/build_feature/estimate",     step_17_scenario_estimate, client))
        durations.append(run_step(16, "GET /v1/health/qdrant",                        step_18_qdrant_health,   client))

    total_ms = (time.monotonic() - t_start) * 1000
    avg_ms = sum(durations) / len(durations) if durations else 0
    print()
    print(f"=== PASSED  {len(durations)} steps  total={total_ms:.0f}ms  avg={avg_ms:.0f}ms ===")


if __name__ == "__main__":
    main()
