from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import fastapi

logger = logging.getLogger(__name__)


class AppRuntimeContainer:
    def __init__(self) -> None:
        self._teardown_fns: list[Any] = []

    def _make_task_store(self) -> Any:
        from backend.App.shared.infrastructure.bootstrap.task_store_factory import get_task_store

        return get_task_store()

    def _make_cancel_fn(self) -> Any:
        from backend.App.orchestration.infrastructure.stream_cancel import cancel_task_by_id
        return cancel_task_by_id

    def _make_agent_factory(self) -> Any:
        from backend.App.orchestration.infrastructure.agent_factory import ConcreteAgentFactory
        return ConcreteAgentFactory()

    def _make_mcp_manager(self, workspace_root: str) -> Any | None:
        if not workspace_root:
            return None
        from backend.App.integrations.infrastructure.mcp.manager import MCPManager
        mcp_manager = MCPManager(workspace_root)
        mcp_manager.start_all()
        self._teardown_fns.append(mcp_manager.stop_all)
        return mcp_manager

    def _make_background_agent(self, workspace_root: str) -> Any | None:
        from backend.App.orchestration.application.agents.background_agent import (
            BackgroundAgent,
            _agent_enabled,
        )
        if not _agent_enabled():
            return None
        agent = BackgroundAgent(
            watch_paths=[workspace_root] if workspace_root else [],
            enabled=True,
        )
        agent.start()
        self._teardown_fns.append(agent.stop)
        return agent

    def wire(self, app: fastapi.FastAPI) -> None:
        from backend.App.integrations.infrastructure.observability.logging_config import configure_logging
        from backend.App.orchestration.infrastructure.stream_cancel import clear_stream_shutdown
        from backend.App.shared.infrastructure.bootstrap.task_store_factory import get_artifacts_root
        from backend.App.shared.infrastructure.rest.utils import _cleanup_old_artifacts, _warn_malformed_urls

        configure_logging()

        app.state.task_store = self._make_task_store()
        app.state.cancel_fn = self._make_cancel_fn()
        app.state.agent_factory = self._make_agent_factory()

        from backend.App.orchestration.infrastructure._singletons import (
            get_session_store,
            get_trace_collector,
            get_session_manager,
        )
        app.state.session_store = get_session_store()
        app.state.trace_collector = get_trace_collector()
        app.state.session_manager = get_session_manager()
        task_store = app.state.task_store
        task_store._session_store = app.state.session_store
        task_store._trace_collector = app.state.trace_collector

        _warn_malformed_urls()
        clear_stream_shutdown()
        asyncio.create_task(_cleanup_old_artifacts(get_artifacts_root()))

        workspace_root = os.getenv("SWARM_WORKSPACE_ROOT", "")

        mcp_manager = self._make_mcp_manager(workspace_root)
        if mcp_manager is not None:
            app.state.mcp_manager = mcp_manager

        bg_agent = self._make_background_agent(workspace_root)
        if bg_agent is not None:
            app.state.background_agent = bg_agent

    async def teardown(self, app: fastapi.FastAPI) -> None:
        from backend.App.orchestration.infrastructure.stream_cancel import mark_stream_shutdown_start
        from backend.App.shared.infrastructure.rest.sse_bridge import _active_tasks

        mark_stream_shutdown_start()

        if _active_tasks:
            logger.info("Shutdown: waiting for %d active tasks...", len(_active_tasks))
            done, pending = await asyncio.wait(_active_tasks, timeout=30.0)
            for t in pending:
                t.cancel()

        for fn in reversed(self._teardown_fns):
            try:
                fn()
            except Exception as exc:
                logger.warning("teardown error: %s", exc)
