from __future__ import annotations

import hashlib

import pytest

from backend.App.orchestration.application.context.repo_evidence import (
    ensure_validated_repo_evidence,
    enforce_repo_evidence_policy,
    parse_repo_evidence_artifact,
    validate_repo_evidence_against_workspace,
)


def test_parse_repo_evidence_artifact_extracts_json_block():
    raw = (
        "Architecture summary.\n\n"
        "```json\n"
        '{"repo_evidence":[{"path":"src/app.py","start_line":1,"end_line":2,'
        '"excerpt":"line1\\nline2","why":"proves framework usage"}],'
        '"unverified_claims":["deployment target still unknown"]}\n'
        "```"
    )

    artifact = parse_repo_evidence_artifact(raw)

    assert artifact["has_artifact"] is True
    assert artifact["repo_evidence"][0]["path"] == "src/app.py"
    assert artifact["unverified_claims"] == ["deployment target still unknown"]


def test_parse_repo_evidence_artifact_extracts_json_from_fenced_block_with_extra_text():
    raw = (
        "Architecture summary.\n\n"
        "```json\n"
        "Use this artifact for review.\n"
        '{"repo_evidence":[{"path":"src/app.py","start_line":1,"end_line":2,'
        '"excerpt":"line1\\nline2","why":"proves framework usage"}],'
        '"unverified_claims":[]}\n'
        "```"
    )

    artifact = parse_repo_evidence_artifact(raw)

    assert artifact["has_artifact"] is True
    assert artifact["repo_evidence"][0]["path"] == "src/app.py"


def test_parse_repo_evidence_artifact_extracts_json_object_from_plain_text():
    raw = (
        "Architecture summary first.\n"
        "Then a canonical artifact follows:\n"
        '{"repo_evidence":[{"path":"src/app.py","start_line":1,"end_line":2,'
        '"excerpt":"line1\\nline2","why":"proves framework usage"}],'
        '"unverified_claims":["deployment target still unknown"]}\n'
        "End of answer."
    )

    artifact = parse_repo_evidence_artifact(raw)

    assert artifact["has_artifact"] is True
    assert artifact["repo_evidence"][0]["path"] == "src/app.py"
    assert artifact["unverified_claims"] == ["deployment target still unknown"]


def test_validate_repo_evidence_against_workspace_adds_sha(tmp_path):
    target = tmp_path / "src" / "app.py"
    target.parent.mkdir(parents=True)
    target.write_text("line1\nline2\nline3\n", encoding="utf-8")
    artifact = {
        "has_artifact": True,
        "repo_evidence": [
            {
                "path": "src/app.py",
                "start_line": 1,
                "end_line": 2,
                "excerpt": "line1\nline2",
                "why": "proof",
            }
        ],
        "unverified_claims": [],
    }

    validated = validate_repo_evidence_against_workspace(
        artifact,
        workspace_root=str(tmp_path),
    )

    assert validated["repo_evidence"][0]["excerpt_sha256"] == hashlib.sha256(
        "line1\nline2".encode("utf-8")
    ).hexdigest()
    assert validated["repo_evidence"][0]["hash"] == validated["repo_evidence"][0]["excerpt_sha256"]
    assert validated["repo_evidence"][0]["transport_evidence"]["kind"] == "repo_excerpt"
    assert validated["repo_evidence"][0]["transport_evidence"]["source"] == "workspace"
    assert validated["repo_evidence"][0]["preview"] == "line1 line2"


def test_validate_repo_evidence_allows_unverified_claims_without_workspace():
    artifact = {
        "has_artifact": True,
        "repo_evidence": [],
        "unverified_claims": ["framework cannot be proven without repository access"],
    }

    validated = validate_repo_evidence_against_workspace(artifact, workspace_root="")

    assert validated["repo_evidence"] == []
    assert validated["unverified_claims"] == artifact["unverified_claims"]


def test_validate_repo_evidence_repairs_excerpt_mismatch(tmp_path):
    target = tmp_path / "src" / "app.py"
    target.parent.mkdir(parents=True)
    target.write_text("line1\nline2\n", encoding="utf-8")
    artifact = {
        "has_artifact": True,
        "repo_evidence": [
            {
                "path": "src/app.py",
                "start_line": 1,
                "end_line": 2,
                "excerpt": "wrong",
                "why": "proof",
            }
        ],
        "unverified_claims": [],
    }

    validated = validate_repo_evidence_against_workspace(artifact, workspace_root=str(tmp_path))

    assert validated["repo_evidence"][0]["excerpt"] == "line1\nline2"
    assert validated["repo_evidence"][0]["model_excerpt"] == "wrong"
    assert validated["repo_evidence"][0]["excerpt_repaired"] is True


def test_validate_repo_evidence_fills_missing_excerpt_from_workspace(tmp_path):
    target = tmp_path / "src" / "app.py"
    target.parent.mkdir(parents=True)
    target.write_text("line1\nline2\n", encoding="utf-8")
    artifact = {
        "has_artifact": True,
        "repo_evidence": [
            {
                "path": "src/app.py",
                "start_line": 1,
                "end_line": 2,
                "why": "proof",
            }
        ],
        "unverified_claims": [],
    }

    validated = validate_repo_evidence_against_workspace(artifact, workspace_root=str(tmp_path))

    assert validated["repo_evidence"][0]["excerpt"] == "line1\nline2"
    assert "model_excerpt" not in validated["repo_evidence"][0]


def test_enforce_repo_evidence_policy_rejects_unverified_claims_with_workspace(tmp_path):
    artifact = {
        "has_artifact": True,
        "repo_evidence": [],
        "unverified_claims": ["claim still unresolved"],
    }

    validated = enforce_repo_evidence_policy(
        artifact,
        workspace_root=str(tmp_path),
        step_id="ba_node",
    )

    assert validated["repo_evidence"] == []
    assert validated["unverified_claims"] == []
    assert validated["suppressed_unverified_claims"] == ["claim still unresolved"]
    assert "suppressed 1 unresolved" in validated["repo_evidence_policy_warning"]


def test_ensure_validated_repo_evidence_retries_on_excerpt_mismatch(tmp_path):
    target = tmp_path / "backend" / "composer.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"name":"demo"}\n', encoding="utf-8")
    calls = {"count": 0}

    def _retry_run(prompt: str) -> str:
        calls["count"] += 1
        assert "Validation error" in prompt
        return (
            "Architecture summary\n```json\n"
            '{"repo_evidence":[{"path":"backend/composer.json","start_line":1,"end_line":1,'
            '"excerpt":"{\\"name\\":\\"demo\\"}","why":"The repository already defines the package manifest."}],'
            '"unverified_claims":[]}\n```'
        )

    output, validated = ensure_validated_repo_evidence(
        raw_output=(
            "Architecture summary\n```json\n"
            '{"repo_evidence":[{"path":"backend/composer.json","start_line":1,"end_line":1,'
            '"excerpt":"wrong","why":"The repository already defines the package manifest."}],'
            '"unverified_claims":[]}\n```'
        ),
        base_prompt="Base prompt",
        workspace_root=str(tmp_path),
        step_id="arch_node",
        retry_run=_retry_run,
    )

    assert calls["count"] == 0
    assert "composer.json" in output
    assert validated["repo_evidence"][0]["path"] == "backend/composer.json"
    assert validated["repo_evidence"][0]["excerpt"] == '{"name":"demo"}'
    assert validated["repo_evidence"][0]["model_excerpt"] == "wrong"


def test_ensure_validated_repo_evidence_uses_artifact_only_repair_when_retry_still_has_no_json(tmp_path):
    target = tmp_path / "backend" / "composer.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"name":"demo"}\n', encoding="utf-8")
    calls = {"count": 0}

    def _retry_run(prompt: str) -> str:
        calls["count"] += 1
        if calls["count"] == 1:
            assert "[CRITICAL RETRY]" in prompt
            return "Architecture summary after retry, but still no canonical artifact"
        assert "[ARTIFACT-ONLY REPAIR]" in prompt
        return (
            "```json\n"
            '{"repo_evidence":[{"path":"backend/composer.json","start_line":1,"end_line":1,'
            '"excerpt":"{\\"name\\":\\"demo\\"}","why":"The repository already defines the package manifest."}],'
            '"unverified_claims":[]}\n```'
        )

    output, validated = ensure_validated_repo_evidence(
        raw_output="Architecture summary without artifact",
        base_prompt="Base prompt",
        workspace_root=str(tmp_path),
        step_id="arch_node",
        retry_run=_retry_run,
    )

    assert calls["count"] == 2
    assert "Architecture summary after retry" in output
    assert '"repo_evidence"' in output
    assert validated["repo_evidence"][0]["path"] == "backend/composer.json"


def test_ensure_validated_repo_evidence_skips_retries_when_cap_is_zero(monkeypatch, tmp_path):
    """SWARM_REPO_EVIDENCE_MAX_RETRIES=0 returns empty evidence without LLM call."""
    monkeypatch.setenv("SWARM_REPO_EVIDENCE_MAX_RETRIES", "0")
    calls = {"count": 0}

    def _retry_run(_prompt: str) -> str:
        calls["count"] += 1
        return ""

    output, validated = ensure_validated_repo_evidence(
        raw_output="narrative without any canonical artifact",
        base_prompt="Base prompt",
        workspace_root=str(tmp_path),
        step_id="devops",
        retry_run=_retry_run,
    )

    assert calls["count"] == 0
    assert output == "narrative without any canonical artifact"
    assert validated == {
        "repo_evidence": [],
        "unverified_claims": [],
        "has_artifact": False,
        "repair_failed": True,
    }


def test_ensure_validated_repo_evidence_caps_retries_at_one(monkeypatch, tmp_path):
    """SWARM_REPO_EVIDENCE_MAX_RETRIES=1 runs the correction pass but skips artifact repair."""
    monkeypatch.setenv("SWARM_REPO_EVIDENCE_MAX_RETRIES", "1")
    calls = {"count": 0}

    def _retry_run(_prompt: str) -> str:
        calls["count"] += 1
        return "still no canonical artifact"

    output, validated = ensure_validated_repo_evidence(
        raw_output="narrative without any canonical artifact",
        base_prompt="Base prompt",
        workspace_root=str(tmp_path),
        step_id="devops",
        retry_run=_retry_run,
    )

    assert calls["count"] == 1
    assert output == "still no canonical artifact"
    assert validated["repair_failed"] is True
    assert validated["has_artifact"] is False


def test_ensure_validated_repo_evidence_rejects_invalid_retry_cap(monkeypatch, tmp_path):
    monkeypatch.setenv("SWARM_REPO_EVIDENCE_MAX_RETRIES", "not-a-number")
    with pytest.raises(RuntimeError, match="must be an integer"):
        ensure_validated_repo_evidence(
            raw_output="anything",
            base_prompt="Base",
            workspace_root=str(tmp_path),
            step_id="devops",
            retry_run=lambda _: "",
        )

    monkeypatch.setenv("SWARM_REPO_EVIDENCE_MAX_RETRIES", "3")
    with pytest.raises(RuntimeError, match="must be 0, 1, or 2"):
        ensure_validated_repo_evidence(
            raw_output="anything",
            base_prompt="Base",
            workspace_root=str(tmp_path),
            step_id="devops",
            retry_run=lambda _: "",
        )
