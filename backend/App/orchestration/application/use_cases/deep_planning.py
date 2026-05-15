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
    return os.getenv("SWARM_DEEP_PLANNING", "0") == "1"


def _deep_planning_model() -> str:
    return os.getenv("SWARM_DEEP_PLANNING_MODEL", "claude-opus-4-6")


def _deep_planning_provider() -> str:
    return os.getenv("SWARM_DEEP_PLANNING_PROVIDER", "").strip()


def _deep_planning_default_llm_kwargs() -> dict[str, Any]:
    environment = _deep_planning_provider()
    if not environment:
        return {}
    from backend.App.orchestration.application.context.current_step import get_current_agent_config
    from backend.App.orchestration.application.nodes._shared import _remote_api_client_kwargs
    from backend.App.orchestration.infrastructure.agents.llm_backend_selector import (
        LLMBackendSelector,
    )

    agent_config = get_current_agent_config() or {}
    remote_kwargs = (
        _remote_api_client_kwargs({"agent_config": agent_config})
        if environment.lower() in {"cloud", "anthropic"}
        else {}
    )
    selector = LLMBackendSelector()
    cfg = selector.select(
        role="deep_planning",
        model=_deep_planning_model(),
        environment=environment,
        remote_provider=remote_kwargs.get("remote_provider"),
        remote_api_key=remote_kwargs.get("remote_api_key"),
        remote_base_url=remote_kwargs.get("remote_base_url"),
    )
    return selector.ask_kwargs(cfg)


@dataclass
class RiskItem:
    id: str
    description: str
    likelihood: str
    impact: str
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

    def analyze(
        self,
        task_id: str,
        task_spec: str,
        workspace_root: str = "",
        llm_kwargs: dict[str, Any] | None = None,
    ) -> "DeepPlan":
        if not _deep_planning_enabled():
            logger.debug("DeepPlanner: disabled (SWARM_DEEP_PLANNING=0)")
            return DeepPlan(task_id=task_id, task_goal=task_spec)

        plan = DeepPlan(task_id=task_id, task_goal=task_spec)
        _kw = dict(llm_kwargs or {})
        logger.info("DeepPlanner: starting 5-stage analysis for task=%s", task_id)

        try:
            from backend.App.integrations.infrastructure.llm.client import chat_completion_text

            logger.info("DeepPlanner: stage 1/5 — scan")
            workspace_index = self._build_workspace_index(workspace_root)
            scan_prompt = (
                f"You are analyzing a software project for task planning.\n"
                f"Task: {task_spec}\n"
                f"Workspace root: {workspace_root or 'not specified'}\n"
                f"{workspace_index}"
                f"Summarize the key files, modules, and components relevant to this task "
                f"in 200-400 words."
            )
            plan.scan_summary = self._call_llm(chat_completion_text, scan_prompt, _kw)
            plan.raw_responses["scan"] = plan.scan_summary

            logger.info("DeepPlanner: stage 2/5 — risks")
            risks_prompt = (
                f"Task: {task_spec}\n"
                f"Context: {plan.scan_summary[:1000]}\n\n"
                f"Identify the top 5 risks for this task. "
                f"Respond in JSON array format:\n"
                f'[{{"id":"R1","description":"...","likelihood":"high|medium|low",'
                f'"impact":"high|medium|low","mitigation":"..."}}]'
            )
            risks_raw = self._call_llm(chat_completion_text, risks_prompt, _kw)
            plan.raw_responses["risks"] = risks_raw
            plan.risks = self._parse_risks(risks_raw)

            logger.info("DeepPlanner: stage 3/5 — alternatives")
            alts_prompt = (
                f"Task: {task_spec}\n"
                f"Risks: {json.dumps([r.description for r in plan.risks[:3]])}\n\n"
                f"Generate 2-3 implementation alternatives with trade-offs.\n"
                f"Respond in JSON array:\n"
                f'[{{"title":"...","description":"...","pros":["..."],"cons":["..."]}}]'
            )
            alts_raw = self._call_llm(chat_completion_text, alts_prompt, _kw)
            plan.raw_responses["alternatives"] = alts_raw
            plan.alternatives = self._parse_alternatives(alts_raw)

            logger.info("DeepPlanner: stage 4/5 — milestones")
            plan_prompt = (
                f"Task: {task_spec}\n"
                f"Context: {plan.scan_summary[:800]}\n\n"
                f"Create a structured execution plan with milestones and rollback points.\n"
                f"Respond in JSON:\n"
                f'{{"recommended_alternative":"...","milestones":['
                f'{{"id":"M1","title":"...","description":"...","dependencies":[],"rollback_point":false}}]}}'
            )
            plan_raw = self._call_llm(chat_completion_text, plan_prompt, _kw)
            plan.raw_responses["plan"] = plan_raw
            self._parse_plan(plan, plan_raw)

            logger.info(
                "DeepPlanner: stage 5/5 — awaiting human gate for task=%s "
                "(risks=%d alternatives=%d milestones=%d)",
                task_id, len(plan.risks), len(plan.alternatives), len(plan.milestones),
            )

        except Exception as exc:
            logger.error("DeepPlanner: analysis failed for task=%s: %s", task_id, exc)
            plan.error = str(exc)

        return plan

    def save_to_disk(self, plan: DeepPlan, artifacts_root: Path) -> Path:
        out_dir = artifacts_root / plan.task_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "deep_plan.json"
        out_path.write_text(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False))
        logger.info("DeepPlanner: plan written to %s", out_path)
        return out_path

    def _build_workspace_index(self, workspace_root: str, max_entries: int = 200) -> str:
        if not workspace_root:
            return ""
        root = Path(workspace_root)
        if not root.is_dir():
            return ""

        entries: list[str] = []
        try:
            for dirpath, dirnames, filenames in os.walk(root):
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

    def _call_llm(self, llm_fn: Any, prompt: str, llm_kwargs: dict[str, Any] | None = None) -> str:
        merged_kwargs = _deep_planning_default_llm_kwargs()
        if llm_kwargs:
            merged_kwargs.update(llm_kwargs)
        return llm_fn(
            model=_deep_planning_model(),
            messages=[{"role": "user", "content": prompt}],
            **merged_kwargs,
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
        text = re.sub(r"```[a-z]*\n?", "", text).strip().rstrip("`")
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
