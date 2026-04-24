"""Тесты подстановки skills в system prompt."""

from __future__ import annotations

import tempfile
from pathlib import Path

from backend.App.integrations.infrastructure.skill_repository import format_role_skills_extra


def test_format_role_skills_reads_workspace_file() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        skill_path = root / "skills" / "x.md"
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        skill_path.write_text("---\nname: X\n---\n\nBody **here**.", encoding="utf-8")
        ac = {
            "skills": {"mine": {"path": "skills/x.md", "title": "Mine"}},
        }
        role_cfg = {"skill_ids": ["mine"]}
        out = format_role_skills_extra(ac, role_cfg, workspace_root=str(root))
        assert "Body **here**." in out
        assert "Mine" in out


def test_empty_without_catalog_or_ids() -> None:
    assert format_role_skills_extra({}, {"skill_ids": ["a"]}, workspace_root="/tmp") == ""
    assert (
        format_role_skills_extra({"skills": {"a": {"path": "p.md"}}}, {}, workspace_root="/tmp")
        == ""
    )


def test_catalog_keys_normalized() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        p = root / "s.md"
        p.write_text("body", encoding="utf-8")
        ac = {"skills": {"My-Skill": {"path": "s.md"}}}
        out = format_role_skills_extra(
            ac, {"skill_ids": ["my-skill"]}, workspace_root=str(root)
        )
        assert "body" in out


def test_whitespace_splits_ids_like_ui() -> None:
    """Пробел/запятая режут список id — как parseSkillIdsFromUi после правки."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        p = root / "s.md"
        p.write_text("x", encoding="utf-8")
        ac = {"skills": {"a": {"path": "s.md"}, "b": {"path": "s.md"}}}
        out = format_role_skills_extra(
            ac, {"skill_ids": "a, b"}, workspace_root=str(root)
        )
        assert out.count("x") >= 2
