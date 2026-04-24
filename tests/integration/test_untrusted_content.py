"""Tests for untrusted_content — wrap_untrusted, is_external_tool, QuarantineAgent."""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Module under test
# ---------------------------------------------------------------------------
from backend.App.orchestration.application.enforcement.untrusted_content import (
    _CLOSE_MARKER,
    _OPEN_MARKER,
    QuarantineAgent,
    is_external_tool,
    is_wrapped,
    wrap_untrusted,
)


# ---------------------------------------------------------------------------
# wrap_untrusted
# ---------------------------------------------------------------------------

class TestWrapUntrusted(unittest.TestCase):

    def test_adds_open_close_markers(self):
        result = wrap_untrusted("hello world", source="test")
        self.assertIn(_OPEN_MARKER, result)
        self.assertIn(_CLOSE_MARKER, result)
        self.assertIn("hello world", result)

    def test_includes_source(self):
        result = wrap_untrusted("data", source="web_search")
        self.assertIn("Source: web_search", result)

    def test_idempotent_no_double_wrap(self):
        wrapped_once = wrap_untrusted("data", source="x")
        wrapped_twice = wrap_untrusted(wrapped_once, source="y")
        self.assertEqual(wrapped_once, wrapped_twice)

    def test_empty_string_returned_as_is(self):
        self.assertEqual("", wrap_untrusted(""))

    def test_whitespace_only_returned_as_is(self):
        self.assertEqual("   ", wrap_untrusted("   "))

    def test_default_source(self):
        result = wrap_untrusted("content")
        self.assertIn("Source: External", result)

    def test_marker_order(self):
        result = wrap_untrusted("body")
        open_pos = result.index(_OPEN_MARKER)
        close_pos = result.index(_CLOSE_MARKER)
        self.assertLess(open_pos, close_pos)


# ---------------------------------------------------------------------------
# is_wrapped
# ---------------------------------------------------------------------------

class TestIsWrapped(unittest.TestCase):

    def test_detects_wrapped(self):
        wrapped = wrap_untrusted("data")
        self.assertTrue(is_wrapped(wrapped))

    def test_detects_not_wrapped(self):
        self.assertFalse(is_wrapped("plain text"))

    def test_empty_string(self):
        self.assertFalse(is_wrapped(""))


# ---------------------------------------------------------------------------
# is_external_tool
# ---------------------------------------------------------------------------

class TestIsExternalTool(unittest.TestCase):

    def test_exact_names(self):
        for name in ("fetch_page", "web_search", "web_fetch", "fetch_url", "search_web"):
            with self.subTest(name=name):
                self.assertTrue(is_external_tool(name))

    def test_prefix_match(self):
        self.assertTrue(is_external_tool("fetch_github_file"))
        self.assertTrue(is_external_tool("web_search_advanced"))
        self.assertTrue(is_external_tool("browser_get_title"))
        self.assertTrue(is_external_tool("search_knowledge_base"))

    def test_internal_tools_not_matched(self):
        for name in ("workspace__write_file", "workspace__edit_file", "read_file", "list_dir"):
            with self.subTest(name=name):
                self.assertFalse(is_external_tool(name))

    def test_case_insensitive(self):
        self.assertTrue(is_external_tool("FETCH_PAGE"))
        self.assertTrue(is_external_tool("Web_Search"))

    def test_empty_string(self):
        self.assertFalse(is_external_tool(""))

    def test_none_safe(self):
        self.assertFalse(is_external_tool(None))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# QuarantineAgent.is_enabled
# ---------------------------------------------------------------------------

class TestQuarantineAgentIsEnabled(unittest.TestCase):

    def test_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SWARM_QUARANTINE_ENABLED", None)
            self.assertFalse(QuarantineAgent.is_enabled())

    def test_enabled_via_1(self):
        with patch.dict(os.environ, {"SWARM_QUARANTINE_ENABLED": "1"}):
            self.assertTrue(QuarantineAgent.is_enabled())

    def test_enabled_via_true(self):
        with patch.dict(os.environ, {"SWARM_QUARANTINE_ENABLED": "true"}):
            self.assertTrue(QuarantineAgent.is_enabled())

    def test_enabled_via_yes(self):
        with patch.dict(os.environ, {"SWARM_QUARANTINE_ENABLED": "yes"}):
            self.assertTrue(QuarantineAgent.is_enabled())

    def test_not_enabled_via_0(self):
        with patch.dict(os.environ, {"SWARM_QUARANTINE_ENABLED": "0"}):
            self.assertFalse(QuarantineAgent.is_enabled())


# ---------------------------------------------------------------------------
# QuarantineAgent.summarize — disabled path
# ---------------------------------------------------------------------------

class TestQuarantineAgentSummarizeDisabled(unittest.TestCase):

    def setUp(self):
        os.environ.pop("SWARM_QUARANTINE_ENABLED", None)

    def test_returns_original_when_disabled(self):
        agent = QuarantineAgent()
        result = agent.summarize("some content", source="web")
        self.assertEqual("some content", result)

    def test_empty_content_returned_as_is(self):
        agent = QuarantineAgent()
        result = agent.summarize("")
        self.assertEqual("", result)


# ---------------------------------------------------------------------------
# QuarantineAgent.summarize — enabled path (mocked BaseAgent)
# ---------------------------------------------------------------------------

class TestQuarantineAgentSummarizeEnabled(unittest.TestCase):

    def setUp(self):
        os.environ["SWARM_QUARANTINE_ENABLED"] = "1"

    def tearDown(self):
        os.environ.pop("SWARM_QUARANTINE_ENABLED", None)

    def _make_mock_agent(self, return_value: str):
        mock_agent = MagicMock()
        mock_agent.run.return_value = return_value
        return mock_agent

    def test_returns_summary_from_agent(self):
        mock_agent = self._make_mock_agent("safe summary")
        with patch(
            "backend.App.orchestration.application.enforcement.untrusted_content.BaseAgent",
            return_value=mock_agent,
        ):
            agent = QuarantineAgent(model="test-model")
            result = agent.summarize("external data with <<inject>>", source="web_search")
        self.assertEqual("safe summary", result)

    def test_falls_back_on_agent_exception(self):
        with patch(
            "backend.App.orchestration.application.enforcement.untrusted_content.BaseAgent",
            side_effect=RuntimeError("model offline"),
        ):
            agent = QuarantineAgent(model="test-model")
            result = agent.summarize("original content", source="web")
        # Must return original, not raise
        self.assertEqual("original content", result)

    def test_falls_back_on_empty_summary(self):
        mock_agent = self._make_mock_agent("")  # model returns empty
        with patch(
            "backend.App.orchestration.application.enforcement.untrusted_content.BaseAgent",
            return_value=mock_agent,
        ):
            agent = QuarantineAgent(model="test-model")
            result = agent.summarize("original content", source="web")
        self.assertEqual("original content", result)

    def test_input_capped_at_max(self):
        long_content = "x" * 20_000
        captured_prompt: list[str] = []

        def capture_run(prompt: str, **_kw):
            captured_prompt.append(prompt)
            return "summary"

        mock_agent = MagicMock()
        mock_agent.run.side_effect = capture_run
        with patch(
            "backend.App.orchestration.application.enforcement.untrusted_content.BaseAgent",
            return_value=mock_agent,
        ), patch.dict(os.environ, {"SWARM_QUARANTINE_MAX_INPUT_CHARS": "100"}):
            agent = QuarantineAgent(model="test-model")
            agent.summarize(long_content, source="web")

        self.assertIn("quarantine: input capped", captured_prompt[0])

    def test_output_capped_at_max(self):
        long_summary = "s" * 5_000
        mock_agent = self._make_mock_agent(long_summary)
        with patch(
            "backend.App.orchestration.application.enforcement.untrusted_content.BaseAgent",
            return_value=mock_agent,
        ), patch.dict(os.environ, {"SWARM_QUARANTINE_MAX_OUTPUT_CHARS": "10"}):
            agent = QuarantineAgent(model="test-model")
            result = agent.summarize("data", source="web")
        self.assertLessEqual(len(result), 50)  # 10 + cap marker
        self.assertIn("quarantine: output capped", result)

    def test_state_provides_environment(self):
        mock_agent = self._make_mock_agent("ok")
        captured_kwargs: list[dict] = []

        def capture_init(**kwargs):
            captured_kwargs.append(kwargs)
            return mock_agent

        with patch(
            "backend.App.orchestration.application.enforcement.untrusted_content.BaseAgent",
            side_effect=capture_init,
        ):
            state = {"agent_config": {"quarantine": {"environment": "lmstudio"}}}
            agent = QuarantineAgent(state=state)
            agent.summarize("data", source="web")

        self.assertEqual("lmstudio", captured_kwargs[0].get("environment"))
