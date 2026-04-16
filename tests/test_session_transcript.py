"""Tests for session_transcript — append_transcript_entry."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


from backend.App.orchestration.application.session_transcript import (
    append_transcript_entry,
)


class TestAppendTranscriptEntry(unittest.TestCase):
    """Integration-style tests that write real files into a tmpdir."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._artifacts_root = self._tmpdir.name
        # Patch SWARM_ARTIFACTS_DIR so all writes go to tmpdir
        self._env_patch = patch.dict(
            os.environ, {"SWARM_ARTIFACTS_DIR": self._artifacts_root}
        )
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()
        self._tmpdir.cleanup()

    def _transcript_path(self, task_id: str) -> Path:
        return Path(self._artifacts_root) / task_id / "session_transcript.jsonl"

    def _read_entries(self, task_id: str) -> list[dict]:
        path = self._transcript_path(task_id)
        if not path.exists():
            return []
        with path.open(encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    # ------------------------------------------------------------------
    # Basic write
    # ------------------------------------------------------------------

    def test_creates_file_and_writes_entry(self):
        state = {"task_id": "abc123"}
        delta = {"pm_output": "some planning output", "pm_model": "gpt-4"}
        append_transcript_entry("pm", state, delta, elapsed_ms=1234.5)

        entries = self._read_entries("abc123")
        self.assertEqual(1, len(entries))
        e = entries[0]
        self.assertEqual("pm", e["step"])
        self.assertEqual("abc123", e["task_id"])
        self.assertAlmostEqual(1234.5, e["elapsed_ms"], places=0)

    def test_appends_multiple_entries(self):
        state = {"task_id": "multi"}
        for step in ("pm", "ba", "arch"):
            append_transcript_entry(step, state, {f"{step}_output": f"output of {step}"})

        entries = self._read_entries("multi")
        self.assertEqual(3, len(entries))
        self.assertEqual(["pm", "ba", "arch"], [e["step"] for e in entries])

    # ------------------------------------------------------------------
    # No task_id — must silently skip
    # ------------------------------------------------------------------

    def test_skips_when_no_task_id(self):
        state = {}
        append_transcript_entry("pm", state, {"pm_output": "text"})
        # No file should exist anywhere
        root = Path(self._artifacts_root)
        all_files = list(root.rglob("session_transcript.jsonl"))
        self.assertEqual([], all_files)

    def test_skips_when_task_id_whitespace(self):
        state = {"task_id": "   "}
        append_transcript_entry("pm", state, {})
        root = Path(self._artifacts_root)
        self.assertEqual([], list(root.rglob("session_transcript.jsonl")))

    # ------------------------------------------------------------------
    # Output previews
    # ------------------------------------------------------------------

    def test_output_preview_included(self):
        state = {"task_id": "prev1"}
        long_text = "A" * 1000
        delta = {"ba_output": long_text}
        with patch.dict(os.environ, {"SWARM_TRANSCRIPT_OUTPUT_PREVIEW_CHARS": "100"}):
            append_transcript_entry("ba", state, delta)

        entries = self._read_entries("prev1")
        self.assertIn("ba_output_preview", entries[0])
        self.assertEqual(100, len(entries[0]["ba_output_preview"]))

    def test_output_preview_disabled_via_zero(self):
        state = {"task_id": "prev2"}
        delta = {"pm_output": "some text"}
        with patch.dict(os.environ, {"SWARM_TRANSCRIPT_OUTPUT_PREVIEW_CHARS": "0"}):
            append_transcript_entry("pm", state, delta)

        entries = self._read_entries("prev2")
        self.assertNotIn("pm_output_preview", entries[0])

    def test_non_string_output_not_included_in_preview(self):
        state = {"task_id": "prev3"}
        delta = {"dev_output": ["list", "not", "str"]}  # type: ignore[dict-item]
        append_transcript_entry("dev", state, delta)
        entries = self._read_entries("prev3")
        self.assertNotIn("dev_output_preview", entries[0])

    # ------------------------------------------------------------------
    # Passthrough keys
    # ------------------------------------------------------------------

    def test_model_provider_passthrough(self):
        state = {"task_id": "pass1"}
        delta = {"pm_model": "qwen3-9b", "pm_provider": "lmstudio", "pm_output": "text"}
        append_transcript_entry("pm", state, delta)

        entries = self._read_entries("pass1")
        self.assertEqual("qwen3-9b", entries[0]["pm_model"])
        self.assertEqual("lmstudio", entries[0]["pm_provider"])

    # ------------------------------------------------------------------
    # Output keys list
    # ------------------------------------------------------------------

    def test_output_keys_sorted(self):
        state = {"task_id": "keys1"}
        delta = {"z_output": "z", "a_output": "a", "m_output": "m"}
        append_transcript_entry("step", state, delta)

        entries = self._read_entries("keys1")
        self.assertEqual(sorted(delta.keys()), entries[0]["output_keys"])

    # ------------------------------------------------------------------
    # elapsed_ms=None
    # ------------------------------------------------------------------

    def test_elapsed_ms_none_stored_as_null(self):
        state = {"task_id": "elapsed1"}
        append_transcript_entry("pm", state, {}, elapsed_ms=None)
        entries = self._read_entries("elapsed1")
        self.assertIsNone(entries[0]["elapsed_ms"])

    # ------------------------------------------------------------------
    # Filesystem errors are swallowed
    # ------------------------------------------------------------------

    def test_os_error_does_not_raise(self):
        state = {"task_id": "err1"}
        with patch(
            "backend.App.orchestration.application.session_transcript.Path.open",
            side_effect=OSError("disk full"),
        ):
            # Must NOT raise
            append_transcript_entry("pm", state, {"pm_output": "text"})

    # ------------------------------------------------------------------
    # JSONL format validity
    # ------------------------------------------------------------------

    def test_each_line_is_valid_json(self):
        state = {"task_id": "jsonl1"}
        for step in ("pm", "ba"):
            append_transcript_entry(step, state, {f"{step}_output": f"text {step}"})

        path = self._transcript_path("jsonl1")
        with path.open(encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                with self.subTest(line=i):
                    parsed = json.loads(line)  # must not raise
                    self.assertIsInstance(parsed, dict)

    # ------------------------------------------------------------------
    # Timestamp format
    # ------------------------------------------------------------------

    def test_timestamp_is_iso8601(self):
        from datetime import datetime
        state = {"task_id": "ts1"}
        append_transcript_entry("pm", state, {})
        entries = self._read_entries("ts1")
        ts = entries[0]["ts"]
        # Must parse without error
        parsed = datetime.fromisoformat(ts)
        self.assertIsNotNone(parsed)

    # ------------------------------------------------------------------
    # Directory auto-created
    # ------------------------------------------------------------------

    def test_creates_missing_parent_dirs(self):
        new_task_id = "new-task-never-seen-before"
        state = {"task_id": new_task_id}
        path = self._transcript_path(new_task_id)
        self.assertFalse(path.exists())

        append_transcript_entry("pm", state, {})

        self.assertTrue(path.exists())
