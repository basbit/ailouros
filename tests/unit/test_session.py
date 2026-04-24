"""Tests for R1.1 — Durable agent sessions."""
from __future__ import annotations

from backend.App.orchestration.domain.session import AgentSession, SessionCheckpoint, SessionStatus
from backend.App.orchestration.infrastructure.in_memory_session_store import InMemorySessionStore
from backend.App.orchestration.application.sessions.session_manager import SessionManager


class TestSessionDomain:
    def test_status_enum_values(self):
        assert SessionStatus.RUNNING == "running"
        assert SessionStatus.COMPLETED == "completed"


class TestInMemorySessionStore:
    def test_save_and_get_session(self):
        store = InMemorySessionStore()
        s = AgentSession("s1", "t1", SessionStatus.PENDING, "2026-01-01", "2026-01-01")
        store.save_session(s)
        assert store.get_session("s1") is s

    def test_get_missing_returns_none(self):
        assert InMemorySessionStore().get_session("x") is None

    def test_save_and_get_checkpoint(self):
        store = InMemorySessionStore()
        cp = SessionCheckpoint("cp1", "s1", "pm", {"step": "pm"}, "2026-01-01")
        store.save_checkpoint(cp)
        assert store.get_latest_checkpoint("s1") is cp

    def test_latest_checkpoint_overwritten(self):
        store = InMemorySessionStore()
        store.save_checkpoint(SessionCheckpoint("cp1", "s1", "pm", {}, "2026-01-01"))
        store.save_checkpoint(SessionCheckpoint("cp2", "s1", "arch", {}, "2026-01-02"))
        assert store.get_latest_checkpoint("s1").checkpoint_id == "cp2"

    def test_list_sessions_filters_by_task(self):
        store = InMemorySessionStore()
        store.save_session(AgentSession("s1", "t1", SessionStatus.RUNNING, "2026-01-01", "2026-01-01"))
        store.save_session(AgentSession("s2", "t2", SessionStatus.RUNNING, "2026-01-01", "2026-01-01"))
        assert len(store.list_sessions("t1")) == 1
        assert store.list_sessions("t1")[0].session_id == "s1"


class TestSessionManager:
    def test_create_session(self):
        mgr = SessionManager(InMemorySessionStore())
        s = mgr.create_session("task-42")
        assert s.status == SessionStatus.PENDING
        assert s.task_id == "task-42"
        assert s.session_id  # non-empty uuid

    def test_checkpoint_updates_session(self):
        store = InMemorySessionStore()
        mgr = SessionManager(store)
        s = mgr.create_session("task-42")
        cp = mgr.checkpoint(s.session_id, "pm", {"pm_output": "done"})
        assert cp.step_name == "pm"
        updated = store.get_session(s.session_id)
        assert updated.last_checkpoint_id == cp.checkpoint_id
        assert updated.status == SessionStatus.RUNNING

    def test_resume_returns_session_and_checkpoint(self):
        mgr = SessionManager(InMemorySessionStore())
        s = mgr.create_session("t1")
        mgr.checkpoint(s.session_id, "pm", {})
        result = mgr.resume_session(s.session_id)
        assert result is not None
        resumed, cp = result
        assert resumed.status == SessionStatus.RESUMING
        assert cp is not None

    def test_complete_session(self):
        store = InMemorySessionStore()
        mgr = SessionManager(store)
        s = mgr.create_session("t1")
        mgr.complete_session(s.session_id)
        assert store.get_session(s.session_id).status == SessionStatus.COMPLETED

    def test_fail_session_with_reason(self):
        store = InMemorySessionStore()
        mgr = SessionManager(store)
        s = mgr.create_session("t1")
        mgr.fail_session(s.session_id, "timeout")
        updated = store.get_session(s.session_id)
        assert updated.status == SessionStatus.FAILED
        assert updated.metadata["failure_reason"] == "timeout"
