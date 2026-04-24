from __future__ import annotations

from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[2]
ORCHESTRATION_APP_ROOT = REPO_ROOT / "backend" / "App" / "orchestration" / "application"
DIRECT_RUN_RE = re.compile(r"\bagent\.run\(")


def test_direct_agent_run_is_confined_to_canonical_helper_and_human_nodes() -> None:
    """Prevent drift back to ad-hoc machine agent execution paths.

    Machine-facing orchestration code must go through the canonical helper in
    `application/agent_runner.py`. Direct `agent.run(...)` calls are allowed only:
    - in the canonical helper itself
    - in human-only nodes that intentionally block for human input
    """

    allowed_line_snippets = {
        "backend/App/orchestration/application/agents/agent_runner.py": [
            "output = agent.run(prompt)",
        ],
        "backend/App/orchestration/application/nodes/arch.py": [
            'return {"arch_human_output": agent.run(bundle)}',
            'return {"spec_human_output": agent.run(bundle)}',
        ],
        "backend/App/orchestration/application/nodes/ba.py": [
            'return {"ba_human_output": agent.run(bundle)}',
        ],
        "backend/App/orchestration/application/nodes/dev.py": [
            'return {"dev_lead_human_output": agent.run(bundle)}',
        ],
        "backend/App/orchestration/application/nodes/dev_lead.py": [
            'return {"dev_lead_human_output": agent.run(bundle)}',
        ],
        "backend/App/orchestration/application/nodes/dev_review.py": [
            'return {"dev_human_output": agent.run(bundle)}',
        ],
        "backend/App/orchestration/application/nodes/devops.py": [
            'return {"devops_human_output": agent.run(bundle)}',
        ],
        "backend/App/orchestration/application/nodes/documentation.py": [
            'return {"code_review_human_output": agent.run(bundle)}',
        ],
        "backend/App/orchestration/application/nodes/pm.py": [
            'return {"clarify_input_human_output": agent.run(bundle)}',
            'return {"pm_human_output": agent.run(bundle)}',
            "result = agent.run(bundle)",
        ],
        "backend/App/orchestration/application/nodes/pm_clarify.py": [
            'return {"clarify_input_human_output": agent.run(bundle)}',
            "result = agent.run(bundle)",
        ],
        "backend/App/orchestration/application/nodes/qa.py": [
            'return {"qa_human_output": agent.run(bundle)}',
        ],
        "backend/App/orchestration/application/nodes/marketing.py": [
            'return {"seo_specialist_human_output": agent.run(bundle)}',
            'return {"ai_citation_strategist_human_output": agent.run(bundle)}',
            'return {"app_store_optimizer_human_output": agent.run(bundle)}',
        ],
        "backend/App/orchestration/application/nodes/design.py": [
            'return {"ux_researcher_human_output": agent.run(bundle)}',
            'return {"ux_architect_human_output": agent.run(bundle)}',
            'return {"ui_designer_human_output": agent.run(bundle)}',
        ],
        # QuarantineAgent is a read-only summarizer with no tools — intentionally
        # calls agent.run() outside the canonical helper as an isolation buffer.
        "backend/App/orchestration/application/enforcement/untrusted_content.py": [
            "summary = agent.run(prompt)",
        ],
    }

    violations: list[str] = []
    for path in ORCHESTRATION_APP_ROOT.rglob("*.py"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        lines = path.read_text(encoding="utf-8").splitlines()
        for lineno, line in enumerate(lines, start=1):
            if not DIRECT_RUN_RE.search(line):
                continue
            allowed = allowed_line_snippets.get(rel, [])
            if any(snippet in line for snippet in allowed):
                continue
            violations.append(f"{rel}:{lineno}: {line.strip()}")

    assert not violations, (
        "Found direct agent.run(...) outside canonical helper / human-only nodes:\n"
        + "\n".join(violations)
    )
