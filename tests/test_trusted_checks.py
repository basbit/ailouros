from __future__ import annotations

from pathlib import Path
import re

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKER_COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"

FRONTEND_SHELL_SCOPE = [
    REPO_ROOT / "frontend/src/pages/swarm-ui/SwarmUiPage.vue",
    REPO_ROOT / "frontend/src/widgets/header/AppHeader.vue",
    REPO_ROOT / "frontend/src/widgets/status-line/StatusLine.vue",
    REPO_ROOT / "frontend/src/widgets/task-panel/EventsFeed.vue",
    REPO_ROOT / "frontend/src/widgets/task-panel/HistoryPanel.vue",
    REPO_ROOT / "frontend/src/widgets/task-panel/HostMetrics.vue",
    REPO_ROOT / "frontend/src/widgets/task-monitor/TaskMonitor.vue",
    REPO_ROOT / "frontend/src/features/project-settings/ProjectSelect.vue",
    REPO_ROOT / "frontend/src/features/memory-panel/MemoryPanel.vue",
    REPO_ROOT / "frontend/src/features/prompt-input/PromptInput.vue",
    REPO_ROOT / "frontend/src/features/workspace/WorkspaceSettings.vue",
    REPO_ROOT / "frontend/src/features/swarm-settings/AutonomousSettings.vue",
    REPO_ROOT / "frontend/src/features/task-gate/ShellGate.vue",
    REPO_ROOT / "frontend/src/features/task-gate/HumanGate.vue",
    REPO_ROOT / "frontend/src/features/task-gate/RetryGate.vue",
    REPO_ROOT / "frontend/src/features/remote-api/RemoteApiProfiles.vue",
    REPO_ROOT / "frontend/src/features/agent-roles/AgentRoles.vue",
    REPO_ROOT / "frontend/src/features/agent-roles/AgentRoleRow.vue",
    REPO_ROOT / "frontend/src/features/dev-roles/DevRoles.vue",
    REPO_ROOT / "frontend/src/features/custom-roles/CustomRoles.vue",
    REPO_ROOT / "frontend/src/features/skills-catalog/SkillsCatalog.vue",
]

FRONTEND_MODEL_FREE_SCOPE = [
    REPO_ROOT / "frontend/src/pages/swarm-ui/SwarmUiPage.vue",
    REPO_ROOT / "frontend/src/widgets/header/AppHeader.vue",
    REPO_ROOT / "frontend/src/widgets/status-line/StatusLine.vue",
    REPO_ROOT / "frontend/src/widgets/task-panel/EventsFeed.vue",
    REPO_ROOT / "frontend/src/widgets/task-panel/HistoryPanel.vue",
    REPO_ROOT / "frontend/src/widgets/task-panel/HostMetrics.vue",
    REPO_ROOT / "frontend/src/widgets/task-monitor/TaskMonitor.vue",
    REPO_ROOT / "frontend/src/features/project-settings/ProjectSelect.vue",
    REPO_ROOT / "frontend/src/features/memory-panel/MemoryPanel.vue",
    REPO_ROOT / "frontend/src/features/prompt-input/PromptInput.vue",
    REPO_ROOT / "frontend/src/features/workspace/WorkspaceSettings.vue",
    REPO_ROOT / "frontend/src/features/swarm-settings/SwarmSettings.vue",
    REPO_ROOT / "frontend/src/features/swarm-settings/DatabaseSettings.vue",
    REPO_ROOT / "frontend/src/features/swarm-settings/McpSettings.vue",
    REPO_ROOT / "frontend/src/features/task-gate/ShellGate.vue",
    REPO_ROOT / "frontend/src/features/task-gate/HumanGate.vue",
    REPO_ROOT / "frontend/src/features/task-gate/RetryGate.vue",
    REPO_ROOT / "frontend/src/widgets/onboarding-wizard/OnboardingWizard.vue",
]

FRONTEND_SCRIPT_I18N_SCOPE = [
    REPO_ROOT / "frontend/src/features/chat/useChat.ts",
    REPO_ROOT / "frontend/src/features/swarm-run/useSwarmRunController.ts",
    REPO_ROOT / "frontend/src/pages/swarm-ui/SwarmUiPage.vue",
    REPO_ROOT / "frontend/src/widgets/header/AppHeader.vue",
]

RAW_UI_LITERAL_SNIPPETS = [
    "Clarification required",
    "Awaiting review input.",
    "Start Pipeline",
    "Manual human review",
    "Invalid MCP JSON",
    "Add at least one pipeline step",
    "Artifacts",
    "Clarify cache:",
    "Workspace identity:",
]

CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
HARDCODED_MODEL_TOKEN_RE = re.compile(
    r"\b(gpt-|claude|gemini|deepseek|qwen|llama|mistral|olmo|haiku|sonnet|ollama)\b",
    re.IGNORECASE,
)


def _template_lines(path: Path) -> list[tuple[int, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    in_script = False
    out: list[tuple[int, str]] = []
    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("<script"):
            in_script = True
        if not in_script:
            out.append((lineno, line))
        if in_script and stripped.startswith("</script>"):
            in_script = False
    return out


def test_docker_compose_infra_services_present() -> None:
    """docker-compose.yml must define the required infrastructure services."""
    data = yaml.safe_load(DOCKER_COMPOSE_PATH.read_text(encoding="utf-8"))
    services = data.get("services") or {}
    assert "redis" in services, "docker-compose must define redis"
    assert "qdrant" in services, "docker-compose must define qdrant"


def test_frontend_shell_scope_has_no_raw_cyrillic_literals() -> None:
    violations: list[str] = []
    for path in FRONTEND_SHELL_SCOPE:
        for lineno, line in _template_lines(path):
            if CYRILLIC_RE.search(line):
                violations.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
    assert not violations, (
        "Frontend shell/components should route user-facing copy through i18n instead "
        "of embedding raw Cyrillic literals:\n" + "\n".join(violations)
    )


def test_frontend_shell_scope_has_no_hardcoded_model_or_provider_tokens() -> None:
    violations: list[str] = []
    for path in FRONTEND_MODEL_FREE_SCOPE:
        for lineno, line in _template_lines(path):
            if HARDCODED_MODEL_TOKEN_RE.search(line):
                violations.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
    assert not violations, (
        "Frontend shell/components should not hardcode model/provider tokens in UI "
        "surfaces covered by trusted check:\n" + "\n".join(violations)
    )


def test_frontend_script_i18n_scope_has_no_raw_cyrillic_literals() -> None:
    violations: list[str] = []
    for path in FRONTEND_SCRIPT_I18N_SCOPE:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if CYRILLIC_RE.search(line):
                violations.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
    assert not violations, (
        "Frontend logic/components in the script i18n scope should route user-facing "
        "copy through i18n instead of embedding raw Cyrillic literals:\n" + "\n".join(violations)
    )


def test_frontend_script_i18n_scope_has_no_known_raw_ui_literals() -> None:
    violations: list[str] = []
    for path in FRONTEND_SCRIPT_I18N_SCOPE:
        text = path.read_text(encoding="utf-8")
        for snippet in RAW_UI_LITERAL_SNIPPETS:
            if snippet in text:
                violations.append(f"{path.relative_to(REPO_ROOT)}: contains raw UI literal {snippet!r}")
    assert not violations, (
        "Frontend logic/components in the script i18n scope should use i18n keys "
        "instead of embedding known raw UI literals:\n" + "\n".join(violations)
    )
