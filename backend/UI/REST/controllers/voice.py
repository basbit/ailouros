"""Voice transcription endpoint backed by a local STT runtime."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()


def _provider() -> str:
    return os.getenv("AILOUROS_VOICE_PROVIDER", "local-whisper").strip() or "local-whisper"


def _runtime_path() -> str:
    return os.getenv("AILOUROS_VOICE_RUNTIME_PATH", "").strip()


def _model_path() -> str:
    return os.getenv("AILOUROS_VOICE_MODEL_PATH", "").strip()


def _language() -> str:
    return os.getenv("AILOUROS_VOICE_LANGUAGE", "auto").strip() or "auto"


@router.get("/v1/voice/status")
def voice_status() -> JSONResponse:
    runtime = _runtime_path()
    model = _model_path()
    runtime_ok = bool(runtime) and Path(runtime).is_file()
    model_ok = bool(model) and Path(model).is_file()
    ready = runtime_ok and model_ok
    return JSONResponse(
        content={
            "ready": ready,
            "provider": _provider(),
            "runtime_present": runtime_ok,
            "model_present": model_ok,
            "language": _language(),
        }
    )


@router.post("/v1/voice/transcribe")
async def voice_transcribe(
    audio: UploadFile = File(...),
    language: str = Form(default=""),
) -> JSONResponse:
    runtime = _runtime_path()
    model = _model_path()
    if not runtime or not Path(runtime).is_file():
        raise HTTPException(
            status_code=503,
            detail={
                "error": "voice_runtime_not_configured",
                "message": (
                    "Local speech-to-text runtime is not installed. "
                    "Install or bundle whisper.cpp and set "
                    "AILOUROS_VOICE_RUNTIME_PATH + AILOUROS_VOICE_MODEL_PATH."
                ),
            },
        )
    if not model or not Path(model).is_file():
        raise HTTPException(
            status_code=503,
            detail={
                "error": "voice_model_not_configured",
                "message": "Speech-to-text model not found at AILOUROS_VOICE_MODEL_PATH.",
            },
        )

    with tempfile.TemporaryDirectory(prefix="ailouros-voice-") as workdir:
        suffix = ""
        if audio.filename:
            suffix = Path(audio.filename).suffix
        if not suffix:
            suffix = ".webm"
        in_path = Path(workdir) / f"input{suffix}"
        wav_path = Path(workdir) / "input.wav"
        with in_path.open("wb") as out:
            chunk = await audio.read(1 << 20)
            while chunk:
                out.write(chunk)
                chunk = await audio.read(1 << 20)
        if shutil.which("ffmpeg") is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "ffmpeg_missing",
                    "message": (
                        "ffmpeg is required to convert browser audio to PCM wav for "
                        "the speech-to-text runtime. Install ffmpeg or ship it with the bundle."
                    ),
                },
            )
        convert = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-i",
            str(in_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "wav",
            str(wav_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, ffmpeg_err = await convert.communicate()
        if convert.returncode != 0:
            logger.warning("voice: ffmpeg failed: %s", ffmpeg_err.decode("utf-8", "ignore")[:400])
            raise HTTPException(
                status_code=500,
                detail={"error": "audio_conversion_failed"},
            )
        chosen_lang = (language or _language() or "auto").strip()
        whisper = await asyncio.create_subprocess_exec(
            runtime,
            "-m",
            model,
            "-f",
            str(wav_path),
            "-l",
            chosen_lang,
            "--no-timestamps",
            "--output-txt",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        whisper_stdout, whisper_stderr = await whisper.communicate()
        if whisper.returncode != 0:
            logger.warning(
                "voice: whisper failed: %s",
                whisper_stderr.decode("utf-8", "ignore")[:400],
            )
            raise HTTPException(
                status_code=500,
                detail={"error": "transcription_failed"},
            )
        text = whisper_stdout.decode("utf-8", "ignore").strip()
        return JSONResponse(content={"text": text, "language": chosen_lang})
