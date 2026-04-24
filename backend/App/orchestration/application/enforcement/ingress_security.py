from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from backend.App.orchestration.application.agents.agent_runner import (
    run_agent_with_boundary,
)
from backend.App.orchestration.application.enforcement.untrusted_content import (
    wrap_untrusted,
)
from backend.App.orchestration.infrastructure.agents.base_agent import (
    BaseAgent,
    load_prompt,
    resolve_agent_model,
    resolve_default_environment,
)
from backend.App.shared.infrastructure.app_config_load import load_app_config_json
from backend.App.shared.domain.validators import is_truthy_value


def _compile_re_flags(names: list[Any]) -> int:
    flags = 0
    for name in names:
        flags |= getattr(re, str(name), 0)
    return flags


def _load_ingress_security_policy() -> tuple[
    dict[str, Any],
    tuple[re.Pattern[str], ...],
    re.Pattern[str],
    re.Pattern[str],
]:
    raw = load_app_config_json("ingress_security_policy.json")
    patterns: list[re.Pattern[str]] = []
    default_flags = list(raw.get("suspicious_patterns_regex_flags") or ["IGNORECASE"])
    for item in raw.get("suspicious_patterns") or ():
        if isinstance(item, str):
            pat_str = item
            flag_names = default_flags
        elif isinstance(item, dict) and "pattern" in item:
            pat_str = str(item["pattern"])
            flag_names = list(item.get("flags") or default_flags)
        else:
            raise ValueError(
                "ingress_security_policy.json: suspicious_patterns entries must be "
                "strings or objects with 'pattern'"
            )
        patterns.append(re.compile(pat_str, _compile_re_flags(flag_names)))
    at_pat = str(raw.get("at_mention_pattern") or "")
    url_pat = str(raw.get("url_pattern") or "")
    if not at_pat or not url_pat:
        raise ValueError(
            "ingress_security_policy.json: at_mention_pattern and url_pattern required"
        )
    return raw, tuple(patterns), re.compile(at_pat), re.compile(url_pat)


_CFG, _SUSPICIOUS_PATTERNS, _AT_MENTION_RE, _URL_RE = _load_ingress_security_policy()

_DEFAULT_PROMPT_PATH = str(_CFG["default_prompt_path"])
_MAX_INPUT_CHARS = int(
    os.getenv(
        "SWARM_SECURITY_REWRITE_MAX_INPUT_CHARS",
        str(int(_CFG["max_input_chars_default"])),
    )
)
_MAX_OUTPUT_CHARS = int(
    os.getenv(
        "SWARM_SECURITY_REWRITE_MAX_OUTPUT_CHARS",
        str(int(_CFG["max_output_chars_default"])),
    )
)
_FALLBACK_SYSTEM_PROMPT = str(_CFG["fallback_system_prompt"])
_HEURISTIC_EMPTY_SAFE_TASK = str(_CFG["heuristic_empty_safe_task"])
_REWRITE_USER_PROMPT_TEMPLATE = str(_CFG["rewrite_user_prompt_template"])
_INPUT_CAP_NOTE_TEMPLATE = str(_CFG["input_cap_note_template"])
_LIMITS: dict[str, Any] = dict(_CFG.get("limits") or {})
_SECURITY_REWRITE_MAX_TOKENS_DEFAULT = int(_CFG["security_rewrite_max_tokens_default"])


def _limit(name: str, default: int) -> int:
    raw = _LIMITS.get(name, default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


@dataclass
class SecurityRewriteResult:
    safe_text: str
    security_flags: list[str] = field(default_factory=list)
    risk_level: str = "low"
    dropped_text_summary: str = ""
    model: str = ""
    provider: str = ""
    used_fallback: bool = False

    def to_meta(self) -> dict[str, Any]:
        return {
            "safe_text": self.safe_text,
            "security_flags": list(self.security_flags),
            "risk_level": self.risk_level,
            "dropped_text_summary": self.dropped_text_summary,
            "model": self.model,
            "provider": self.provider,
            "used_fallback": self.used_fallback,
        }


def ingress_security_enabled(agent_config: dict[str, Any] | None = None) -> bool:
    return is_truthy_value(os.getenv("SWARM_SECURITY_REWRITE", "1"), default=True)


def _effective_role_cfg(agent_config: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(agent_config, dict):
        return {}
    for key in ("security_rewrite", "reviewer"):
        cfg = agent_config.get(key)
        if isinstance(cfg, dict):
            return cfg
    return {}


def _remote_api_kwargs(
    agent_config: dict[str, Any] | None, role_cfg: dict[str, Any]
) -> dict[str, Any]:
    if not isinstance(agent_config, dict):
        return {}

    remote_api = agent_config.get("remote_api")
    legacy_cloud = agent_config.get("cloud")
    provider = ""
    api_key = ""
    base_url = ""
    if isinstance(remote_api, dict):
        provider = str(remote_api.get("provider") or "").strip().lower()
        api_key = str(remote_api.get("api_key") or "").strip()
        base_url = str(remote_api.get("base_url") or "").strip()
    if isinstance(legacy_cloud, dict):
        if not api_key:
            api_key = str(legacy_cloud.get("api_key") or "").strip()
        if not base_url:
            base_url = str(legacy_cloud.get("base_url") or "").strip()
        if not provider and (api_key or base_url):
            provider = "anthropic"

    profiles = agent_config.get("remote_api_profiles")
    profile_name = str(
        role_cfg.get("remote_profile") or role_cfg.get("remote_api_profile") or ""
    ).strip()
    if profile_name and isinstance(profiles, dict):
        profile = profiles.get(profile_name)
        if isinstance(profile, dict):
            provider = str(profile.get("provider") or provider).strip().lower()
            api_key = str(profile.get("api_key") or api_key).strip()
            base_url = str(profile.get("base_url") or base_url).strip()

    out: dict[str, Any] = {}
    if provider:
        out["remote_provider"] = provider
    if api_key:
        out["remote_api_key"] = api_key
    if base_url:
        out["remote_base_url"] = base_url
    return out


def _extract_json(text: str) -> Any:
    text = (text or "").strip()
    if not text:
        raise json.JSONDecodeError("empty", "", 0)
    text = re.sub(r"```[a-zA-Z0-9_-]*\n?", "", text).strip().rstrip("`")
    decoder = json.JSONDecoder()
    for start_char in ("{", "["):
        idx = text.find(start_char)
        if idx == -1:
            continue
        try:
            obj, _ = decoder.raw_decode(text, idx)
            return obj
        except json.JSONDecodeError:
            continue
    return decoder.decode(text)


def _heuristic_rewrite(raw_text: str) -> SecurityRewriteResult:
    flags: list[str] = []
    kept_lines: list[str] = []
    dropped_lines: list[str] = []
    for line in (raw_text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(pattern.search(stripped) for pattern in _SUSPICIOUS_PATTERNS):
            dropped_lines.append(stripped)
            flags.append("prompt_injection")
            continue
        kept_lines.append(stripped)

    safe_text = "\n".join(kept_lines).strip()
    if not safe_text:
        safe_text = _HEURISTIC_EMPTY_SAFE_TASK

    mentions = list(dict.fromkeys(_AT_MENTION_RE.findall(raw_text or "")))
    urls = list(dict.fromkeys(_URL_RE.findall(raw_text or "")))
    extras: list[str] = []
    m_cap = _limit("heuristic_output_mentions_max", 10)
    u_cap = _limit("heuristic_output_urls_max", 8)
    if mentions:
        extras.append("Referenced files: " + ", ".join(mentions[:m_cap]))
    if urls:
        extras.append("Referenced URLs: " + ", ".join(urls[:u_cap]))
    if extras:
        safe_text = safe_text.rstrip() + "\n\n" + "\n".join(extras)

    dl_cap = _limit("heuristic_dropped_lines_in_summary", 5)
    ds_max = _limit("heuristic_dropped_summary_max_chars", 500)
    return SecurityRewriteResult(
        safe_text=safe_text[:_MAX_OUTPUT_CHARS],
        security_flags=list(dict.fromkeys((flags or []) + ["heuristic_fallback"])),
        risk_level="high" if flags else "medium",
        dropped_text_summary="; ".join(dropped_lines[:dl_cap])[:ds_max],
        model="heuristic_fallback",
        provider="heuristic_fallback",
        used_fallback=True,
    )


def rewrite_untrusted_input(
    raw_text: str,
    agent_config: dict[str, Any] | None = None,
    *,
    task_id: str = "",
    source: str = "user_input",
) -> SecurityRewriteResult:

    text = str(raw_text or "").strip()
    if not text:
        return SecurityRewriteResult(safe_text="")
    if not ingress_security_enabled(agent_config):
        return SecurityRewriteResult(
            safe_text=text[:_MAX_OUTPUT_CHARS],
            security_flags=["disabled"],
            risk_level="low",
            model="disabled",
            provider="disabled",
        )

    capped = text[:_MAX_INPUT_CHARS]
    if len(text) > _MAX_INPUT_CHARS:
        capped += _INPUT_CAP_NOTE_TEMPLATE.format(max_chars=_MAX_INPUT_CHARS)

    role_cfg = _effective_role_cfg(agent_config)
    try:
        prompt = load_prompt(_DEFAULT_PROMPT_PATH, _FALLBACK_SYSTEM_PROMPT)
        model = (
            str(role_cfg.get("model") or "").strip()
            or os.getenv("SWARM_SECURITY_REWRITE_MODEL", "").strip()
        )
        if not model:
            model = resolve_agent_model("REVIEWER")
        environment = (
            str(role_cfg.get("environment") or "").strip()
            or resolve_default_environment()
        )
        agent = BaseAgent(
            role="SECURITY_REWRITE",
            system_prompt=prompt,
            model=model,
            environment=environment,
            max_tokens=int(
                os.getenv(
                    "SWARM_SECURITY_REWRITE_MAX_TOKENS",
                    str(_SECURITY_REWRITE_MAX_TOKENS_DEFAULT),
                )
            ),
            **_remote_api_kwargs(agent_config, role_cfg),
        )
        boundary_state = {
            "task_id": task_id,
            "_current_step_id": "security_rewrite",
        }
        user_prompt = _REWRITE_USER_PROMPT_TEMPLATE.format(
            source=source,
            wrapped=wrap_untrusted(capped, source=source),
        )
        raw_output = run_agent_with_boundary(
            boundary_state,
            agent,
            user_prompt,
            step_id="security_rewrite",
        )
        payload = _extract_json(raw_output)
        if not isinstance(payload, dict):
            raise ValueError("security rewrite output is not a JSON object")

        safe_task = str(payload.get("safe_task") or "").strip()
        constraints_raw = payload.get("constraints")
        preserved_literals_raw = payload.get("preserved_literals")
        flags_raw = payload.get("security_flags")
        if not safe_task:
            raise ValueError("security rewrite returned empty safe_task")

        parts = [safe_task]
        constraints = (
            [str(item).strip() for item in (constraints_raw or []) if str(item).strip()]
            if isinstance(constraints_raw, list)
            else []
        )
        c_max = _limit("assemble_constraints_max", 12)
        if constraints:
            parts.append("Constraints:\n- " + "\n- ".join(constraints[:c_max]))

        literals = (
            [
                str(item).strip()
                for item in (preserved_literals_raw or [])
                if str(item).strip()
            ]
            if isinstance(preserved_literals_raw, list)
            else []
        )

        mentions = list(dict.fromkeys(_AT_MENTION_RE.findall(text)))
        urls = list(dict.fromkeys(_URL_RE.findall(text)))
        if mentions:
            literals.extend(
                f"@{item}" for item in mentions if f"@{item}" not in literals
            )
        for url in urls:
            if url not in literals:
                literals.append(url)
        lit_max = _limit("assemble_literals_max", 20)
        if literals:
            parts.append(
                "Preserve these literal references:\n- "
                + "\n- ".join(literals[:lit_max])
            )

        safe_text = "\n\n".join(part.strip() for part in parts if part.strip())
        dr_max = _limit("rewrite_dropped_summary_max_chars", 800)
        return SecurityRewriteResult(
            safe_text=safe_text[:_MAX_OUTPUT_CHARS],
            security_flags=(
                [str(item).strip() for item in (flags_raw or []) if str(item).strip()]
                if isinstance(flags_raw, list)
                else []
            ),
            risk_level=str(payload.get("risk_level") or "low").strip().lower() or "low",
            dropped_text_summary=str(payload.get("dropped_text_summary") or "").strip()[
                :dr_max
            ],
            model=agent.used_model or model,
            provider=agent.used_provider or environment,
        )
    except Exception:
        return _heuristic_rewrite(text)
