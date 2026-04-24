from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_READONLY_TOOL_NAMES: frozenset[str] = frozenset({
    "read_file",
    "read_multiple_files",
    "list_directory",
    "list_directory_tree",
    "directory_tree",
    "search_files",
    "get_file_info",
    "list_allowed_directories",
    "list_files",
    "find_files",
})


def _git_repo_validation_error(server_def: dict[str, Any]) -> str | None:
    if str(server_def.get("name") or "").strip() != "git":
        return None

    raw_args = server_def.get("args")
    if not isinstance(raw_args, list):
        return None

    try:
        repo_idx = raw_args.index("--repository")
        repo_value = str(raw_args[repo_idx + 1]).strip()
    except (ValueError, IndexError):
        return "Git MCP server is missing the --repository argument."

    if not repo_value:
        return "Git MCP server repository path is empty."

    repo_path = Path(repo_value).expanduser()
    if not repo_path.exists():
        return f"Configured Git MCP repository does not exist: {repo_path}"
    if not (repo_path / ".git").exists():
        return f"Configured Git MCP repository is not a Git repository: {repo_path}"
    return None


def _mcp_compact_tools_enabled() -> bool:
    explicit = os.getenv("SWARM_MCP_COMPACT_TOOLS", "").strip().lower()
    if explicit in ("1", "true", "yes", "on"):
        return True
    if explicit in ("0", "false", "no", "off"):
        return False
    route = os.getenv("SWARM_ROUTE_DEFAULT", "local").strip().lower()
    return route == "local"


def _mcp_tool_description_max_chars() -> int:
    env_value = os.getenv("SWARM_MCP_TOOL_DESCRIPTION_MAX_CHARS", "").strip()
    if env_value.isdigit():
        parsed_int = int(env_value)
        if parsed_int > 0:
            return parsed_int
    return 200


def coerce_mcp_config_dict(cfg: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(cfg, dict):
        return cfg
    servers = cfg.get("servers")
    if isinstance(servers, list) and len(servers) > 0:
        return cfg
    ms = cfg.get("mcpServers")
    if not isinstance(ms, dict) or not ms:
        return cfg
    conv: list[dict[str, Any]] = []
    for server_key, server_config in ms.items():
        if not isinstance(server_config, dict):
            continue
        name = str(server_key).strip() or "mcp"
        cmd = server_config.get("command")
        if cmd is None:
            continue
        entry: dict[str, Any] = {"name": name, "command": cmd}
        if isinstance(server_config.get("args"), list):
            entry["args"] = server_config["args"]
        if server_config.get("cwd") is not None:
            entry["cwd"] = server_config["cwd"]
        if isinstance(server_config.get("env"), dict):
            entry["env"] = server_config["env"]
        conv.append(entry)
    if not conv:
        return cfg
    result = dict(cfg)
    result["servers"] = conv
    return result


def load_mcp_server_defs(cfg: Any) -> list[dict[str, Any]]:
    if isinstance(cfg, str):
        from pathlib import Path

        config_path = Path(cfg).expanduser()
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict):
        return []
    cfg = coerce_mcp_config_dict(cfg)
    servers = cfg.get("servers")
    if not isinstance(servers, list):
        return []
    server_defs: list[dict[str, Any]] = []
    for server in servers:
        if not isinstance(server, dict):
            continue
        server_name = str(server.get("name") or "server").strip() or "server"
        cmd = server.get("command")
        if isinstance(cmd, str):
            command = [cmd]
        elif isinstance(cmd, list):
            command = [str(x) for x in cmd]
        else:
            continue
        command_args = server.get("args")
        if isinstance(command_args, list):
            command = command + [str(x) for x in command_args]
        if len(command) < 1:
            continue
        server_defs.append({"name": server_name, "command": command, "cwd": server.get("cwd"), "env": server.get("env")})
    return server_defs


class MCPPool:

    def __init__(self, defs: list[dict[str, Any]], cancel_event: Optional[Any] = None) -> None:
        from backend.App.integrations.infrastructure.mcp.stdio.session import (
            MCPStdioSession,
            _mcp_init_timeout_sec,
        )
        self._sessions: list[MCPStdioSession] = []
        self._cancel_event: Optional[Any] = cancel_event
        self._mcp_init_timeout_sec = _mcp_init_timeout_sec
        self._workspace_root: str = ""
        for d in defs:
            if d.get("name") == "workspace":
                cmd = d.get("command") or []
                if isinstance(cmd, list) and len(cmd) >= 2:
                    candidate = cmd[-1]
                    if isinstance(candidate, str) and os.path.isabs(candidate):
                        self._workspace_root = candidate
                        break
        for d in defs:
            raw_env = d.get("env")
            env: Optional[dict[str, str]] = None
            if isinstance(raw_env, dict):
                env = {str(k): str(v) for k, v in raw_env.items()}
            merged_env = None
            if env:
                merged_env = {**os.environ, **env}
            sess = MCPStdioSession(
                name=d["name"],
                command=d["command"],
                cwd=d.get("cwd"),
                env=merged_env,
            )
            self._sessions.append(sess)

    def __enter__(self) -> "MCPPool":
        started = []
        init_timeout = self._mcp_init_timeout_sec()
        try:
            for session in self._sessions:
                spawn_start_time = time.monotonic()
                session.start()
                spawn_elapsed_ms = (time.monotonic() - spawn_start_time) * 1000
                logger.info(
                    "MCP: phase=spawn server=%s elapsed_ms=%.0f",
                    session.name, spawn_elapsed_ms,
                )
                started.append(session)

                init_start_time = time.monotonic()
                try:
                    session.handshake(cancel_event=self._cancel_event, init_timeout_sec=init_timeout)
                except RuntimeError as exc:
                    init_elapsed_ms = (time.monotonic() - init_start_time) * 1000
                    logger.error(
                        "MCP: phase=init_failed server=%s elapsed_ms=%.0f error=%s",
                        session.name, init_elapsed_ms, exc,
                    )
                    raise
                init_elapsed_ms = (time.monotonic() - init_start_time) * 1000
                logger.info(
                    "MCP: phase=init server=%s elapsed_ms=%.0f",
                    session.name, init_elapsed_ms,
                )
        except BaseException:
            for session in started:
                try:
                    session.close()
                except Exception as close_exc:
                    logger.warning("MCP: error closing session %s during cleanup: %s", session.name, close_exc)
            raise
        return self

    def __exit__(self, *exc: Any) -> None:
        for session in self._sessions:
            try:
                session.close()
            except Exception as close_exc:
                logger.warning("MCP: error closing session %s: %s", session.name, close_exc)

    def set_cancel_event(self, ev: Optional[Any]) -> None:
        self._cancel_event = ev

    def sessions(self) -> list:
        return self._sessions

    def openai_tools(
        self,
        cancel_event: Optional[Any] = None,
        *,
        readonly: bool = False,
    ) -> list[dict[str, Any]]:
        start_time = time.monotonic()
        tools: list[dict[str, Any]] = []
        effective_cancel_event = cancel_event or self._cancel_event
        readonly_names = _READONLY_TOOL_NAMES
        env_override = os.getenv("SWARM_MCP_READONLY_TOOL_NAMES", "").strip()
        if env_override:
            readonly_names = frozenset(n.strip() for n in env_override.split(",") if n.strip())
        compact_mode = _mcp_compact_tools_enabled()
        desc_limit = _mcp_tool_description_max_chars() if compact_mode else 8000
        for sess in self._sessions:
            prefix = sess.name + "__"
            for tool_def in sess.list_tools(cancel_event=effective_cancel_event):
                tool_name = str(tool_def.get("name") or "tool")
                if readonly and tool_name not in readonly_names:
                    continue
                prefixed_tool_name = prefix + tool_name
                raw_description = str(tool_def.get("description") or "")
                description = raw_description[:desc_limit]
                if compact_mode and len(raw_description) > desc_limit:
                    logger.debug(
                        "MCP compact_tools: description for %r truncated from %d to %d chars",
                        prefixed_tool_name, len(raw_description), desc_limit,
                    )
                input_schema = tool_def.get("inputSchema") or {"type": "object", "properties": {}}
                if not isinstance(input_schema, dict):
                    input_schema = {"type": "object", "properties": {}}
                tools.append(
                    {
                        "type": "function",
                        "function": {
                            "name": prefixed_tool_name,
                            "description": f"[MCP:{sess.name}] {description}",
                            "parameters": input_schema,
                        },
                    }
                )
        elapsed_ms = (time.monotonic() - start_time) * 1000
        logger.info(
            "MCP: phase=tools_list tool_count=%d elapsed_ms=%.0f",
            len(tools), elapsed_ms,
        )
        return tools

    def dispatch_tool(
        self,
        prefixed_name: str,
        arguments: dict[str, Any],
        cancel_event: Optional[Any] = None,
    ) -> str:
        if "__" not in prefixed_name:
            raise ValueError(f"bad tool name: {prefixed_name!r}")
        sname, tname = prefixed_name.split("__", 1)
        if sname == "workspace" and self._workspace_root:
            _ws = self._workspace_root
            _updated = False
            for _path_key in ("path", "source", "destination"):
                _pv = arguments.get(_path_key)
                if isinstance(_pv, str) and _pv and not os.path.isabs(_pv):
                    arguments = {**arguments, _path_key: os.path.join(_ws, _pv)}
                    _updated = True
            paths_val = arguments.get("paths")
            if isinstance(paths_val, list):
                normalized = [
                    os.path.join(_ws, p) if isinstance(p, str) and p and not os.path.isabs(p) else p
                    for p in paths_val
                ]
                if normalized != paths_val:
                    arguments = {**arguments, "paths": normalized}
                    _updated = True
            if _updated:
                logger.info("MCP: normalized relative path(s) in %s for tool %s", list(arguments.keys()), prefixed_name)
        if sname == "workspace" and self._workspace_root:
            _ws_real = os.path.realpath(self._workspace_root)
            for _path_key in ("path", "source", "destination"):
                _pv = arguments.get(_path_key)
                if isinstance(_pv, str) and _pv and os.path.isabs(_pv):
                    _pv_real = os.path.realpath(_pv)
                    if _pv_real != _ws_real and not _pv_real.startswith(_ws_real + os.sep):
                        logger.warning(
                            "MCP: BLOCKED absolute path outside workspace_root: "
                            "path=%r workspace_root=%r tool=%s",
                            _pv, self._workspace_root, prefixed_name,
                        )
                        return (
                            f"ERROR: path '{_pv}' is outside the allowed workspace "
                            f"'{self._workspace_root}'. Use a relative path or a path "
                            f"inside the workspace."
                        )
            _paths_list = arguments.get("paths")
            if isinstance(_paths_list, list):
                for _p in _paths_list:
                    if isinstance(_p, str) and _p and os.path.isabs(_p):
                        _p_real = os.path.realpath(_p)
                        if _p_real != _ws_real and not _p_real.startswith(_ws_real + os.sep):
                            logger.warning(
                                "MCP: BLOCKED absolute path in 'paths' outside workspace_root: "
                                "path=%r workspace_root=%r tool=%s",
                                _p, self._workspace_root, prefixed_name,
                            )
                            return (
                                f"ERROR: path '{_p}' is outside the allowed workspace "
                                f"'{self._workspace_root}'. Use paths inside the workspace only."
                            )
        if sname == "workspace" and tname in ("write_file", "edit_file") and self._workspace_root:
            _write_path = arguments.get("path", "")
            if _write_path and os.path.isabs(_write_path):
                _parent = os.path.dirname(_write_path)
                if _parent and not os.path.isdir(_parent):
                    try:
                        os.makedirs(_parent, exist_ok=True)
                        logger.info("MCP: auto-created parent directory %s for %s", _parent, tname)
                    except OSError as _mkdir_err:
                        logger.warning("MCP: failed to auto-create %s: %s", _parent, _mkdir_err)
        effective_cancel_event = cancel_event or self._cancel_event
        for sess in self._sessions:
            if sess.name == sname:
                tool_result = sess.call_tool(tname, arguments, cancel_event=effective_cancel_event)
                if isinstance(tool_result, dict) and "content" in tool_result:
                    parts = tool_result.get("content") or []
                    texts: list[str] = []
                    for content_part in parts:
                        if isinstance(content_part, dict) and content_part.get("type") == "text":
                            texts.append(str(content_part.get("text") or ""))
                        elif isinstance(content_part, str):
                            texts.append(content_part)
                    return "\n".join(texts) if texts else json.dumps(tool_result, ensure_ascii=False)
                return json.dumps(tool_result, ensure_ascii=False)
        raise KeyError(prefixed_name)


def mcp_preflight_check(server_def: dict[str, Any]) -> dict[str, Any]:
    from backend.App.integrations.infrastructure.mcp.stdio.session import (
        MCPStdioSession,
        _mcp_init_timeout_sec,
    )

    server_name = server_def.get("name", "mcp")
    timeout = _mcp_init_timeout_sec()
    try:
        timeout_from_env = float(os.getenv("SWARM_MCP_SPAWN_TIMEOUT_SECS", str(timeout)))
        if timeout_from_env > 0:
            timeout = timeout_from_env
    except ValueError:
        pass

    raw_cmd = server_def.get("command", "")
    if isinstance(raw_cmd, str):
        full_command: list[str] = [raw_cmd] if raw_cmd else []
    elif isinstance(raw_cmd, list):
        full_command = [str(x) for x in raw_cmd]
    else:
        full_command = []
    raw_args = server_def.get("args")
    if isinstance(raw_args, list):
        full_command = full_command + [str(x) for x in raw_args]

    raw_env = server_def.get("env")
    merged_env: Optional[dict[str, str]] = None
    if isinstance(raw_env, dict):
        merged_env = {**os.environ, **{str(k): str(v) for k, v in raw_env.items()}}

    validation_error = _git_repo_validation_error(server_def)
    if validation_error is not None:
        logger.error(
            "MCP preflight FAILED: server=%s phase=validate error=%s",
            server_name,
            validation_error,
        )
        return {
            "status": "failed",
            "phase": "validate",
            "server": server_name,
            "error": validation_error,
        }

    sess = MCPStdioSession(
        name=server_name,
        command=full_command,
        cwd=server_def.get("cwd"),
        env=merged_env,
    )

    try:
        t_spawn = time.monotonic()
        sess.start()
        logger.info(
            "MCP preflight: phase=spawn server=%s elapsed_ms=%.0f",
            server_name, (time.monotonic() - t_spawn) * 1000,
        )

        t_init = time.monotonic()
        sess.handshake(init_timeout_sec=timeout)
        logger.info(
            "MCP preflight: phase=init server=%s elapsed_ms=%.0f",
            server_name, (time.monotonic() - t_init) * 1000,
        )

        t_tools = time.monotonic()
        tools: list[dict[str, Any]] = []
        for tool_entry in (sess.list_tools() or []):
            tools.append(tool_entry)
        logger.info(
            "MCP preflight: phase=tools_list server=%s tool_count=%d elapsed_ms=%.0f",
            server_name, len(tools), (time.monotonic() - t_tools) * 1000,
        )

        return {
            "status": "ok",
            "phase": "ready",
            "server": server_name,
            "tool_count": len(tools),
            "error": None,
        }
    except Exception as exc:
        phase = "spawn" if not getattr(sess, "_proc", None) else "tools_list"
        logger.error(
            "MCP preflight FAILED: server=%s phase=%s error=%s",
            server_name, phase, exc,
        )
        return {
            "status": "failed",
            "phase": phase,
            "server": server_name,
            "error": str(exc),
        }
    finally:
        try:
            sess.close()
        except Exception as close_error:
            logger.debug("mcp_preflight_check: close failed: %s", close_error)
