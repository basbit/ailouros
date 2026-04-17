"""Smoke test: route modules can be imported without circular dependency (H-1)."""

from __future__ import annotations

import importlib


def test_routes_chat_importable() -> None:
    importlib.import_module("backend.UI.REST.controllers.chat")


def test_routes_misc_importable() -> None:
    importlib.import_module("backend.UI.REST.controllers.misc")


def test_routes_misc_no_app_mod_import() -> None:
    """Verify controllers/misc does not import orchestrator.app at module level."""
    import ast
    import pathlib

    src = pathlib.Path("backend/UI/REST/controllers/misc.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "orchestrator.app", (
                        "controllers/misc.py must not import orchestrator.app at module level"
                    )
