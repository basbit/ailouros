from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class VisualProbeUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class StartPlan:
    arguments: list[str]
    working_directory: Path
    base_url: str
    command_text: str
    environment: dict[str, str]


def render_start_command(command: str, *, port: int, base_url: str) -> str:
    return (
        command
        .replace("{port}", str(port))
        .replace("{host}", "127.0.0.1")
        .replace("{base_url}", base_url)
    )


def node_start_plan(
    package_json: Path,
    working_directory: Path,
    port: int,
    base_url: str,
    environment: dict[str, str],
) -> StartPlan:
    try:
        package_data = json.loads(package_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise VisualProbeUnavailable(f"Invalid package.json: {package_json}") from error
    scripts = (
        package_data.get("scripts")
        if isinstance(package_data, dict)
        else {}
    )
    if not isinstance(scripts, dict):
        scripts = {}
    script_name = pick_script(scripts)
    if not script_name:
        raise VisualProbeUnavailable(
            "package.json has no dev, preview, or start script"
        )
    package_manager = package_manager_for(working_directory)
    script_text = str(scripts.get(script_name) or "")
    command_flags = script_host_port_flags(package_data, script_text, port)
    arguments = script_arguments(package_manager, script_name, command_flags)
    return StartPlan(
        arguments=arguments,
        working_directory=working_directory,
        base_url=base_url,
        command_text=shlex.join(arguments),
        environment=environment,
    )


def pick_script(scripts: dict[str, Any]) -> str:
    for script_name in ("preview", "dev", "start"):
        if str(scripts.get(script_name) or "").strip():
            return script_name
    return ""


def package_manager_for(working_directory: Path) -> str:
    if (working_directory / "pnpm-lock.yaml").is_file():
        return "pnpm"
    if (working_directory / "yarn.lock").is_file():
        return "yarn"
    if (
        (working_directory / "bun.lockb").is_file()
        or (working_directory / "bun.lock").is_file()
    ):
        return "bun"
    return "npm"


def script_arguments(
    package_manager: str,
    script_name: str,
    command_flags: list[str],
) -> list[str]:
    if package_manager == "npm":
        return (
            ["npm", "run", script_name, "--", *command_flags]
            if command_flags
            else ["npm", "run", script_name]
        )
    if package_manager == "pnpm":
        return (
            ["pnpm", "run", script_name, "--", *command_flags]
            if command_flags
            else ["pnpm", "run", script_name]
        )
    if package_manager == "yarn":
        return ["yarn", script_name, *command_flags]
    if package_manager == "bun":
        return ["bun", "run", script_name, *command_flags]
    return ["npm", "run", script_name]


def script_host_port_flags(
    package_data: dict[str, Any],
    script_text: str,
    port: int,
) -> list[str]:
    lower_script_text = script_text.lower()
    dependencies: dict[str, Any] = {}
    for key in ("dependencies", "devDependencies"):
        value = package_data.get(key)
        if isinstance(value, dict):
            dependencies.update(value)
    dependency_names = {str(key).lower() for key in dependencies}
    if (
        "vite" in lower_script_text
        or "vite" in dependency_names
        or "astro" in dependency_names
    ):
        return ["--host", "127.0.0.1", "--port", str(port)]
    if "next" in lower_script_text or "next" in dependency_names:
        return ["-H", "127.0.0.1", "-p", str(port)]
    return []


def normalise_pages(pages: list[str], max_pages: int) -> list[str]:
    normalised_pages: list[str] = []
    for page in pages or ["/"]:
        value = str(page or "").strip()
        if not value:
            continue
        normalised_pages.append(value)
        if len(normalised_pages) >= max(1, max_pages):
            break
    return normalised_pages or ["/"]


__all__ = (
    "StartPlan",
    "VisualProbeUnavailable",
    "render_start_command",
    "node_start_plan",
    "pick_script",
    "package_manager_for",
    "script_arguments",
    "script_host_port_flags",
    "normalise_pages",
)
