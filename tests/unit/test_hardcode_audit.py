from __future__ import annotations

import textwrap
from pathlib import Path

from scripts.audit.find_hardcoded_constants import main as audit_main


def _write(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")


def test_audit_flags_top_level_list_literal(tmp_path, capsys):
    target = tmp_path / "sample.py"
    _write(
        target,
        """
        VALID_TIERS = ["public", "internal", "secret"]


        def use():
            return VALID_TIERS[0]
        """,
    )
    exit_code = audit_main([str(target)])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "VALID_TIERS" in captured.out


def test_audit_ignores_owned_marker(tmp_path, capsys):
    target = tmp_path / "ok.py"
    _write(
        target,
        """
        VALID_TIERS = ["public", "internal", "secret"]  # config-discipline: code-owned
        """,
    )
    exit_code = audit_main([str(target)])
    assert exit_code == 0


def test_audit_ignores_short_literal(tmp_path, capsys):
    target = tmp_path / "short.py"
    _write(
        target,
        """
        PAIR = ["x", "y"]
        """,
    )
    exit_code = audit_main([str(target)])
    assert exit_code == 0


def test_audit_ignores_dict_with_owned_marker(tmp_path):
    target = tmp_path / "dict_ok.py"
    _write(
        target,
        """
        MAP = {"a": 1, "b": 2, "c": 3}  # config-discipline: code-owned
        """,
    )
    exit_code = audit_main([str(target)])
    assert exit_code == 0


def test_audit_max_findings_threshold(tmp_path):
    target = tmp_path / "many.py"
    _write(
        target,
        """
        ALPHA = [1, 2, 3, 4]
        BRAVO = [5, 6, 7, 8]
        """,
    )
    over = audit_main([str(target), "--max-findings", "1"])
    assert over == 1
    under = audit_main([str(target), "--max-findings", "5"])
    assert under == 0
