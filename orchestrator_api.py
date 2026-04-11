"""Thin shim: `uvicorn orchestrator_api:app` — app lives in backend.UI.REST.app."""

from backend.UI.REST.app import app  # noqa: F401

__all__ = ["app"]
