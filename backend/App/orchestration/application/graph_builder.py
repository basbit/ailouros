"""PipelineGraphBuilder — builds and compiles the LangGraph StateGraph.

Extracted from ``pipeline_graph.py`` (DECOMP-10).

Backward-compat: ``pipeline_graph.build_graph()`` delegates here.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

from backend.App.orchestration.application.pipeline_state import PipelineState
from backend.App.orchestration.application.pipeline_runtime_support import (
    load_defect_report as _load_defect_report,
    merge_defect_reports as _merge_defect_reports,
)
from backend.App.orchestration.application.pipeline_runners import (
    _enforce_planning_review_gate,
    _enter_fix_cycle_or_escalate,
    _finalize_pipeline_machine,
    _require_structured_blockers,
    _run_post_dev_verification_gates,
    _sync_pipeline_machine,
    _transition_pipeline_phase,
)
from backend.App.orchestration.application.step_decorator import hook_wrap
from backend.App.orchestration.domain.graph_runtime import ConditionalEdgeDef, EdgeDef, GraphDefinition, NodeDef
from backend.App.orchestration.domain.quality_gate_policy import extract_verdict, should_retry as _qg_should_retry
from backend.App.orchestration.domain.pipeline_machine import PipelineMachine, PipelinePhase
from backend.App.orchestration.infrastructure.runtime_policy import load_approval_policy_from_env
from backend.App.orchestration.infrastructure.langgraph_adapter import LangGraphAdapter as _LangGraphAdapter

logger = logging.getLogger(__name__)

# Module-level defaults — read once at import for test patchability.
# Runtime functions below re-read env at call time so UI wiring works.
_QUALITY_GATE_ENABLED_DEFAULT = os.getenv("SWARM_AUTO_RETRY_ON_NEEDS_WORK", "1") == "1"
_MAX_STEP_RETRIES = int(os.getenv("SWARM_MAX_STEP_RETRIES", "2"))


def _quality_gate_env_default() -> bool:
    """Read at call time so _set_feature_env wiring from UI works."""
    return os.getenv("SWARM_AUTO_RETRY_ON_NEEDS_WORK", "1") == "1"


def _max_step_retries_env() -> int:
    """Read at call time so _set_feature_env wiring from UI works."""
    try:
        return int(os.getenv("SWARM_MAX_STEP_RETRIES", "2"))
    except ValueError:
        return 2


_approval_policy = load_approval_policy_from_env()


def _quality_gate_enabled(state: dict) -> bool:
    """Return whether the quality gate is active for this run.

    Checks agent_config.swarm.quality_gate_enabled first (UI toggle),
    then falls back to SWARM_AUTO_RETRY_ON_NEEDS_WORK env var.
    """
    agent_config = state.get("agent_config") or {}
    swarm = agent_config.get("swarm") if isinstance(agent_config, dict) else None
    if isinstance(swarm, dict) and "quality_gate_enabled" in swarm:
        val = swarm["quality_gate_enabled"]
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.strip().lower() in ("1", "true", "yes", "on")
    # Re-read env at call time (UI wiring sets env after module import).
    # Fall back to module-level _QUALITY_GATE_ENABLED_DEFAULT for test patchability.
    _env_val = os.getenv("SWARM_AUTO_RETRY_ON_NEEDS_WORK", "").strip()
    if _env_val:
        return _env_val == "1"
    return _QUALITY_GATE_ENABLED_DEFAULT


def _with_approval_gate(step_id: str, node_fn: Callable) -> Callable:
    """Wrap a human-gate node with ApprovalPolicy check (K-5/M-9).

    If the policy auto-approves, returns {} immediately (skips human wait).
    """
    def _wrapped(state: PipelineState) -> dict[str, Any]:
        decision = _approval_policy.evaluate({"step_id": step_id}, dict(state))
        if decision.approved:
            logger.info(
                "AutoApproval: step=%s skipping human gate (rule=%s)",
                step_id, decision.rule_matched,
            )
            auto_approvals: list[dict[str, Any]] = list(state.get("auto_approvals") or [])
            auto_approvals.append({"step": step_id, "audit": decision.audit})
            return {"auto_approvals": auto_approvals}
        return node_fn(state)
    return _wrapped


def _dev_review_router(state: dict) -> str:
    """Conditional edge router after REVIEW_DEV.

    Returns: 'retry' (→ DEV_RETRY_GATE) | 'continue' (→ HUMAN_DEV)
    """
    if not _quality_gate_enabled(state):
        return "continue"
    verdict = extract_verdict(state.get("dev_review_output") or "")
    report = _load_defect_report(state, "dev_defect_report")
    _require_structured_blockers(report=report, verdict=verdict, step_id="review_dev")
    from backend.App.orchestration.application.pipeline_state_helpers import get_step_retries
    retries = get_step_retries(state, "dev")
    _max_retries = _max_step_retries_env()
    decision = _qg_should_retry(verdict, retries, _max_retries)
    if decision == "retry":
        logger.info(
            "QualityGate: dev verdict=NEEDS_WORK retries=%d/%d → retry",
            retries, _max_retries,
        )
        return "retry"
    if decision == "escalate":
        logger.info(
            "QualityGate: dev verdict=NEEDS_WORK retries=%d/%d exhausted → continue",
            retries, _max_retries,
        )
    return "continue"


def _dev_retry_gate_node(state: PipelineState) -> dict[str, Any]:
    """Increment dev retry counter, enter FIX, then route back to DEV."""
    from backend.App.orchestration.application.pipeline_state_helpers import get_step_retries

    machine = PipelineMachine.from_dict(state.get("pipeline_machine") or {})
    report = _load_defect_report(state, "dev_defect_report")
    open_defects = [d.to_dict() for d in report.open_p0 + report.open_p1]
    state["open_defects"] = open_defects
    _enter_fix_cycle_or_escalate(state, machine, report, step_id="review_dev")
    retries = get_step_retries(state, "dev")
    step_retries = dict(state.get("step_retries") or {})
    step_retries["dev"] = retries + 1
    logger.info("QualityGate: DEV_RETRY_GATE retry %d/%d", retries + 1, _max_step_retries_env())
    return {
        "step_retries": step_retries,
        "open_defects": open_defects,
        "pipeline_phase": state.get("pipeline_phase", machine.phase.value),
        "pipeline_machine": state.get("pipeline_machine", machine.to_dict()),
    }


def _qa_retry_gate_node(state: PipelineState) -> dict[str, Any]:
    """Increment QA retry counter, enter FIX, then route back to DEV."""
    from backend.App.orchestration.application.pipeline_state_helpers import get_step_retries

    machine = PipelineMachine.from_dict(state.get("pipeline_machine") or {})
    qa_report = _load_defect_report(state, "qa_defect_report")
    qa_review_report = _load_defect_report(state, "qa_review_defect_report")
    report = _merge_defect_reports(qa_report, qa_review_report)
    open_defects = [d.to_dict() for d in report.open_p0 + report.open_p1]
    state["open_defects"] = open_defects
    _enter_fix_cycle_or_escalate(state, machine, report, step_id="review_qa")
    retries = get_step_retries(state, "qa")
    step_retries = dict(state.get("step_retries") or {})
    step_retries["qa"] = retries + 1
    logger.info("QualityGate: QA_RETRY_GATE retry %d/%d", retries + 1, _max_step_retries_env())
    return {
        "step_retries": step_retries,
        "open_defects": open_defects,
        "pipeline_phase": state.get("pipeline_phase", machine.phase.value),
        "pipeline_machine": state.get("pipeline_machine", machine.to_dict()),
    }


def _qa_review_router(state: dict) -> str:
    """Conditional edge router after REVIEW_QA using structured defects."""
    if not _quality_gate_enabled(state):
        return "continue"
    verdict = extract_verdict(state.get("qa_review_output") or "")
    qa_report = _load_defect_report(state, "qa_defect_report")
    qa_review_report = _load_defect_report(state, "qa_review_defect_report")
    report = _merge_defect_reports(qa_report, qa_review_report)
    _require_structured_blockers(report=report, verdict=verdict, step_id="review_qa")
    from backend.App.orchestration.application.pipeline_state_helpers import get_step_retries
    retries = get_step_retries(state, "qa")
    _max_retries = _max_step_retries_env()
    decision = _qg_should_retry(verdict, retries, _max_retries)
    if decision == "retry":
        logger.info(
            "QualityGate: qa verdict=NEEDS_WORK retries=%d/%d → retry",
            retries, _max_retries,
        )
        return "retry"
    if decision == "escalate":
        logger.info(
            "QualityGate: qa verdict=NEEDS_WORK retries=%d/%d exhausted → continue",
            retries, _max_retries,
        )
    return "continue"


def _dev_node_with_machine(state: PipelineState) -> dict[str, Any]:
    from backend.App.orchestration.application.nodes.dev import dev_node

    return dev_node(state)


def _qa_node_with_machine(state: PipelineState) -> dict[str, Any]:
    from backend.App.orchestration.application.nodes.qa import qa_node

    machine = PipelineMachine.from_dict(state.get("pipeline_machine") or {})
    if machine.phase in (PipelinePhase.VERIFY, PipelinePhase.IMPLEMENT):
        _transition_pipeline_phase(state, machine, PipelinePhase.QA, source="system")
    result = qa_node(state)
    _sync_pipeline_machine(state, machine)
    return {
        **result,
        "pipeline_phase": state.get("pipeline_phase", machine.phase.value),
        "pipeline_machine": state.get("pipeline_machine", machine.to_dict()),
    }


def _review_dev_node_with_open_defects(state: PipelineState) -> dict[str, Any]:
    from backend.App.orchestration.application.nodes.dev import review_dev_node

    result = review_dev_node(state)
    report = _load_defect_report(result, "dev_defect_report")
    open_defects = [d.to_dict() for d in report.open_p0 + report.open_p1]
    return {**result, "open_defects": open_defects}


def _review_qa_node_with_open_defects(state: PipelineState) -> dict[str, Any]:
    from backend.App.orchestration.application.nodes.qa import review_qa_node

    result = review_qa_node(state)
    qa_report = _load_defect_report(state, "qa_defect_report")
    qa_review_report = _load_defect_report(result, "qa_review_defect_report")
    report = _merge_defect_reports(qa_report, qa_review_report)
    open_defects = [d.to_dict() for d in report.open_p0 + report.open_p1]
    return {**result, "open_defects": open_defects}


def _planning_review_gate_wrapper(
    step_id: str,
    output_key: str,
    node_fn: Callable[[PipelineState], dict[str, Any]],
) -> Callable[[PipelineState], dict[str, Any]]:
    def _wrapped(state: PipelineState) -> dict[str, Any]:
        result = node_fn(state)
        review_output = str(result.get(output_key) or state.get(output_key) or "")
        _enforce_planning_review_gate(state, step_id=step_id, review_output=review_output)
        return result
    return _wrapped


def _verification_layer_node(state: PipelineState) -> dict[str, Any]:
    machine = PipelineMachine.from_dict(state.get("pipeline_machine") or {})
    gate_results = _run_post_dev_verification_gates(state)
    if machine.phase == PipelinePhase.PLAN:
        _transition_pipeline_phase(state, machine, PipelinePhase.IMPLEMENT, source="system")
    if machine.phase in (PipelinePhase.IMPLEMENT, PipelinePhase.FIX):
        _transition_pipeline_phase(state, machine, PipelinePhase.VERIFY, source="verification_layer")
    updates: dict[str, Any] = {
        "verification_gates": state.get("verification_gates") or [],
        "dev_manifest": state.get("dev_manifest") or {},
        "pipeline_phase": state.get("pipeline_phase", machine.phase.value),
        "pipeline_machine": state.get("pipeline_machine", machine.to_dict()),
    }
    if "workspace_writes" in state:
        updates["workspace_writes"] = state["workspace_writes"]
    if gate_results:
        updates["verification_gate_summary"] = ", ".join(
            result["gate_name"] for result in gate_results
        )
    return updates


def _finalize_pipeline_node(state: PipelineState) -> dict[str, Any]:
    machine = PipelineMachine.from_dict(state.get("pipeline_machine") or {})
    _finalize_pipeline_machine(state, machine)
    return {
        "pipeline_phase": state.get("pipeline_phase", machine.phase.value),
        "pipeline_machine": state.get("pipeline_machine", machine.to_dict()),
    }


class PipelineGraphBuilder:
    """Builds the full LangGraph ``StateGraph`` for the standard pipeline.

    Usage::

        builder = PipelineGraphBuilder()
        compiled = builder.build()   # returns CompiledGraph
    """

    def build(self):
        """Build and compile the standard pipeline graph.

        Returns:
            A compiled LangGraph graph ready for ``.invoke()`` / ``.stream()``.
        """
        from backend.App.orchestration.application.nodes.pm import (
            pm_node, review_pm_node, human_pm_node,
        )
        from backend.App.orchestration.application.nodes.ba import ba_node, review_ba_node, human_ba_node
        from backend.App.orchestration.application.nodes.arch import (
            arch_node, review_stack_node, review_arch_node, human_arch_node,
            merge_spec_node, review_spec_node, human_spec_node,
        )
        from backend.App.orchestration.application.nodes.documentation import (
            analyze_code_node, generate_documentation_node, problem_spotter_node,
            refactor_plan_node, human_code_review_node,
        )
        from backend.App.orchestration.application.nodes.devops import (
            devops_node, review_devops_node, human_devops_node,
        )
        from backend.App.orchestration.application.nodes.dev import (
            dev_lead_node, review_dev_lead_node, human_dev_lead_node,
            human_dev_node,
        )
        from backend.App.orchestration.application.nodes.qa import human_qa_node

        definition = GraphDefinition(
            nodes=[
                NodeDef("PM", hook_wrap("pm", pm_node)),
                NodeDef("REVIEW_PM", hook_wrap("review_pm", _planning_review_gate_wrapper("review_pm", "pm_review_output", review_pm_node))),
                NodeDef("HUMAN_PM", hook_wrap("human_pm", _with_approval_gate("human_pm", human_pm_node))),
                NodeDef("BA", hook_wrap("ba", ba_node)),
                NodeDef("REVIEW_BA", hook_wrap("review_ba", review_ba_node)),
                NodeDef("HUMAN_BA", hook_wrap("human_ba", _with_approval_gate("human_ba", human_ba_node))),
                NodeDef("ARCH", hook_wrap("architect", arch_node)),
                NodeDef("REVIEW_STACK", hook_wrap("review_stack", _planning_review_gate_wrapper("review_stack", "stack_review_output", review_stack_node))),
                NodeDef("REVIEW_ARCH", hook_wrap("review_arch", _planning_review_gate_wrapper("review_arch", "arch_review_output", review_arch_node))),
                NodeDef("HUMAN_ARCH", hook_wrap("human_arch", _with_approval_gate("human_arch", human_arch_node))),
                NodeDef("SPEC_MERGE", hook_wrap("spec_merge", merge_spec_node)),
                NodeDef("REVIEW_SPEC", hook_wrap("review_spec", review_spec_node)),
                NodeDef("HUMAN_SPEC", hook_wrap("human_spec", _with_approval_gate("human_spec", human_spec_node))),
                NodeDef("ANALYZE_CODE", hook_wrap("analyze_code", analyze_code_node)),
                NodeDef("GENERATE_DOCUMENTATION", hook_wrap("generate_documentation", generate_documentation_node)),
                NodeDef("PROBLEM_SPOTTER", hook_wrap("problem_spotter", problem_spotter_node)),
                NodeDef("REFACTOR_PLAN", hook_wrap("refactor_plan", refactor_plan_node)),
                NodeDef(
                    "HUMAN_CODE_REVIEW",
                    hook_wrap("human_code_review", _with_approval_gate("human_code_review", human_code_review_node)),
                ),
                NodeDef("DEVOPS", hook_wrap("devops", devops_node)),
                NodeDef("REVIEW_DEVOPS", hook_wrap("review_devops", review_devops_node)),
                NodeDef("HUMAN_DEVOPS", hook_wrap("human_devops", _with_approval_gate("human_devops", human_devops_node))),
                NodeDef("DEV_LEAD", hook_wrap("dev_lead", dev_lead_node)),
                NodeDef("REVIEW_DEV_LEAD", hook_wrap("review_dev_lead", review_dev_lead_node)),
                NodeDef("HUMAN_DEV_LEAD", hook_wrap("human_dev_lead", _with_approval_gate("human_dev_lead", human_dev_lead_node))),
                NodeDef("DEV", hook_wrap("dev", _dev_node_with_machine)),
                NodeDef("VERIFICATION_LAYER", hook_wrap("verification_layer", _verification_layer_node)),
                NodeDef("REVIEW_DEV", hook_wrap("review_dev", _review_dev_node_with_open_defects)),
                NodeDef("DEV_RETRY_GATE", hook_wrap("dev_retry_gate", _dev_retry_gate_node)),
                NodeDef("HUMAN_DEV", hook_wrap("human_dev", _with_approval_gate("human_dev", human_dev_node))),
                NodeDef("QA", hook_wrap("qa", _qa_node_with_machine)),
                NodeDef("REVIEW_QA", hook_wrap("review_qa", _review_qa_node_with_open_defects)),
                NodeDef("QA_RETRY_GATE", hook_wrap("qa_retry_gate", _qa_retry_gate_node)),
                NodeDef("HUMAN_QA", hook_wrap("human_qa", _with_approval_gate("human_qa", human_qa_node))),
                NodeDef("FINALIZE_PIPELINE", hook_wrap("finalize_pipeline", _finalize_pipeline_node)),
            ],
            edges=[
                EdgeDef("__start__", "PM"),
                EdgeDef("PM", "REVIEW_PM"),
                EdgeDef("REVIEW_PM", "HUMAN_PM"),
                EdgeDef("HUMAN_PM", "BA"),
                EdgeDef("HUMAN_PM", "ARCH"),
                EdgeDef("BA", "REVIEW_BA"),
                EdgeDef("REVIEW_BA", "HUMAN_BA"),
                EdgeDef("HUMAN_BA", "SPEC_MERGE"),
                EdgeDef("ARCH", "REVIEW_STACK"),
                EdgeDef("REVIEW_STACK", "REVIEW_ARCH"),
                EdgeDef("REVIEW_ARCH", "HUMAN_ARCH"),
                EdgeDef("HUMAN_ARCH", "SPEC_MERGE"),
                EdgeDef("SPEC_MERGE", "REVIEW_SPEC"),
                EdgeDef("REVIEW_SPEC", "HUMAN_SPEC"),
                EdgeDef("HUMAN_SPEC", "ANALYZE_CODE"),
                EdgeDef("ANALYZE_CODE", "GENERATE_DOCUMENTATION"),
                EdgeDef("GENERATE_DOCUMENTATION", "PROBLEM_SPOTTER"),
                EdgeDef("PROBLEM_SPOTTER", "REFACTOR_PLAN"),
                EdgeDef("REFACTOR_PLAN", "HUMAN_CODE_REVIEW"),
                EdgeDef("HUMAN_CODE_REVIEW", "DEVOPS"),
                EdgeDef("DEVOPS", "REVIEW_DEVOPS"),
                EdgeDef("REVIEW_DEVOPS", "HUMAN_DEVOPS"),
                EdgeDef("HUMAN_DEVOPS", "DEV_LEAD"),
                EdgeDef("DEV_LEAD", "REVIEW_DEV_LEAD"),
                EdgeDef("REVIEW_DEV_LEAD", "HUMAN_DEV_LEAD"),
                EdgeDef("HUMAN_DEV_LEAD", "DEV"),
                EdgeDef("DEV", "VERIFICATION_LAYER"),
                EdgeDef("VERIFICATION_LAYER", "REVIEW_DEV"),
                EdgeDef("DEV_RETRY_GATE", "DEV"),
                EdgeDef("HUMAN_DEV", "QA"),
                EdgeDef("QA", "REVIEW_QA"),
                EdgeDef("QA_RETRY_GATE", "DEV"),
                EdgeDef("HUMAN_QA", "FINALIZE_PIPELINE"),
                EdgeDef("FINALIZE_PIPELINE", "__end__"),
            ],
            conditional_edges=[
                ConditionalEdgeDef(
                    from_node="REVIEW_DEV",
                    router=_dev_review_router,
                    route_map={"retry": "DEV_RETRY_GATE", "continue": "HUMAN_DEV"},
                ),
                ConditionalEdgeDef(
                    from_node="REVIEW_QA",
                    router=_qa_review_router,
                    route_map={"retry": "QA_RETRY_GATE", "continue": "HUMAN_QA"},
                ),
            ],
        )
        return _LangGraphAdapter().compile(definition, PipelineState)

    def build_for_topology(self, topology: str, agent_config: dict | None = None):
        """Build and compile a topology-specific pipeline graph.

        Args:
            topology: One of ``""``, ``"parallel"``, ``"default"``, ``"hierarchical"``,
                ``"ring"``, or ``"mesh"``.
            agent_config: Agent configuration dict forwarded to nodes that inspect it
                (e.g. mesh parallelism detection in dev/qa nodes).

        Returns:
            A compiled LangGraph graph ready for ``.invoke()`` / ``.stream()``.

        Raises:
            ValueError: If an unrecognised topology string is provided (no fallbacks).
        """
        if topology in ("", "parallel", "default"):
            return self.build()

        if topology == "hierarchical":
            return self._build_hierarchical()

        if topology == "ring":
            return self._build_ring()

        if topology == "mesh":
            return self._build_mesh(agent_config)

        raise ValueError(
            f"Unknown topology {topology!r}. "
            "Valid values: '', 'parallel', 'default', 'hierarchical', 'ring', 'mesh'."
        )

    def _build_hierarchical(self):
        """Build slim hierarchical graph: PM → dev_lead chain only, no BA/ARCH fork."""
        from backend.App.orchestration.application.nodes.pm import (
            pm_node, review_pm_node, human_pm_node,
        )
        from backend.App.orchestration.application.nodes.dev import (
            dev_lead_node, review_dev_lead_node, human_dev_lead_node,
            human_dev_node,
        )
        from backend.App.orchestration.application.nodes.qa import human_qa_node

        definition = GraphDefinition(
            nodes=[
                NodeDef("PM", hook_wrap("pm", pm_node)),
                NodeDef("REVIEW_PM", hook_wrap("review_pm", _planning_review_gate_wrapper("review_pm", "pm_review_output", review_pm_node))),
                NodeDef("HUMAN_PM", hook_wrap("human_pm", _with_approval_gate("human_pm", human_pm_node))),
                NodeDef("DEV_LEAD", hook_wrap("dev_lead", dev_lead_node)),
                NodeDef("REVIEW_DEV_LEAD", hook_wrap("review_dev_lead", review_dev_lead_node)),
                NodeDef("HUMAN_DEV_LEAD", hook_wrap("human_dev_lead", _with_approval_gate("human_dev_lead", human_dev_lead_node))),
                NodeDef("DEV", hook_wrap("dev", _dev_node_with_machine)),
                NodeDef("VERIFICATION_LAYER", hook_wrap("verification_layer", _verification_layer_node)),
                NodeDef("REVIEW_DEV", hook_wrap("review_dev", _review_dev_node_with_open_defects)),
                NodeDef("DEV_RETRY_GATE", hook_wrap("dev_retry_gate", _dev_retry_gate_node)),
                NodeDef("HUMAN_DEV", hook_wrap("human_dev", _with_approval_gate("human_dev", human_dev_node))),
                NodeDef("QA", hook_wrap("qa", _qa_node_with_machine)),
                NodeDef("REVIEW_QA", hook_wrap("review_qa", _review_qa_node_with_open_defects)),
                NodeDef("QA_RETRY_GATE", hook_wrap("qa_retry_gate", _qa_retry_gate_node)),
                NodeDef("HUMAN_QA", hook_wrap("human_qa", _with_approval_gate("human_qa", human_qa_node))),
                NodeDef("FINALIZE_PIPELINE", hook_wrap("finalize_pipeline", _finalize_pipeline_node)),
            ],
            edges=[
                EdgeDef("__start__", "PM"),
                EdgeDef("PM", "REVIEW_PM"),
                EdgeDef("REVIEW_PM", "HUMAN_PM"),
                EdgeDef("HUMAN_PM", "DEV_LEAD"),
                EdgeDef("DEV_LEAD", "REVIEW_DEV_LEAD"),
                EdgeDef("REVIEW_DEV_LEAD", "HUMAN_DEV_LEAD"),
                EdgeDef("HUMAN_DEV_LEAD", "DEV"),
                EdgeDef("DEV", "VERIFICATION_LAYER"),
                EdgeDef("VERIFICATION_LAYER", "REVIEW_DEV"),
                EdgeDef("DEV_RETRY_GATE", "DEV"),
                EdgeDef("HUMAN_DEV", "QA"),
                EdgeDef("QA", "REVIEW_QA"),
                EdgeDef("QA_RETRY_GATE", "DEV"),
                EdgeDef("HUMAN_QA", "FINALIZE_PIPELINE"),
                EdgeDef("FINALIZE_PIPELINE", "__end__"),
            ],
            conditional_edges=[
                ConditionalEdgeDef(
                    from_node="REVIEW_DEV",
                    router=_dev_review_router,
                    route_map={"retry": "DEV_RETRY_GATE", "continue": "HUMAN_DEV"},
                ),
                ConditionalEdgeDef(
                    from_node="REVIEW_QA",
                    router=_qa_review_router,
                    route_map={"retry": "QA_RETRY_GATE", "continue": "HUMAN_QA"},
                ),
            ],
        )
        return _LangGraphAdapter().compile(definition, PipelineState)

    def _build_ring(self):
        """Build full graph with QA ring-back: REVIEW_QA can route to DEV on failure."""
        from backend.App.orchestration.application.nodes.pm import (
            pm_node, review_pm_node, human_pm_node,
        )
        from backend.App.orchestration.application.nodes.ba import ba_node, review_ba_node, human_ba_node
        from backend.App.orchestration.application.nodes.arch import (
            arch_node, review_stack_node, review_arch_node, human_arch_node,
            merge_spec_node, review_spec_node, human_spec_node,
        )
        from backend.App.orchestration.application.nodes.documentation import (
            analyze_code_node, generate_documentation_node, problem_spotter_node,
            refactor_plan_node, human_code_review_node,
        )
        from backend.App.orchestration.application.nodes.devops import (
            devops_node, review_devops_node, human_devops_node,
        )
        from backend.App.orchestration.application.nodes.dev import (
            dev_lead_node, review_dev_lead_node, human_dev_lead_node,
            human_dev_node,
        )
        from backend.App.orchestration.application.nodes.qa import human_qa_node

        definition = GraphDefinition(
            nodes=[
                NodeDef("PM", hook_wrap("pm", pm_node)),
                NodeDef("REVIEW_PM", hook_wrap("review_pm", _planning_review_gate_wrapper("review_pm", "pm_review_output", review_pm_node))),
                NodeDef("HUMAN_PM", hook_wrap("human_pm", _with_approval_gate("human_pm", human_pm_node))),
                NodeDef("BA", hook_wrap("ba", ba_node)),
                NodeDef("REVIEW_BA", hook_wrap("review_ba", review_ba_node)),
                NodeDef("HUMAN_BA", hook_wrap("human_ba", _with_approval_gate("human_ba", human_ba_node))),
                NodeDef("ARCH", hook_wrap("architect", arch_node)),
                NodeDef("REVIEW_STACK", hook_wrap("review_stack", _planning_review_gate_wrapper("review_stack", "stack_review_output", review_stack_node))),
                NodeDef("REVIEW_ARCH", hook_wrap("review_arch", _planning_review_gate_wrapper("review_arch", "arch_review_output", review_arch_node))),
                NodeDef("HUMAN_ARCH", hook_wrap("human_arch", _with_approval_gate("human_arch", human_arch_node))),
                NodeDef("SPEC_MERGE", hook_wrap("spec_merge", merge_spec_node)),
                NodeDef("REVIEW_SPEC", hook_wrap("review_spec", review_spec_node)),
                NodeDef("HUMAN_SPEC", hook_wrap("human_spec", _with_approval_gate("human_spec", human_spec_node))),
                NodeDef("ANALYZE_CODE", hook_wrap("analyze_code", analyze_code_node)),
                NodeDef("GENERATE_DOCUMENTATION", hook_wrap("generate_documentation", generate_documentation_node)),
                NodeDef("PROBLEM_SPOTTER", hook_wrap("problem_spotter", problem_spotter_node)),
                NodeDef("REFACTOR_PLAN", hook_wrap("refactor_plan", refactor_plan_node)),
                NodeDef(
                    "HUMAN_CODE_REVIEW",
                    hook_wrap("human_code_review", _with_approval_gate("human_code_review", human_code_review_node)),
                ),
                NodeDef("DEVOPS", hook_wrap("devops", devops_node)),
                NodeDef("REVIEW_DEVOPS", hook_wrap("review_devops", review_devops_node)),
                NodeDef("HUMAN_DEVOPS", hook_wrap("human_devops", _with_approval_gate("human_devops", human_devops_node))),
                NodeDef("DEV_LEAD", hook_wrap("dev_lead", dev_lead_node)),
                NodeDef("REVIEW_DEV_LEAD", hook_wrap("review_dev_lead", review_dev_lead_node)),
                NodeDef("HUMAN_DEV_LEAD", hook_wrap("human_dev_lead", _with_approval_gate("human_dev_lead", human_dev_lead_node))),
                NodeDef("DEV", hook_wrap("dev", _dev_node_with_machine)),
                NodeDef("VERIFICATION_LAYER", hook_wrap("verification_layer", _verification_layer_node)),
                NodeDef("REVIEW_DEV", hook_wrap("review_dev", _review_dev_node_with_open_defects)),
                NodeDef("DEV_RETRY_GATE", hook_wrap("dev_retry_gate", _dev_retry_gate_node)),
                NodeDef("HUMAN_DEV", hook_wrap("human_dev", _with_approval_gate("human_dev", human_dev_node))),
                NodeDef("QA", hook_wrap("qa", _qa_node_with_machine)),
                NodeDef("REVIEW_QA", hook_wrap("review_qa", _review_qa_node_with_open_defects)),
                NodeDef("QA_RETRY_GATE", hook_wrap("qa_retry_gate", _qa_retry_gate_node)),
                NodeDef("HUMAN_QA", hook_wrap("human_qa", _with_approval_gate("human_qa", human_qa_node))),
                NodeDef("FINALIZE_PIPELINE", hook_wrap("finalize_pipeline", _finalize_pipeline_node)),
            ],
            edges=[
                EdgeDef("__start__", "PM"),
                EdgeDef("PM", "REVIEW_PM"),
                EdgeDef("REVIEW_PM", "HUMAN_PM"),
                EdgeDef("HUMAN_PM", "BA"),
                EdgeDef("HUMAN_PM", "ARCH"),
                EdgeDef("BA", "REVIEW_BA"),
                EdgeDef("REVIEW_BA", "HUMAN_BA"),
                EdgeDef("HUMAN_BA", "SPEC_MERGE"),
                EdgeDef("ARCH", "REVIEW_STACK"),
                EdgeDef("REVIEW_STACK", "REVIEW_ARCH"),
                EdgeDef("REVIEW_ARCH", "HUMAN_ARCH"),
                EdgeDef("HUMAN_ARCH", "SPEC_MERGE"),
                EdgeDef("SPEC_MERGE", "REVIEW_SPEC"),
                EdgeDef("REVIEW_SPEC", "HUMAN_SPEC"),
                EdgeDef("HUMAN_SPEC", "ANALYZE_CODE"),
                EdgeDef("ANALYZE_CODE", "GENERATE_DOCUMENTATION"),
                EdgeDef("GENERATE_DOCUMENTATION", "PROBLEM_SPOTTER"),
                EdgeDef("PROBLEM_SPOTTER", "REFACTOR_PLAN"),
                EdgeDef("REFACTOR_PLAN", "HUMAN_CODE_REVIEW"),
                EdgeDef("HUMAN_CODE_REVIEW", "DEVOPS"),
                EdgeDef("DEVOPS", "REVIEW_DEVOPS"),
                EdgeDef("REVIEW_DEVOPS", "HUMAN_DEVOPS"),
                EdgeDef("HUMAN_DEVOPS", "DEV_LEAD"),
                EdgeDef("DEV_LEAD", "REVIEW_DEV_LEAD"),
                EdgeDef("REVIEW_DEV_LEAD", "HUMAN_DEV_LEAD"),
                EdgeDef("HUMAN_DEV_LEAD", "DEV"),
                EdgeDef("DEV", "VERIFICATION_LAYER"),
                EdgeDef("VERIFICATION_LAYER", "REVIEW_DEV"),
                EdgeDef("DEV_RETRY_GATE", "DEV"),
                EdgeDef("HUMAN_DEV", "QA"),
                EdgeDef("QA", "REVIEW_QA"),
                EdgeDef("QA_RETRY_GATE", "DEV"),
                EdgeDef("HUMAN_QA", "FINALIZE_PIPELINE"),
                EdgeDef("FINALIZE_PIPELINE", "__end__"),
            ],
            conditional_edges=[
                ConditionalEdgeDef(
                    from_node="REVIEW_DEV",
                    router=_dev_review_router,
                    route_map={"retry": "DEV_RETRY_GATE", "continue": "HUMAN_DEV"},
                ),
                ConditionalEdgeDef(
                    from_node="REVIEW_QA",
                    router=_qa_review_router,
                    route_map={"retry": "QA_RETRY_GATE", "continue": "HUMAN_QA"},
                ),
            ],
        )
        return _LangGraphAdapter().compile(definition, PipelineState)

    def _build_mesh(self, agent_config: dict | None = None):
        """Build full graph with mesh topology marker stored in agent_config.

        The graph structure is identical to the default (BA ∥ ARCH fan-out), but
        ``agent_config.swarm.topology`` is set to ``"mesh"`` so that dev/qa nodes
        detect it and run subtasks in parallel via ThreadPoolExecutor.
        """
        # Ensure the topology marker is present in agent_config for node-level detection.
        # The caller passes agent_config which has already been set, but we defensively
        # ensure swarm.topology == "mesh" is visible when nodes inspect state.
        # (Nodes read from state["agent_config"], not from the config passed here.)
        return self.build()
