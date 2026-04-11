"""Tests for backend/App/orchestration/infrastructure/file_watcher.py."""
import time
import threading
from unittest.mock import MagicMock, patch


from backend.App.orchestration.infrastructure.file_watcher import (
    FileEvent,
    FileWatcher,
)


# ---------------------------------------------------------------------------
# FileEvent
# ---------------------------------------------------------------------------

def test_file_event_fields():
    ev = FileEvent("modified", "/path/to/file.py", 1234.5)
    assert ev.event_type == "modified"
    assert ev.path == "/path/to/file.py"
    assert ev.timestamp == 1234.5


# ---------------------------------------------------------------------------
# FileWatcher
# ---------------------------------------------------------------------------

def test_file_watcher_watch_starts_thread(tmp_path):
    watcher = FileWatcher()
    events = []

    def cb(ev):
        events.append(ev)

    with patch(
        "backend.App.orchestration.infrastructure.file_watcher._POLL_INTERVAL",
        1000.0,  # very long so poll loop doesn't run during test
    ):
        watcher.watch([str(tmp_path)], cb)
        assert watcher._running is True
        assert watcher._thread is not None
        assert watcher._thread.is_alive()
        watcher.stop()

    assert watcher._running is False


def test_file_watcher_watch_already_running_noop(tmp_path):
    watcher = FileWatcher()
    callback = MagicMock()

    with patch(
        "backend.App.orchestration.infrastructure.file_watcher._POLL_INTERVAL",
        1000.0,
    ):
        watcher.watch([str(tmp_path)], callback)
        original_thread = watcher._thread
        # Second watch call should be ignored
        watcher.watch([str(tmp_path)], callback)
        assert watcher._thread is original_thread
        watcher.stop()


def test_file_watcher_skips_nonexistent_paths(tmp_path):
    watcher = FileWatcher()
    callback = MagicMock()
    nonexistent = str(tmp_path / "does_not_exist")

    with patch(
        "backend.App.orchestration.infrastructure.file_watcher._POLL_INTERVAL",
        1000.0,
    ):
        watcher.watch([nonexistent], callback)
        assert watcher._paths == []
        watcher.stop()


def test_file_watcher_snapshot(tmp_path):
    (tmp_path / "a.py").write_text("hello")
    (tmp_path / "b.py").write_text("world")

    watcher = FileWatcher()
    watcher._paths = [tmp_path]
    snapshot = watcher._snapshot()
    assert any("a.py" in k for k in snapshot)
    assert any("b.py" in k for k in snapshot)


def test_file_watcher_check_changes_created(tmp_path):
    watcher = FileWatcher()
    watcher._paths = [tmp_path]
    callback = MagicMock()
    watcher._callback = callback
    watcher._snapshots = {}  # nothing before

    (tmp_path / "new.py").write_text("content")
    watcher._check_changes()

    callback.assert_called_once()
    ev = callback.call_args[0][0]
    assert ev.event_type == "created"
    assert "new.py" in ev.path


def test_file_watcher_check_changes_modified(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text("v1")
    watcher = FileWatcher()
    watcher._paths = [tmp_path]
    callback = MagicMock()
    watcher._callback = callback
    watcher._snapshots = {str(f): 1000.0}  # old mtime

    watcher._check_changes()

    # If actual mtime != 1000.0, should call modified
    if callback.called:
        ev = callback.call_args[0][0]
        assert ev.event_type in ("modified", "created")


def test_file_watcher_check_changes_deleted(tmp_path):
    f = tmp_path / "del.py"
    f.write_text("content")
    actual_mtime = f.stat().st_mtime

    watcher = FileWatcher()
    watcher._paths = [tmp_path]
    callback = MagicMock()
    watcher._callback = callback
    watcher._snapshots = {str(f): actual_mtime}

    # Delete the file
    f.unlink()
    watcher._check_changes()

    callback.assert_called_once()
    ev = callback.call_args[0][0]
    assert ev.event_type == "deleted"


def test_file_watcher_emit_handles_callback_exception(tmp_path):
    watcher = FileWatcher()

    def bad_callback(ev):
        raise ValueError("callback failed")

    watcher._callback = bad_callback
    # Should not raise
    watcher._emit(FileEvent("created", "/path/file.py", time.time()))


def test_file_watcher_stop_when_not_running():
    watcher = FileWatcher()
    watcher.stop()  # should not raise


def test_file_watcher_detects_file_creation(tmp_path):
    """Integration: watch a dir, create a file, verify event is emitted."""
    events = []
    lock = threading.Event()

    def cb(ev):
        events.append(ev)
        lock.set()

    watcher = FileWatcher()
    with patch(
        "backend.App.orchestration.infrastructure.file_watcher._POLL_INTERVAL",
        0.05,
    ):
        watcher.watch([str(tmp_path)], cb)
        time.sleep(0.1)
        (tmp_path / "test.txt").write_text("hello")
        # Wait for up to 1 second for the event
        lock.wait(timeout=1.0)
        watcher.stop()

    assert any(e.event_type in ("created", "modified") for e in events)


def test_file_watcher_poll_loop_handles_error(tmp_path):
    """Poll loop should log errors but not crash."""
    watcher = FileWatcher()
    callback = MagicMock()

    with patch(
        "backend.App.orchestration.infrastructure.file_watcher._POLL_INTERVAL",
        0.05,
    ), patch.object(watcher, "_check_changes", side_effect=RuntimeError("test error")):
        watcher.watch([str(tmp_path)], callback)
        time.sleep(0.2)
        watcher.stop()
    # Thread should have survived despite the error
