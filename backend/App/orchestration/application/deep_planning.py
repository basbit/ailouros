"""Deep planning mode for PM agent (K-11).

When SWARM_DEEP_PLANNING=1, the PM agent runs a 5-stage analysis before
producing the pipeline plan. Human review gate is inserted after plan generation
before execution begins (INV-4, INV-6).

Rules:
- INV-4: Topology immutable by AI — plan is presented for Apply, never auto-executed
- INV-6: Config only via Apply — deep plan is a proposal, not a command
- INV-1: Every planning stage logged explicitly
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEEP_PLANNING_TIMEOUT = int(os.getenv("SWARM_DEEP_PLANNING_TIMEOUT_SECONDS", "1800"))


def _deep_planning_enabled() -> bool:
    """Read at call time so that env set by _initial_pipeline_state is honoured."""
    return os.getenv("SWARM_DEEP_PLANNING", "0") == "1"


def _deep_planning_model() -> str:
    """Read at call time so that env set by _initial_pipeline_state is honoured."""
    return os.getenv("SWARM_DEEP_PLANNING_MODEL", "claude-opus-4-6")


@dataclass
class RiskItem:
    id: str
    description: str
    likelihood: str  # "low" | "medium" | "high"
    impact: str      # "low" | "medium" | "high"
    mitigation: str


@dataclass
class Alternative:
    title: str
    description: str
    pros: list[str] = field(default_factory=list)
    cons: list[str] = field(default_factory=list)


@dataclass
class Milestone:
    id: str
    title: str
    description: str
    dependencies: list[str] = field(default_factory=list)
    rollback_point: bool = False


@dataclass
class DeepPlan:
    """Result of deep planning analysis — proposal only, requires human Apply."""

    task_id: str
    task_goal: str
    scan_summary: str = ""
    risks: list[RiskItem] = field(default_factory=list)
    alternatives: list[Alternative] = field(default_factory=list)
    milestones: list[Milestone] = field(default_factory=list)
    recommended_alternative: str = ""
    raw_responses: dict[str, str] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        import dataclasses
        return dataclasses.asdict(self)


class DeepPlanner:
    """Runs 5-stage deep analysis for complex tasks.

    Usage:
        planner = DeepPlanner()
        plan = planner.analyze(task_id="t-123", task_spec="Build auth system")
        # plan is a DeepPlan — human gate required before execution (INV-4)
    """

    def analyze(self, task_id: str, task_spec: str, workspace_root: str = "") -> DeepPlan:
        """Run full 5-stage deep analysis. Returns DeepPlan proposal.

        INV-4: result is a proposal only — caller must show to human before executing.
        INV-1: each stage is logged.
        """
        if not _deep_planning_enabled():
            logger.debug("DeepPlanner: disabled (SWARM_DEEP_PLANNING=0)")
            return DeepPlan(task_id=task_id, task_goal=task_spec)

        plan = DeepPlan(task_id=task_id, task_goal=task_spec)
        logger.info("DeepPlanner: starting 5-stage analysis for task=%s", task_id)  # INV-1

        try:
            from backend.App.integrations.infrastructure.llm.client import chat_completion_text

            # Stage 1: Scan
            logger.info("DeepPlanner: stage 1/5 — scan")  # INV-1
            workspace_index = self._build_workspace_index(workspace_root)
            scan_prompt = (
                f"You are analyzing a software project for task planning.\n"
                f"Task: {task_spec}\n"
                f"Workspace root: {workspace_root or 'not specified'}\n"
                f"{workspace_index}"
                f"Summarize the key files, modules, and components relevant to this task "
                f"in 200-400 words."
            )
            plan.scan_summary = self._call_llm(chat_completion_text, scan_prompt)
            plan.raw_responses["scan"] = plan.scan_summary

            # Stage 2: Risks
            logger.info("DeepPlanner: stage 2/5 — risks")  # INV-1
            risks_prompt = (
                f"Task: {task_spec}\n"
                f"Context: {plan.scan_summary[:1000]}\n\n"
                f"Identify the top 5 risks for this task. "
                f"Respond in JSON array format:\n"
                f'[{{"id":"R1","description":"...","likelihood":"high|medium|low",'
                f'"impact":"high|medium|low","mitigation":"..."}}]'
            )
            risks_raw = self._call_llm(chat_completion_text, risks_prompt)
            plan.raw_responses["risks"] = risks_raw
            plan.risks = self._parse_risks(risks_raw)

            # Stage 3: Alternatives
            logger.info("DeepPlanner: stage 3/5 — alternatives")  # INV-1
            alts_prompt = (
                f"Task: {task_spec}\n"
                f"Risks: {json.dumps([r.description for r in plan.risks[:3]])}\n\n"
                f"Generate 2-3 implementation alternatives with trade-offs.\n"
                f"Respond in JSON array:\n"
                f'[{{"title":"...","description":"...","pros":["..."],"cons":["..."]}}]'
            )
            alts_raw = self._call_llm(chat_completion_text, alts_prompt)
            plan.raw_responses["alternatives"] = alts_raw
            plan.alternatives = self._parse_alternatives(alts_raw)

            # Stage 4: Plan with milestones
            logger.info("DeepPlanner: stage 4/5 — milestones")  # INV-1
            plan_prompt = (
                f"Task: {task_spec}\n"
                f"Context: {plan.scan_summary[:800]}\n\n"
                f"Create a structured execution plan with milestones and rollback points.\n"
                f"Respond in JSON:\n"
                f'{{"recommended_alternative":"...","milestones":['
                f'{{"id":"M1","title":"...","description":"...","dependencies":[],"rollback_point":false}}]}}'
            )
            plan_raw = self._call_llm(chat_completion_text, plan_prompt)
            plan.raw_responses["plan"] = plan_raw
            self._parse_plan(plan, plan_raw)

            logger.info(  # INV-1, INV-4
                "DeepPlanner: stage 5/5 — awaiting human gate for task=%s "
                "(risks=%d alternatives=%d milestones=%d)",
                task_id, len(plan.risks), len(plan.alternatives), len(plan.milestones),
            )

        except Exception as exc:
            logger.error("DeepPlanner: analysis failed for task=%s: %s", task_id, exc)  # INV-1
            plan.error = str(exc)

        return plan

    def save_to_disk(self, plan: DeepPlan, artifacts_root: Path) -> Path:
        """Write deep_plan.json to artifacts dir. Returns path written."""
        out_dir = artifacts_root / plan.task_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "deep_plan.json"
        out_path.write_text(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False))
        logger.info("DeepPlanner: plan written to %s", out_path)
        return out_path

    def _build_workspace_index(self, workspace_root: str, max_entries: int = 200) -> str:
        """Return a formatted file-tree block for Stage 1 scan prompt.

        Walks `workspace_root` (up to `max_entries` entries) and returns a
        markdown code block with the relative paths. Returns an empty string
        if workspace_root is not set or does not exist.
        """
        if not workspace_root:
            return ""
        root = Path(workspace_root)
        if not root.is_dir():
            return ""

        entries: list[str] = []
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                # Skip hidden and common noise directories in-place
                dirnames[:] = [
                    d for d in sorted(dirnames)
                    if not d.startswith(".") and d not in (
                        "__pycache__", "node_modules", ".git", ".venv",
                        "venv", "dist", "build", ".mypy_cache",
                    )
                ]
                rel_dir = Path(dirpath).relative_to(root)
                for fname in sorted(filenames):
                    if fname.startswith("."):
                        continue
                    entries.append(str(rel_dir / fname) if str(rel_dir) != "." else fname)
                    if len(entries) >= max_entries:
                        break
                if len(entries) >= max_entries:
                    entries.append(f"… (truncated at {max_entries} entries)")
                    break
        except Exception as exc:
            logger.warning("DeepPlanner._build_workspace_index: walk error: %s", exc)
            return ""

        if not entries:
            return ""
        lines = "\n".join(entries)
        return f"\nWorkspace file index ({len(entries)} entries):\n```\n{lines}\n```\n\n"

    def _call_llm(self, llm_fn: Any, prompt: str) -> str:
        return llm_fn(
            model=_deep_planning_model(),
            messages=[{"role": "user", "content": prompt}],
        )

    def _parse_risks(self, raw: str) -> list[RiskItem]:
        try:
            data = self._extract_json(raw)
            if isinstance(data, list):
                return [
                    RiskItem(
                        id=str(r.get("id", f"R{i+1}")),
                        description=str(r.get("description", "")),
                        likelihood=str(r.get("likelihood", "medium")),
                        impact=str(r.get("impact", "medium")),
                        mitigation=str(r.get("mitigation", "")),
                    )
                    for i, r in enumerate(data[:5])
                ]
        except Exception:
            pass
        return []

    def _parse_alternatives(self, raw: str) -> list[Alternative]:
        try:
            data = self._extract_json(raw)
            if isinstance(data, list):
                return [
                    Alternative(
                        title=str(a.get("title", "")),
                        description=str(a.get("description", "")),
                        pros=list(a.get("pros", [])),
                        cons=list(a.get("cons", [])),
                    )
                    for a in data[:3]
                ]
        except Exception:
            pass
        return []

    def _parse_plan(self, plan: DeepPlan, raw: str) -> None:
        try:
            data = self._extract_json(raw)
            if isinstance(data, dict):
                plan.recommended_alternative = str(data.get("recommended_alternative", ""))
                milestones = data.get("milestones", [])
                if isinstance(milestones, list):
                    plan.milestones = [
                        Milestone(
                            id=str(m.get("id", f"M{i+1}")),
                            title=str(m.get("title", "")),
                            description=str(m.get("description", "")),
                            dependencies=list(m.get("dependencies", [])),
                            rollback_point=bool(m.get("rollback_point", False)),
                        )
                        for i, m in enumerate(milestones)
                    ]
        except Exception:
            pass

    def _extract_json(self, text: str) -> Any:
        import re
        text = text.strip()
        # Remove markdown fences
        text = re.sub(r"```[a-z]*\n?", "", text).strip().rstrip("`")
        # Try raw_decode starting from first [ or { — handles trailing content correctly
        decoder = json.JSONDecoder()
        for start_char in ("[", "{"):
            s = text.find(start_char)
            if s != -1:
                try:
                    obj, _ = decoder.raw_decode(text, s)
                    return obj
                except json.JSONDecodeError:
                    continue
        return decoder.decode(text)
