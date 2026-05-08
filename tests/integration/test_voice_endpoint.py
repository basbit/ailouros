"""Tests for /v1/voice/status — runtime detection and config surface."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    for key in (
        "AILOUROS_VOICE_PROVIDER",
        "AILOUROS_VOICE_RUNTIME_PATH",
        "AILOUROS_VOICE_MODEL_PATH",
        "AILOUROS_VOICE_LANGUAGE",
    ):
        monkeypatch.delenv(key, raising=False)
    yield


def _client():
    from fastapi import FastAPI

    from backend.UI.REST.controllers.voice import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_status_reports_unconfigured_by_default():
    client = _client()
    response = client.get("/v1/voice/status")
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is False
    assert body["runtime_present"] is False
    assert body["model_present"] is False
    assert body["provider"] == "local-whisper"


def test_status_picks_up_env(monkeypatch, tmp_path):
    runtime = tmp_path / "whisper-cli"
    runtime.write_text("#!/bin/sh\nexit 0\n")
    runtime.chmod(0o755)
    model = tmp_path / "tiny.bin"
    model.write_bytes(b"x")
    monkeypatch.setenv("AILOUROS_VOICE_RUNTIME_PATH", str(runtime))
    monkeypatch.setenv("AILOUROS_VOICE_MODEL_PATH", str(model))
    monkeypatch.setenv("AILOUROS_VOICE_LANGUAGE", "en")
    monkeypatch.setenv("AILOUROS_VOICE_PROVIDER", "local-whisper")

    response = _client().get("/v1/voice/status")
    body = response.json()
    assert body["ready"] is True
    assert body["runtime_present"] is True
    assert body["model_present"] is True
    assert body["language"] == "en"


def test_transcribe_returns_503_when_runtime_missing():
    client = _client()
    response = client.post(
        "/v1/voice/transcribe",
        files={"audio": ("audio.webm", b"\x00\x01", "audio/webm")},
    )
    assert response.status_code == 503
    body = response.json()
    detail = body["detail"]
    assert detail["error"] == "voice_runtime_not_configured"
    assert "speech-to-text" in detail["message"].lower()


def test_transcribe_returns_503_when_model_missing(monkeypatch, tmp_path):
    runtime = tmp_path / "whisper-cli"
    runtime.write_text("#!/bin/sh\nexit 0\n")
    runtime.chmod(0o755)
    monkeypatch.setenv("AILOUROS_VOICE_RUNTIME_PATH", str(runtime))
    monkeypatch.setenv("AILOUROS_VOICE_MODEL_PATH", "/nonexistent/model.bin")

    response = _client().post(
        "/v1/voice/transcribe",
        files={"audio": ("audio.webm", b"\x00\x01", "audio/webm")},
    )
    assert response.status_code == 503
    body = response.json()
    assert body["detail"]["error"] == "voice_model_not_configured"
