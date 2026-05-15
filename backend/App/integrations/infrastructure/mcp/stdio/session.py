from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from backend.App.integrations.infrastructure.mcp.stdio.mcp_pool import (
    MCPPool,
    coerce_mcp_config_dict,
    load_mcp_server_defs,
    mcp_preflight_check,
)

logger = logging.getLogger(__name__)

__all__ = [
    "MCPStdioSession",
    "MCPPool",
    "coerce_mcp_config_dict",
    "load_mcp_server_defs",
    "mcp_preflight_check",
]

_MCP_RX_EOF = object()


def _mcp_rpc_wait_sec() -> float:
    try:
        return max(_mcp_min_floor_sec(), float(os.getenv("SWARM_MCP_RPC_WAIT_SEC", str(_mcp_rpc_wait_default()))))
    except ValueError:
        return _mcp_rpc_wait_default()


def _mcp_config() -> dict[str, Any]:
    from backend.App.shared.infrastructure.app_config_load import load_app_config_json

    return load_app_config_json("mcp.json")


def _mcp_min_floor_sec() -> float:
    raw = _mcp_config().get("rpc_min_floor_sec", 5)
    if isinstance(raw, (int, float)) and raw > 0:
        return float(raw)
    raise RuntimeError("mcp.json: rpc_min_floor_sec must be a positive number")


def _mcp_rpc_wait_default() -> float:
    raw = _mcp_config().get("rpc_wait_sec", 600)
    if isinstance(raw, (int, float)) and raw > 0:
        return float(raw)
    raise RuntimeError("mcp.json: rpc_wait_sec must be a positive number")


def _mcp_init_timeout_default() -> float:
    raw = _mcp_config().get("init_timeout_sec", 60)
    if isinstance(raw, (int, float)) and raw > 0:
        return float(raw)
    raise RuntimeError("mcp.json: init_timeout_sec must be a positive number")


def _mcp_init_timeout_sec() -> float:
    try:
        return max(_mcp_min_floor_sec(), float(os.getenv("SWARM_MCP_INIT_TIMEOUT_SEC", str(_mcp_init_timeout_default()))))
    except ValueError:
        return _mcp_init_timeout_default()


def _frame_message(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


def _frame_message_newline(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def _read_one_message(stream) -> Optional[dict[str, Any]]:
    while True:
        line = stream.readline()
        if not line:
            return None
        if not line.lower().startswith(b"content-length:"):
            try:
                return json.loads(line.decode("utf-8").strip())
            except json.JSONDecodeError:
                logger.debug("MCP: skipping non-JSON stdout noise: %r", line[:120])
                continue
        try:
            n = int(line.decode("utf-8", errors="replace").split(":", 1)[1].strip())
        except (ValueError, IndexError):
            logger.debug("MCP: malformed Content-Length header: %r", line[:120])
            continue
        crlf = stream.read(2)
        if crlf != b"\r\n":
            stream.read(max(0, n))
            continue
        raw = stream.read(n)
        if len(raw) != n:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            logger.debug("MCP: JSON decode failed for framed message len=%d", n)
            continue


@dataclass
class MCPStdioSession:
    name: str
    command: list[str]
    cwd: Optional[str] = None
    env: Optional[dict[str, str]] = None
    _proc: Optional[subprocess.Popen] = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _next_id: int = 1
    _rx_q: queue.Queue = field(default_factory=queue.Queue, repr=False)
    _reader_thread: Optional[threading.Thread] = field(default=None, repr=False)
    _reader_stop: threading.Event = field(default_factory=threading.Event, repr=False)
    _stderr_tail: bytearray = field(default_factory=bytearray, repr=False)
    _newline_proto: bool = False

    @staticmethod
    def _drain_stderr_loop(proc: subprocess.Popen, tail: bytearray) -> None:
        err = proc.stderr
        if err is None:
            return
        try:
            while True:
                chunk = err.read(4096)
                if not chunk:
                    break
                tail.extend(chunk)
                if len(tail) > 4096:
                    del tail[:-4096]
        except Exception as exc:
            logger.debug("MCP stderr reader error: %s", exc)

    def _stdout_reader_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            self._rx_q.put(_MCP_RX_EOF)
            return
        out = proc.stdout
        try:
            while not self._reader_stop.is_set():
                msg = _read_one_message(out)
                if msg is None:
                    self._rx_q.put(_MCP_RX_EOF)
                    return
                self._rx_q.put(msg)
        except BaseException:
            self._rx_q.put(_MCP_RX_EOF)

    def start(self) -> None:
        if self._proc is not None:
            return
        self._stderr_tail.clear()
        self._proc = subprocess.Popen(
            self.command,
            cwd=self.cwd or None,
            env=self.env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        if self._proc.stdin is None or self._proc.stdout is None:
            raise RuntimeError("MCP subprocess missing pipes")
        threading.Thread(
            target=self._drain_stderr_loop,
            args=(self._proc, self._stderr_tail),
            daemon=True,
            name=f"mcp-stderr-{self.name}",
        ).start()
        self._reader_stop.clear()
        while True:
            try:
                self._rx_q.get_nowait()
            except queue.Empty:
                break
        rt = threading.Thread(
            target=self._stdout_reader_loop,
            daemon=True,
            name=f"mcp-stdout-{self.name}",
        )
        self._reader_thread = rt
        rt.start()

    def close(self) -> None:
        self._reader_stop.set()
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.terminate()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        try:
            if proc.stdout:
                proc.stdout.close()
        except OSError:
            pass
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=5.0)
            if self._reader_thread.is_alive():
                logger.warning("MCP %s: stdout reader thread did not exit after close", self.name)
            self._reader_thread = None
        self._proc = None

    def _write_msg(self, msg: dict[str, Any]) -> None:
        data = _frame_message_newline(msg) if self._newline_proto else _frame_message(msg)
        assert self._proc is not None and self._proc.stdin is not None
        self._proc.stdin.write(data)
        self._proc.stdin.flush()

    def request(
        self,
        method: str,
        params: Optional[dict[str, Any]] = None,
        cancel_event: Optional[threading.Event] = None,
        _deadline_override: Optional[float] = None,
        _force_newline: Optional[bool] = None,
    ) -> Any:
        self.start()
        if self._proc is None or not self._proc.stdin or not self._proc.stdout:
            raise RuntimeError("MCP session not started — call start() first")
        with self._lock:
            req_id = self._next_id
            self._next_id += 1
            msg = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
                "params": params or {},
            }
            use_newline = self._newline_proto if _force_newline is None else _force_newline
            data = _frame_message_newline(msg) if use_newline else _frame_message(msg)
            self._proc.stdin.write(data)
            self._proc.stdin.flush()

        deadline = _deadline_override if _deadline_override is not None else time.monotonic() + _mcp_rpc_wait_sec()
        _out_of_order: dict[int, dict] = {}
        while True:
            if req_id in _out_of_order:
                resp = _out_of_order.pop(req_id)
                if "error" in resp:
                    e = resp["error"]
                    raise RuntimeError(f"MCP {self.name} error: {e}")
                return resp.get("result")

            if cancel_event is not None and cancel_event.is_set():
                from backend.App.shared.domain.exceptions import OperationCancelled

                raise OperationCancelled(
                    source="mcp", detail=f"server={self.name}"
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if _deadline_override is not None:
                    raise RuntimeError(
                        f"MCP {self.name}: init timeout (SWARM_MCP_INIT_TIMEOUT_SEC={int(_mcp_init_timeout_sec())}s) — "
                        "increase via SWARM_MCP_INIT_TIMEOUT_SEC"
                    )
                raise RuntimeError(
                    f"MCP {self.name}: response wait timeout (SWARM_MCP_RPC_WAIT_SEC)"
                )
            try:
                resp = self._rx_q.get(timeout=min(max(0.1, remaining), 1.0))
            except queue.Empty:
                continue
            if resp is _MCP_RX_EOF:
                err = self._stderr_tail[-2000:].decode("utf-8", errors="replace") if self._stderr_tail else ""
                raise RuntimeError(
                    f"MCP {self.name}: EOF or corrupt response (reader). stderr: {err}"
                )
            if "id" not in resp:
                continue
            if resp.get("method") == "notifications/message":
                continue
            if resp["id"] != req_id:
                _out_of_order[resp["id"]] = resp
                continue
            if "error" in resp:
                e = resp["error"]
                raise RuntimeError(f"MCP {self.name} error: {e}")
            return resp.get("result")

    def handshake(
        self,
        cancel_event: Optional[threading.Event] = None,
        init_timeout_sec: Optional[float] = None,
    ) -> None:
        t_sec = init_timeout_sec if init_timeout_sec is not None else _mcp_init_timeout_sec()
        deadline = time.monotonic() + t_sec

        init_params = {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "ailouros", "version": "0.1"},
        }

        probe_budget = max(5.0, min(10.0, t_sec * 0.20))
        probe_deadline = time.monotonic() + probe_budget

        try:
            self.request(
                "initialize",
                init_params,
                _deadline_override=probe_deadline,
                cancel_event=cancel_event,
                _force_newline=True,
            )
            self._newline_proto = True
            logger.debug("MCP %s: protocol=newline-JSON (SDK ≥ v1.28)", self.name)
        except RuntimeError as exc:
            if "init timeout" not in str(exc):
                raise
            logger.info(
                "MCP %s: newline probe timed out (%.0fs), retrying with Content-Length framing",
                self.name,
                probe_budget,
            )
            self._newline_proto = False
            self.request(
                "initialize",
                init_params,
                _deadline_override=deadline,
                cancel_event=cancel_event,
                _force_newline=False,
            )
            logger.debug("MCP %s: protocol=Content-Length (legacy SDK < v1.28)", self.name)

        self.notify("notifications/initialized", {})

    def notify(self, method: str, params: Optional[dict[str, Any]] = None) -> None:
        if self._proc is None or not self._proc.stdin:
            raise RuntimeError("MCP session not started — call start() first")
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        with self._lock:
            self._write_msg(msg)

    def list_tools(self, cancel_event: Optional[threading.Event] = None) -> list[dict[str, Any]]:
        res = self.request("tools/list", {}, cancel_event=cancel_event)
        tools = (res or {}).get("tools") or []
        return list(tools)

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        cancel_event: Optional[threading.Event] = None,
    ) -> Any:
        from backend.App.shared.infrastructure.activity_recorder import (
            record as _record_activity,
        )
        from backend.App.shared.infrastructure.tracing import trace_span

        start = time.monotonic()
        try:
            with trace_span(
                "mcp.call_tool",
                attributes={"server": self.name, "tool": name},
            ):
                result = self.request(
                    "tools/call",
                    {"name": name, "arguments": arguments},
                    cancel_event=cancel_event,
                )
        except Exception as exc:
            _record_activity(
                "mcp_calls",
                {
                    "server": self.name,
                    "tool": name,
                    "args": arguments,
                    "status": "error",
                    "error": type(exc).__name__,
                    "message": str(exc),
                    "elapsed_ms": round((time.monotonic() - start) * 1000.0, 1),
                },
            )
            raise
        _record_activity(
            "mcp_calls",
            {
                "server": self.name,
                "tool": name,
                "args": arguments,
                "status": "ok",
                "result_preview": _summarise_tool_result(result),
                "elapsed_ms": round((time.monotonic() - start) * 1000.0, 1),
            },
        )
        return result


def _summarise_tool_result(result: Any) -> str:
    if isinstance(result, str):
        return result[:500]
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict):
                text = first.get("text")
                if isinstance(text, str):
                    return text[:500]
    return str(result)[:500] if result is not None else ""
