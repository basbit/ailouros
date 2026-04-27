from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable

from backend.App.orchestration.domain.ports import TaskId, TaskStatus, TaskStorePort
from backend.App.workspace.domain.ports import WorkspaceIOPort
from backend.App.shared.domain.validators import is_truthy_value


@runtime_checkable
class PipelineRunnerProtocol(Protocol):
    def __call__(
        self,
        user_input: str,
        agent_config: dict[str, Any],
        steps: Optional[list[str]],
        workspace_root: str,
        workspace_apply_writes: bool,
        task_id: str,
        /,
        **kwargs: Any,
    ) -> dict[str, Any]: ...


logger = logging.getLogger(__name__)


def _generate_workspace_wiki(
    workspace_root: Path,
    pipeline_state: dict[str, Any],
    task_id: str,
) -> None:
    wiki_root = workspace_root / ".swarm" / "wiki"
    wiki_root.mkdir(parents=True, exist_ok=True)

    _prompt = str(pipeline_state.get("input") or "").strip()[:300]

    index_links: list[str] = []
    for md_file in sorted(wiki_root.rglob("*.md")):
        if md_file.name == "index.md":
            continue
        try:
            rel = md_file.relative_to(wiki_root).with_suffix("").as_posix()
            title = md_file.stem.replace("-", " ").title()
            first_line = md_file.read_text(encoding="utf-8").split("\n")
            for line in first_line:
                if line.startswith("title:"):
                    title = line[len("title:"):].strip()
                    break
            index_links.append(f"- [[{rel}]] — {title}")
        except (OSError, ValueError):
            continue

    index_path = wiki_root / "index.md"
    existing_index = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
    run_section = (
        f"## Run {task_id[:8]}\n\nTask: {_prompt}\n\n"
        + ("\n".join(index_links) if index_links else "_No articles yet._")
        + "\n\n"
    )
    index_path.write_text(
        "---\ntitle: Project Index\ntags: [index]\n---\n\n# Project Wiki\n\n"
        + run_section
        + (existing_index.split("## Run", 1)[1] if "## Run" in existing_index else ""),
        encoding="utf-8",
    )

    from backend.App.workspace.application.wiki_service import build_wiki_graph
    import json as _json
    graph = build_wiki_graph(wiki_root)
    graph_file = wiki_root / "graph.json"
    graph_file.write_text(_json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(
        "wiki_gen: index updated + graph rebuilt (%d nodes) for task=%s ws=%s",
        len(graph["nodes"]), task_id, workspace_root,
    )


@dataclass
class StartPipelineRunCommand:
    task_id: TaskId
    user_prompt: str
    effective_prompt: str
    agent_config: dict[str, Any]
    steps: Optional[list[str]]
    workspace_root_str: str
    workspace_apply_writes: bool
    workspace_meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class StartPipelineRunResult:
    status: TaskStatus
    task_id: TaskId
    final_text: str = ""
    last_agent: str = ""
    error: str = ""
    exc_type: str = ""
    human_approval_step: str = ""
    partial_state: dict[str, Any] = field(default_factory=dict)
    resume_from_step: str = ""


class StartPipelineRunUseCase:
    def __init__(
        self,
        task_store: TaskStorePort,
        workspace_io: WorkspaceIOPort,
        pipeline_runner: PipelineRunnerProtocol,
    ) -> None:
        self._task_store = task_store
        self._workspace_io = workspace_io
        self._pipeline_runner = pipeline_runner

    def execute(self, command: StartPipelineRunCommand) -> StartPipelineRunResult:
        from backend.App.orchestration.domain.exceptions import HumanApprovalRequired
        from backend.App.orchestration.application.pipeline.pipeline_state import pipeline_workspace_parts_from_meta
        from backend.App.integrations.infrastructure.swarm_planner import plan_pipeline_steps as _plan

        tid = command.task_id
        logger.info(
            "StartPipelineRunUseCase.execute: task_id=%s workspace=%s",
            tid,
            bool(command.workspace_root_str.strip()),
        )

        effective_steps = list(command.steps or [])
        swarm_cfg = (command.agent_config or {}).get("swarm") or {}
        from backend.App.orchestration.application.use_cases.asset_pipeline_inclusion import (
            augment_pipeline_steps_for_assets,
        )
        augmented_steps, asset_steps_added = augment_pipeline_steps_for_assets(
            effective_steps,
            command.user_prompt or "",
            command.agent_config or {},
        )
        if asset_steps_added:
            effective_steps = augmented_steps
            logger.info(
                "Asset pipeline inclusion: added %s to pipeline_steps for task=%s",
                asset_steps_added, tid,
            )
        if not effective_steps and is_truthy_value(swarm_cfg.get("auto_plan")):
            try:
                plan_result = _plan(
                    command.user_prompt,
                    agent_config=command.agent_config,
                    constraints=str(swarm_cfg.get("auto_plan_constraints") or ""),
                )
                planned_steps = plan_result.get("pipeline_steps") or []
                if planned_steps:
                    effective_steps = planned_steps
                    logger.info(
                        "Auto-planner selected %d steps: %s (rationale: %s)",
                        len(effective_steps),
                        effective_steps,
                        str(plan_result.get("rationale") or "")[:200],
                    )
                else:
                    logger.warning("Auto-planner returned empty steps, using DEFAULT_PIPELINE_STEP_IDS")
                recommended_models = plan_result.get("recommended_models")
                if isinstance(recommended_models, dict) and recommended_models:
                    agent_cfg: dict = dict(command.agent_config or {})
                    for role, capability in recommended_models.items():
                        role_cfg: dict = dict(agent_cfg.get(role) or {})
                        if not role_cfg.get("model"):
                            role_cfg["_planner_capability"] = capability
                            agent_cfg[role] = role_cfg
                        logger.info(
                            "Auto-planner recommends capability '%s' for role '%s'",
                            capability,
                            role,
                        )
                    command.agent_config = agent_cfg
            except Exception as plan_exc:
                logger.error("Auto-planner failed: %s — using default steps", plan_exc)

        try:
            self._task_store.update_task(
                tid,
                status=TaskStatus.IN_PROGRESS,
                agent="orchestrator",
                message="pipeline started",
            )

            result = self._pipeline_runner(
                command.effective_prompt,
                command.agent_config,
                effective_steps,
                command.workspace_root_str,
                command.workspace_apply_writes,
                tid.value,
                pipeline_workspace_parts=pipeline_workspace_parts_from_meta(command.workspace_meta),
                pipeline_step_ids=effective_steps,
            )

        except HumanApprovalRequired as exc:
            self._task_store.update_task(
                tid,
                status=TaskStatus.AWAITING_HUMAN,
                agent="orchestrator",
                message=str(exc)[:2000],
            )
            return StartPipelineRunResult(
                status=TaskStatus.AWAITING_HUMAN,
                task_id=tid,
                error=str(exc),
                human_approval_step=exc.step,
                partial_state=exc.partial_state or {},
                resume_from_step=exc.resume_pipeline_step or "",
            )
        except Exception as exc:
            self._task_store.update_task(
                tid,
                status=TaskStatus.FAILED,
                agent="orchestrator",
                message=str(exc)[:2000],
            )
            return StartPipelineRunResult(
                status=TaskStatus.FAILED,
                task_id=tid,
                error=str(exc),
                exc_type=type(exc).__name__,
            )

        final_text = ""
        last_agent = ""
        if isinstance(result, dict):
            for key in ("qa_output", "dev_output", "arch_output", "ba_output", "pm_output"):
                val = result.get(key, "")
                if val:
                    final_text = val
                    last_agent = key.replace("_output", "")
                    break
            if not final_text:
                final_text = result.get("input", "")

            _ws = str(result.get("workspace_root") or command.workspace_root_str or "").strip()
            if _ws:
                try:
                    _generate_workspace_wiki(
                        workspace_root=Path(_ws),
                        pipeline_state=result,
                        task_id=tid.value,
                    )
                except Exception as wiki_exc:
                    logger.warning("Wiki generation failed (non-fatal): %s", wiki_exc)

        self._task_store.update_task(
            tid,
            status=TaskStatus.COMPLETED,
            agent=last_agent or "orchestrator",
            message="pipeline completed",
        )
        return StartPipelineRunResult(
            status=TaskStatus.COMPLETED,
            task_id=tid,
            final_text=final_text,
            last_agent=last_agent,
        )
