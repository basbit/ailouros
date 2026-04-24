from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_BRIDGE_MAX_OUTPUT_CHARS = int(os.getenv("SWARM_AGENT_BRIDGE_MAX_OUTPUT_CHARS", "8000"))


class AgentToolBridge:

    def __init__(self, state: dict[str, Any]) -> None:
        self._state = state
        self._progress_queue = state.get("_stream_progress_queue")

    def call_agent(
        self,
        role: str,
        user_input: str,
        *,
        max_output_chars: int = _BRIDGE_MAX_OUTPUT_CHARS,
    ) -> str:
        import queue as _q
        import json as _json

        if isinstance(self._progress_queue, _q.Queue):
            try:
                self._progress_queue.put(_json.dumps({
                    "_event_type": "sub_agent_call",
                    "caller": "bridge",
                    "callee": role,
                    "message": f"[bridge] spawning sub-agent: {role}",
                }))
            except Exception:
                pass

        logger.info("AgentToolBridge: spawning sub-agent role=%r input_chars=%d", role, len(user_input))
        try:
            result = self._run_agent(role, user_input)
        except Exception as exc:
            logger.warning("AgentToolBridge: sub-agent %r failed: %s", role, exc)
            result = f"Sub-agent '{role}' raised an error: {exc}"

        if len(result) > max_output_chars:
            result = result[:max_output_chars] + f"\n…[sub-agent output truncated at {max_output_chars} chars]"

        if isinstance(self._progress_queue, _q.Queue):
            try:
                self._progress_queue.put(_json.dumps({
                    "_event_type": "sub_agent_done",
                    "caller": "bridge",
                    "callee": role,
                    "output_chars": len(result),
                    "message": f"[bridge] sub-agent {role} done ({len(result)} chars)",
                }))
            except Exception:
                pass

        return result

    def _run_agent(self, role: str, user_input: str) -> str:
        state = self._state
        agent_config: dict[str, Any] = state.get("agent_config") or {}
        role_cfg: dict[str, Any] = agent_config.get(role) or {}

        from backend.App.orchestration.application.nodes._shared import (
            _cfg_model,
            _remote_api_client_kwargs_for_role,
            _skills_extra_for_role_cfg,
        )

        agent: Any
        try:
            if role == "dev":
                from backend.App.orchestration.infrastructure.agents.dev_agent import DevAgent
                agent = DevAgent(
                    model_override=_cfg_model(role_cfg),
                    environment_override=role_cfg.get("environment"),
                    system_prompt_extra=_skills_extra_for_role_cfg(state, role_cfg),
                    **_remote_api_client_kwargs_for_role(state, role_cfg),
                )
            elif role in ("architect", "arch"):
                from backend.App.orchestration.infrastructure.agents.arch_agent import ArchitectAgent
                agent = ArchitectAgent(
                    model_override=_cfg_model(role_cfg),
                    environment_override=role_cfg.get("environment"),
                    system_prompt_extra=_skills_extra_for_role_cfg(state, role_cfg),
                    **_remote_api_client_kwargs_for_role(state, role_cfg),
                )
            elif role == "ba":
                from backend.App.orchestration.infrastructure.agents.ba_agent import BAAgent
                agent = BAAgent(
                    model_override=_cfg_model(role_cfg),
                    environment_override=role_cfg.get("environment"),
                    system_prompt_extra=_skills_extra_for_role_cfg(state, role_cfg),
                    **_remote_api_client_kwargs_for_role(state, role_cfg),
                )
            elif role == "qa":
                from backend.App.orchestration.infrastructure.agents.qa_agent import QAAgent
                agent = QAAgent(
                    model_override=_cfg_model(role_cfg),
                    environment_override=role_cfg.get("environment"),
                    system_prompt_extra=_skills_extra_for_role_cfg(state, role_cfg),
                    **_remote_api_client_kwargs_for_role(state, role_cfg),
                )
            else:
                from backend.App.orchestration.infrastructure.agents.base_agent import BaseAgent
                model = str(role_cfg.get("model") or os.getenv("SWARM_MODEL", "claude-haiku-4-5-20251001"))
                agent = BaseAgent(
                    role=role,
                    system_prompt=f"You are a {role} specialist agent.",
                    model=model,
                    environment=role_cfg.get("environment", ""),
                )
        except Exception as exc:
            logger.warning(
                "AgentToolBridge: failed to create agent for role=%r: %s — using generic BaseAgent",
                role, exc,
            )
            from backend.App.orchestration.infrastructure.agents.base_agent import BaseAgent
            model = str(role_cfg.get("model") or os.getenv("SWARM_MODEL", "claude-haiku-4-5-20251001"))
            agent = BaseAgent(
                role=role,
                system_prompt=f"You are a {role} specialist agent.",
                model=model,
                environment=role_cfg.get("environment", ""),
            )

        return agent.run(user_input, _progress_queue=self._progress_queue)

    def ask_architect(self, question: str) -> str:
        state = self._state
        ctx_parts: list[str] = []
        for key in ("arch_output", "ba_output", "pm_output", "spec_merged_output"):
            val = (state.get(key) or "").strip()
            if val:
                ctx_parts.append(f"## {key}\n{val[:1500]}")
        ctx_block = ("\n\n".join(ctx_parts) + "\n\n") if ctx_parts else ""
        return self.call_agent(
            "architect",
            f"{ctx_block}## Clarifying question from a peer agent\n{question}",
        )

    def ask_ba(self, question: str) -> str:
        state = self._state
        ctx_parts: list[str] = []
        for key in ("ba_output", "pm_output"):
            val = (state.get(key) or "").strip()
            if val:
                ctx_parts.append(f"## {key}\n{val[:1500]}")
        ctx_block = ("\n\n".join(ctx_parts) + "\n\n") if ctx_parts else ""
        return self.call_agent(
            "ba",
            f"{ctx_block}## Clarifying question about requirements\n{question}",
        )

    def execute_dev_subtask(self, spec: str) -> str:
        return self.call_agent("dev", spec)

    def request_qa_verification(self, dev_output: str) -> str:
        return self.call_agent(
            "qa",
            f"Please verify the following development output and report any issues:\n\n{dev_output}",
        )
