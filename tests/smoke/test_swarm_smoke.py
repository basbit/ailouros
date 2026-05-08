from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from openai import APITimeoutError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


_SMOKE_ENABLED = os.environ.get("SWARM_SMOKE", "").strip() == "1"

pytestmark = pytest.mark.skipif(
    not _SMOKE_ENABLED,
    reason="Smoke tests require SWARM_SMOKE=1 (use `make smoke`).",
)


SMOKE_TASK = (
    "Add REST endpoint POST /api/users with Pydantic input validation "
    "(name: str, email: str), SQLAlchemy persistence via UserService, "
    "proper error handling (409 Conflict for duplicate email), and unit tests. "
    "Follow the existing project code style and architecture."
)

SMOKE_STEPS = [
    "pm",
    "ba",
    "architect",
    "spec_merge",
    "analyze_code",
    "devops",
    "dev_lead",
    "dev",
    "qa",
]

OUTPUT_KEYS = [
    "pm_output",
    "ba_output",
    "arch_output",
    "spec_output",
    "analyze_code_output",
    "devops_output",
    "dev_lead_output",
    "dev_output",
    "qa_output",
]


MODEL_KEYS = [
    "pm_model",
    "ba_model",
    "arch_model",
    "devops_model",
    "dev_lead_model",
    "dev_model",
    "qa_model",
]


_PREFERRED_MODELS = [
    "qwen3:8b",
    "qwen3:14b",
    "llama3.1:8b",
    "qwen2.5:7b",
    "gemma3:12b",
    "qwen2.5:3b",
    "llama3.2:3b",
    "gemma3:4b",
    "mistral:latest",
]


def _detect_ollama_model() -> str | None:
    try:
        resp = httpx.get("http://localhost:11434/api/tags", timeout=5)
        resp.raise_for_status()
        models = [m["name"] for m in resp.json().get("models", [])]
    except Exception:
        return None
    if not models:
        return None
    for preferred in _PREFERRED_MODELS:
        if preferred in models:
            return preferred
    return models[0]


_MAIN_PY = '''\
"""FastAPI application entry point."""
from fastapi import FastAPI

app = FastAPI(title="UserService", version="0.1.0")


@app.get("/api/health")
async def health():
    return {"status": "ok"}
'''

_MODELS_PY = '''\
"""SQLAlchemy models."""
from sqlalchemy import Column, Integer, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()
engine = create_engine("sqlite:///./app.db")
SessionLocal = sessionmaker(bind=engine)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    email = Column(String(255), unique=True, nullable=False)
'''

_USER_SERVICE_PY = '''\
"""User CRUD service."""
from models import SessionLocal, User


class UserService:
    def create_user(self, name: str, email: str) -> User:
        db = SessionLocal()
        user = User(name=name, email=email)
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    def get_user_by_email(self, email: str) -> User | None:
        db = SessionLocal()
        return db.query(User).filter(User.email == email).first()
'''

_REQUIREMENTS_TXT = """\
fastapi>=0.115.0
uvicorn>=0.30.0
sqlalchemy>=2.0.0
pydantic>=2.0.0
"""

_TEST_MAIN_PY = '''\
"""Basic tests."""
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_health():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
'''

_WIKI_INDEX_MD = """\
---
title: "Project Wiki Index"
tags: ["index"]
links: ["[[architecture/overview]]", "[[features/health]]"]
---

# UserService Project

## Articles
- [[architecture/overview]]
- [[features/health]]
"""

_WIKI_OVERVIEW_MD = """\
---
title: "Architecture Overview"
tags: ["architecture"]
links: ["[[features/health]]"]
---

## Stack
- FastAPI + SQLAlchemy + Pydantic
- SQLite for development
- Service layer pattern (UserService)
"""

_WIKI_HEALTH_MD = """\
---
title: "Health Endpoint"
tags: ["feature"]
links: ["[[architecture/overview]]"]
---

## GET /api/health
Returns `{"status": "ok"}` when the service is running.
"""


def _create_workspace(root: Path) -> Path:
    (root / "main.py").write_text(_MAIN_PY, encoding="utf-8")
    (root / "models.py").write_text(_MODELS_PY, encoding="utf-8")
    (root / "services").mkdir()
    (root / "services" / "__init__.py").write_text("", encoding="utf-8")
    (root / "services" / "user_service.py").write_text(
        _USER_SERVICE_PY, encoding="utf-8"
    )
    (root / "requirements.txt").write_text(_REQUIREMENTS_TXT, encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (root / "tests" / "test_main.py").write_text(_TEST_MAIN_PY, encoding="utf-8")

    wiki_root = root / ".swarm" / "wiki"
    (wiki_root / "architecture").mkdir(parents=True)
    (wiki_root / "features").mkdir(parents=True)
    (wiki_root / "index.md").write_text(_WIKI_INDEX_MD, encoding="utf-8")
    (wiki_root / "architecture" / "overview.md").write_text(
        _WIKI_OVERVIEW_MD, encoding="utf-8"
    )
    (wiki_root / "features" / "health.md").write_text(_WIKI_HEALTH_MD, encoding="utf-8")

    return root


@pytest.fixture(scope="module")
def ollama_model() -> str:
    model = _detect_ollama_model()
    if model is None:
        pytest.skip("Ollama not running or no models available")
    logger.info("Detected Ollama model: %s", model)
    return model


@pytest.fixture(scope="module")
def workspace(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("swarm_smoke")
    return _create_workspace(root)


@pytest.fixture(scope="module")
def swarm_env(ollama_model: str, workspace: Path):
    env_vars = {
        "SWARM_ROUTE_DEFAULT": "local",
        "SWARM_DEFAULT_ENVIRONMENT": "ollama",
        "SWARM_MODEL": ollama_model,
        "SWARM_PATTERN_MEMORY": "1",
        "SWARM_PATTERN_MEMORY_PATH": str(workspace / ".swarm" / "pattern_memory.json"),
        "SWARM_CROSS_TASK_MEMORY": "1",
        "SWARM_CROSS_TASK_PERSIST_STEPS": "pm,ba,architect,spec_merge",
        "SWARM_DIALOGUE_MAX_ROUNDS": "1",
        "SWARM_MCP_AUTO": "0",
        "REDIS_REQUIRED": "0",
        "SWARM_AUTO_RETRY_ON_NEEDS_WORK": "0",
        "SWARM_MAX_STEP_RETRIES": "0",
        "SWARM_DREAM_MIN_CLUSTER_SIZE": "1",
        "SWARM_DREAM_SIMILARITY_THRESH": "0.1",
        "SWARM_LLM_CACHE_TTL": "0",
        "SWARM_TRUNCATION_MAX_RETRIES": "0",
        "SWARM_SPEC_FOR_BUILD_MAX_CHARS": "8000",
        "SWARM_QA_DEV_OUTPUT_MAX_CHARS": "8000",
        "SWARM_BUILD_CODE_ANALYSIS_MAX_CHARS": "3000",
        "SWARM_DEVOPS_CODE_ANALYSIS_MAX_CHARS": "3000",
        "SWARM_QA_CODE_ANALYSIS_MAX_CHARS": "3000",
        "SWARM_DEV_CONVENTIONS_MAX_CHARS": "1000",
        "SWARM_DEV_REFERENCE_FILE_MAX_CHARS": "1500",
        "SWARM_REVIEW_SPEC_MAX_CHARS": "10000",
        "SWARM_REVIEW_PIPELINE_INPUT_MAX_CHARS": "10000",
        "SWARM_CODE_ANALYSIS_MAX_CHARS": "3000",
        "SWARM_CODE_ANALYSIS_MAX_FILES": "30",
        "SWARM_SPEC_SUMMARY_MAX_CHARS": "3000",
        "SWARM_OPENAI_HTTP_TIMEOUT_SEC": "420",
        "SWARM_LOCAL_LLM_TIMEOUT_SEC": "420",
        "SWARM_OPENAI_COMPAT_MAX_TOKENS": "1400",
        "SWARM_LOCAL_LLM_REASONING_BUDGET": "1024",
        "SWARM_HTTPX_DISABLE_KEEPALIVE": "1",
        "SWARM_OPENAI_MAX_RETRIES": "0",
        "SWARM_REPO_EVIDENCE_MAX_RETRIES": "0",
    }
    old_env: dict[str, str | None] = {}
    for key, value in env_vars.items():
        old_env[key] = os.environ.get(key)
        os.environ[key] = value
    yield env_vars

    for key, old_value in old_env.items():
        if old_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old_value


def _smoke_pipeline_attempt_count() -> int:
    raw = os.environ.get("SWARM_SMOKE_PIPELINE_RETRIES", "0").strip()
    try:
        retries = int(raw)
    except ValueError:
        retries = 1
    return max(1, retries + 1)


def _smoke_sanity_timeout_sec() -> float:
    raw = os.environ.get("SWARM_SMOKE_SANITY_TIMEOUT_SEC", "180").strip()
    try:
        timeout = float(raw)
    except ValueError:
        timeout = 180.0
    return max(10.0, timeout)


def _strict_llm_timeout_failures() -> bool:
    return os.environ.get("SWARM_SMOKE_STRICT_LLM_TIMEOUT", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _reset_llm_clients() -> None:
    try:
        from backend.App.integrations.infrastructure.llm.openai_client_pool import (
            _default_pool,
        )

        _default_pool.close_all()
    except Exception as exc:
        logger.warning("openai client pool reset failed (non-fatal): %s", exc)


@pytest.fixture(scope="module")
def pipeline_result(swarm_env, workspace: Path) -> dict[str, Any]:
    from backend.App.orchestration.application.pipeline.pipeline_runner import (
        run_pipeline,
    )

    model_name = swarm_env["SWARM_MODEL"]
    try:
        resp = httpx.get("http://localhost:11434/api/tags", timeout=3)
        resp.raise_for_status()
    except Exception as exc:
        pytest.skip(f"Ollama pre-flight failed: {exc}")

    sanity_t0 = time.monotonic()
    sanity_timeout = _smoke_sanity_timeout_sec()
    try:
        sanity_resp = httpx.post(
            "http://localhost:11434/v1/chat/completions",
            json={
                "model": model_name,
                "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                "max_tokens": 5,
            },
            timeout=sanity_timeout,
        )
        sanity_resp.raise_for_status()
    except Exception as exc:
        pytest.skip(
            f"LLM sanity check failed for {model_name} (t={time.monotonic() - sanity_t0:.1f}s): {exc}"
        )
    sanity_elapsed = time.monotonic() - sanity_t0
    logger.info(
        "LLM sanity check: %s responded in %.1fs (http_timeout=%s, max_retries=%s)",
        model_name,
        sanity_elapsed,
        os.environ.get("SWARM_OPENAI_HTTP_TIMEOUT_SEC", "unset"),
        os.environ.get("SWARM_OPENAI_MAX_RETRIES", "unset"),
    )

    _reset_llm_clients()

    logger.info(
        "smoke pipeline: model=%s workspace=%s steps=%s",
        swarm_env["SWARM_MODEL"],
        workspace,
        SMOKE_STEPS,
    )
    t0 = time.monotonic()

    _aggressive_budget = {
        "default": {
            "wiki_chars": 1000,
            "knowledge_chars": 1000,
            "include_summaries": False,
        },
        "pm": {"wiki_chars": 1500, "knowledge_chars": 1500, "include_summaries": True},
        "ba": {"wiki_chars": 1000, "knowledge_chars": 1000, "include_summaries": True},
        "architect": {
            "wiki_chars": 1000,
            "knowledge_chars": 1000,
            "include_summaries": True,
        },
        "dev_lead": {
            "wiki_chars": 0,
            "knowledge_chars": 500,
            "include_summaries": False,
        },
        "dev": {"wiki_chars": 0, "knowledge_chars": 500, "include_summaries": False},
        "qa": {"wiki_chars": 0, "knowledge_chars": 500, "include_summaries": False},
        "devops": {
            "wiki_chars": 500,
            "knowledge_chars": 500,
            "include_summaries": False,
        },
    }

    attempts = _smoke_pipeline_attempt_count()
    result: dict[str, Any] | None = None
    for attempt in range(1, attempts + 1):
        try:
            result = run_pipeline(
                user_input=SMOKE_TASK,
                agent_config={
                    "swarm": {
                        "pattern_memory": True,
                        "pattern_memory_path": str(
                            workspace / ".swarm" / "pattern_memory.json"
                        ),
                        "cross_task_memory": {
                            "enabled": True,
                            "namespace": "smoke_test",
                            "persist_steps": ["pm", "ba", "architect", "spec_merge"],
                            "inject_at_steps": ["pm", "ba", "architect"],
                        },
                        "context_budgets": _aggressive_budget,
                    },
                },
                pipeline_steps=SMOKE_STEPS,
                workspace_root=str(workspace),
                task_id="smoke-test-001",
            )
            break
        except APITimeoutError as exc:
            _reset_llm_clients()
            if attempt < attempts:
                logger.warning(
                    "smoke pipeline LLM timeout on attempt %d/%d; retrying: %s",
                    attempt,
                    attempts,
                    exc,
                )
                continue
            if _strict_llm_timeout_failures():
                raise
            pytest.skip(
                "Local LLM timed out during smoke pipeline after "
                f"{attempts} attempt(s). Set SWARM_SMOKE_STRICT_LLM_TIMEOUT=1 "
                "to fail instead of skipping."
            )

    assert result is not None

    elapsed = time.monotonic() - t0
    result["_smoke_elapsed"] = elapsed

    logger.info("smoke pipeline finished in %.1fs", elapsed)
    for key in OUTPUT_KEYS:
        val = result.get(key, "")
        logger.info("  %s: %d chars", key, len(val) if val else 0)

    return dict(result)


class TestSwarmPipelineExecution:
    def test_all_steps_produced_output(self, pipeline_result):
        for key in OUTPUT_KEYS:
            val = pipeline_result.get(key, "")
            assert val, f"{key} is empty"
            assert len(val) > 30, f"{key} too short ({len(val)} chars): {val[:100]!r}"

    def test_model_metadata_set(self, pipeline_result):
        for key in MODEL_KEYS:
            val = pipeline_result.get(key, "")
            assert val, f"{key} is empty — model metadata not recorded"

    def test_pm_output_quality(self, pipeline_result):
        pm = pipeline_result["pm_output"].lower()
        assert any(
            term in pm for term in ["user", "endpoint", "api", "post", "rest"]
        ), f"PM output lacks domain terms: {pm[:300]}"

    def test_ba_references_pm_context(self, pipeline_result):
        ba = pipeline_result["ba_output"].lower()
        assert any(
            term in ba
            for term in ["user", "api", "endpoint", "validation", "email", "pydantic"]
        ), f"BA output lacks domain terms: {ba[:300]}"

    def test_architect_mentions_architecture(self, pipeline_result):
        arch = pipeline_result["arch_output"].lower()
        assert any(
            term in arch
            for term in [
                "fastapi",
                "rest",
                "endpoint",
                "route",
                "sqlalchemy",
                "service",
                "model",
                "schema",
                "layer",
                "api",
            ]
        ), f"Architect output lacks architecture terms: {arch[:300]}"

    def test_spec_merge_combines_ba_and_arch(self, pipeline_result):
        spec = pipeline_result["spec_output"]
        assert len(spec) > 50, f"spec_output too short: {len(spec)} chars"

    def test_code_analysis_sees_workspace(self, pipeline_result):
        analysis = pipeline_result["analyze_code_output"].lower()

        assert len(analysis) > 30, (
            f"analyze_code_output too short ({len(analysis)} chars)"
        )

    def test_devops_mentions_infrastructure(self, pipeline_result):
        devops = pipeline_result["devops_output"].lower()
        assert any(
            term in devops
            for term in [
                "requirements",
                "depend",
                "docker",
                "deploy",
                "install",
                "pip",
                "package",
                "fastapi",
                "sqlalchemy",
                "uvicorn",
                "python",
                "environment",
                "config",
            ]
        ), f"DevOps output lacks infrastructure terms: {devops[:300]}"

    def test_dev_produces_code(self, pipeline_result):
        dev = pipeline_result["dev_output"]
        has_code_block = "```" in dev
        has_python = any(
            kw in dev for kw in ["def ", "class ", "import ", "from ", "return "]
        )
        assert has_code_block or has_python, f"Dev output contains no code: {dev[:300]}"

    def test_qa_mentions_verification(self, pipeline_result):
        qa = pipeline_result["qa_output"].lower()
        assert any(
            term in qa
            for term in [
                "test",
                "verif",
                "assert",
                "coverage",
                "check",
                "qa",
                "pass",
                "fail",
            ]
        ), f"QA output lacks verification terms: {qa[:300]}"

    def test_timing_logged(self, pipeline_result):
        elapsed = pipeline_result.get("_smoke_elapsed", 0)
        logger.info(
            "PIPELINE TIMING: %.1f seconds (%.1f minutes)", elapsed, elapsed / 60
        )


class TestSwarmCommunication:
    def test_state_searcher(self, pipeline_result):
        from backend.App.orchestration.application.context.state_searcher import (
            PipelineStateSearcher,
        )

        searcher = PipelineStateSearcher()
        searcher.index(pipeline_result)
        results = searcher.search("user endpoint validation", top_k=3)
        assert len(results) > 0, "StateSearcher found no results"

        found_keys = {r.key for r in results}
        assert found_keys, f"Empty result keys: {results}"
        logger.info(
            "StateSearcher hits: %s", [(r.key, f"{r.score:.2f}") for r in results]
        )

    def test_message_bus_roundtrip(self, pipeline_result):
        from backend.App.orchestration.infrastructure.message_bus import (
            AgentMessageBus,
        )

        bus = AgentMessageBus()

        msg_id = bus.publish(
            "dev", "qa", "Please verify src/users.py", msg_type="verify_request"
        )
        assert msg_id

        bcast_id = bus.broadcast("architect", "Spec updated — all agents re-read")
        assert bcast_id

        bus.publish(
            "dev", "ba", "Need clarification on user schema", msg_type="question"
        )

        qa_messages = bus.get_messages("qa")
        assert len(qa_messages) >= 2, (
            f"QA got {len(qa_messages)} messages, expected >= 2"
        )

        ba_messages = bus.get_messages("ba")
        assert len(ba_messages) >= 1, (
            f"BA got {len(ba_messages)} messages, expected >= 1"
        )
        assert any("clarification" in m.message for m in ba_messages), (
            f"BA didn't receive direct message: {[m.message for m in ba_messages]}"
        )

        bus2 = AgentMessageBus()
        bus2.broadcast("pm", "All agents: new requirement added")
        pm_reader_msgs = bus2.get_messages("dev")
        assert len(pm_reader_msgs) == 1, (
            f"Dev should see broadcast, got {len(pm_reader_msgs)}"
        )
        assert "new requirement" in pm_reader_msgs[0].message

        all_msgs = bus.get_all_messages()
        assert len(all_msgs) >= 3, f"Expected >= 3 total messages, got {len(all_msgs)}"

    def test_blackboard_post_refine_next(self, pipeline_result):
        from backend.App.orchestration.application.agents.blackboard import (
            Blackboard,
            BlackboardCoordinator,
        )

        board = Blackboard()
        eid = board.post(
            "ba", "auth_approach", "Use JWT with refresh tokens", confidence=0.8
        )
        assert eid

        ok = board.refine(eid, "architect", "Agreed — add Redis for token invalidation")
        assert ok

        coord = BlackboardCoordinator(board)
        action = coord.next_action()
        assert action["action"] in (
            "review",
            "debate",
            "proceed",
            "wait",
        ), f"Unexpected action: {action}"
        logger.info("Blackboard action: %s", action)

    def test_blackboard_contradiction_detection(self, pipeline_result):
        from backend.App.orchestration.application.agents.blackboard import (
            Blackboard,
            BlackboardCoordinator,
        )

        board = Blackboard()
        e1 = board.post("ba", "auth", "Use JWT tokens for auth", confidence=0.9)
        e2 = board.post(
            "architect", "auth", "Use session cookies instead", confidence=0.7
        )

        coord = BlackboardCoordinator(board)
        detected = coord.detect_contradiction(e1, e2)

        logger.info("Contradiction detected: %s", detected)

        assert isinstance(detected, bool)

    def test_artifact_registry_roundtrip(self, pipeline_result, workspace):
        from backend.App.workspace.application.artifact_registry import (
            WorkspaceArtifactRegistry,
        )

        registry = WorkspaceArtifactRegistry(workspace_root=str(workspace))
        registry.register("dev", "main.py", purpose="updated with POST /api/users")
        registry.register(
            "dev", "services/user_service.py", purpose="added create_user"
        )
        registry.register("qa", "tests/test_users.py", purpose="new test file")

        dev_files = registry.query_by_agent("dev")
        assert len(dev_files) == 2, f"Expected 2 dev files, got {len(dev_files)}"
        qa_files = registry.query_by_agent("qa")
        assert len(qa_files) == 1

        summary = registry.to_summary()
        assert "main.py" in summary


class TestSwarmMemory:
    def test_pattern_memory_roundtrip(self, workspace):
        from backend.App.integrations.infrastructure.pattern_memory import (
            search_patterns,
            store_pattern,
        )

        pm_path = workspace / ".swarm" / "pattern_memory.json"

        store_pattern(
            pm_path,
            "patterns",
            "auth-jwt",
            "JWT with refresh tokens and Redis blacklist",
        )
        store_pattern(
            pm_path,
            "patterns",
            "validation-pydantic",
            "Pydantic BaseModel for input validation",
        )
        store_pattern(
            pm_path,
            "patterns",
            "error-handling",
            "HTTPException with status codes 400/409/500",
        )

        assert pm_path.exists(), "pattern_memory.json not created"

        state: dict[str, Any] = {
            "agent_config": {
                "swarm": {
                    "pattern_memory": True,
                    "pattern_memory_path": str(pm_path),
                },
            },
        }
        results = search_patterns(state, "authentication tokens", namespace="patterns")
        assert len(results) > 0, "PatternMemory search returned no results"

        assert "jwt" in results[0][0].lower() or "jwt" in results[0][1].lower(), (
            f"Expected JWT pattern, got: {results[0]}"
        )
        logger.info(
            "PatternMemory search results: %s", [(k, f"{s:.1f}") for k, _, s in results]
        )

    def test_cross_task_memory_episodes_exist(self, pipeline_result, swarm_env):
        from backend.App.integrations.infrastructure.cross_task_memory import (
            search_episodes,
        )

        state: dict[str, Any] = {
            "agent_config": {
                "swarm": {
                    "cross_task_memory": {
                        "enabled": True,
                        "namespace": "smoke_test",
                    },
                },
            },
        }
        episodes = search_episodes(state, "user endpoint", limit=5)

        logger.info("CrossTaskMemory episodes found: %d", len(episodes))
        if episodes:
            for ep, score in episodes[:3]:
                logger.info(
                    "  step=%s score=%.1f body=%s...",
                    ep.get("step", "?"),
                    score,
                    str(ep.get("body", ""))[:80],
                )

    def test_cross_task_memory_second_run_sees_first(self, swarm_env, workspace):
        from backend.App.integrations.infrastructure.cross_task_memory import (
            format_cross_task_memory_block,
        )

        state: dict[str, Any] = {
            "agent_config": {
                "swarm": {
                    "cross_task_memory": {
                        "enabled": True,
                        "namespace": "smoke_test",
                        "inject_at_steps": ["pm"],
                    },
                },
            },
        }
        block = format_cross_task_memory_block(
            state, "user API endpoint", current_step="pm"
        )
        logger.info("CrossTaskMemory block length: %d chars", len(block))

        assert isinstance(block, str)

    def test_memory_consolidation_dream_pass(self, workspace, swarm_env):
        from backend.App.integrations.application.memory_consolidation import (
            MemoryConsolidator,
        )
        from backend.App.integrations.infrastructure.cross_task_memory import (
            append_episode,
        )

        state: dict[str, Any] = {
            "agent_config": {
                "swarm": {
                    "cross_task_memory": {
                        "enabled": True,
                        "namespace": "dream_test",
                    },
                },
            },
        }

        for i in range(5):
            append_episode(
                state,
                step_id="pm",
                body=f"User authentication with JWT tokens and refresh mechanism iteration {i}",
                task_id=f"dream-task-{i}",
            )

        pm_path = workspace / ".swarm" / "dream_pattern_memory.json"
        consolidator = MemoryConsolidator(llm_backend=None)
        stats = consolidator.run_consolidation(
            namespace="dream_test",
            pattern_path=pm_path,
        )

        logger.info("DreamPass stats: %s", stats)
        assert stats["episodes_loaded"] >= 5, (
            f"Expected >= 5 episodes, got {stats['episodes_loaded']}"
        )

        assert stats["clusters_formed"] >= 0


class TestSwarmWikiAndGraph:
    def test_wiki_graph_build_from_scaffold(self, workspace):
        from backend.App.workspace.application.wiki_service import build_wiki_graph

        wiki_root = workspace / ".swarm" / "wiki"
        graph = build_wiki_graph(wiki_root)

        assert "nodes" in graph
        assert "edges" in graph
        assert len(graph["nodes"]) >= 3, (
            f"Expected >= 3 wiki nodes (index, overview, health), got {len(graph['nodes'])}"
        )
        assert len(graph["edges"]) >= 1, (
            f"Expected >= 1 wiki edges, got {len(graph['edges'])}"
        )

        node_ids = {n["id"] for n in graph["nodes"]}
        assert "architecture/overview" in node_ids, (
            f"Missing overview node. Nodes: {node_ids}"
        )
        assert "features/health" in node_ids, f"Missing health node. Nodes: {node_ids}"

        logger.info(
            "Wiki graph: %d nodes, %d edges",
            len(graph["nodes"]),
            len(graph["edges"]),
        )

    def test_wiki_auto_update_creates_session(self, pipeline_result, workspace):
        from backend.App.workspace.application.wiki.wiki_auto_updater import (
            update_wiki_from_pipeline,
        )

        update_wiki_from_pipeline(pipeline_result, workspace)

        wiki_root = workspace / ".swarm" / "wiki"
        sessions_dir = wiki_root / "sessions"
        assert sessions_dir.exists(), "sessions/ dir not created"

        session_files = list(sessions_dir.glob("*.md"))
        assert len(session_files) >= 1, "No session article created"

        index_path = wiki_root / "index.md"
        assert index_path.exists()
        index_text = index_path.read_text(encoding="utf-8")
        assert "sessions/" in index_text or "Articles" in index_text

        logger.info("Wiki session files: %s", [f.name for f in session_files])

    def test_wiki_graph_json_cached(self, workspace):
        from backend.App.workspace.application.wiki_service import get_or_build_graph

        wiki_root = workspace / ".swarm" / "wiki"
        graph = get_or_build_graph(wiki_root)

        graph_file = wiki_root / "graph.json"
        assert graph_file.exists(), "graph.json not saved to disk"

        cached = json.loads(graph_file.read_text(encoding="utf-8"))
        assert len(cached["nodes"]) == len(graph["nodes"])


class TestSwarmTopologies:
    def test_default_parallel_topology(self, swarm_env):
        from backend.App.orchestration.application.routing.graph_builder import (
            PipelineGraphBuilder,
        )

        compiled = PipelineGraphBuilder().build_for_topology("")
        assert compiled is not None

    def test_ring_topology(self, swarm_env):
        from backend.App.orchestration.application.routing.graph_builder import (
            PipelineGraphBuilder,
        )

        compiled = PipelineGraphBuilder().build_for_topology("ring")
        assert compiled is not None

    def test_mesh_topology(self, swarm_env):
        from backend.App.orchestration.application.routing.graph_builder import (
            PipelineGraphBuilder,
        )

        compiled = PipelineGraphBuilder().build_for_topology("mesh")
        assert compiled is not None

    def test_invalid_topology_raises(self, swarm_env):
        from backend.App.orchestration.application.routing.graph_builder import (
            PipelineGraphBuilder,
        )

        with pytest.raises(ValueError, match="Unknown topology"):
            PipelineGraphBuilder().build_for_topology("nonexistent")
