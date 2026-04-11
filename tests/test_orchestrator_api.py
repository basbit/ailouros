import pytest
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import backend.UI.REST.app as orchestrator_api
import backend.UI.REST.task_instance as _task_instance_mod
from backend.App.orchestration.domain.exceptions import HumanApprovalRequired
from backend.App.integrations.infrastructure.model_proxy import (
    normalize_ollama_tags_payload,
    normalize_openai_v1_models_payload,
    remote_openai_compatible_models_dict,
)


@pytest.fixture(autouse=True)
def _orchestrator_tests_isolated_artifacts(tmp_path, monkeypatch):
    """TestClient пишет в tmp, не в ./artifacts — иначе UUID от pytest смешиваются с боевыми прогонами."""
    d = (tmp_path / "pytest_artifacts").resolve()
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SWARM_ARTIFACTS_DIR", str(d))
    monkeypatch.setattr(_task_instance_mod, "ARTIFACTS_ROOT", d)


def test_chat_completions_non_stream(monkeypatch):
    def fake_run(
        prompt,
        agent_config=None,
        pipeline_steps=None,
        workspace_root="",
        workspace_apply_writes=False,
        task_id="",
        **_,
    ):
        return {"qa_output": f"done: {prompt}"}

    monkeypatch.setattr("backend.App.orchestration.application.tasks.run_pipeline", fake_run)
    client = TestClient(orchestrator_api.app)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "swarm-orchestrator",
            "messages": [{"role": "user", "content": "build auth"}],
            "stream": False,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["choices"][0]["message"]["content"] == "done: build auth"
    assert response.headers.get("x-task-id")


def test_chat_completions_bad_pipeline_steps():
    client = TestClient(orchestrator_api.app)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "swarm-orchestrator",
            "messages": [{"role": "user", "content": "x"}],
            "stream": False,
            "pipeline_steps": ["not_a_real_step"],
        },
    )
    assert response.status_code == 400


def test_normalize_openai_v1_models_payload():
    payload = {
        "object": "list",
        "data": [
            {"id": "m1", "object": "model", "capabilities": ["completion", "vision"]},
            {"id": "m2"},
        ],
    }
    rows = normalize_openai_v1_models_payload(payload)
    assert len(rows) == 2
    assert rows[0]["id"] == "m1"
    assert "completion" in rows[0]["label"] and "vision" in rows[0]["label"]
    assert rows[1]["id"] == "m2"
    assert rows[1]["label"] == "m2"


def test_remote_openai_compatible_models_dict_anthropic():
    out = remote_openai_compatible_models_dict(provider="anthropic", api_key="x")
    assert out["ok"] is True
    assert len(out["models"]) > 0
    assert all("200k ctx" in m["label"] for m in out["models"])
    # TEST-04: model IDs and context_window field
    ids = {m["id"] for m in out["models"]}
    assert "claude-sonnet-4-6" in ids
    assert "claude-opus-4-6" in ids
    for m in out["models"]:
        assert m.get("context_window") == 200_000, f"{m['id']} missing context_window=200000"


def test_remote_openai_compatible_models_dict_missing_ollama_cloud_base():
    out = remote_openai_compatible_models_dict(provider="ollama_cloud", base_url="", api_key="")
    assert out["ok"] is False
    err = (out.get("error") or "").lower()
    assert "url" in err


def test_ui_remote_models_endpoint(monkeypatch):
    def fake_resp(**_kw):
        return JSONResponse(
            content={
                "ok": True,
                "models": [{"id": "m1", "label": "m1"}],
                "source": "https://example.com/v1/models",
            }
        )

    monkeypatch.setattr(
        "backend.UI.REST.controllers.ui.remote_openai_compatible_models_response",
        fake_resp,
    )
    client = TestClient(orchestrator_api.app)
    r = client.post(
        "/ui/remote-models",
        json={"provider": "groq", "base_url": "https://api.groq.com/openai/v1", "api_key": "k"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["models"][0]["id"] == "m1"


def test_normalize_ollama_tags_payload():
    payload = {
        "models": [
            {"name": "qwen:latest", "capabilities": ["completion"]},
            {"name": "llama3", "size": 1},
        ],
    }
    rows = normalize_ollama_tags_payload(payload)
    assert len(rows) == 2
    assert rows[0]["id"] == "qwen:latest"
    assert "completion" in rows[0]["label"]
    assert rows[1]["id"] == "llama3"
    assert rows[1]["label"] == "llama3"


def test_ws_ui_tick():
    client = TestClient(orchestrator_api.app)
    with client.websocket_connect("/ws/ui") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "tick"
        assert "metrics" in msg
        assert "task" in msg


def test_chat_completions_human_gate_non_stream(monkeypatch):
    def need_human(*_a, **_k):
        raise HumanApprovalRequired("pm", "manual approval")

    monkeypatch.setattr("backend.App.orchestration.application.tasks.run_pipeline", need_human)
    client = TestClient(orchestrator_api.app)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "swarm-orchestrator",
            "messages": [{"role": "user", "content": "x"}],
            "stream": False,
        },
    )
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["type"] == "human_approval_required"
    assert body["error"]["step"] == "pm"


def test_chat_completions_stream_human_gate(monkeypatch):
    def boom_stream(*_a, **_k):
        yield {"agent": "pm", "status": "in_progress", "message": "…"}
        raise HumanApprovalRequired("pm", "stop here")

    monkeypatch.setattr(
        "backend.UI.REST.presentation.stream_handlers.run_pipeline_stream",
        boom_stream,
    )
    client = TestClient(orchestrator_api.app)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "swarm-orchestrator",
            "messages": [{"role": "user", "content": "x"}],
            "stream": True,
        },
    )
    assert response.status_code == 200
    assert "awaiting_human" in response.text
    assert "data: [DONE]" in response.text


def test_chat_completions_stream(monkeypatch):
    def fake_stream(_, agent_config=None, pipeline_steps=None, **__):
        yield {"agent": "pm", "status": "in_progress", "message": "PM думает"}
        yield {"agent": "dev", "status": "completed", "message": "Dev готов"}

    monkeypatch.setattr(
        "backend.UI.REST.presentation.stream_handlers.run_pipeline_stream",
        fake_stream,
    )

    client = TestClient(orchestrator_api.app)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "swarm-orchestrator",
            "messages": [{"role": "user", "content": "build auth"}],
            "stream": True,
        },
    )
    assert response.status_code == 200
    body = response.text
    assert "[pm] in_progress: PM думает" in body
    assert "[dev] completed: Dev готов" in body
    assert "data: [DONE]" in body


# ---------------------------------------------------------------------------
# TEST-06 · Human-resume with redacted agent_config
# ---------------------------------------------------------------------------

def test_human_resume_agent_config_forwarded(monkeypatch):
    """agent_config in POST /human-resume body is forwarded to _stream_human_resume_chunks."""
    from backend.UI.REST import task_instance as _task_instance

    # ------------------------------------------------------------------
    # 1. Inject a task into the store via get_task monkeypatch so the route
    #    can find it regardless of the underlying store implementation.
    # ------------------------------------------------------------------
    task_id = "test-resume-task-001"
    fake_task = {
        "task_id": task_id,
        "status": "awaiting_human",
        "agents": ["pm"],
        "history": [],
        "version": 1,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }

    original_get_task = _task_instance.task_store.get_task

    def patched_get_task(tid):
        if str(tid) == task_id:
            return fake_task
        return original_get_task(tid)

    monkeypatch.setattr(_task_instance.task_store, "get_task", patched_get_task)

    # ------------------------------------------------------------------
    # 2. Patch _stream_human_resume_chunks to capture override_agent_config.
    #    The route calls this function and passes req.agent_config as
    #    override_agent_config — this is what BUG-07 wires up.
    # ------------------------------------------------------------------
    captured: dict = {}

    def fake_chunks(
        task_id_arg, feedback, model,
        artifacts_root=None, task_store=None,
        cancel_event=None, override_agent_config=None,
    ):
        captured["override_agent_config"] = override_agent_config
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(
        "backend.UI.REST.presentation.stream_handlers._stream_human_resume_chunks",
        fake_chunks,
    )

    supplied_config = {"provider": "anthropic", "api_key": "sk-test-key-123"}

    client = TestClient(orchestrator_api.app)
    response = client.post(
        f"/v1/tasks/{task_id}/human-resume",
        json={"feedback": "looks good", "stream": True, "agent_config": supplied_config},
    )

    assert response.status_code == 200

    # ------------------------------------------------------------------
    # 3. The override_agent_config must have been forwarded exactly.
    # ------------------------------------------------------------------
    assert captured.get("override_agent_config") == supplied_config


# ---------------------------------------------------------------------------
# TEST-08 · WebSocket /ws/ui disconnect cleanup
# ---------------------------------------------------------------------------

def test_ws_ui_disconnect_sets_stop():
    """Closing the WebSocket from the client side must not hang the server loop."""
    client = TestClient(orchestrator_api.app)
    received = []
    with client.websocket_connect("/ws/ui") as ws:
        msg = ws.receive_json()
        received.append(msg)
        # Close explicitly from the client side — the context manager exit
        # sends a close frame; the server's recv_loop catches WebSocketDisconnect
        # and sets stop, ending pump_loop without resource leaks.

    # We must have received at least one tick message before disconnecting.
    assert len(received) >= 1
    assert received[0]["type"] == "tick"
    assert "metrics" in received[0]
